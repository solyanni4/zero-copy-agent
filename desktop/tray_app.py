"""
zero-copy-agent Desktop Tray Application
Cross-platform Desktop UI for Windows, macOS, and Linux.
Runs in the System Tray / Notification Area.
"""
import os
import sys
import logging
import subprocess
import threading
import time
import webbrowser
from pathlib import Path
from typing import Optional

# Добавляем корень проекта в путь (безопасно для PyInstaller)
if getattr(sys, "frozen", False):
    ROOT_DIR = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
else:
    ROOT_DIR = Path(__file__).resolve().parent.parent
    if str(ROOT_DIR) not in sys.path:
        sys.path.insert(0, str(ROOT_DIR))

# Импортируем FastAPI и основное приложение на уровне модуля для бандлинга в бинарник
import uvicorn
from main import app

# Настройка безопасного логирования для Windows (в т.ч. при запуске с --noconsole)
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
        self._encoding = getattr(stream, "encoding", "utf-8") or "utf-8"
        self._errors = getattr(stream, "errors", "replace") or "replace"

    @property
    def encoding(self):
        return self._encoding

    @property
    def errors(self):
        return self._errors

    def write(self, message):
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

from PIL import Image

pystray = None
MenuItem = None
has_gui_support = False

def check_gui_available() -> bool:
    global pystray, MenuItem, has_gui_support
    if pystray is not None:
        return has_gui_support

    # On Linux, ensure DISPLAY or WAYLAND_DISPLAY is set before attempting X11 connection
    if sys.platform.startswith("linux"):
        if not os.getenv("DISPLAY") and not os.getenv("WAYLAND_DISPLAY"):
            has_gui_support = False
            return False

    try:
        import pystray as _pystray
        from pystray import MenuItem as _MenuItem
        pystray = _pystray
        MenuItem = _MenuItem
        has_gui_support = True
    except Exception as e:
        logging.warning(f"GUI/Tray is not available: {e}. Falling back to CLI mode.")
        has_gui_support = False
    return has_gui_support

from desktop.app_icon import create_default_icon, ensure_icon_file
from core.config import config

def run_desktop_auth(provider: str, account_id: Optional[str] = None):
    """Окно авторизации для десктопной версии (сохраняет сессию в изолированную подпапку browser_profiles/<account_id>)."""
    from core.auth import authenticate
    try:
        authenticate(provider, account_id=account_id)
    except Exception as e:
        print(f"[Auth Assistant] Error during authorization of {provider}: {e}")

