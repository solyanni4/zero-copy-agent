"""
Browser Authentication Assistant for zero-copy-agent.
Launches an interactive Chromium/Chrome window with anti-bot evasion and auto-proxy.
"""
import argparse
import json
import logging
import os
import shutil
import socket
import sys
import threading
import time
from pathlib import Path
from typing import Optional, Any
from urllib.parse import urlparse
from core.config import config

PROVIDER_URLS = {
    "gemini": "https://gemini.google.com/app",
    "chatgpt": "https://chatgpt.com",
    "claude": "https://claude.ai",
    "deepseek": "https://chat.deepseek.com",
    "qwen": "https://chat.qwen.ai",
    "grok": "https://grok.com"
}

KEY_AUTH_COOKIES = {
    "gemini": ["__Secure-1PSID", "SID"],
    "chatgpt": ["__Secure-next-auth.session-token", "__Secure-next-auth.session-token.0"],
    "claude": ["sessionKey"],
    "deepseek": ["ds_session_id", "userToken"],
    "qwen": ["token", "login_aliyunid_ticket", "cna"],
    "grok": ["sso", "sso-rw"],
}

def has_key_auth_cookies(provider: str, cookies: list) -> bool:
    """Checks whether the cookie list contains at least one key authentication cookie for provider."""
    required_keys = KEY_AUTH_COOKIES.get((provider or "").strip().lower())
    if not required_keys:
        return True
    return any(c.get("name") in required_keys for c in cookies if isinstance(c, dict))

def prune_browser_profile_caches(profile_dir: Path) -> None:
    """
    Safely prunes transient Chrome caches, shader caches, and heavy downloaded models
    from a browser profile directory to keep its size minimal, while preserving all
    authentication cookies, session storage, local storage, indexedDB, and preferences.
    """
    if not profile_dir or not profile_dir.exists():
        return

    root_prune = [
        "GrShaderCache", "GraphiteDawnCache", "ShaderCache", "Crashpad",
        "OptGuideOnDeviceModel", "AutofillAiModelCache", "ComponentCrxCache",
        "OptimizationHints", "Safe Browsing", "WidevineCdm", "ZxcvbnData",
        "OnDeviceHeadSuggestModel", "hyphen-data", "Subresource Filter"
    ]
    for item_name in root_prune:
        p = profile_dir / item_name
        if p.exists():
            try:
                if p.is_dir():
                    shutil.rmtree(p, ignore_errors=True)
                else:
                    p.unlink(missing_ok=True)
            except Exception:
                pass

    default_dir = profile_dir / "Default"
    if default_dir.exists() and default_dir.is_dir():
        default_prune = [
            "Cache", "Code Cache", "GPUCache", "DawnGraphiteCache", "DawnWebGPUCache",
            "Service Worker/CacheStorage", "Service Worker/ScriptCache"
        ]
        for sub_path in default_prune:
            p = default_dir / sub_path
            if p.exists():
                try:
                    if p.is_dir():
                        shutil.rmtree(p, ignore_errors=True)
                    else:
                        p.unlink(missing_ok=True)
                except Exception:
                    pass

def is_proxy_alive(proxy_url: Optional[str], timeout: float = 0.4) -> bool:
    """Verifies that proxy_url is not empty/none and host:port accepts a TCP connection."""
    if not proxy_url or proxy_url.strip().lower() in ("", "none", "direct"):
        return False
    try:
        clean_url = proxy_url if "://" in proxy_url else f"http://{proxy_url}"
        parsed = urlparse(clean_url)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or (7890 if "7890" in proxy_url else 8080)
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        code = s.connect_ex((host, port))
        s.close()
        return code == 0
    except Exception:
        return False

