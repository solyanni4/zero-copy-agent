"""
Mock / Standby Driver for zero-copy-agent.
Provides instant, guaranteed responses for out-of-the-box IDE testing.
"""
import asyncio
from typing import AsyncGenerator
from core.drivers.base import BaseDriver

class MockDriver(BaseDriver):
    def __init__(self, page=None):
        super().__init__(page)

    async def is_authenticated(self) -> bool:
        return True

    async def send_prompt(self, prompt: str) -> str:
        await asyncio.sleep(0.3)
        return self._generate_response(prompt)

    async def send_prompt_stream(self, prompt: str) -> AsyncGenerator[str, None]:
        full_text = self._generate_response(prompt)
        chunks = full_text.split(" ")
        for i, word in enumerate(chunks):
            yield word + (" " if i < len(chunks) - 1 else "")
            await asyncio.sleep(0.015)

    def _generate_response(self, prompt: str) -> str:
        prompt_preview = prompt[:180] + ("..." if len(prompt) > 180 else "")
        return (
            f"**IDE связь установлена успешно!** Запрос получен и обработан через локальный OpenAI-совместимый шлюз.\n\n"
            f"- **Эндпоинт:** `http://127.0.0.1:8000/v1`\n"
            f"- **Длина промпта:** {len(prompt)} символов\n"
            f"- **Режим:** Standby / Demo (готов к подключению веб-сессии)\n\n"
            f"> **Входящий запрос:**\n"
            f"> `{prompt_preview}`\n\n"
            f"---\n"
            f"### Как переключить на живую подписку (Gemini Advanced / ChatGPT Plus):\n"
            f"В терминале выполните разовую авторизацию:\n"
            f"```bash\n"
            f"python -m core.auth --provider gemini\n"
            f"```\n"
            f"После авторизации все запросы из Cursor / Aider пойдут напрямую в веб-интерфейс без оплаты API-токенов!"
        )
