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
    "claude": "https://claude.ai/chats",
    "deepseek": "https://chat.deepseek.com",
    "qwen": "https://chat.qwen.ai",
    "grok": "https://grok.com"
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

def authenticate(provider: str, account_id: str = None):
    url = PROVIDER_URLS.get(provider.lower())
    if not url:
        print(f"[ERROR] Unsupported provider: {provider}. Options: {list(PROVIDER_URLS.keys())}")
        sys.exit(1)

    from core.config import get_runtime_cwd
    from core.healer import config_healer

    prov = provider.lower()
    if not account_id:
        cfg, _ = config_healer.load_or_create()
        primary_id = f"{prov}-web-primary"
        primary_acc = next((a for a in cfg.accounts if a.id == primary_id and a.auth_type == "browser_session"), None)
        if primary_acc and primary_acc.status == "active":
            existing_ids = {a.id for a in cfg.accounts}
            idx = 2
            while f"{prov}-web-{idx}" in existing_ids:
                idx += 1
            account_id = f"{prov}-web-{idx}"
        else:
            account_id = primary_id

    profile_dir = (get_runtime_cwd() / "browser_profiles" / account_id).resolve()
    profile_dir.mkdir(parents=True, exist_ok=True)

    proxy_server = detect_proxy()

    print(f"\n=======================================================")
    print(f"  [ZCA] zero-copy-agent Authentication Assistant")
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
        is_frozen = getattr(sys, "frozen", False)
        channels = ["chrome", "msedge"] if (is_frozen or sys.platform == "win32") else ["chrome", "msedge", None]
        last_err = None
        for channel in channels:
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
                last_err = e
                continue

        if not context:
            print(f"[ERROR] Failed to start browser: {last_err}")
            sys.exit(1)

        # Stealth: mask navigator.webdriver
        context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined
            });
        """)

        page = context.new_page() if len(context.pages) == 0 else context.pages[0]
        page.goto(url)

        input("\nPress [ENTER] after you have successfully logged in: ")

        print("\n[2/3] Saving cookies and persistent storage state...")
        time.sleep(2)
        context.close()

    print("[3/3] [OK] Session profile successfully saved!")
    print(f"      You can now start the daemon: .\\start.ps1\n")

def kill_zombie_browser_processes(profile_path: Path):
    """Terminates stale Chrome/Edge processes holding this specific profile directory on Windows."""
    if sys.platform != "win32":
        return
    try:
        import subprocess
        prof_norm = str(profile_path).replace("/", "\\").lower()
        prof_single = prof_norm.replace("'", "''")
        cmd = f'Get-CimInstance Win32_Process | Where-Object {{ ($_.Name -eq \'chrome.exe\' -or $_.Name -eq \'msedge.exe\') -and $_.CommandLine -and $_.CommandLine.ToLower().Contains(\'{prof_single}\') }} | ForEach-Object {{ Stop-Process -Id $_.ProcessId -Force }}'
        subprocess.run(["powershell", "-NoProfile", "-Command", cmd], capture_output=True, timeout=5)
    except Exception as e:
        logging.debug(f"[Auth Assistant] Zombie cleanup warning: {e}")


def authenticate_desktop(provider: str, account_id: str = None):
    """Окно авторизации для десктопной версии (поддерживает Chrome/Edge, проверку блокировок и авто-сохранение сессии)."""
    prov = provider.strip().lower()
    url = PROVIDER_URLS.get(prov, "https://gemini.google.com/app")

    from core.config import get_runtime_cwd
    from core.healer import config_healer

    if not account_id:
        cfg, _ = config_healer.load_or_create()
        primary_id = f"{prov}-web-primary"
        primary_acc = next((a for a in cfg.accounts if a.id == primary_id and a.auth_type == "browser_session"), None)
        if primary_acc and primary_acc.status == "active":
            existing_ids = {a.id for a in cfg.accounts}
            idx = 2
            while f"{prov}-web-{idx}" in existing_ids:
                idx += 1
            account_id = f"{prov}-web-{idx}"
        else:
            account_id = primary_id

    profile_dir = (get_runtime_cwd() / "browser_profiles" / account_id).resolve()
    profile_dir.mkdir(parents=True, exist_ok=True)

    # Release any lingering zombie Chrome/Edge processes holding this profile
    kill_zombie_browser_processes(profile_dir)

    proxy_server = detect_proxy()
    proxy_cfg = {"server": proxy_server} if proxy_server else None

    logging.info(f"[Auth Assistant] Запуск браузера для {prov.upper()} (account: {account_id}) по адресу {url} (profile: {profile_dir})...")
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
            channel_errors = {}
            is_frozen = getattr(sys, "frozen", False)
            channels_to_try = ["chrome", "msedge"] if (is_frozen or sys.platform == "win32") else ["chrome", "msedge", None]

            for channel in channels_to_try:
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
                    channel_errors[channel] = e
                    logging.warning(f"[Auth Assistant] Канал '{channel}' не подошел: {e}")
                    continue

            if not context:
                all_err_str = " ".join(str(e) for e in channel_errors.values())
                is_locked = any(kw in all_err_str.lower() for kw in [
                    "user data directory is already in use",
                    "singletonlock",
                    "ebusy",
                    "exitcode=21",
                    "target page, context or browser has been closed"
                ])
                if is_locked:
                    msg = (
                        f"Профиль браузера занят другим процессом Chrome или Edge:\n{profile_dir}\n\n"
                        f"Закройте открытые окна браузера или фоновые процессы zero-copy-agent и повторите попытку."
                    )
                else:
                    err_detail = str(last_err or "Не удалось запустить контекст браузера")
                    msg = (
                        f"Не удалось запустить браузер для {prov.upper()}.\n\n"
                        f"Убедитесь, что в системе установлен Google Chrome или Microsoft Edge.\n\n"
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
                if prov == "chatgpt":
                    alias_name = "ChatGPT Free / Plus (Веб-сессия)"
                elif prov == "deepseek":
                    alias_name = "DeepSeek Chat / Reasoner (Веб-сессия)"
                elif prov == "qwen":
                    alias_name = "Qwen Max / Coder (Веб-сессия)"
                elif prov == "grok":
                    alias_name = "xAI Grok 3 (Веб-сессия)"
                else:
                    alias_name = f"{prov.upper()} Web Session ({account_id})"
                acc, mods = config_healer.register_web_session(
                    provider=prov,
                    account_id=account_id,
                    alias=alias_name,
                    profile_dir=f"browser_profiles/{account_id}"
                )
                logging.info(f"[Auth Assistant] Веб-сессия {prov} добавлена в subscriptions.yaml (id={acc.id}, profile={acc.profile_dir}, mods={mods})")
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
    parser.add_argument("--provider", type=str, default="gemini", choices=["gemini", "chatgpt", "claude", "deepseek", "qwen", "grok"],
                        help="Provider to authenticate (gemini, chatgpt, claude, deepseek, qwen, grok)")
    parser.add_argument("--account-id", type=str, default=None,
                        help="Optional specific account ID (e.g. gemini-web-primary or gemini-web-2)")
    args = parser.parse_args()
    authenticate(args.provider, account_id=args.account_id)
