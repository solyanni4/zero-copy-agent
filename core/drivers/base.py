"""
Base WebDriver interface and common utilities for zero-copy-agent.
Provides Fail-Fast diagnostics (auth redirect & CAPTCHA detection),
common banner dismissal, and smart TTFT + stability response waiting.
"""
import asyncio
import time
import logging
from abc import ABC, abstractmethod
from typing import AsyncGenerator, Callable, Awaitable, Optional

logger = logging.getLogger("zcao.drivers.base")


class WebSessionAuthError(RuntimeError):
    """Raised when a browser web session encounters an auth redirect, login page, or CAPTCHA."""
    def __init__(self, message: str = "Требуется повторная авторизация"):
        super().__init__(message)
        self.message = message


COMMON_COOKIE_DISMISS_JS = '''() => {
    try {
        const buttons = Array.from(document.querySelectorAll('button, a, div[role="button"]'));
        for (const b of buttons) {
            const txt = (b.innerText || '').toLowerCase().trim();
            if (txt === 'accept all' || txt === 'принять все' || txt === 'i agree' || 
                txt === 'got it' || txt === 'понятно' || txt === 'dismiss' || 
                txt === 'close' || txt === 'закрыть') {
                b.click();
            }
        }
        const closeBtns = document.querySelectorAll('button[aria-label="Close"], button[aria-label="Закрыть"], button.close, [class*="close-icon"]');
        for (const cb of closeBtns) {
            cb.click();
        }
    } catch(e) {}
}'''


