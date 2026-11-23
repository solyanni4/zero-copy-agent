"""
Web Driver for xAI Grok (https://grok.com).
Supports Grok 3 (grok-3) and Grok 3 Mini (grok-3-mini).
"""
import asyncio
import logging
import time
from typing import AsyncGenerator
from core.drivers.base import BaseDriver

logger = logging.getLogger("zcao.driver.grok")


class GrokDriver(BaseDriver):
    URL = "https://grok.com"

    async def is_authenticated(self) -> bool:
        if not self.page:
            return False
        try:
            if "grok.com" not in self.page.url:
                await self.page.goto(self.URL, wait_until="domcontentloaded", timeout=20000)
                await asyncio.sleep(1)

            # Wait for SPA hydration in a loop up to 8s (1s step)
            for _ in range(8):
                # Fail-fast check for login redirect or captcha
                await self.check_fast_auth_or_captcha("Grok")

                if any(x in self.page.url for x in ("/login", "/signin", "/auth")):
                    return False

                # 1. Check for explicit login/sign-in buttons
                login_btn = await self.page.query_selector(
                    'button:has-text("Sign in"), button:has-text("Sign In"), button:has-text("Log in"), '
                    'button:has-text("Войти"), a[href*="login"], a[href*="signin"], div[class*="login"]'
                )
                if login_btn:
                    return False

                # 2. Check for chat input element
                input_elem = await self.page.query_selector(
                    'textarea[placeholder*="Grok"], textarea[placeholder*="Ask"], textarea, '
                    'div[contenteditable="true"]'
                )
                if input_elem:
                    return True

                await asyncio.sleep(1)

            return False
        except Exception as e:
            logger.debug(f"[Grok] is_authenticated check error: {e}")
            return False

    async def send_prompt(self, prompt: str) -> str:
        if not self.page:
            raise RuntimeError("Browser page not initialized")

        if "grok.com" not in self.page.url:
            await self.page.goto(self.URL, wait_until="domcontentloaded", timeout=20000)
            await asyncio.sleep(1)

        # Fail-fast check and popup dismissal
        await self.check_fast_auth_or_captcha("Grok")
        await self.dismiss_popups()

        input_selectors = [
            'textarea[placeholder*="Grok"]',
            'textarea[placeholder*="Ask"]',
            'textarea',
            'div[contenteditable="true"]'
        ]
        input_elem = None
        for sel in input_selectors:
            try:
                input_elem = await self.page.wait_for_selector(sel, timeout=3000)
                if input_elem:
                    break
            except Exception:
                continue

        if not input_elem:
            await self.check_fast_auth_or_captcha("Grok")
            raise RuntimeError("Could not locate Grok prompt area. Please ensure you are logged in.")

        try:
            await input_elem.click(force=True, timeout=2000)
        except Exception:
            pass

        await self.page.evaluate(
            '''([el, text]) => {
                el.focus();
                if (el.tagName && el.tagName.toLowerCase() === 'textarea') {
                    el.value = text;
                    el.dispatchEvent(new Event('input', { bubbles: true }));
                    el.dispatchEvent(new Event('change', { bubbles: true }));
                } else {
                    try {
                        document.execCommand('insertText', false, text);
                    } catch (e) {}
                    if (!el.innerText || el.innerText.trim() === '') {
                        el.innerText = text;
                        el.dispatchEvent(new Event('input', { bubbles: true }));
                    }
                }
            }''',
            [input_elem, prompt]
        )
        await asyncio.sleep(0.3)

        send_selectors = [
            'button[aria-label="Submit"]',
            'button[aria-label*="Send"]',
            'button[type="submit"]',
            'div[role="button"][aria-label*="Send"]',
            'button:has(svg)'
        ]
        clicked = False
        for btn_sel in send_selectors:
            btn = await self.page.query_selector(btn_sel)
            if btn:
                try:
                    await btn.click()
                    clicked = True
                    break
                except Exception:
                    continue

        if not clicked:
            await self.page.keyboard.press("Enter")

        # Smart response waiting with TTFT cut-off and stability detection
        async def is_generating_fn() -> bool:
            stop_btn = await self.page.query_selector(
                'button[aria-label*="Stop"], div[role="button"][aria-label*="Stop"], button[class*="stop"]'
            )
            return bool(stop_btn)

        try:
            response_text = await self.wait_for_smart_response(
                extract_fn=self._extract_last_response,
                is_generating_fn=is_generating_fn,
                ttft_timeout=25.0,
                max_generation_time=120.0,
                stability_window=3.5
            )
            return response_text or "[zero-copy-agent] Ответ от Grok получен, но не удалось извлечь DOM разметку."
        except Exception as e:
            await self.save_failure_screenshot("Grok")
            raise

    async def reset_to_new_chat(self):
        """Resets Grok tab to a fresh conversation to avoid context accumulation."""
        if not self.page or getattr(self.page, "is_closed", lambda: True)():
            return
        try:
            new_btn = await self.page.query_selector(
                'a[href="/"], button[aria-label*="New chat"], button:has-text("New chat"), button:has-text("Новый чат")'
            )
            if new_btn:
                await new_btn.click()
                await asyncio.sleep(0.5)
            else:
                if "/chat/" in self.page.url or "grok.com" not in self.page.url:
                    await self.page.goto(self.URL, wait_until="domcontentloaded", timeout=15000)
        except Exception:
            pass

    async def send_prompt_stream(self, prompt: str) -> AsyncGenerator[str, None]:
        full_response = await self.send_prompt(prompt)
        chunks = full_response.split(" ")
        for i, chunk in enumerate(chunks):
            yield chunk + (" " if i < len(chunks) - 1 else "")
            await asyncio.sleep(0.01)

    async def _extract_last_response(self) -> str:
        response_selectors = [
            '.message-bubble',
            'div[data-testid*="message"]',
            'div.prose',
            '.markdown',
            'div[class*="response"]'
        ]
        for sel in response_selectors:
            elems = await self.page.query_selector_all(sel)
            if elems:
                text = await elems[-1].inner_text()
                if text and text.strip():
                    return text.strip()
        return ""
