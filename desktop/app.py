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

from core.config import config
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
    def __init__(self, port: int = None, start_hidden: bool = False):
        self.preferred_port = port or config.server.port or 8000
        self.port = self.preferred_port
        self.host = config.server.host or "0.0.0.0"
        self.client_host = "127.0.0.1" if self.host in ("0.0.0.0", "::", "") else self.host
        self.api_url = f"http://{self.client_host}:{self.port}/v1"
        self.mcp_url = f"http://{self.client_host}:{self.port}/sse"
        self.start_hidden = start_hidden

        self.server_thread = None
        self.uvicorn_server = None
        self.tray_icon = None
        self.window = None
        self.stop_event = threading.Event()
        self.is_running = True
        self._notified_tray_hide = False

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

    def open_browser_auth(self, provider: str):
        """Launches interactive browser authentication in separate isolated subprocess."""
        try:
            prov = provider.strip().lower()
            if getattr(sys, "frozen", False):
                cmd = [sys.executable, "--auth", prov]
            else:
                cmd = [sys.executable, str(Path(__file__).resolve()), "--auth", prov]
            logging.info(f"Spawning browser auth subprocess: {cmd}")
            subprocess.Popen(cmd, cwd=str(ROOT_DIR))
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
            pystray.MenuItem("📋 Скопировать API URL (Cursor/IDE)", _copy_api),
            pystray.MenuItem("🔌 Скопировать MCP URL (Antigravity)", _copy_mcp),
            pystray.MenuItem("⚡ Настроить все IDE (1 клик)", lambda icon, item: self.auto_setup_ides()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("🔑 Войти в Gemini", lambda icon, item: self.open_browser_auth("gemini")),
            pystray.MenuItem("🔑 Войти в ChatGPT", lambda icon, item: self.open_browser_auth("chatgpt")),
            pystray.MenuItem("🔑 Войти в Claude", lambda icon, item: self.open_browser_auth("claude")),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("🚪 Выход", self.stop)
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
    if "--auth" in sys.argv:
        provider = "gemini"
        try:
            idx = sys.argv.index("--auth")
            if len(sys.argv) > idx + 1:
                provider = sys.argv[idx + 1]
            logging.info(f"Main entrypoint executing desktop auth for '{provider}'")
            from core.auth import authenticate_desktop
            authenticate_desktop(provider)
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

    port = None
    if "--port" in sys.argv:
        try:
            idx = sys.argv.index("--port")
            port = int(sys.argv[idx + 1])
        except (ValueError, IndexError):
            pass

    app = ZeroCopyUnifiedApp(port=port, start_hidden=start_hidden)
    app.run(force_cli=force_cli)


if __name__ == "__main__":
    main()
