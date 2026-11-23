"""
Browser Session Engine for zero-copy-agent.
Manages headless persistent contexts, driver lifecycle, stealth, proxy routing,
session keep-alive daemon (SKD), and LRU multi-account isolation.
"""
import asyncio
import os
import sys
import time
import socket
import logging
from pathlib import Path
from typing import AsyncGenerator, Optional, Dict, Any

from core.config import config, get_runtime_cwd
from core.drivers import (
    GeminiDriver,
    ChatGPTDriver,
    DeepSeekDriver,
    QwenDriver,
    GrokDriver,
    MockDriver,
    BaseDriver
)

logger = logging.getLogger("zcao.browser")

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

def get_model_badge(model_id: str) -> str:
    m = model_id.lower()
    if "neon" in m:
        label = "Gemini 4 Neon (Deeper Analysis)"
    elif "hydrogen" in m:
        label = "Gemini 4 Hydrogen (Everyday Help)"
    elif "auto" in m:
        label = "Gemini Auto (Adaptive)"
    elif "gemini" in m:
        label = "Gemini Advanced (Живая веб-подписка)"
    elif "chatgpt" in m or "gpt" in m:
        label = "ChatGPT Plus / GPT-4o (Живая веб-подписка)"
    elif "claude" in m:
        label = "Claude Pro (Живая веб-подписка)"
    elif "deepseek" in m:
        label = "DeepSeek V3/R1 (Живая веб-подписка)"
    elif "qwen" in m:
        label = "Qwen Max/Coder (Живая веб-подписка)"
    elif "grok" in m:
        label = "xAI Grok 3 (Живая веб-подписка)"
    else:
        label = f"{model_id} (Живая веб-сессия)"
    return f"**[zero-copy-agent | {label}]**\n\n"

