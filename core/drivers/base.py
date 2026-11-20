"""
Base WebDriver interface for zero-copy-agent.
"""
from abc import ABC, abstractmethod
from typing import AsyncGenerator

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
