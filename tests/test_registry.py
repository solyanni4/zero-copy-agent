import pytest
from core.registry import ModelRegistry

class MockOllamaDriver:
    def __init__(self, connected: bool = True, models=None):
        self._connected = connected
        self._models = models or ["qwen2.5-coder:14b", "nomic-embed-text:latest"]

    async def is_authenticated(self):
        return self._connected

    async def list_models(self):
        return self._models

class MockBrowserEngine:
    def __init__(self, ready: bool = True, provider: str = "gemini"):
        self.is_ready = ready
        self.active_provider = provider

@pytest.mark.asyncio
async def test_registry_filters_only_connected():
    registry = ModelRegistry(ttl_seconds=0.1)

    # 1. When Ollama is offline and browser is ready
    ollama_offline = MockOllamaDriver(connected=False)
    browser_ready = MockBrowserEngine(ready=True, provider="gemini")

    models = await registry.get_models(
        include_inactive=False,
        ollama_driver=ollama_offline,
        browser_engine=browser_ready,
        force_refresh=True
    )
    ids = [m.id for m in models]

    assert "gemini-advanced" in ids
    assert "mock-test" in ids
    # Ollama model should NOT be in active list
    assert "qwen2.5-coder:14b" not in ids

@pytest.mark.asyncio
async def test_registry_includes_inactive_when_requested():
    registry = ModelRegistry(ttl_seconds=0.1)

    ollama_offline = MockOllamaDriver(connected=False)
    browser_ready = MockBrowserEngine(ready=True, provider="gemini")

    all_models = await registry.get_models(
        include_inactive=True,
        ollama_driver=ollama_offline,
        browser_engine=browser_ready,
        force_refresh=True
    )
    all_ids = [m.id for m in all_models]

    assert "gemini-advanced" in all_ids
    assert "gpt-4o" in all_ids  # Present in full catalog even if inactive

@pytest.mark.asyncio
async def test_registry_dynamic_aliases_connected():
    registry = ModelRegistry(ttl_seconds=0.1)

    ollama_online = MockOllamaDriver(connected=True, models=["qwen2.5-coder:14b"])
    browser_ready = MockBrowserEngine(ready=True, provider="gemini")
    aliases = {
        "tier:local-fast": "qwen2.5-coder:14b",
        "tier:reasoning-top": "gemini-4-neon",
        "cascade:architect": "cascade:architect"
    }

    models = await registry.get_models(
        include_inactive=False,
        ollama_driver=ollama_online,
        browser_engine=browser_ready,
        aliases=aliases,
        force_refresh=True
    )
    ids = [m.id for m in models]

    assert "tier:local-fast" in ids
    assert "tier:reasoning-top" in ids
    assert "cascade:architect" in ids
    assert "qwen2.5-coder:14b" in ids
