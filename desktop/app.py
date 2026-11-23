"""
zero-copy-agent Unified Desktop Application (v1.0-beta)
All-in-One Desktop Window + System Tray.
Runs FastAPI/Uvicorn, WebView2 native desktop window, and System Tray in a single unified process.
"""
import os
import sys
import socket
import logging
import threading
import time
import subprocess
from pathlib import Path

# Add project root to sys.path (safe for frozen PyInstaller packages)
if getattr(sys, "frozen", False):
    ROOT_DIR = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
else:
    ROOT_DIR = Path(__file__).resolve().parent.parent
    if str(ROOT_DIR) not in sys.path:
        sys.path.insert(0, str(ROOT_DIR))

# Logging configuration
LOG_DIR = Path(os.getenv("LOCALAPPDATA", str(Path.home()))) / "zero-copy-agent"
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / "zero-copy-agent.log"

logging.basicConfig(
    filename=str(LOG_FILE),
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    encoding="utf-8"
)

class DualWriter:
    def __init__(self, stream, log_func):
        self.stream = stream
        self.log_func = log_func
        self._in_write = False
        self._encoding = getattr(stream, "encoding", "utf-8") or "utf-8"
        self._errors = getattr(stream, "errors", "replace") or "replace"

    @property
    def encoding(self):
        return self._encoding

    @property
    def errors(self):
        return self._errors

    def write(self, message):
        if self._in_write:
            return len(message) if message else 0
        self._in_write = True
        try:
            if self.stream:
                try:
                    self.stream.write(message)
                    self.stream.flush()
                except Exception:
                    pass
            try:
                msg = str(message).strip()
                if msg:
                    self.log_func(msg)
            except Exception:
                pass
        finally:
            self._in_write = False
        return len(message) if message else 0

    def flush(self):
        if self.stream:
            try:
                self.stream.flush()
            except Exception:
                pass

    def isatty(self):
        if self.stream and hasattr(self.stream, "isatty"):
            try:
                return bool(self.stream.isatty())
            except Exception:
                return False
        return False

    def fileno(self):
        if self.stream and hasattr(self.stream, "fileno"):
            try:
                return self.stream.fileno()
            except Exception:
                pass
        raise OSError("fileno not supported")

if sys.stdout is None or not hasattr(sys.stdout, "write"):
    sys.stdout = DualWriter(None, logging.info)
else:
    sys.stdout = DualWriter(sys.stdout, logging.info)

if sys.stderr is None or not hasattr(sys.stderr, "write"):
    sys.stderr = DualWriter(None, logging.error)
else:
    sys.stderr = DualWriter(sys.stderr, logging.error)

from core.config import config, get_runtime_cwd
from desktop.app_icon import create_default_icon, ensure_icon_file

# Check GUI availability
HAS_WEBVIEW = False
HAS_PYSTRAY = False
try:
    import webview
    HAS_WEBVIEW = True
except Exception as e:
    logging.warning(f"pywebview is not available: {e}")

try:
    import pystray
    from pystray import MenuItem
    HAS_PYSTRAY = True
except Exception as e:
    logging.warning(f"pystray is not available: {e}")


def is_gui_supported() -> bool:
    """Returns True if display environment and GUI libraries are available."""
    if not (HAS_WEBVIEW and HAS_PYSTRAY):
        return False
    if sys.platform.startswith("linux"):
        if not os.getenv("DISPLAY") and not os.getenv("WAYLAND_DISPLAY"):
            return False
    return True