class BaseDriver(ABC):
    def __init__(self, page):
        self.page = page

    @abstractmethod
    async def is_authenticated(self) -> bool:
        """Checks if the active browser session is logged into the provider."""
        pass

    @abstractmethod
    async def send_prompt(self, prompt: str) -> str:
        """Sends prompt to web interface and waits for complete response."""
        pass

    @abstractmethod
    async def send_prompt_stream(self, prompt: str) -> AsyncGenerator[str, None]:
        """Streams response tokens from web interface as they are rendered."""
        pass

    async def check_fast_auth_or_captcha(self, provider_name: str = ""):
        """
        Fail-Fast Check (evaluated immediately after page open / first 5-8s).
        Detects login redirects (accounts.google.com, /login, /signin) and CAPTCHAs.
        Raises WebSessionAuthError immediately if session is invalidated.
        """
        if not self.page:
            return
        url = getattr(self.page, "url", "") or ""

        # 1. URL checks
        if "accounts.google.com" in url or "ServiceLogin" in url:
            raise WebSessionAuthError(f"Требуется повторная авторизация в {provider_name or 'Google'}: обнаружен редирект на {url}")

        if any(p in url.lower() for p in ("/login", "/signin", "/auth/login", "auth0.", "challenge", "checkpoint")):
            raise WebSessionAuthError(f"Требуется повторная авторизация в {provider_name}: редирект на страницу входа ({url})")

        # 2. CAPTCHA checks (DOM)
        try:
            captcha_found = await self.page.evaluate('''() => {
                const iframes = Array.from(document.querySelectorAll('iframe'));
                for (const f of iframes) {
                    const src = (f.src || '').toLowerCase();
                    if (src.includes('recaptcha') || src.includes('hcaptcha') || src.includes('turnstile') || src.includes('challenge')) {
                        return 'iframe ' + src;
                    }
                }
                if (document.querySelector('.cf-turnstile, #challenge-stage, #cf-challenge-running, [id*="captcha"]')) {
                    return 'captcha_container';
                }
                const bodyText = (document.body ? document.body.innerText : '').toLowerCase();
                if (bodyText.includes('verify you are human') || 
                    bodyText.includes('подтвердите, что вы человек') ||
                    bodyText.includes('security check to continue')) {
                    return 'verify_text';
                }
                return null;
            }''')
            if captcha_found:
                raise WebSessionAuthError(f"Обнаружена проверка на человека (CAPTCHA: {captcha_found}) в {provider_name}")
        except WebSessionAuthError:
            raise
        except Exception as e:
            logger.debug(f"[BaseDriver] CAPTCHA eval check note: {e}")

    async def dismiss_popups(self):
        """Dismisses common cookie banners, announcements and overlay modals."""
        if not self.page:
            return
        try:
            await self.page.evaluate(COMMON_COOKIE_DISMISS_JS)
        except Exception:
            pass

    async def save_failure_screenshot(self, provider_name: str = "driver") -> Optional[str]:
        """Saves diagnostic screenshot to logs/web_failures/<provider>_<timestamp>.png upon failure or timeout."""
        if not self.page:
            return None
        try:
            if getattr(self.page, "is_closed", lambda: True)():
                return None
            try:
                from core.config import get_runtime_cwd
                fail_dir = get_runtime_cwd() / "logs" / "web_failures"
            except Exception:
                from pathlib import Path
                fail_dir = Path("logs") / "web_failures"
            fail_dir.mkdir(parents=True, exist_ok=True)
            shot_path = fail_dir / f"{provider_name.lower()}_{int(time.time())}.png"
            await self.page.screenshot(path=str(shot_path), timeout=5000)
            logger.info(f"[{provider_name}] Failure screenshot captured: {shot_path}")
            return str(shot_path)
        except Exception as e:
            logger.debug(f"[{provider_name}] Could not capture failure screenshot: {e}")
            return None

    async def wait_for_smart_response(
        self,
        extract_fn: Callable[[], Awaitable[str]],
        is_generating_fn: Callable[[], Awaitable[bool]],
        ttft_timeout: float = 25.0,
        max_generation_time: float = 120.0,
        stability_window: float = 3.5,
        baseline_text: str = ""
    ) -> str:
        """
        Smart two-stage response waiting with Thinking model support:
        1. TTFT check (25s): aborts if model does not begin generating within 25 seconds.
           Ignores current_text while it matches baseline_text.
        2. Active generation / thinking tracking (up to 120s):
           - While is_generating is True: does NOT cut off after stability_window (3.5s).
             Allows thinking pauses. Emergency exit only if text stalls > 25.0s and len > 80.
           - When is_generating is False and text is non-empty:
             Returns result after 2.5s (or stability_window) of stability.
        """
        start_time = time.monotonic()
        first_char_seen = False
        last_text = ""
        stable_since = None
        base_clean = (baseline_text or "").strip()

        def is_new_content(txt: Optional[str]) -> bool:
            if not txt:
                return False
            clean = txt.strip()
            if not clean:
                return False
            if base_clean and clean == base_clean:
                return False
            return True

        # Stage 1: Wait for TTFT (first token or generating/thinking indicator)
        while (time.monotonic() - start_time) < ttft_timeout:
            raw_text = await extract_fn()
            is_generating = await is_generating_fn()

            if is_new_content(raw_text):
                first_char_seen = True
                last_text = raw_text.strip()
                break

            if is_generating:
                first_char_seen = True
                break

            await asyncio.sleep(0.5)

        if not first_char_seen:
            raise TimeoutError(f"TTFT timeout ({int(ttft_timeout)}s): модель не начала отвечать")

        # Stage 2: Active generation up to max_generation_time
        gen_start = time.monotonic()
        while (time.monotonic() - gen_start) < max_generation_time:
            await asyncio.sleep(0.5)
            raw_text = await extract_fn()
            is_generating = await is_generating_fn()

            current_text = raw_text.strip() if is_new_content(raw_text) else ""
            curr_len = len(current_text)
            last_len = len(last_text)

            if curr_len > last_len:
                # Text is actively growing
                last_text = current_text
                stable_since = None
            elif curr_len > 0 and curr_len == last_len:
                # Text hasn't grown since last tick
                if stable_since is None:
                    stable_since = time.monotonic()
                else:
                    elapsed_stable = time.monotonic() - stable_since
                    if is_generating:
                        # Model is still actively thinking or generating.
                        # Do not interrupt on stability_window!
                        # Only emergency exit if text stalled > 25.0s and length > 80.
                        if elapsed_stable >= 25.0 and curr_len > 80:
                            return current_text
                    else:
                        # Generation finished (is_generating is False) and text is non-empty
                        exit_window = min(stability_window, 2.5)
                        if elapsed_stable >= exit_window:
                            return current_text
            elif not is_generating and curr_len > 0:
                if stable_since is None:
                    stable_since = time.monotonic()
                elif (time.monotonic() - stable_since) >= min(stability_window, 2.5):
                    return current_text
            elif not is_generating and last_len > 0:
                if stable_since is None:
                    stable_since = time.monotonic()
                elif (time.monotonic() - stable_since) >= min(stability_window, 2.5):
                    return last_text

        final_raw = await extract_fn()
        final_text = final_raw.strip() if is_new_content(final_raw) else ""
        return final_text or last_text

    async def reset_to_new_chat(self):
        """Prepares current tab for a new conversation while keeping context warm."""
        pass
