"""
Web Driver for DeepSeek (https://chat.deepseek.com).
Supports DeepSeek-V3 (deepseek-chat) and DeepSeek-R1 (deepseek-reasoner / DeepThink).
"""
import asyncio
import logging
from typing import AsyncGenerator
from core.drivers.base import BaseDriver

logger = logging.getLogger("zcao.driver.deepseek")


class DeepSeekDriver(BaseDriver):
    URL = "https://chat.deepseek.com"

    async def is_authenticated(self) -> bool:
        if not self.page:
            return False
        try:
            if "chat.deepseek.com" not in self.page.url:
                await self.page.goto(self.URL, wait_until="domcontentloaded", timeout=20000)
                await asyncio.sleep(1)

            # Wait for SPA hydration in a loop up to 8s (1s step)
            for _ in range(8):
                if any(x in self.page.url for x in ("/sign_in", "/login")):
                    return False

                # 1. Check for explicit login or registration buttons
                login_btn = await self.page.query_selector(
                    'a[href*="/sign_in"], a[href*="/login"], button:has-text("Log in"), '
                    'button:has-text("Sign in"), button:has-text("Войти"), button:has-text("Регистрация"), '
                    'div[class*="login"], div[class*="signIn"]'
                )
                if login_btn:
                    return False

                # 2. Check for chat input element (presence indicates authenticated session)
                input_elem = await self.page.query_selector(
                    '#chat-input, textarea[placeholder*="DeepSeek"], textarea[placeholder*="Сообщение"], '
                    'div[contenteditable="true"], textarea'
                )
                if input_elem:
                    return True

                await asyncio.sleep(1)

            return False
        except Exception as e:
            logger.debug(f"[DeepSeek] is_authenticated check error: {e}")
            return False

    async def send_prompt(self, prompt: str) -> str:
        if not self.page:
            raise RuntimeError("Browser page not initialized")

        if "chat.deepseek.com" not in self.page.url:
            await self.page.goto(self.URL, wait_until="domcontentloaded", timeout=20000)
            await asyncio.sleep(2)

        # Suppress cookie/announcement overlays if present
        try:
            await self.page.evaluate('''() => {
                const buttons = Array.from(document.querySelectorAll('button, a, div[role="button"]'));
                for (const b of buttons) {
                    const txt = (b.innerText || '').toLowerCase().trim();
                    if (txt === 'accept all' || txt === 'принять все' || txt === 'accept' || 
                        txt === 'got it' || txt === 'понятно' || txt === 'close' || 
                        txt === 'dismiss' || txt === 'not now' || txt === 'закрыть') {
                        b.click();
                    }
                }
                const closeBtns = document.querySelectorAll('button[aria-label="Close"], button.close, div[class*="close"]');
                for (const cb of closeBtns) {
                    cb.click();
                }
            }''')
        except Exception:
            pass

        input_selectors = [
            '#chat-input',
            'textarea[placeholder*="DeepSeek"]',
            'textarea[placeholder*="Сообщение"]',
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
            raise RuntimeError("Could not locate DeepSeek prompt area. Please ensure you are logged in.")

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
            'div[role="button"][aria-label*="Send"]',
            'button[aria-label*="Send"]',
            'div[class*="send-button"]',
            'div[class*="sendButton"]',
            'div.ds-icon-button'
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

        # Wait for generation to complete
        await asyncio.sleep(2)
        max_wait = 90
        waited = 0
        last_text = ""
        while waited < max_wait:
            await asyncio.sleep(1)
            waited += 1

            stop_btn = await self.page.query_selector(
                'div[role="button"][aria-label*="Stop"], button[aria-label*="Stop"], div[class*="stop-button"], div[class*="stopButton"]'
            )
            if not stop_btn and waited > 3:
                current_text = await self._extract_last_response()
                if current_text and current_text == last_text:
                    return current_text
                last_text = current_text

        return last_text or "[zero-copy-agent] Ответ от DeepSeek получен, но не удалось извлечь DOM разметку."

    async def send_prompt_stream(self, prompt: str) -> AsyncGenerator[str, None]:
        full_response = await self.send_prompt(prompt)
        chunks = full_response.split(" ")
        for i, chunk in enumerate(chunks):
            yield chunk + (" " if i < len(chunks) - 1 else "")
            await asyncio.sleep(0.01)

    async def _extract_last_response(self) -> str:
        response_selectors = [
            '.ds-markdown',
            'div[class*="message-content"]',
            'div[class*="chat-message"] .markdown',
            '.fbb737a4',
            'div.markdown'
        ]
        for sel in response_selectors:
            elems = await self.page.query_selector_all(sel)
            if elems:
                text = await elems[-1].inner_text()
                if text and text.strip():
                    return text.strip()
        return ""
