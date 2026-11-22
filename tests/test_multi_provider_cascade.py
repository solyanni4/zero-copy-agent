import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from core.drivers import DeepSeekDriver, QwenDriver, GrokDriver
from core.providers import get_preset, list_presets, discover_remote_models
from core.healer import AccountSubscription, ConfigHealer
from core.config import RoleConfig, CascadeConfig, AppConfig
from core.orchestrator import HybridOrchestrator


def test_driver_instantiation():
    """Verify that new web drivers can be instantiated and have expected methods."""
    mock_page = MagicMock()
    ds = DeepSeekDriver(mock_page)
    qw = QwenDriver(mock_page)
    gr = GrokDriver(mock_page)

    assert hasattr(ds, "is_authenticated")
    assert hasattr(ds, "send_prompt")
    assert hasattr(ds, "send_prompt_stream")
    assert hasattr(qw, "is_authenticated")
    assert hasattr(qw, "send_prompt")
    assert hasattr(gr, "is_authenticated")
    assert hasattr(gr, "send_prompt")


def test_provider_presets():
    """Verify provider presets catalog and retrieval."""
    presets = list_presets()
    preset_ids = [p["id"] for p in presets]
    assert "deepseek" in preset_ids
    assert "qwen" in preset_ids
    assert "grok" in preset_ids
    assert "openrouter" in preset_ids
    assert "groq" in preset_ids
    assert "custom" in preset_ids

    ds_preset = get_preset("deepseek")
    assert ds_preset is not None
    assert "https://api.deepseek.com/v1" in ds_preset["base_url"]
    assert "deepseek-reasoner" in ds_preset["default_models"]

    qwen_preset = get_preset("qwen")
    assert "dashscope" in qwen_preset["base_url"]

    grok_preset = get_preset("grok")
    assert "api.x.ai" in grok_preset["base_url"]


@pytest.mark.asyncio
async def test_discover_remote_models():
    """Verify discover_remote_models parses OpenAI-compatible model lists."""
    mock_client = AsyncMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "data": [
            {"id": "deepseek-chat"},
            {"id": "deepseek-reasoner"}
        ]
    }
    mock_client.get.return_value = mock_resp

    with patch("httpx.AsyncClient") as mock_ac:
        mock_ac.return_value.__aenter__.return_value = mock_client
        models = await discover_remote_models("https://api.deepseek.com/v1", "sk-test")
        assert "deepseek-chat" in models
        assert "deepseek-reasoner" in models


def test_healer_base_url_support(tmp_path):
    """Verify healer preserves and serializes base_url."""
    cfg_file = tmp_path / "subscriptions.yaml"
    healer = ConfigHealer(target_path=cfg_file)

    healer.add_account_key(
        "deepseek",
        "sk-test-secret-key-12345",
        alias="ds-account-1",
        base_url="https://api.deepseek.com/v1",
        models=["deepseek-chat", "deepseek-reasoner"]
    )

    cfg, _ = healer.load_or_create()
    saved = next((a for a in cfg.accounts if a.alias == "ds-account-1"), None)
    assert saved is not None
    assert saved.base_url == "https://api.deepseek.com/v1"
    saved_model_ids = [m.id if hasattr(m, "id") else m for m in saved.models]
    assert "deepseek-reasoner" in saved_model_ids

    masked = healer.list_accounts_masked()
    masked_acc = next((a for a in masked if a.get("alias") == "ds-account-1"), None)
    assert masked_acc is not None
    assert masked_acc["base_url"] == "https://api.deepseek.com/v1"


def test_cascade_role_config():
    """Verify RoleConfig and CascadeConfig role mappings."""
    planner = RoleConfig(primary="deepseek-reasoner", fallback_order=["gemini-4-neon", "gpt-4o"])
    coder = RoleConfig(primary="qwen2.5-coder:14b", fallback_order=["deepseek-chat"])
    reviewer = RoleConfig(primary="gemini-4-neon", fallback_order=["claude-3-7-sonnet"])

    cascade = CascadeConfig(
        planner=planner,
        coder=coder,
        reviewer=reviewer,
        fallback_order=["deepseek", "qwen", "gemini"]
    )

    assert cascade.planner.primary == "deepseek-reasoner"
    assert cascade.planner.fallback_order == ["gemini-4-neon", "gpt-4o"]
    assert cascade.coder.primary == "qwen2.5-coder:14b"
    assert cascade.reviewer.primary == "gemini-4-neon"
    assert cascade.fallback_order == ["deepseek", "qwen", "gemini"]


@pytest.mark.asyncio
async def test_query_role_with_fallback_success():
    """Verify query_role_with_fallback fails over when first candidate fails."""
    cfg = AppConfig()
    cfg.cascade.planner = RoleConfig(
        primary="model-failing-1",
        fallback_order=["model-failing-2", "model-working-3"]
    )
    orch = HybridOrchestrator(browser_engine=None)
    orch.config = cfg

    call_history = []

    async def mock_query(model, prompt, **kwargs):
        call_history.append(model)
        if model in ("model-failing-1", "model-failing-2"):
            raise RuntimeError(f"Simulated 429 quota exhaustion on {model}")
        return {
            "text": f"Success output from {model}",
            "model": model,
            "latency_ms": 120
        }

    orch._query_model = mock_query

    res, used_model = await orch.query_role_with_fallback("planner", "Test architecture prompt")
    assert res is not None
    assert used_model == "model-working-3"
    assert res["model"] == "model-working-3"
    assert "Success output" in res["text"]
    assert call_history == ["model-failing-1", "model-failing-2", "model-working-3"]


@pytest.mark.asyncio
async def test_api_endpoints_presets_and_orchestration():
    """Verify FastAPI routes for presets and orchestration role settings."""
    from fastapi.testclient import TestClient
    from main import app

    client = TestClient(app)

    # 1. Presets endpoint
    resp = client.get("/v1/providers/presets")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    preset_ids = [p["id"] for p in data["presets"]]
    assert "deepseek" in preset_ids
    assert "grok" in preset_ids

    # 2. Orchestration config endpoint
    get_orch = client.get("/v1/orchestration/config")
    assert get_orch.status_code == 200
    orch_data = get_orch.json()
    assert "config" in orch_data
    assert "planner" in orch_data["config"]

    # 3. Post updated cascade settings
    post_orch = client.post("/v1/orchestration/config", json={
        "architect_model": "deepseek-reasoner",
        "coder_model": "deepseek-chat",
        "auditor_model": "grok-3",
        "planner": {"primary": "deepseek-reasoner", "fallback_order": ["gemini-4-neon"]},
        "coder": {"primary": "deepseek-chat", "fallback_order": ["qwen-coder"]},
        "reviewer": {"primary": "grok-3", "fallback_order": ["claude-3-7-sonnet"]},
        "fallback_order": ["deepseek", "grok", "gemini"]
    })
    assert post_orch.status_code == 200
    post_data = post_orch.json()
    assert post_data["status"] == "ok"
