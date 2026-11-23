"""
Web Driver for OpenAI ChatGPT Plus (https://chatgpt.com).
"""
import asyncio
import time
from typing import AsyncGenerator
from core.drivers.base import BaseDriver

class ChatGPTDriver(BaseDriver):
    URL = "https://chatgpt.com"

    async def is_authenticated(self) -> bool:
        if not self.page:
            return False
        try:
            if "chatgpt.com" not in self.page.url:
                await self.page.goto(self.URL, wait_until="domcontentloaded", timeout=20000)
                await asyncio.sleep(1)

            # Ожидаем гидратацию SPA в цикле до 8 секунд с шагом 1 сек
            for _ in range(8):
                # Fail-fast check for login redirect or captcha
                await self.check_fast_auth_or_captcha("ChatGPT")

                # 1. Проверяем явную кнопку входа или регистрации
                login_btn = await self.page.query_selector(
                    'button[data-testid="login-button"], a[href*="/login"], button:has-text("Log in"), '
                    'button:has-text("Войти"), button:has-text("Sign up"), a[href*="/signup"]'
                )
                if login_btn:
                    return False

                # 2. Проверяем наличие поля ввода промпта (признак авторизованного чата)
                input_elem = await self.page.query_selector(
                    '#prompt-textarea, div#prompt-textarea[contenteditable="true"], textarea[data-id="root"], '
                    'div[contenteditable="true"], textarea'
                )
                if input_elem:
                    return True

                await asyncio.sleep(1)

            return False
        except Exception:
            return False

    async def send_prompt(self, prompt: str) -> str:
        if not self.page:
            raise RuntimeError("Browser page not initialized")

        if "chatgpt.com" not in self.page.url:
            await self.page.goto(self.URL, wait_until="domcontentloaded", timeout=20000)
            await asyncio.sleep(1)

        # Fail-fast check and popup dismissal
        await self.check_fast_auth_or_captcha("ChatGPT")
        await self.dismiss_popups()

        input_selectors = [
            '#prompt-textarea',
            'div#prompt-textarea[contenteditable="true"]',
            'textarea[data-id="root"]',
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
            await self.check_fast_auth_or_captcha("ChatGPT")
            raise RuntimeError("Could not locate ChatGPT prompt area. Please ensure you are logged in.")

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
            'button[data-testid="send-button"]',
            'button[aria-label="Send prompt"]',
            'button[data-testid*="send"]'
        ]
        clicked = False
        for btn_sel in send_selectors:
            btn = await self.page.query_selector(btn_sel)
            if btn and await btn.is_enabled():
                await btn.click()
                clicked = True
                break

        if not clicked:
            await self.page.keyboard.press("Enter")

        # Smart response waiting with TTFT cut-off and stability detection
        async def is_generating_fn() -> bool:
            stop_btn = await self.page.query_selector(
                'button[data-testid="stop-button"], button[aria-label*="Stop"], button[aria-label*="stop"]'
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
            return response_text or "[zero-copy-agent] Ответ от ChatGPT получен, но не удалось извлечь DOM разметку."
        except Exception as e:
            await self.save_failure_screenshot("ChatGPT")
            raise

    async def reset_to_new_chat(self):
        """Resets ChatGPT tab to a fresh conversation to avoid context accumulation."""
        if not self.page or getattr(self.page, "is_closed", lambda: True)():
            return
        try:
            new_btn = await self.page.query_selector(
                'a[href="/"], button[data-testid="new-chat-button"], button[aria-label*="New chat"], button:has-text("New chat"), button:has-text("Новый чат")'
            )
            if new_btn:
                await new_btn.click()
                await asyncio.sleep(0.5)
            else:
                if "/c/" in self.page.url or "chatgpt.com" not in self.page.url:
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
            'div[data-message-author-role="assistant"] .markdown',
            'div[data-message-author-role="assistant"]',
            '.agent-turn .markdown',
            'div.markdown'
        ]
        for sel in response_selectors:
            elems = await self.page.query_selector_all(sel)
            if elems:
                text = await elems[-1].inner_text()
                if text and text.strip():
                    return text.strip()
        return ""
