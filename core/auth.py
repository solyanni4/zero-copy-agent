"""
Browser Authentication Assistant for zero-copy-agent.
Launches an interactive Chromium/Chrome window with anti-bot evasion and auto-proxy.
"""
import argparse
import os
import socket
import sys
import time
import logging
from pathlib import Path
from core.config import config

PROVIDER_URLS = {
    "gemini": "https://gemini.google.com/app",
    "chatgpt": "https://chatgpt.com",
    "claude": "https://claude.ai/chats"
}

def detect_proxy():
    # 1. Environment variables
    env_proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY") or os.environ.get("ALL_PROXY")
    if env_proxy:
        return env_proxy
    # 2. Local gateway auto-detection (localhost:7890 / 127.0.0.1:7890)
    for host in ("127.0.0.1", "localhost"):
        try:
            s = socket.socket()
            s.settimeout(0.2)
            if s.connect_ex((host, 7890)) == 0:
                s.close()
                return f"http://{host}:7890"
            s.close()
        except Exception:
            pass
    return None

def authenticate(provider: str):
    url = PROVIDER_URLS.get(provider.lower())
    if not url:
        print(f"[ERROR] Unsupported provider: {provider}. Options: {list(PROVIDER_URLS.keys())}")
        sys.exit(1)

    profile_dir = Path(config.browser.user_data_dir).resolve()
    profile_dir.mkdir(parents=True, exist_ok=True)

    proxy_server = detect_proxy()

    print(f"\n=======================================================")
    print(f"  🔐 zero-copy-agent Authentication Assistant")
    print(f"  Target Provider : {provider.upper()} ({url})")
    print(f"  Profile Storage : {profile_dir}")
    if proxy_server:
        print(f"  Network Gateway : {proxy_server} (US Pool)")
    print(f"=======================================================\n")
    print("[1/3] Launching interactive browser...")
    print("      Please complete the login in the opened browser window.")
    print("      Once you see your chats, press ENTER here in the terminal to save session.\n")

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("[ERROR] Playwright is not installed.")
        sys.exit(1)

    launch_args = [
        "--disable-blink-features=AutomationControlled",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-infobars"
    ]

    proxy_cfg = {"server": proxy_server} if proxy_server else None

    with sync_playwright() as p:
        context = None
        # Try real installed Google Chrome or Edge first for maximum Google trust
        for channel in ["chrome", "msedge", None]:
            try:
                kwargs = {
                    "user_data_dir": str(profile_dir),
                    "headless": False,
                    "viewport": {"width": 1280, "height": 800},
                    "args": launch_args,
                    "ignore_default_args": ["--enable-automation"]
                }
                if channel:
                    kwargs["channel"] = channel
                if proxy_cfg:
                    kwargs["proxy"] = proxy_cfg

                context = p.chromium.launch_persistent_context(**kwargs)
                break
            except Exception as e:
                if channel:
                    continue
                print(f"[ERROR] Failed to start browser: {e}")
                sys.exit(1)

        # Stealth: mask navigator.webdriver
        context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined
            });
        """)

        page = context.new_page() if len(context.pages) == 0 else context.pages[0]
        page.goto(url)

        input("\n👉 Press [ENTER] after you have successfully logged in: ")

        print("\n[2/3] Saving cookies and persistent storage state...")
        time.sleep(2)
        context.close()

    print("[3/3] ✅ Session profile successfully saved!")
    print(f"      You can now start the daemon: .\\start.ps1\n")

def authenticate_desktop(provider: str):
    """Окно авторизации для десктопной версии (поддерживает Chrome/Edge, проверку блокировок и авто-сохранение сессии)."""
    prov = provider.strip().lower()
    url = PROVIDER_URLS.get(prov, "https://gemini.google.com/app")
    profile_dir = Path(config.browser.user_data_dir).resolve()
    profile_dir.mkdir(parents=True, exist_ok=True)
    proxy_server = detect_proxy()
    proxy_cfg = {"server": proxy_server} if proxy_server else None

    logging.info(f"[Auth Assistant] Запуск браузера для {prov.upper()} по адресу {url}...")
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        err_msg = "Playwright не установлен или не найдены зависимости браузера."
        logging.error(f"[Auth Assistant] {err_msg} ({e})")
        if sys.platform == "win32":
            try:
                import ctypes
                ctypes.windll.user32.MessageBoxW(0, f"{err_msg}\n\nДетали: {e}", "zero-copy-agent | Ошибка Playwright", 0x10)
            except Exception:
                pass
        return

    try:
        with sync_playwright() as p:
            context = None
            last_err = None
            for channel in ["chrome", "msedge", None]:
                try:
                    kwargs = {
                        "user_data_dir": str(profile_dir),
                        "headless": False,
                        "viewport": {"width": 1280, "height": 800},
                        "args": [
                            "--disable-blink-features=AutomationControlled",
                            "--no-first-run",
                            "--no-default-browser-check"
                        ],
                        "ignore_default_args": ["--enable-automation"]
                    }
                    if channel:
                        kwargs["channel"] = channel
                    if proxy_cfg:
                        kwargs["proxy"] = proxy_cfg
                    context = p.chromium.launch_persistent_context(**kwargs)
                    logging.info(f"[Auth Assistant] Браузер успешно запущен через канал: {channel or 'chromium'}")
                    break
                except Exception as e:
                    last_err = e
                    logging.warning(f"[Auth Assistant] Канал '{channel}' не подошел: {e}")
                    continue

            if not context:
                err_detail = str(last_err or "Не удалось запустить контекст браузера")
                if "user data directory is already in use" in err_detail.lower() or "singletonlock" in err_detail.lower() or "ebusy" in err_detail.lower():
                    msg = (
                        f"Профиль браузера занят другим процессом:\n{profile_dir}\n\n"
                        f"Закройте открытые окна браузера или фоновые процессы zero-copy-agent и повторите попытку."
                    )
                else:
                    msg = (
                        f"Не удалось запустить браузер для {prov.upper()}.\n\n"
                        f"Убедитесь, что в системе установлен Google Chrome, Microsoft Edge или Chromium.\n\n"
                        f"Ошибка: {err_detail}"
                    )
                logging.error(f"[Auth Assistant] {msg}")
                if sys.platform == "win32":
                    try:
                        import ctypes
                        ctypes.windll.user32.MessageBoxW(0, msg, "zero-copy-agent | Ошибка запуска браузера", 0x10)
                    except Exception:
                        pass
                return

            page = context.new_page() if len(context.pages) == 0 else context.pages[0]
            page.goto(url)

            if sys.platform == "win32":
                try:
                    import ctypes
                    ctypes.windll.user32.MessageBoxW(
                        0,
                        f"Открыт браузер для авторизации в {prov.upper()}.\n\n"
                        f"1. Войдите в свой аккаунт в открывшемся окне браузера.\n"
                        f"2. Дождитесь загрузки страницы чата/диалогов.\n"
                        f"3. Нажмите 'ОК' в этом системном окне, чтобы сохранить профиль сессии.",
                        f"zero-copy-agent | Авторизация {prov.upper()}",
                        0x40 | 0x0
                    )
                except Exception:
                    pass
            else:
                try:
                    page.wait_for_event("close", timeout=300000)
                except Exception:
                    pass

            time.sleep(1)
            try:
                context.close()
            except Exception:
                pass
            logging.info(f"[Auth Assistant] Сессия для {prov} успешно сохранена!")

            try:
                from core.healer import config_healer
                alias_name = "ChatGPT Free / Plus (Веб-сессия)" if prov == "chatgpt" else f"{prov.upper()} (Веб-сессия)"
                acc, mods = config_healer.register_web_session(provider=prov, alias=alias_name)
                logging.info(f"[Auth Assistant] Веб-сессия {prov} добавлена в subscriptions.yaml (id={acc.id}, mods={mods})")
            except Exception as e:
                logging.exception(f"[Auth Assistant] Ошибка добавления в subscriptions: {e}")

    except Exception as e:
        logging.exception(f"[Auth Assistant] Непредвиденная ошибка при авторизации {prov}: {e}")
        if sys.platform == "win32":
            try:
                import ctypes
                ctypes.windll.user32.MessageBoxW(0, f"Произошла ошибка при авторизации в {prov.upper()}:\n\n{e}", "zero-copy-agent | Ошибка авторизации", 0x10)
            except Exception:
                pass

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Authenticate web subscriptions for zero-copy-agent")
    parser.add_argument("--provider", type=str, default="gemini", choices=["gemini", "chatgpt", "claude"],
                        help="Provider to authenticate (gemini, chatgpt, claude)")
    args = parser.parse_args()
    authenticate(args.provider)
