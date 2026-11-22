"""
Web Driver for Google Gemini Advanced (https://gemini.google.com/app).
"""
import asyncio
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

            # Ожидаем загрузку SPA в цикле до 8 секунд с шагом 1 сек
            for _ in range(8):
                # Проверяем редирект на форму авторизации Google
                if "accounts.google.com" in self.page.url:
                    return False

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
            await asyncio.sleep(2)

        # 1.5 Auto-dismiss consent and overlay banners if present
        try:
            await self.page.evaluate('''() => {
                const buttons = Array.from(document.querySelectorAll('button, a'));
                for (const b of buttons) {
                    const txt = (b.innerText || '').toLowerCase().trim();
                    if (txt === 'accept all' || txt === 'принять все' || txt === 'i agree' || txt === 'got it' || txt === 'понятно' || txt === 'dismiss' || txt === 'close') {
                        b.click();
                    }
                }
            }''')
        except Exception:
            pass

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

        # 5. Wait for generation
        await asyncio.sleep(2)
        # Wait until generation finishes (stop button disappears or text stabilizes)
        max_wait = 60
        waited = 0
        last_text = ""
        while waited < max_wait:
            await asyncio.sleep(1)
            waited += 1

            # Check if stop button is present
            stop_btn = await self.page.query_selector(
                'button[aria-label*="Stop"], button[aria-label*="Остановить"]'
            )
            if not stop_btn and waited > 3:
                # Text stabilization check
                current_text = await self._extract_last_response()
                if current_text and current_text == last_text:
                    return current_text
                last_text = current_text

        return last_text or "[zero-copy-agent] Ответ от Gemini получен, но не удалось извлечь DOM разметку."

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
