"""
Browser Session Engine for zero-copy-agent.
Manages headless persistent context, driver lifecycle, stealth, proxy routing,
session keep-alive daemon (SKD), and model badges.
"""
import asyncio
import os
import socket
from pathlib import Path
from typing import AsyncGenerator, Optional
from core.config import config
from core.drivers import GeminiDriver, ChatGPTDriver, MockDriver, BaseDriver

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
    else:
        label = f"{model_id} (Живая веб-сессия)"
    return f"**[zero-copy-agent | {label}]**\n\n"

class BrowserSessionEngine:
    def __init__(self):
        self.profile_dir = Path(config.browser.user_data_dir).resolve()
        self.headless = config.browser.headless
        self.playwright = None
        self.context = None
        self.page = None
        self.active_provider = config.browser.provider
        self.lock = asyncio.Lock()
        self.is_ready = False
        self.mock_driver = MockDriver()
        self.keepalive_task = None

    async def initialize(self):
        """Initializes the background browser session with stealth and proxy."""
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        proxy_server = detect_proxy()
        proxy_cfg = {"server": proxy_server} if proxy_server else None

        launch_args = [
            "--disable-blink-features=AutomationControlled",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-infobars"
        ]

        try:
            from playwright.async_api import async_playwright
            self.playwright = await async_playwright().start()

            for channel in ["chrome", "msedge", None]:
                try:
                    kwargs = {
                        "user_data_dir": str(self.profile_dir),
                        "headless": self.headless,
                        "viewport": {"width": 1280, "height": 800},
                        "args": launch_args,
                        "ignore_default_args": ["--enable-automation"]
                    }
                    if channel:
                        kwargs["channel"] = channel
                    if proxy_cfg:
                        kwargs["proxy"] = proxy_cfg

                    self.context = await self.playwright.chromium.launch_persistent_context(**kwargs)
                    break
                except Exception:
                    if channel:
                        continue
                    raise

            # Stealth: mask navigator.webdriver
            await self.context.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', {
                    get: () => undefined
                });
            """)

            self.page = self.context.pages[0] if self.context.pages else await self.context.new_page()
            self.is_ready = True
            print(f"[ENGINE] Headless browser session initialized (profile: {self.profile_dir}, proxy: {proxy_server})")

            # Start Session Keep-Alive Daemon (SKD) to prevent cookie expiration
            self.keepalive_task = asyncio.create_task(self._keepalive_loop())
        except Exception as e:
            print(f"[ENGINE-WARN] Could not launch Playwright browser ({e}). Running in Zero-Copy Standby mode.")
            self.is_ready = False

    async def _keepalive_loop(self):
        """Periodically pings the active web session every 90 minutes to keep cookies fresh."""
        while True:
            try:
                await asyncio.sleep(5400)  # 90 minutes
                if self.is_ready and self.page:
                    print("[KEEP-ALIVE] Refreshing web subscription session to prevent cookie expiration...")
                    async with self.lock:
                        if "gemini.google.com" in self.page.url:
                            await self.page.goto("https://gemini.google.com/app", wait_until="domcontentloaded", timeout=20000)
                        elif "chatgpt.com" in self.page.url:
                            await self.page.goto("https://chatgpt.com", wait_until="domcontentloaded", timeout=20000)
                    print("[KEEP-ALIVE] Session refreshed successfully. Active for long-running workflows.")
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"[KEEP-ALIVE-WARN] Could not ping session: {e}")

    async def close(self):
        """Gracefully closes browser context and stops keepalive daemon."""
        if self.keepalive_task:
            self.keepalive_task.cancel()
        if self.context:
            await self.context.close()
        if self.playwright:
            await self.playwright.stop()
        self.is_ready = False

    async def _get_page_for_model(self, model_id: str):
        """Gets or creates dedicated tab for the model provider."""
        if not self.is_ready or not self.context:
            return None

        if not hasattr(self, "provider_pages"):
            self.provider_pages = {}

        model_lower = model_id.lower()
        if "chatgpt" in model_lower or "gpt" in model_lower:
            prov = "chatgpt"
            url = "https://chatgpt.com"
        elif "claude" in model_lower:
            prov = "claude"
            url = "https://claude.ai"
        else:
            prov = "gemini"
            url = "https://gemini.google.com/app"

        page = self.provider_pages.get(prov)
        if page is not None and not page.is_closed():
            return page

        for p in self.context.pages:
            if prov in p.url.lower():
                self.provider_pages[prov] = p
                return p

        if self.page and (self.page.url == "about:blank" or prov in self.page.url.lower()):
            self.provider_pages[prov] = self.page
            return self.page

        try:
            new_p = await self.context.new_page()
            await new_p.goto(url, wait_until="domcontentloaded", timeout=25000)
            self.provider_pages[prov] = new_p
            return new_p
        except Exception as e:
            print(f"[ENGINE-WARN] Could not open dedicated tab for {prov}: {e}")
            return self.page

    async def _get_driver_async(self, model_id: str) -> BaseDriver:
        """Selects driver based on model requested with dedicated provider page."""
        if not self.is_ready:
            return self.mock_driver

        model_lower = model_id.lower()
        if "mock" in model_lower:
            return self.mock_driver

        page = await self._get_page_for_model(model_id) or self.page
        if not page:
            return self.mock_driver

        if "chatgpt" in model_lower or "gpt" in model_lower:
            return ChatGPTDriver(page)
        return GeminiDriver(page)

    def _get_driver(self, model_id: str) -> BaseDriver:
        """Synchronous driver selector fallback."""
        if not self.is_ready or not self.page:
            return self.mock_driver

        model_lower = model_id.lower()
        if "chatgpt" in model_lower or "gpt" in model_lower:
            return ChatGPTDriver(self.page)
        elif "mock" in model_lower:
            return self.mock_driver
        return GeminiDriver(self.page)

    async def query(self, prompt: str, model_id: str) -> str:
        """Executes query through selected driver with automatic fallback."""
        async with self.lock:
            driver = await self._get_driver_async(model_id)
            if driver != self.mock_driver:
                try:
                    if await driver.is_authenticated():
                        raw_result = await driver.send_prompt(prompt)
                        return raw_result
                    else:
                        print(f"[ENGINE-INFO] Session not authenticated for {model_id}. Serving Standby response.")
                except Exception as e:
                    print(f"[ENGINE-ERR] Driver execution failed ({e}). Falling back to Mock.")

            return await self.mock_driver.send_prompt(prompt)

    async def query_stream(self, prompt: str, model_id: str) -> AsyncGenerator[str, None]:
        """Streams response tokens from web session."""
        async with self.lock:
            driver = await self._get_driver_async(model_id)
            if driver != self.mock_driver:
                try:
                    if await driver.is_authenticated():
                        async for token in driver.send_prompt_stream(prompt):
                            yield token
                        return
                    else:
                        print(f"[ENGINE-INFO] Session not authenticated for {model_id}. Serving Standby stream.")
                except Exception as e:
                    print(f"[ENGINE-ERR] Stream execution failed ({e}). Falling back to Mock stream.")

            async for token in self.mock_driver.send_prompt_stream(prompt):
                yield token

engine = BrowserSessionEngine()
