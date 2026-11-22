"""
Web Driver for OpenAI ChatGPT Plus (https://chatgpt.com).
"""
import asyncio
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
            await asyncio.sleep(2)

        # Закрываем всплывающие оверлеи/cookie-модалки, если они перекрывают экран
        try:
            await self.page.evaluate('''() => {
                const buttons = Array.from(document.querySelectorAll('button, a, div[role="button"]'));
                for (const b of buttons) {
                    const txt = (b.innerText || '').toLowerCase().trim();
                    if (txt === 'accept all' || txt === 'принять все' || txt === 'accept cookies' || 
                        txt === 'i agree' || txt === 'stay logged out' || txt === 'close' || 
                        txt === 'dismiss' || txt === 'not now' || txt === 'позже' || txt === 'закрыть') {
                        b.click();
                    }
                }
                const closeBtns = document.querySelectorAll('button[aria-label="Close"], button[aria-label="Закрыть"], button.close, [data-testid="close-button"]');
                for (const cb of closeBtns) {
                    cb.click();
                }
            }''')
        except Exception:
            pass

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

        # Wait for generation to finish
        await asyncio.sleep(2)
        max_wait = 60
        waited = 0
        last_text = ""
        while waited < max_wait:
            await asyncio.sleep(1)
            waited += 1

            stop_btn = await self.page.query_selector(
                'button[data-testid="stop-button"], button[aria-label*="Stop"]'
            )
            if not stop_btn and waited > 3:
                current_text = await self._extract_last_response()
                if current_text and current_text == last_text:
                    return current_text
                last_text = current_text

        return last_text or "[zero-copy-agent] Ответ от ChatGPT получен, но не удалось извлечь DOM разметку."

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
