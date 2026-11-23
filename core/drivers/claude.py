"""
Web Driver for Anthropic Claude (https://claude.ai).
"""
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Solyanni4 (https://github.com/solyanni4/zero-copy-agent)

import asyncio
import time
import logging
from typing import AsyncGenerator
from core.drivers.base import BaseDriver

logger = logging.getLogger("zcao.drivers.claude")


class ClaudeDriver(BaseDriver):
    URL = "https://claude.ai"

    async def is_authenticated(self) -> bool:
        """Checks if active browser page is authenticated on Claude.ai."""
        if not self.page:
            return False
        try:
            if "claude.ai" not in self.page.url:
                await self.page.goto(self.URL, wait_until="domcontentloaded", timeout=20000)
                await asyncio.sleep(1)

            # Ожидаем рендер контента страницы (если страница пока пустая/белая)
            start_render_wait = time.time()
            while time.time() - start_render_wait < 5.0:
                try:
                    eval_fn = self.page.evaluate("() => (document.body ? document.body.innerText : '').trim()")
                    body_text = (await eval_fn) if asyncio.iscoroutine(eval_fn) else str(eval_fn or "")
                    if body_text:
                        break
                except Exception:
                    break
                await asyncio.sleep(0.5)

            # Проверка на Cloudflare Turnstile / капчу / редирект
            await self.check_fast_auth_or_captcha("Claude")

            for _ in range(8):
                await self.check_fast_auth_or_captcha("Claude")

                # 1. Проверяем явные кнопки или формы входа
                login_indicator = await self.page.query_selector(
                    'a[href*="/login"], button:has-text("Log In"), button:has-text("Sign In"), '
                    'button:has-text("Войти"), input[type="email"], button:has-text("Continue with Google")'
                )
                if login_indicator:
                    # Дополнительно проверяем, нет ли уже активного поля ввода чата
                    chat_input = await self.page.query_selector(
                        'div.ProseMirror[contenteditable="true"], fieldset div[contenteditable="true"], textarea'
                    )
                    if not chat_input:
                        return False

                # 2. Проверяем наличие поля ввода сообщения
                input_elem = await self.page.query_selector(
                    'div.ProseMirror[contenteditable="true"], fieldset div[contenteditable="true"], '
                    'div[contenteditable="true"], textarea'
                )
                if input_elem:
                    return True

                await asyncio.sleep(1)

            return False
        except Exception as e:
            logger.debug(f"[ClaudeDriver] is_authenticated check failed: {e}")
            return False

    async def send_prompt(self, prompt: str) -> str:
        """Sends prompt to Claude web UI and waits for smart complete response."""
        if not self.page:
            raise RuntimeError("Browser page not initialized")

        if "claude.ai" not in self.page.url:
            await self.page.goto(self.URL, wait_until="domcontentloaded", timeout=20000)
            await asyncio.sleep(1)

        # Ожидаем рендер контента страницы (если страница пока пустая/белая)
        start_render_wait = time.time()
        while time.time() - start_render_wait < 5.0:
            body_text = await self.page.evaluate("() => (document.body ? document.body.innerText : '').trim()")
            if body_text:
                break
            await asyncio.sleep(0.5)

        # Fail-fast check and popup dismissal
        await self.check_fast_auth_or_captcha("Claude")
        await self.dismiss_popups()

        input_selectors = [
            'div.ProseMirror[contenteditable="true"]',
            'fieldset div[contenteditable="true"]',
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
            await self.check_fast_auth_or_captcha("Claude")
            raise RuntimeError("Could not locate Claude prompt area. Please ensure you are logged in to claude.ai.")

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
            'button[aria-label*="Отправить"]',
            'button[data-testid="send-button"]',
            'button:has(svg)'
        ]
        clicked = False
        for btn_sel in send_selectors:
            btn = await self.page.query_selector(btn_sel)
            if btn:
                try:
                    if await btn.is_enabled():
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
                'button[aria-label*="Stop"], button:has-text("Stop response"), button:has-text("Stop"), button[data-testid*="stop"]'
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
            return response_text or "[zero-copy-agent] Ответ от Claude получен, но не удалось извлечь DOM разметку."
        except Exception:
            await self.save_failure_screenshot("Claude")
            raise

    async def reset_to_new_chat(self):
        """Resets Claude tab to a fresh conversation to avoid context accumulation."""
        if not self.page or getattr(self.page, "is_closed", lambda: True)():
            return
        try:
            new_btn = await self.page.query_selector(
                'a[href="/new"], button:has-text("Start new chat"), button[aria-label*="Start new chat"], button[aria-label*="New chat"]'
            )
            if new_btn:
                await new_btn.click()
                await asyncio.sleep(0.5)
            else:
                if "/chat/" in self.page.url or "claude.ai" not in self.page.url:
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
            'div[data-is-streaming]',
            'div.font-claude-message',
            'div.standard-markdown',
            'div[data-testid*="chat-message"]',
            'div[class*="font-claude"]'
        ]
        for sel in response_selectors:
            elems = await self.page.query_selector_all(sel)
            if elems:
                text = await elems[-1].inner_text()
                if text and text.strip():
                    return text.strip()
        return ""