def find_free_port(preferred: int = 8000) -> int:
    """Checks preferred port; if occupied, finds next available port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        if s.connect_ex(("127.0.0.1", preferred)) != 0:
            return preferred
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]




class ZeroCopyUnifiedApp:
    def __init__(self, port: int = None, start_hidden: bool = False, is_post_update: bool = False):
        self.preferred_port = port or config.server.port or 8000
        self.port = self.preferred_port
        self.host = config.server.host or "0.0.0.0"
        self.client_host = "127.0.0.1" if self.host in ("0.0.0.0", "::", "") else self.host
        self.api_url = f"http://{self.client_host}:{self.port}/v1"
        self.mcp_url = f"http://{self.client_host}:{self.port}/sse"
        self.is_post_update = is_post_update
        self.start_hidden = False if is_post_update else start_hidden

        self.server_thread = None
        self.uvicorn_server = None
        self.tray_icon = None
        self.window = None
        self.stop_event = threading.Event()
        self.is_running = True
        self._notified_tray_hide = False

    def _run_post_update_watcher(self, has_gui: bool):
        """Monitors health check after self-update and brings window/browser to front."""
        import urllib.request
        import webbrowser
        from core.version import __version__

        log_file = get_runtime_cwd() / "update.log"
        def log_u(msg: str):
            try:
                with open(log_file, "a", encoding="utf-8") as f:
                    f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] [POST-UPDATE] {msg}\n")
            except Exception:
                pass

        log_u(f"Watcher started (has_gui={has_gui}). Waiting for http://127.0.0.1:{self.port}/health -> 200 OK...")

        health_url = f"http://127.0.0.1:{self.port}/health"
        server_ok = False
        start_t = time.time()
        while time.time() - start_t < 30.0:
            try:
                req = urllib.request.Request(health_url)
                with urllib.request.urlopen(req, timeout=1.0) as resp:
                    if resp.status == 200:
                        server_ok = True
                        break
            except Exception:
                time.sleep(0.3)

        if server_ok:
            log_u(f"Server is healthy (200 OK) after {round(time.time() - start_t, 2)}s.")
        else:
            log_u("Server health check timed out after 30s. Proceeding with window activation.")

        ui_url = f"http://{self.client_host}:{self.port}/?post_update=1&version={__version__}"

        if has_gui and self.window:
            log_u("Activating desktop GUI window...")
            time.sleep(0.5)
            try:
                self.window.show()
                self.window.restore()
                if hasattr(self.window, "on_top"):
                    self.window.on_top = True
                    time.sleep(0.15)
                    self.window.on_top = False
                if hasattr(self.window, "focus"):
                    self.window.focus()
            except Exception as e:
                log_u(f"Webview window activation note: {e}")

            if sys.platform == "win32":
                try:
                    import ctypes
                    hwnd = ctypes.windll.user32.FindWindowW(None, "zero-copy-agent")
                    if hwnd:
                        ctypes.windll.user32.ShowWindow(hwnd, 9)  # SW_RESTORE
                        ctypes.windll.user32.SetForegroundWindow(hwnd)
                        log_u(f"SetForegroundWindow activated successfully (HWND {hwnd}).")
                except Exception as e:
                    log_u(f"Win32 SetForegroundWindow warning: {e}")
        else:
            log_u(f"Opening browser Web UI: {ui_url}")
            try:
                webbrowser.open(ui_url)
                log_u("Browser opened successfully.")
            except Exception as e:
                log_u(f"Failed to open browser: {e}")

    def copy_to_clipboard(self, text: str) -> bool:
        """Copies text to system clipboard."""
        try:
            import pyperclip
            pyperclip.copy(text)
            return True
        except ImportError:
            if sys.platform == "win32":
                subprocess.run(f"echo {text}|clip", shell=True)
                return True
            elif sys.platform == "darwin":
                subprocess.run("pbcopy", input=text.encode(), check=False)
                return True
            elif sys.platform.startswith("linux"):
                try:
                    subprocess.run(["xclip", "-selection", "clipboard"], input=text.encode(), check=False)
                    return True
                except FileNotFoundError:
                    pass
        return False

    def notify_tray(self, message: str, title: str = "zero-copy-agent"):
        """Displays balloon notification in system tray."""
        if self.tray_icon and hasattr(self.tray_icon, "notify"):
            try:
                self.tray_icon.notify(message, title=title)
            except Exception:
                pass

    def start_api_server(self):
        """Starts FastAPI/Uvicorn in dedicated background thread."""
        import uvicorn
        from main import app

        app.state.desktop_app = self

        uv_config = uvicorn.Config(
            app=app,
            host=self.host,
            port=self.port,
            log_level="warning",
            loop="asyncio",
            use_colors=False
        )
        self.uvicorn_server = uvicorn.Server(uv_config)

        def run_server():
            logging.info(f"Starting API server on http://{self.client_host}:{self.port}")
            self.uvicorn_server.run()

        self.server_thread = threading.Thread(target=run_server, daemon=True)
        self.server_thread.start()

    def show_window(self, icon=None, item=None):
        """Restores and brings main window to front."""
        if self.window:
            try:
                self.window.show()
                self.window.restore()
            except Exception as e:
                logging.error(f"Error restoring window: {e}")

    def hide_window(self):
        """Hides main window to tray."""
        if self.window:
            self.window.hide()

    def open_browser_auth(self, provider: str, account_id: str = None):
        """Launches interactive browser authentication in separate isolated subprocess."""
        try:
            prov = provider.strip().lower()
            # If local API server is running, delegate to api_web_auth to release background engine safely
            try:
                import urllib.request
                target_url = f"http://{self.client_host}:{self.port}/v1/providers/web-auth/{prov}"
                if account_id:
                    target_url += f"?account_id={account_id}"
                req = urllib.request.Request(target_url, data=b"", method="POST")
                with urllib.request.urlopen(req, timeout=4) as resp:
                    if resp.status == 200:
                        self.notify_tray(f"Открывается окно входа для {prov.upper()}...", title="zero-copy-agent Auth")
                        return
            except Exception as net_ex:
                logging.debug(f"[Desktop App] Delegating auth to HTTP API skipped ({net_ex}), spawning directly.")

            if getattr(sys, "frozen", False):
                cmd = [sys.executable, "--auth", prov]
            else:
                cmd = [sys.executable, str(Path(__file__).resolve()), "--auth", prov]
            if account_id:
                cmd.extend(["--account-id", account_id])
            cwd_path = get_runtime_cwd()
            logging.info(f"Spawning browser auth subprocess: {cmd} (cwd={cwd_path})")
            subprocess.Popen(cmd, cwd=str(cwd_path))
            self.notify_tray(f"Открывается окно входа для {prov.upper()}...", title="zero-copy-agent Auth")
        except Exception as e:
            logging.exception(f"Failed to spawn auth subprocess for {provider}: {e}")
            if sys.platform == "win32":
                try:
                    import ctypes
                    ctypes.windll.user32.MessageBoxW(
                        0,
                        f"Не удалось запустить процесс авторизации для {provider}:\n\n{e}",
                        "zero-copy-agent | Ошибка запуска авторизации",
                        0x10 | 0x0
                    )
                except Exception:
                    pass

    def auto_setup_ides(self):
        """Configures all detected IDEs in 1 click."""
        try:
            from core.ide_setup import configure_targets
            results = configure_targets(["all"])
            configured = [name for name, ok, _ in results if ok]
            msg = "Настроены среды:\n" + "\n".join(f"• {c}" for c in configured)
            self.notify_tray(msg, title="zero-copy-agent IDE")
        except Exception as e:
            self.notify_tray(f"Ошибка настройки: {e}", title="zero-copy-agent IDE")

    def stop(self, icon=None, item=None):
        """Cleanly terminates the application."""
        if not self.is_running:
            return
        import traceback
        logging.info("Stopping zero-copy-agent application... Call stack:\n%s", "".join(traceback.format_stack()))
        self.is_running = False
        self.stop_event.set()

        if self.uvicorn_server:
            self.uvicorn_server.should_exit = True
        if self.tray_icon:
            try:
                self.tray_icon.stop()
            except Exception:
                pass
        if self.window:
            try:
                self.window.destroy()
            except Exception:
                pass
        # Force terminate process cleanly on Windows
        import os
        os._exit(0)

    def setup_tray(self):
        """Builds system tray icon and menu."""
        if not HAS_PYSTRAY:
            return None

        icon_path = ensure_icon_file()
        try:
            from PIL import Image
            image = Image.open(icon_path)
        except Exception:
            image = create_default_icon(64)

        def _copy_api(icon=None, item=None):
            self.copy_to_clipboard(self.api_url)
            logging.info(f"Copied API URL to clipboard: {self.api_url}")
            self.notify_tray(f"Скопировано: {self.api_url}", title="zero-copy-agent API")

        def _copy_mcp(icon=None, item=None):
            self.copy_to_clipboard(self.mcp_url)
            logging.info(f"Copied MCP URL to clipboard: {self.mcp_url}")
            self.notify_tray(f"Скопировано: {self.mcp_url}", title="zero-copy-agent MCP")

        menu = pystray.Menu(
            pystray.MenuItem("Открыть zero-copy-agent", self.show_window, default=True),
            pystray.MenuItem(f"Статус: Порт {self.port} активен", lambda icon, item: None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Скопировать API URL (Cursor/IDE)", _copy_api),
            pystray.MenuItem("Скопировать MCP URL (Antigravity)", _copy_mcp),
            pystray.MenuItem("Настроить все IDE (1 клик)", lambda icon, item: self.auto_setup_ides()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Войти в Gemini", lambda icon, item: self.open_browser_auth("gemini")),
            pystray.MenuItem("Войти в ChatGPT", lambda icon, item: self.open_browser_auth("chatgpt")),
            pystray.MenuItem("Войти в Claude", lambda icon, item: self.open_browser_auth("claude")),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Выход", self.stop)
        )

        self.tray_icon = pystray.Icon(
            name="zero-copy-agent",
            icon=image,
            title=f"zero-copy-agent (Порт {self.port})",
            menu=menu
        )
        return self.tray_icon

    def run(self, force_cli: bool = False):
        """Main application lifecycle."""
        # 1. Start background API
        self.start_api_server()

        # If headless or CLI requested
        if force_cli or not is_gui_supported():
            if self.is_post_update:
                threading.Thread(target=self._run_post_update_watcher, args=(False,), daemon=True).start()
            print(f"\n[CLI Mode] zero-copy-agent запущен в фоновом консольном режиме")
            print(f"  API Endpoint : {self.api_url}")
            print(f"  Web Dashboard: http://{self.client_host}:{self.port}/")
            print(f"  Swagger Docs : http://{self.client_host}:{self.port}/docs")
            print(f"  Нажмите CTRL+C для остановки.\n")
            try:
                while self.is_running:
                    time.sleep(1)
            except KeyboardInterrupt:
                self.stop()
            return

        # 2. Build and start system tray in background thread
        tray = self.setup_tray()
        if tray:
            tray_thread = threading.Thread(target=tray.run, daemon=True)
            tray_thread.start()

        # 3. Wait for FastAPI server socket to be active and ready to accept HTTP
        def wait_for_server(host: str, port: int, timeout: float = 12.0) -> bool:
            start_time = time.time()
            while time.time() - start_time < timeout:
                try:
                    with socket.create_connection((host, port), timeout=0.3):
                        return True
                except (OSError, ConnectionRefusedError):
                    time.sleep(0.08)
            return False

        ready = wait_for_server(self.client_host, self.port, timeout=12.0)
        if not ready:
            logging.warning("Server took longer than expected to bind port, proceeding anyway...")

        # 4. Create unified desktop window in main thread (socket is 100% active, prevents ERR_CONNECTION_REFUSED)
        self.window = webview.create_window(
            title="zero-copy-agent",
            url=f"http://{self.client_host}:{self.port}/",
            width=1180,
            height=760,
            min_size=(960, 620),
            hidden=self.start_hidden,
            background_color="#0b0f17",
            text_select=True
        )

        from main import app as fastapi_app
        fastapi_app.state.desktop_window = self.window
        fastapi_app.state.desktop_app = self

        def on_closing():
            """Intercept close [X] button -> hide to tray instead of killing server."""
            if not self.stop_event.is_set():
                self.window.hide()
                if not self._notified_tray_hide:
                    self._notified_tray_hide = True
                    self.notify_tray(
                        "Приложение свернуто в трей и продолжает работать.\nДважды кликните по иконке, чтобы открыть окно.",
                        title="zero-copy-agent"
                    )
                return False
            return True

        self.window.events.closing += on_closing

        if self.is_post_update:
            threading.Thread(target=self._run_post_update_watcher, args=(True,), daemon=True).start()

        # Blocking GUI event loop in main thread
        try:
            logging.info("Starting webview GUI event loop...")
            webview.start(debug=False)
            logging.info("webview GUI event loop exited normally.")
        except Exception as e:
            logging.exception(f"Fatal error in webview.start: {e}")
        finally:
            self.stop()


def main():
    if "--daemon" in sys.argv:
        # Launch detached background process
        cmd = [sys.executable]
        if not getattr(sys, "frozen", False):
            cmd.append(str(Path(__file__).resolve()))
        extra_args = [a for a in sys.argv[1:] if a != "--daemon"]
        if "--headless" not in extra_args and "--cli" not in extra_args:
            extra_args.append("--headless")
        cmd.extend(extra_args)

        creationflags = 0
        if sys.platform == "win32":
            creationflags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP

        from core.config import get_runtime_cwd, config
        proc = subprocess.Popen(
            cmd,
            creationflags=creationflags,
            close_fds=True,
            cwd=str(get_runtime_cwd())
        )

        target_port = config.server.port or 8000
        if "--port" in sys.argv:
            try:
                target_port = int(sys.argv[sys.argv.index("--port") + 1])
            except Exception:
                pass

        health_url = f"http://127.0.0.1:{target_port}/health"
        started = False
        start_wait = time.time()
        import httpx
        while time.time() - start_wait < 15.0:
            try:
                with httpx.Client(timeout=1.0) as client:
                    r = client.get(health_url)
                    if r.status_code == 200:
                        started = True
                        break
            except Exception:
                pass
            time.sleep(0.5)

        if started:
            print(f"ZCA daemon started (PID {proc.pid})")
            sys.exit(0)
        else:
            print(f"[ERROR] ZCA daemon failed to respond at {health_url} within 15s.")
            sys.exit(1)

    if "--smoke-test" in sys.argv:
        from core.config import config
        import httpx
        target_port = 8000
        if "--port" in sys.argv:
            try:
                target_port = int(sys.argv[sys.argv.index("--port") + 1])
            except Exception:
                pass
        else:
            target_port = config.server.port or 8000

        app_inst = ZeroCopyUnifiedApp(port=target_port, start_hidden=True)
        srv_thread = threading.Thread(target=app_inst._start_uvicorn, daemon=True)
        srv_thread.start()

        health_url = f"http://127.0.0.1:{target_port}/health"
        status_url = f"http://127.0.0.1:{target_port}/v1/ui/status"
        success = False
        start_wait = time.time()
        while time.time() - start_wait < 10.0:
            try:
                with httpx.Client(timeout=1.0) as client:
                    r_h = client.get(health_url)
                    r_s = client.get(status_url)
                    if r_h.status_code == 200 and r_s.status_code == 200:
                        print(f"[Smoke Test] Health: OK ({r_h.status_code}), UI Status: OK ({r_s.status_code})")
                        success = True
                        break
            except Exception:
                pass
            time.sleep(0.3)

        if success:
            time.sleep(2.0)
            print("[Smoke Test] PASSED successfully.")
            sys.exit(0)
        else:
            print(f"[Smoke Test] FAILED: server did not respond at {health_url} within 10s.")
            sys.exit(1)

    if "--auth" in sys.argv:
        provider = "gemini"
        account_id = None
        try:
            idx = sys.argv.index("--auth")
            if len(sys.argv) > idx + 1:
                provider = sys.argv[idx + 1]
            if "--account-id" in sys.argv:
                acc_idx = sys.argv.index("--account-id")
                if len(sys.argv) > acc_idx + 1:
                    account_id = sys.argv[acc_idx + 1]
            logging.info(f"Main entrypoint executing desktop auth for '{provider}' (account_id={account_id})")
            from core.auth import authenticate_desktop
            authenticate_desktop(provider, account_id=account_id)
            sys.exit(0)
        except Exception as e:
            logging.exception(f"Fatal error during desktop authentication for {provider}: {e}")
            if sys.platform == "win32":
                try:
                    import ctypes
                    ctypes.windll.user32.MessageBoxW(
                        0,
                        f"Произошла ошибка при авторизации в {provider}:\n\n{e}",
                        "zero-copy-agent | Ошибка авторизации",
                        0x10 | 0x0
                    )
                except Exception:
                    pass
            sys.exit(1)

    if "--help" in sys.argv or "-h" in sys.argv:
        print("zero-copy-agent — Unified Desktop Application & AI Gateway")
        print("\nИспользование:")
        print("  zero-copy-agent [ОПЦИИ]")
        print("\nОпции:")
        print("  --cli, --headless   Запуск в консольном/фоновом режиме без окна")
        print("  --minimized         Запуск сразу в системном трее (без открытия окна)")
        print("  --auth <provider>   Запуск помощника авторизации (gemini, chatgpt, claude)")
        print("  --port <port>       Указать порт сервера (по умолчанию 8000)")
        print("  --version           Показать версию")
        print("  --help, -h          Показать эту справку")
        sys.exit(0)

    if "--version" in sys.argv:
        from core import __version__
        print(f"zero-copy-agent v{__version__}")
        sys.exit(0)


    force_cli = ("--cli" in sys.argv) or ("--headless" in sys.argv)
    start_hidden = ("--minimized" in sys.argv) or ("--tray-only" in sys.argv)
    is_post_update = ("--post-update" in sys.argv)
    if is_post_update:
        start_hidden = False

    port = None
    if "--port" in sys.argv:
        try:
            idx = sys.argv.index("--port")
            port = int(sys.argv[idx + 1])
        except (ValueError, IndexError):
            pass

    app = ZeroCopyUnifiedApp(port=port, start_hidden=start_hidden, is_post_update=is_post_update)
    app.run(force_cli=force_cli)


if __name__ == "__main__":
    main()
