"""
Web Driver for Google Gemini Advanced (https://gemini.google.com/app).
"""
import asyncio
import time
from typing import AsyncGenerator
from core.drivers.base import BaseDriver

class GeminiDriver(BaseDriver):
    URL = "https://gemini.google.com/app"

    async def is_authenticated(self) -> bool:
        if not self.page:
            return False
        try:
            if "gemini.google.com" not in self.page.url:
                await self.page.goto(self.URL, wait_until="domcontentloaded", timeout=15000)
                await asyncio.sleep(1)

            # Fail-fast check for login redirect or captcha
            await self.check_fast_auth_or_captcha("Gemini")

            # Ожидаем загрузку SPA в цикле до 8 секунд с шагом 1 сек
            for _ in range(8):
                await self.check_fast_auth_or_captcha("Gemini")

                # Проверяем явные кнопки входа
                signin_btn = await self.page.query_selector(
                    'a[href*="ServiceLogin"], a[href*="InteractiveLogin"], button:has-text("Sign in"), button:has-text("Войти")'
                )
                if signin_btn:
                    return False

                # Проверяем наличие поля ввода чата (признак авторизованной сессии)
                input_elem = await self.page.query_selector(
                    'rich-textarea, div.ql-editor[contenteditable="true"], div[contenteditable="true"], textarea'
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

        # 1. Navigate if needed
        if "gemini.google.com" not in self.page.url:
            await self.page.goto(self.URL, wait_until="domcontentloaded", timeout=20000)
            await asyncio.sleep(1)

        # 1.5 Fail-fast check and popup dismissal
        await self.check_fast_auth_or_captcha("Gemini")
        await self.dismiss_popups()

        # 2. Locate input area
        input_selectors = [
            'div.ql-editor[contenteditable="true"]',
            'rich-textarea div[contenteditable="true"]',
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
            # Re-check if auth was invalidated before declaring input not found
            await self.check_fast_auth_or_captcha("Gemini")
            raise RuntimeError("Could not locate Gemini input area. Please ensure you are logged in.")

        # 3. Enter prompt
        try:
            await input_elem.click(force=True, timeout=2000)
        except Exception:
            pass
        # Fast insertion via evaluate to support multiline code snippets
        await self.page.evaluate(
            '''([el, text]) => {
                el.focus();
                el.innerText = text;
                el.dispatchEvent(new Event('input', { bubbles: true }));
            }''',
            [input_elem, prompt]
        )
        await asyncio.sleep(0.3)

        # 4. Click Send or press Enter
        send_selectors = [
            'button[aria-label*="Send"]',
            'button[aria-label*="Отправить"]',
            'button.send-button',
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
            # Fallback to Enter key
            await self.page.keyboard.press("Enter")

        # 5. Smart response waiting with TTFT cut-off and stability detection
        async def is_generating_fn() -> bool:
            stop_btn = await self.page.query_selector(
                'button[aria-label*="Stop"], button[aria-label*="Остановить"], button[aria-label*="stop"], button[data-testid*="stop"]'
            )
            loading = await self.page.query_selector('mat-progress-bar, .loading, [aria-label*="Loading"]')
            return bool(stop_btn or loading)

        response_text = await self.wait_for_smart_response(
            extract_fn=self._extract_last_response,
            is_generating_fn=is_generating_fn,
            ttft_timeout=25.0,
            max_generation_time=120.0,
            stability_window=3.5
        )
        return response_text or "[zero-copy-agent] Ответ от Gemini получен, но не удалось извлечь DOM разметку."

    async def reset_to_new_chat(self):
        """Resets Gemini tab to a fresh conversation to keep context warm."""
        if not self.page or self.page.is_closed():
            return
        try:
            new_chat_btn = await self.page.query_selector(
                'button[aria-label*="New chat"], button[aria-label*="Новый чат"], a[aria-label*="New chat"], a[aria-label*="Новый чат"]'
            )
            if new_chat_btn:
                await new_chat_btn.click()
                await asyncio.sleep(0.5)
            else:
                if "gemini.google.com/app" not in self.page.url:
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
            '.model-response-text',
            'message-content',
            '.response-container-content',
            'div.markdown'
        ]
        for sel in response_selectors:
            elems = await self.page.query_selector_all(sel)
            if elems:
                text = await elems[-1].inner_text()
                if text and text.strip():
                    return text.strip()
        return ""
