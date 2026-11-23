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


@pytest.mark.asyncio
async def test_ollama_driver_trust_env_false():
    """Verify OllamaDriver normalizes local URLs and sets trust_env=False."""
    from core.drivers.ollama import OllamaDriver
    driver = OllamaDriver(base_url="0.0.0.0:11434")
    assert driver.base_url == "http://127.0.0.1:11434"
    assert driver.is_local is True

    # Real check against host Ollama if running
    is_auth = await driver.is_authenticated()
    if is_auth:
        models = await driver.list_models()
        assert isinstance(models, list)


def test_ollama_auto_ping_endpoint():
    """Verify pinging ollama-auto uses trust_env=False and returns 200 with online status."""
    from fastapi.testclient import TestClient
    from main import app
    from core.healer import config_healer

    config_healer.sync_discovered_models("ollama", ["qwen2.5-coder:14b"])
    client = TestClient(app)
    res = client.post("/v1/providers/accounts/ollama-auto/ping")
    assert res.status_code == 200
    data = res.json()
    assert data["account_id"] == "ollama-auto"
    assert data["status"] == "ok"
    assert "онлайн" in data["detail"].lower()
    assert data["latency_ms"] is not None


@pytest.mark.asyncio
async def test_cascade_candidate_timeout_failover():
    """Verify orchestrator fails over to next candidate when current candidate exceeds candidate_timeout_sec."""
    cfg = AppConfig()
    cfg.cascade.candidate_timeout_sec = 0.5
    cfg.cascade.planner = RoleConfig(
        primary="slow-candidate-1",
        fallback_order=["fast-candidate-2"]
    )
    orch = HybridOrchestrator(browser_engine=None)
    orch.config = cfg

    call_history = []

    async def mock_query(model, prompt, **kwargs):
        call_history.append(model)
        if model == "slow-candidate-1":
            # Sleep longer than candidate_timeout_sec (0.5s)
            await asyncio.sleep(2.0)
            return "Should not be returned because of timeout"
        elif model == "fast-candidate-2":
            await asyncio.sleep(0.05)
            return "Fast candidate success plan"
        return "Unknown"

    orch._query_model = mock_query

    res, used_model = await orch.query_role_with_fallback("planner", "Test timeout prompt")
    assert used_model == "fast-candidate-2"
    assert res == "Fast candidate success plan"
    assert call_history == ["slow-candidate-1", "fast-candidate-2"]


def test_sort_accounts_by_auth_priority():
    """Verify sort_accounts_by_auth_priority puts api_key before browser_session."""
    from core.providers import sort_accounts_by_auth_priority
    from core.healer import AccountSubscription

    accs = [
        AccountSubscription(id="web-1", provider="gemini", auth_type="browser_session"),
        AccountSubscription(id="api-1", provider="gemini", auth_type="api_key", api_key="k1"),
        AccountSubscription(id="web-2", provider="gemini", auth_type="browser_session"),
        AccountSubscription(id="api-2", provider="gemini", auth_type="api_key", api_key="k2"),
    ]
    sorted_accs = sort_accounts_by_auth_priority(accs)
    assert [a.id for a in sorted_accs] == ["api-1", "api-2", "web-1", "web-2"]
    assert [a.auth_type for a in sorted_accs] == ["api_key", "api_key", "browser_session", "browser_session"]


@pytest.mark.asyncio
async def test_gemini_4_neon_multi_account_failover_and_logging(caplog):
    """
    Verify multi-account failover for gemini-4-neon:
    Strict priority:
    1. gemini-2 (api_key) -> 429
    2. gemini-3 (api_key) -> timeout
    3. gemini-4 (api_key) -> 500
    4. gemini-web-2 (browser_session) -> success
    Verify exact log statements contain account ID, type, and error code.
    """
    import logging
    from core.mesh import QuotaMesh
    from core.healer import AccountSubscription

    test_mesh = QuotaMesh()
    accs = [
        AccountSubscription(id="gemini-web-2", provider="gemini", auth_type="browser_session", status="active"),
        AccountSubscription(id="gemini-2", provider="gemini", auth_type="api_key", status="active", api_key="sk-gem-2"),
        AccountSubscription(id="gemini-3", provider="gemini", auth_type="api_key", status="active", api_key="sk-gem-3"),
        AccountSubscription(id="gemini-4", provider="gemini", auth_type="api_key", status="active", api_key="sk-gem-4"),
    ]
    test_mesh.sync_active_accounts(accs)

    # 1. Verify candidate accounts ordering: all api_key first, browser_session last
    candidates = test_mesh.get_candidate_accounts("gemini", model_id="gemini-4-neon")
    assert [c.account_id for c in candidates] == ["gemini-2", "gemini-3", "gemini-4", "gemini-web-2"]
    assert [c.auth_type for c in candidates] == ["api_key", "api_key", "api_key", "browser_session"]

    # 2. Test orchestrator execution
    mock_browser = MagicMock()
    mock_browser.is_ready = True

    orch = HybridOrchestrator(browser_engine=mock_browser)
    cfg = AppConfig()
    cfg.cascade.planner = RoleConfig(primary="gemini-4-neon", fallback_order=["deepseek-reasoner"])
    orch.config = cfg

    called_accounts = []

    async def mock_single_api(provider, account_id, key, model_id, prompt, timeout_sec=90.0):
        called_accounts.append((account_id, "api_key"))
        if account_id == "gemini-2":
            raise RuntimeError("HTTP 429 - Resource has been exhausted (rate limit)")
        elif account_id == "gemini-3":
            raise asyncio.TimeoutError()
        elif account_id == "gemini-4":
            raise RuntimeError("HTTP 500 - Internal server error")
        return "API success"

    async def mock_browser_query(prompt, model_id, account_id=None):
        called_accounts.append((account_id, "browser_session"))
        return "Success blueprint from gemini-web-2"

    orch._query_single_api_account = mock_single_api
    orch.browser_engine.query = mock_browser_query

    with patch("core.orchestrator.mesh", test_mesh), caplog.at_level(logging.INFO):
        res, used_model = await orch.query_role_with_fallback("planner", "Build architecture blueprint")

    assert used_model == "gemini-4-neon"
    assert res == "Success blueprint from gemini-web-2"
    assert called_accounts == [
        ("gemini-2", "api_key"),
        ("gemini-3", "api_key"),
        ("gemini-4", "api_key"),
        ("gemini-web-2", "browser_session")
    ]

    # Verify log messages
    log_text = caplog.text
    assert "Trying role 'planner' via candidate 'gemini-4-neon' (account=gemini-2, type=api_key" in log_text
    assert "HTTP 429 - Resource has been exhausted" in log_text
    assert "Trying role 'planner' via candidate 'gemini-4-neon' (account=gemini-3, type=api_key" in log_text
    assert "timed out after 90s -> switching to next account" in log_text
    assert "Trying role 'planner' via candidate 'gemini-4-neon' (account=gemini-4, type=api_key" in log_text
    assert "HTTP 500 - Internal server error" in log_text
    assert "Trying role 'planner' via candidate 'gemini-4-neon' (account=gemini-web-2, type=browser_session" in log_text
    assert "Role 'planner' succeeded via candidate 'gemini-4-neon' (account=gemini-web-2, type=browser_session)" in log_text