class ZeroCopyDesktopApp:
    def __init__(self):
        self.server_thread = None
        self.uvicorn_server = None
        self.icon = None
        self.port = config.server.port or 8000
        self.host = config.server.host or "0.0.0.0"
        self.client_host = "127.0.0.1" if self.host in ("0.0.0.0", "::", "") else self.host
        self.api_url = f"http://{self.client_host}:{self.port}/v1"
        self.is_running = True

    def start_api_server(self):
        """Запускает FastAPI/Uvicorn в фоновом потоке."""
        uv_config = uvicorn.Config(
            app=app,
            host=self.host,
            port=self.port,
            log_level="info",
            loop="asyncio",
            use_colors=False
        )
        self.uvicorn_server = uvicorn.Server(uv_config)

        def run_server():
            print(f"[Desktop App] Starting background API server on {self.api_url}...")
            self.uvicorn_server.run()

        self.server_thread = threading.Thread(target=run_server, daemon=True)
        self.server_thread.start()

    def open_browser_auth(self, provider: str):
        """Запускает окно авторизации в выбранном сервисе."""
        python_exe = sys.executable
        if getattr(sys, "frozen", False):
            cmd = [python_exe, "--auth", provider]
        else:
            cmd = [python_exe, str(ROOT_DIR / "desktop" / "tray_app.py"), "--auth", provider]
        subprocess.Popen(cmd, cwd=str(ROOT_DIR))

    def copy_api_url_to_clipboard(self):
        """Копирует URL API в буфер обмена."""
        try:
            import pyperclip
            pyperclip.copy(self.api_url)
        except ImportError:
            if sys.platform == "win32":
                subprocess.run(f"echo {self.api_url}|clip", shell=True)
            elif sys.platform == "darwin":
                subprocess.run("pbcopy", input=self.api_url.encode(), check=False)
            elif sys.platform.startswith("linux"):
                try:
                    subprocess.run(["xclip", "-selection", "clipboard"], input=self.api_url.encode(), check=False)
                except FileNotFoundError:
                    pass

        if self.icon:
            self.icon.notify(
                f"Скопировано в буфер:\n{self.api_url}\n\nВставьте в Base URL в Cursor/Aider!",
                title="zero-copy-agent"
            )

    def copy_mcp_url_to_clipboard(self):
        """Копирует SSE MCP URL в буфер обмена."""
        mcp_url = f"http://{self.client_host}:{self.port}/sse"
        try:
            import pyperclip
            pyperclip.copy(mcp_url)
        except ImportError:
            if sys.platform == "win32":
                subprocess.run(f"echo {mcp_url}|clip", shell=True)
            elif sys.platform == "darwin":
                subprocess.run("pbcopy", input=mcp_url.encode(), check=False)
            elif sys.platform.startswith("linux"):
                try:
                    subprocess.run(["xclip", "-selection", "clipboard"], input=mcp_url.encode(), check=False)
                except FileNotFoundError:
                    pass

        if self.icon:
            self.icon.notify(
                f"Скопировано в буфер:\n{mcp_url}\n\nВставьте в MCP Server URL в Cursor/Antigravity!",
                title="zero-copy-agent"
            )

    def auto_setup_ides(self):
        """Автоматически настраивает все обнаруженные среды с созданием точек отката."""
        try:
            from core.ide_setup import configure_targets
            results = configure_targets(["all"])
            configured = [name for name, ok, _ in results if ok]
            msg = f"[OK] Настроены среды:\n" + "\n".join(f"- {c}" for c in configured)
            if self.icon:
                self.icon.notify(msg, title="zero-copy-agent IDE Setup")
        except Exception as e:
            if self.icon:
                self.icon.notify(f"Ошибка настройки: {e}", title="zero-copy-agent IDE Setup")

    def open_dashboard(self):
        """Открывает веб-дашборд и интерактивный мастер онбординга."""
        webbrowser.open(f"http://{self.client_host}:{self.port}/")

    def open_docs(self):
        """Открывает Swagger UI в веб-браузере."""
        webbrowser.open(f"http://{self.client_host}:{self.port}/docs")

    def open_profiles_folder(self):
        """Открывает директорию с профилями в файловом менеджере ОС."""
        folder = Path(config.browser.user_data_dir).resolve()
        folder.mkdir(parents=True, exist_ok=True)
        if sys.platform == "win32":
            os.startfile(str(folder))
        elif sys.platform == "darwin":
            subprocess.run(["open", str(folder)])
        else:
            subprocess.run(["xdg-open", str(folder)])

    def open_log_file(self):
        """Открывает лог-файл приложения."""
        if LOG_FILE.exists():
            if sys.platform == "win32":
                os.startfile(str(LOG_FILE))
            elif sys.platform == "darwin":
                subprocess.run(["open", str(LOG_FILE)])
            else:
                subprocess.run(["xdg-open", str(LOG_FILE)])

    def stop(self):
        """Завершает работу приложения."""
        self.is_running = False
        print("[Desktop App] Stopping zero-copy-agent...")
        if self.uvicorn_server:
            self.uvicorn_server.should_exit = True
        if self.icon:
            self.icon.stop()
        sys.exit(0)

    def run(self, force_cli: bool = False):
        """Точка входа запуска приложения (Трей или CLI/Серверный режим)."""
        self.start_api_server()

        # Если запрошен CLI режим или графическая оболочка недоступна:
        if force_cli or not check_gui_available():
            print(f"\n[CLI Mode] [ZCA] zero-copy-agent running in background console mode")
            print(f"  API Endpoint : {self.api_url}")
            print(f"  Web Dashboard: http://{self.client_host}:{self.port}/")
            print(f"  Swagger Docs : http://{self.client_host}:{self.port}/docs")
            print(f"  Press CTRL+C to stop.\n")
            try:
                while self.is_running:
                    time.sleep(1)
            except KeyboardInterrupt:
                self.stop()
            return

        # Графический режим (System Tray)
        icon_path = ensure_icon_file()
        try:
            image = Image.open(icon_path)
        except Exception:
            image = create_default_icon(64)

        # Создаем меню системного трея
        menu = pystray.Menu(
            MenuItem("zero-copy-agent", lambda: None, enabled=False),
            MenuItem(f"Статус: Порт {self.port} активен", lambda: None, enabled=False),
            pystray.Menu.SEPARATOR,
            MenuItem("Мастер настройки (Onboarding Wizard)", lambda: self.open_dashboard()),
            MenuItem("Открыть Дашборд & Песочницу", lambda: self.open_dashboard()),
            pystray.Menu.SEPARATOR,
            MenuItem("Скопировать API URL для Cursor/IDE", lambda: self.copy_api_url_to_clipboard()),
            MenuItem("Скопировать MCP URL (Antigravity / Cursor)", lambda: self.copy_mcp_url_to_clipboard()),
            MenuItem("Автонастройка всех IDE (1 клик)", lambda: self.auto_setup_ides()),
            pystray.Menu.SEPARATOR,
            MenuItem("Войти в Gemini Advanced", lambda: self.open_browser_auth("gemini")),
            MenuItem("Войти в ChatGPT Plus", lambda: self.open_browser_auth("chatgpt")),
            MenuItem("Войти в Claude Pro", lambda: self.open_browser_auth("claude")),
            pystray.Menu.SEPARATOR,
            MenuItem("Открыть Web Swagger API", lambda: self.open_docs()),
            MenuItem("Открыть папку профилей браузера", lambda: self.open_profiles_folder()),
            MenuItem("Открыть лог-файл (Troubleshooting)", lambda: self.open_log_file()),
            pystray.Menu.SEPARATOR,
            MenuItem("Выход (Stop & Exit)", lambda: self.stop())
        )

        self.icon = pystray.Icon(
            name="zero-copy-agent",
            icon=image,
            title="zero-copy-agent — Локальный ИИ-шлюз",
            menu=menu
        )

        print("[Desktop App] Tray icon ready. Right-click on tray icon to manage.")
        self.icon.run()

def main():
    if "--help" in sys.argv or "-h" in sys.argv:
        print("[ZCA] zero-copy-agent — Self-Hosted AI Gateway & Sidecar")
        print("\nUsage:")
        print("  zero-copy-agent [OPTIONS]")
        print("\nOptions:")
        print("  --cli, --headless   Run in console/headless mode without system tray")
        print("  --auth <provider>   Launch interactive login browser (gemini, chatgpt, claude)")
        print("  --version           Show application version")
        print("  --help, -h          Show this help message")
        sys.exit(0)

    if "--version" in sys.argv:
        from core import __version__
        print(f"zero-copy-agent v{__version__}")
        sys.exit(0)

    if "--auth" in sys.argv:
        idx = sys.argv.index("--auth")
        provider = sys.argv[idx + 1] if len(sys.argv) > idx + 1 else "gemini"
        run_desktop_auth(provider)
        sys.exit(0)

    from desktop.app import main as unified_main
    unified_main()

if __name__ == "__main__":
    main()
