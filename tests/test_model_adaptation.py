import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from core.registry import classify_model, format_model_with_badge, get_best_replacement_model
from core.providers import clean_and_filter_model_id, discover_remote_models
from core.orchestrator import HybridOrchestrator


def test_classify_model_power_badges():
    """Verify classification and human-friendly badge assignment."""
    strong_models = ["gemini-3.8-pro", "deepseek-reasoner", "claude-3-opus", "o1", "o3-mini", "gemini-4-neon", "qwen-max"]
    for m in strong_models:
        c = classify_model(m)
        assert "Сильная" in c["badge"], f"Expected 'Сильная' in badge for {m}, got {c['badge']}"

    fast_models = ["gemini-2.5-flash", "claude-3-7-sonnet", "gpt-4o-mini", "haiku-3.5", "turbo-2024"]
    for m in fast_models:
        c = classify_model(m)
        assert "Быстрая" in c["badge"], f"Expected 'Быстрая' in badge for {m}, got {c['badge']}"

    base_models = ["gemini-lite", "llama-3b", "qwen-7b", "small-model"]
    for m in base_models:
        c = classify_model(m)
        assert "Базовая" in c["badge"], f"Expected 'Базовая' in badge for {m}, got {c['badge']}"

    # Local Ollama models
    c_local_large = classify_model("qwen2.5-coder:14b", provider="ollama")
    assert "Локальная GPU" in c_local_large["badge"]
    assert "Сильная" in c_local_large["badge"]

    c_local_small = classify_model("llama3.2:3b", provider="ollama")
    assert "Локальная GPU" in c_local_small["badge"]


def test_format_model_with_badge():
    """Verify model display string contains appropriate badge."""
    res = format_model_with_badge("gemini-4-neon")
    assert "[" in res and "]" in res
    assert "Сильная" in res


def test_get_best_replacement_model():
    """Verify tier-based auto-replacement logic."""
    available = ["gemini-2.5-flash", "qwen-7b", "deepseek-reasoner", "ollama/qwen2.5-coder:14b"]

    # Architect prefers strong flagship models
    best_arch = get_best_replacement_model("gemini-4-neon", "architect", available)
    assert best_arch == "deepseek-reasoner"

    # Executor prefers local GPU or fast coder models
    best_exec = get_best_replacement_model("qwen2.5-coder:7b", "executor", available)
    assert best_exec == "ollama/qwen2.5-coder:14b"


def test_remote_model_discovery_filtering():
    """Verify discover_remote_models strips models/ prefix and excludes non-text models."""
    assert clean_and_filter_model_id("models/gemini-2.5-pro") == "gemini-2.5-pro"
    assert clean_and_filter_model_id("models/text-embedding-004") is None
    assert clean_and_filter_model_id("imagen-3.0-generate-002") is None
    assert clean_and_filter_model_id("models/tts-1") is None
    assert clean_and_filter_model_id("models/gemini-live-2.0") is None
    assert clean_and_filter_model_id("deepseek-chat") == "deepseek-chat"


@pytest.mark.asyncio
async def test_query_stream_429_failover_to_ollama():
    """Verify query_stream automatically fails over to local Ollama when API returns 429 quota exhaustion."""
    orch = HybridOrchestrator(browser_engine=MagicMock())
    orch.browser_engine.is_ready = False

    # Mock API provider stream throwing quota exhaustion error
    async def mock_api_stream(*args, **kwargs):
        raise RuntimeError("HTTP 429: Resource has been exhausted (rate limit)")
        yield ""

    orch._query_api_provider_stream = mock_api_stream

    # Mock local Ollama
    orch.ollama = MagicMock()
    orch.ollama.list_models = AsyncMock(return_value=["qwen2.5-coder:14b"])
    async def mock_ollama_stream(prompt, model):
        yield "Local "
        yield "GPU "
        yield "Code"
    orch.ollama.send_prompt_stream = mock_ollama_stream

    with patch("core.orchestrator.mesh.get_active_key", return_value="sk-exhausted"):
        tokens = []
        async for tok in orch.query_stream("Test code prompt", "gemini-2.5-flash", inject_signature=False):
            tokens.append(tok)

    result_text = "".join(tokens)
    assert "Local GPU Code" in result_text