def detect_proxy() -> Optional[str]:
    # 1. Environment variables
    env_proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY") or os.environ.get("ALL_PROXY")
    if env_proxy and is_proxy_alive(env_proxy, timeout=0.4):
        return env_proxy
    # 2. Local gateway auto-detection (localhost:7890 / 127.0.0.1:7890)
    for host in ("127.0.0.1", "localhost"):
        candidate = f"http://{host}:7890"
        if is_proxy_alive(candidate, timeout=0.4):
            return candidate
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
    kill_zombie_browser_processes(profile_dir)
    prune_browser_profile_caches(profile_dir)

    proxy_server = detect_proxy()
    if proxy_server and is_proxy_alive(proxy_server, timeout=0.4):
        proxy_cfg = {"server": proxy_server}
        proxy_display = f"{proxy_server} (US Pool)"
    else:
        proxy_cfg = None
        proxy_display = "DIRECT"

    print(f"\n=======================================================")
    print(f"  [ZCA] zero-copy-agent Authentication Assistant")
    print(f"  Target Provider : {provider.upper()} ({url})")
    print(f"  Profile Storage : {profile_dir}")
    print(f"  Network Gateway : {proxy_display}")
    print(f"=======================================================\n")
    print("[1/3] Launching interactive browser...")
    print("      Please complete the login in the opened browser window.")
    print("      Once you see your chats, press ENTER here in the terminal to save session.\n")

    using_patchright = False
    try:
        from patchright.sync_api import sync_playwright
        using_patchright = True
    except ImportError:
        try:
            from playwright.sync_api import sync_playwright
            using_patchright = False
        except ImportError:
            print("[ERROR] Neither patchright nor playwright is installed.")
            sys.exit(1)

    launch_args = [
        "--disable-blink-features=AutomationControlled",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-infobars"
    ]

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

        # Stealth: mask navigator.webdriver only for standard playwright
        if not using_patchright:
            context.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', {
                    get: () => undefined
                });
            """)

        page = context.new_page() if len(context.pages) == 0 else context.pages[0]
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=15000)
        except Exception:
            pass

        print("\n[2/3] Ожидание завершения авторизации в браузере (до 300с)...")
        print("      Войдите в свой аккаунт или закройте окно браузера для сохранения сессии.")
        start_auth = time.time()
        while time.time() - start_auth < 300.0:
            if page.is_closed() or (context and len(context.pages) == 0):
                break
            try:
                curr_url = page.url.lower()
                is_on_login = any(kw in curr_url for kw in ("accounts.google.com", "/login", "/signin", "auth0", "appleid.apple.com"))
                chat_input_sel = "#prompt-textarea, #chat-input, rich-textarea, div.ql-editor[contenteditable='true'], textarea[placeholder*='DeepSeek'], textarea[placeholder*='Grok']"
                has_input = page.locator(chat_input_sel).count() > 0
                has_login_btn = page.locator("text='Sign in', text='Log in', text='Войти'").count() > 0
                if not is_on_login and has_input and not has_login_btn:
                    time.sleep(2)
                    break
            except Exception:
                pass
            time.sleep(2.0)

        print("\n[2/3] Сохранение cookies и состояния сессии...")
        time.sleep(2)
        try:
            context.close()
        except Exception:
            pass

    print("[3/3] [OK] Профиль сессии успешно сохранен!")
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
        print(f"      Сессия зарегистрирована в subscriptions.yaml (id={acc.id})\n")
    except Exception as e:
        print(f"      Предупреждение при регистрации в subscriptions: {e}\n")

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

    cfg, _ = config_healer.load_or_create()
    target_acc = None
    if not account_id:
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
            target_acc = primary_acc
    else:
        target_acc = next((a for a in cfg.accounts if a.id == account_id), None)

    profile_dir = (get_runtime_cwd() / "browser_profiles" / account_id).resolve()
    profile_dir.mkdir(parents=True, exist_ok=True)
    kill_zombie_browser_processes(profile_dir)
    prune_browser_profile_caches(profile_dir)

    account_proxy = getattr(target_acc, "proxy", None) if target_acc else None
    proxy_server = account_proxy or detect_proxy()
    if proxy_server and is_proxy_alive(proxy_server, timeout=0.4):
        proxy_cfg = {"server": proxy_server}
        logging.info(f"[Auth Assistant] Proxy: {proxy_server}")
    else:
        proxy_cfg = None
        logging.info("[Auth Assistant] Proxy: DIRECT (gateway offline or disabled)")

    logging.info(f"[Auth Assistant] Запуск браузера для {prov.upper()} (account: {account_id}) по адресу {url} (profile: {profile_dir})...")
    using_patchright = False
    try:
        from patchright.sync_api import sync_playwright
        using_patchright = True
    except ImportError:
        try:
            from playwright.sync_api import sync_playwright
            using_patchright = False
        except ImportError as e:
            err_msg = "Движок браузера (patchright / playwright) не установлен."
            logging.error(f"[Auth Assistant] {err_msg} ({e})")
            if sys.platform == "win32":
                try:
                    import ctypes
                    ctypes.windll.user32.MessageBoxW(0, f"{err_msg}\n\nДетали: {e}", "zero-copy-agent | Ошибка браузера", 0x10)
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
                            "--no-default-browser-check",
                            "--disk-cache-size=10485760",
                            "--media-cache-size=1048576",
                            "--disable-component-update",
                            "--disable-background-networking",
                            "--disable-sync",
                            "--disable-default-apps",
                            "--metrics-recording-only",
                            "--no-pings"
                        ],
                        "ignore_default_args": ["--enable-automation"]
                    }
                    if channel:
                        kwargs["channel"] = channel
                    if proxy_cfg:
                        kwargs["proxy"] = proxy_cfg
                    context = p.chromium.launch_persistent_context(**kwargs)
                    if not using_patchright:
                        context.add_init_script("""
                            Object.defineProperty(navigator, 'webdriver', {
                                get: () => undefined
                            });
                        """)
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
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=15000)
            except Exception:
                pass

            if sys.platform == "win32":
                msg_done = threading.Event()

                def show_dialog():
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
                    finally:
                        msg_done.set()

                dialog_t = threading.Thread(target=show_dialog, daemon=True)
                dialog_t.start()

                start_auth = time.time()
                while not msg_done.is_set() and (time.time() - start_auth) < 300.0:
                    if page.is_closed() or (context and len(context.pages) == 0):
                        break
                    try:
                        page.wait_for_timeout(500)
                    except Exception:
                        break
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

            synced_cookie_file = profile_dir / "synced_cookies.json"
            if synced_cookie_file.exists():
                try:
                    synced_cookie_file.unlink()
                    logging.info(f"[Auth Assistant] Удален устаревший {synced_cookie_file} в пользу свежей ручной сессии")
                except Exception as e:
                    logging.warning(f"[Auth Assistant] Не удалось удалить {synced_cookie_file}: {e}")

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


def ensure_chrome_extension_ready(cfg: Optional[Any] = None) -> Path:
    """
    Ensures chrome_extension directory exists in runtime cwd, unpacks it if needed,
    and writes defaults.json with the active syncToken and zcaUrl.
    """
    import json
    import shutil
    from core.config import get_runtime_cwd, config as global_config

    if cfg is None:
        cfg = global_config

    token = getattr(cfg, "cookie_sync_token", "") or ""
    server_port = getattr(cfg.server, "port", 8000) if hasattr(cfg, "server") else 8000
    zca_url = f"http://127.0.0.1:{server_port}"

    is_frozen = getattr(sys, "frozen", False)
    if is_frozen:
        source_dir = Path(getattr(sys, "_MEIPASS", "")) / "chrome_extension"
    else:
        source_dir = Path(__file__).resolve().parent.parent / "chrome_extension"

    target_dir = (get_runtime_cwd() / "chrome_extension").resolve()
    target_dir.mkdir(parents=True, exist_ok=True)

    if source_dir.resolve() != target_dir:
        if source_dir.exists() and source_dir.is_dir():
            shutil.copytree(source_dir, target_dir, dirs_exist_ok=True)

    defaults_path = target_dir / "defaults.json"
    defaults_data = {
        "zcaUrl": zca_url,
        "syncToken": token
    }
    with open(defaults_path, "w", encoding="utf-8") as f:
        json.dump(defaults_data, f, indent=2, ensure_ascii=False)

    return target_dir


def open_chrome_extension_setup(cfg: Optional[Any] = None) -> Path:
    """
    Ensures extension is unpacked with defaults.json, selects it in Windows Explorer,
    and opens chrome://extensions in installed Google Chrome.
    """
    ext_folder = ensure_chrome_extension_ready(cfg)

    if sys.platform == "win32":
        import subprocess

        # Try copying extension folder path to clipboard for 1-click paste into Chrome picker
        try:
            import pyperclip
            pyperclip.copy(str(ext_folder))
        except Exception:
            pass

        # Highlight the chrome_extension folder itself in parent directory
        try:
            subprocess.Popen(["explorer", f"/select,{str(ext_folder)}"])
        except Exception as e:
            logging.warning(f"[Auth Assistant] Failed to open explorer: {e}")

        # Safe discovery of chrome.exe
        chrome_candidates = [
            Path(os.environ.get("LOCALAPPDATA", "")) / "Google" / "Chrome" / "Application" / "chrome.exe",
            Path(os.environ.get("ProgramFiles", "C:\\Program Files")) / "Google" / "Chrome" / "Application" / "chrome.exe",
            Path(os.environ.get("ProgramFiles(x86)", "C:\\Program Files (x86)")) / "Google" / "Chrome" / "Application" / "chrome.exe",
        ]
        chrome_bin = next((p for p in chrome_candidates if p.exists()), None)
        if chrome_bin:
            try:
                subprocess.Popen([str(chrome_bin), "chrome://extensions"])
            except Exception as e:
                logging.warning(f"[Auth Assistant] Failed to launch Chrome: {e}")
        else:
            try:
                os.startfile("chrome://extensions")
            except Exception:
                pass

    return ext_folder


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Authenticate web subscriptions for zero-copy-agent")
    parser.add_argument("--provider", type=str, default="gemini", choices=["gemini", "chatgpt", "claude", "deepseek", "qwen", "grok"],
                        help="Provider to authenticate (gemini, chatgpt, claude, deepseek, qwen, grok)")
    parser.add_argument("--account-id", type=str, default=None,
                        help="Optional specific account ID (e.g. gemini-web-primary or gemini-web-2)")
    args = parser.parse_args()
    authenticate(args.provider, account_id=args.account_id)