class BrowserSessionEngine:
    def __init__(self):
        raw_user_data = Path(config.browser.user_data_dir)
        if not raw_user_data.is_absolute():
            self.profile_dir = (get_runtime_cwd() / raw_user_data).resolve()
        else:
            self.profile_dir = raw_user_data.resolve()
        self.headless = config.browser.headless
        self.max_active_contexts: int = getattr(config.browser, "max_active_contexts", 3)
        self.playwright = None
        self._contexts: Dict[str, Any] = {}
        self._locks: Dict[str, asyncio.Lock] = {}
        self._last_used: Dict[str, float] = {}
        self._pages: Dict[str, Any] = {}
        self._drivers: Dict[str, BaseDriver] = {}
        self.active_provider = config.browser.provider
        self.lock = asyncio.Lock()
        self.is_ready = False
        self.mock_driver = MockDriver()
        self.keepalive_task = None

    @property
    def context(self):
        if self._contexts:
            return next(iter(self._contexts.values()))
        return None

    @context.setter
    def context(self, val):
        if val is None:
            self._contexts.clear()
        else:
            self._contexts["default"] = val

    @property
    def page(self):
        if self._pages:
            return next(iter(self._pages.values()))
        return None

    @page.setter
    def page(self, val):
        if val is None:
            self._pages.clear()
        else:
            self._pages["default"] = val

    def _get_lock(self, account_id: str) -> asyncio.Lock:
        if account_id not in self._locks:
            self._locks[account_id] = asyncio.Lock()
        return self._locks[account_id]

    async def initialize(self):
        """Initializes the background browser runtime in lazy standby mode without pre-locking profiles."""
        try:
            try:
                from patchright.async_api import async_playwright, BrowserContext, Page
            except ImportError:
                from playwright.async_api import async_playwright, BrowserContext, Page
            if not self.playwright:
                try:
                    self.playwright = await asyncio.wait_for(async_playwright().start(), timeout=8.0)
                except (asyncio.TimeoutError, Exception) as driver_err:
                    logger.warning(f"[ENGINE-WARN] Primary browser driver start note ({driver_err}), falling back...")
                    from playwright.async_api import async_playwright
                    self.playwright = await asyncio.wait_for(async_playwright().start(), timeout=8.0)
            self.is_ready = True
            logger.info("[ENGINE] Browser runtime ready in lazy multi-account standby mode.")
            self.keepalive_task = asyncio.create_task(self._keepalive_loop())
        except Exception as e:
            logger.warning(f"[ENGINE-WARN] Could not initialize Browser runtime ({e}). Running in Standby mode.")
            self.is_ready = False

    async def close_account(self, account_id: str):
        """Cleanly closes browser context for a specific account without affecting others."""
        ctx = self._contexts.pop(account_id, None)
        self._pages.pop(account_id, None)
        self._drivers.pop(account_id, None)
        self._last_used.pop(account_id, None)
        if ctx:
            try:
                await ctx.close()
                logger.info(f"[ENGINE] Successfully closed browser context for account '{account_id}'")
            except Exception as e:
                logger.warning(f"[ENGINE] Error closing context for '{account_id}': {e}")

    async def ensure_account_ready(self, account_id: str) -> bool:
        """Initializes or reconnects browser context for a specific account ID."""
        acc_id = account_id.strip()
        lock = self._get_lock(acc_id)
        async with lock:
            if acc_id in self._contexts:
                page = self._pages.get(acc_id)
                if page and not page.is_closed():
                    self._last_used[acc_id] = time.time()
                    return True
                # Stale or closed, clean up before re-launching
                await self.close_account(acc_id)

            from core.healer import config_healer
            cfg, _ = config_healer.load_or_create()
            acc = next((a for a in cfg.accounts if a.id == acc_id), None)
            provider = acc.provider if acc else ("chatgpt" if "chatgpt" in acc_id.lower() else ("claude" if "claude" in acc_id.lower() else "gemini"))
            p_dir_rel = acc.profile_dir if (acc and acc.profile_dir) else f"browser_profiles/{acc_id}"

            profile_path = (get_runtime_cwd() / p_dir_rel).resolve()
            profile_path.mkdir(parents=True, exist_ok=True)

            # LRU Eviction check
            if len(self._contexts) >= self.max_active_contexts:
                candidates = [aid for aid in self._last_used if aid != acc_id and aid in self._contexts]
                if candidates:
                    lru_id = min(candidates, key=self._last_used.get)
                    logger.info(f"[ENGINE] LRU limit reached ({self.max_active_contexts}). Evicting account '{lru_id}'...")
                    await self.close_account(lru_id)

            if not self.playwright:
                try:
                    try:
                        from patchright.async_api import async_playwright
                    except ImportError:
                        from playwright.async_api import async_playwright
                    self.playwright = await async_playwright().start()
                except Exception as pw_err:
                    logger.error(f"[ENGINE] Could not start browser runtime: {pw_err}")
                    return False

            proxy_server = detect_proxy()
            proxy_cfg = {"server": proxy_server} if proxy_server else None

            from core.auth import kill_zombie_browser_processes
            kill_zombie_browser_processes(profile_path)

            launch_args = [
                "--disable-blink-features=AutomationControlled",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-infobars"
            ]
            is_frozen = getattr(sys, "frozen", False)
            channels = ["chrome", "msedge"] if (is_frozen or sys.platform == "win32") else ["chrome", "msedge", None]

            context = None
            last_err = None
            for channel in channels:
                try:
                    kwargs = {
                        "user_data_dir": str(profile_path),
                        "headless": self.headless,
                        "viewport": {"width": 1280, "height": 800},
                        "args": launch_args,
                        "ignore_default_args": ["--enable-automation"]
                    }
                    if channel:
                        kwargs["channel"] = channel
                    if proxy_cfg:
                        kwargs["proxy"] = proxy_cfg
                    context = await self.playwright.chromium.launch_persistent_context(**kwargs)
                    break
                except Exception as e:
                    last_err = e
                    continue

            if not context:
                err_str = str(last_err or "")
                if any(k in err_str.lower() for k in ["target page, context or browser has been closed", "exitcode=21", "singletonlock", "user data directory is already in use"]):
                    logger.error(f"[ENGINE] Profile for '{acc_id}' ({profile_path}) is locked by another process.")
                else:
                    logger.error(f"[ENGINE] Failed to launch context for '{acc_id}': {last_err}")
                return False

            try:
                await context.add_init_script("""
                    Object.defineProperty(navigator, 'webdriver', {
                        get: () => undefined
                    });
                """)
            except Exception:
                pass

            page = context.pages[0] if context.pages else await context.new_page()
            target_url = PROVIDER_URLS.get(provider.lower(), "https://gemini.google.com/app")
            if page.url == "about:blank":
                try:
                    await page.goto(target_url, wait_until="domcontentloaded", timeout=25000)
                except Exception as nav_e:
                    logger.warning(f"[ENGINE] Initial navigation for '{acc_id}' timed out or errored: {nav_e}")

            if provider == "chatgpt":
                driver = ChatGPTDriver(page)
            elif provider == "deepseek":
                driver = DeepSeekDriver(page)
            elif provider == "qwen":
                driver = QwenDriver(page)
            elif provider == "grok":
                driver = GrokDriver(page)
            else:
                driver = GeminiDriver(page)

            self._contexts[acc_id] = context
            self._pages[acc_id] = page
            self._drivers[acc_id] = driver
            self._last_used[acc_id] = time.time()
            self.is_ready = True
            logger.info(f"[ENGINE] Account '{acc_id}' ready (channel={channel or 'chromium'}, profile={profile_path})")
            return True

    def get_driver(self, account_id: str) -> BaseDriver:
        return self._drivers.get(account_id, self.mock_driver)

    def resolve_target_account(self, model_id: str, account_id: Optional[str] = None) -> Optional[str]:
        if account_id:
            return account_id
        model_lower = model_id.lower()
        if "deepseek" in model_lower:
            prov = "deepseek"
        elif "qwen" in model_lower:
            prov = "qwen"
        elif "grok" in model_lower:
            prov = "grok"
        elif "chatgpt" in model_lower or "gpt" in model_lower:
            prov = "chatgpt"
        elif "claude" in model_lower:
            prov = "claude"
        else:
            prov = "gemini"

        try:
            from core.mesh import mesh
            mesh_acc = mesh.get_active_web_account(prov)
            if mesh_acc:
                return mesh_acc
        except Exception:
            pass

        from core.healer import config_healer
        cfg, _ = config_healer.load_or_create()
        matching = [
            a for a in cfg.accounts
            if a.provider == prov and a.auth_type == "browser_session" and a.status == "active"
        ]
        if matching:
            return matching[0].id
        return None

    async def _keepalive_loop(self):
        """Periodically pings active web sessions every 90 minutes to keep cookies fresh."""
        while True:
            try:
                await asyncio.sleep(5400)  # 90 minutes
                active_ids = list(self._contexts.keys())
                for aid in active_ids:
                    page = self._pages.get(aid)
                    if page and not page.is_closed():
                        lock = self._get_lock(aid)
                        async with lock:
                            logger.info(f"[KEEP-ALIVE] Refreshing web subscription session for '{aid}'...")
                            if "gemini.google.com" in page.url:
                                await page.goto("https://gemini.google.com/app", wait_until="domcontentloaded", timeout=20000)
                            elif "chatgpt.com" in page.url:
                                await page.goto("https://chatgpt.com", wait_until="domcontentloaded", timeout=20000)
                            elif "deepseek.com" in page.url:
                                await page.goto("https://chat.deepseek.com", wait_until="domcontentloaded", timeout=20000)
                            elif "qwen.ai" in page.url:
                                await page.goto("https://chat.qwen.ai", wait_until="domcontentloaded", timeout=20000)
                            elif "grok.com" in page.url:
                                await page.goto("https://grok.com", wait_until="domcontentloaded", timeout=20000)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug(f"[KEEP-ALIVE-WARN] Could not ping sessions: {e}")

    async def close(self):
        """Gracefully closes all browser contexts and stops playwright runtime."""
        if self.keepalive_task:
            self.keepalive_task.cancel()
        for aid in list(self._contexts.keys()):
            await self.close_account(aid)
        if self.playwright:
            try:
                await self.playwright.stop()
            except Exception:
                pass
            self.playwright = None
        self.is_ready = False

    async def query(self, prompt: str, model_id: str, account_id: Optional[str] = None) -> str:
        """Executes query through selected account driver with automatic fallback."""
        target_acc = self.resolve_target_account(model_id, account_id)
        if not target_acc:
            return await self.mock_driver.send_prompt(prompt)

        lock = self._get_lock(target_acc)
        async with lock:
            ready = await self.ensure_account_ready(target_acc)
            if not ready:
                logger.warning(f"[ENGINE-WARN] Account '{target_acc}' is not ready. Serving Mock.")
                return await self.mock_driver.send_prompt(prompt)

            prov = "chatgpt" if "chatgpt" in target_acc.lower() else ("claude" if "claude" in target_acc.lower() else "gemini")
            driver = self.get_driver(target_acc)
            if driver != self.mock_driver:
                try:
                    if await driver.is_authenticated():
                        self._last_used[target_acc] = time.time()
                        try:
                            from core.mesh import mesh
                            mesh.record_web_success(prov, target_acc)
                        except Exception:
                            pass
                        return await driver.send_prompt(prompt)
                    else:
                        logger.info(f"[ENGINE-INFO] Session not authenticated for '{target_acc}'. Serving Standby response.")
                except Exception as e:
                    logger.error(f"[ENGINE-ERR] Driver execution failed on '{target_acc}' ({e}). Falling back to Mock.")
                    try:
                        from core.mesh import mesh
                        mesh.record_web_failure(prov, target_acc)
                    except Exception:
                        pass

            return await self.mock_driver.send_prompt(prompt)

    async def query_stream(self, prompt: str, model_id: str, account_id: Optional[str] = None) -> AsyncGenerator[str, None]:
        """Streams response tokens from web session."""
        target_acc = self.resolve_target_account(model_id, account_id)
        if not target_acc:
            async for token in self.mock_driver.send_prompt_stream(prompt):
                yield token
            return

        lock = self._get_lock(target_acc)
        async with lock:
            ready = await self.ensure_account_ready(target_acc)
            if not ready:
                logger.warning(f"[ENGINE-WARN] Account '{target_acc}' is not ready. Serving Mock stream.")
                async for token in self.mock_driver.send_prompt_stream(prompt):
                    yield token
                return

            prov = "chatgpt" if "chatgpt" in target_acc.lower() else ("claude" if "claude" in target_acc.lower() else "gemini")
            driver = self.get_driver(target_acc)
            if driver != self.mock_driver:
                try:
                    if await driver.is_authenticated():
                        self._last_used[target_acc] = time.time()
                        try:
                            from core.mesh import mesh
                            mesh.record_web_success(prov, target_acc)
                        except Exception:
                            pass
                        async for token in driver.send_prompt_stream(prompt):
                            yield token
                        return
                    else:
                        logger.info(f"[ENGINE-INFO] Session not authenticated for '{target_acc}'. Serving Standby stream.")
                except Exception as e:
                    logger.error(f"[ENGINE-ERR] Stream execution failed on '{target_acc}' ({e}). Falling back to Mock stream.")
                    try:
                        from core.mesh import mesh
                        mesh.record_web_failure(prov, target_acc)
                    except Exception:
                        pass

            async for token in self.mock_driver.send_prompt_stream(prompt):
                yield token

engine = BrowserSessionEngine()
