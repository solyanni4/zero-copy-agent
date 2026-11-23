"""
Web Driver for Alibaba Qwen (https://chat.qwen.ai).
Supports Qwen-Max (qwen-max), Qwen-Coder (qwen-coder), and QwQ (qwq).
"""
import asyncio
import logging
import time
from typing import AsyncGenerator
from core.drivers.base import BaseDriver

logger = logging.getLogger("zcao.driver.qwen")


class QwenDriver(BaseDriver):
    URL = "https://chat.qwen.ai"

    async def is_authenticated(self) -> bool:
        if not self.page:
            return False
        try:
            if "chat.qwen.ai" not in self.page.url:
                await self.page.goto(self.URL, wait_until="domcontentloaded", timeout=20000)
                await asyncio.sleep(1)

            # Fail-fast check for login redirect or captcha
            await self.check_fast_auth_or_captcha("Qwen")

            # Wait for SPA hydration in a loop up to 8s (1s step)
            for _ in range(8):
                await self.check_fast_auth_or_captcha("Qwen")

                # 1. Check for explicit login or signup buttons
                login_btn = await self.page.query_selector(
                    'button:has-text("Log in"), button:has-text("Sign in"), button:has-text("Sign In"), '
                    'button:has-text("Войти"), a[href*="login"], a[href*="signin"], div[class*="login-btn"]'
                )
                if login_btn:
                    return False

                # 2. Check for chat input element
                input_elem = await self.page.query_selector(
                    '#chat-input, textarea[placeholder*="Qwen"], textarea[placeholder*="Ask"], '
                    'div[contenteditable="true"], textarea'
                )
                if input_elem:
                    return True

                await asyncio.sleep(1)

            return False
        except Exception as e:
            logger.debug(f"[Qwen] is_authenticated check error: {e}")
            return False

    async def send_prompt(self, prompt: str) -> str:
        if not self.page:
            raise RuntimeError("Browser page not initialized")

        if "chat.qwen.ai" not in self.page.url:
            await self.page.goto(self.URL, wait_until="domcontentloaded", timeout=20000)
            await asyncio.sleep(1)

        # Fail-fast check and popup dismissal
        await self.check_fast_auth_or_captcha("Qwen")
        await self.dismiss_popups()

        input_selectors = [
            '#chat-input',
            'textarea[placeholder*="Qwen"]',
            'textarea[placeholder*="Ask"]',
            'div[contenteditable="true"]',
            'textarea'
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
            await self.check_fast_auth_or_captcha("Qwen")
            raise RuntimeError("Could not locate Qwen prompt area. Please ensure you are logged in.")

        # Capture baseline response before sending prompt
        baseline_text = await self._extract_last_response()

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
            'button[aria-label*="Send"]',
            'div[role="button"][aria-label*="Send"]',
            'button[data-testid*="send"]',
            'div[class*="send-btn"]',
            'button.send-button'
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

        # Smart response waiting with thinking indicator support
        async def is_generating_fn() -> bool:
            stop_btn = await self.page.query_selector(
                'button[aria-label*="Stop"], div[role="button"][aria-label*="Stop"], button[class*="stop"], button[data-testid*="stop"]'
            )
            thinking = await self.page.query_selector(
                '[class*="thinking"], [class*="reasoning"], [aria-busy="true"], .ant-spin, .loading, [class*="generating"]'
            )
            return bool(stop_btn or thinking)

        response_text = await self.wait_for_smart_response(
            extract_fn=self._extract_last_response,
            is_generating_fn=is_generating_fn,
            ttft_timeout=25.0,
            max_generation_time=120.0,
            stability_window=3.5,
            baseline_text=baseline_text
        )
        return response_text or "[zero-copy-agent] Ответ от Qwen получен, но не удалось извлечь DOM разметку."

    async def reset_to_new_chat(self):
        """Resets Qwen tab to a fresh conversation to keep context warm."""
        if not self.page or self.page.is_closed():
            return
        try:
            new_btn = await self.page.query_selector(
                'button:has-text("New chat"), button:has-text("Новый чат"), div[class*="new-chat"]'
            )
            if new_btn:
                await new_btn.click()
                await asyncio.sleep(0.5)
            else:
                if "chat.qwen.ai" not in self.page.url or self.page.url != "https://chat.qwen.ai/":
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
        if not self.page:
            return ""
        try:
            text = await self.page.evaluate('''() => {
                const assistants = document.querySelectorAll(
                    'div[data-role="assistant"], div[class*="message-assistant"], .qwen-chat-message, .message-item[data-is-user="false"]'
                );
                let target = null;
                if (assistants && assistants.length > 0) {
                    target = assistants[assistants.length - 1];
                } else {
                    const mds = document.querySelectorAll('.markdown-body, div.markdown');
                    if (mds && mds.length > 0) target = mds[mds.length - 1];
                }
                if (!target) return '';

                const clone = target.cloneNode(true);
                // Exclude thinking/reasoning and temporary status indicators
                const extra = clone.querySelectorAll('[class*="thinking"], [class*="reasoning"], .ant-spin, [class*="generating"], .status-tag');
                extra.forEach(e => e.remove());

                const md = clone.querySelector('.markdown-body, div.markdown');
                const raw = (md ? md.innerText : clone.innerText) || '';
                return raw.trim();
            }''')
            if text and text.strip():
                return text.strip()
        except Exception:
            pass

        response_selectors = [
            'div[data-role="assistant"] .markdown-body',
            'div[class*="message-assistant"] .markdown-body',
            '.qwen-chat-message .markdown-body',
            '.markdown-body',
            'div[data-role="assistant"]',
            'div[class*="message-assistant"]',
            '.qwen-chat-message',
            'div.markdown'
        ]
        for sel in response_selectors:
            elems = await self.page.query_selector_all(sel)
            if elems:
                text = await elems[-1].inner_text()
                if text and text.strip():
                    return text.strip()
        return ""
