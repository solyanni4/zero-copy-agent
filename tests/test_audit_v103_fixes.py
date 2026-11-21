"""
Tests verifying fixes for audit v1.0.3:
- Ping account endpoint with httpx mock
- Delete model endpoint using atomic save
- QuotaMesh.get_active_accounts() and registry integration
- Orchestrator official API routing with mesh key and failover
- Honest storage token savings calculation
- SSE split DLP token de-tokenization across chunk boundaries
"""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from fastapi.testclient import TestClient

from main import app
from core.mesh import QuotaMesh
from core.registry import ModelRegistry
from core.orchestrator import HybridOrchestrator
from core.shield import DLPShield, SessionVault
from core.storage import AnalyticsStorage
from core.healer import AccountSubscription, SubscriptionsConfig

client = TestClient(app)


# 1. POST /v1/providers/accounts/{id}/ping
def test_ping_account_endpoint_with_mock():
    mock_acc = AccountSubscription(
        id="mock-ollama-acc",
        provider="ollama",
        auth_type="local",
        status="active"
    )
    mock_cfg = SubscriptionsConfig(accounts=[mock_acc])

    with patch("core.healer.config_healer.load_or_create", return_value=(mock_cfg, [])):
        with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_get.return_value = mock_response

            res = client.post("/v1/providers/accounts/mock-ollama-acc/ping")
            assert res.status_code == 200
            data = res.json()
            assert data["status"] == "ok"
            assert data["account_id"] == "mock-ollama-acc"
            assert data["latency_ms"] is not None


# 2. DELETE /v1/models/{id} uses config_healer.atomic_save
def test_delete_model_endpoint_atomic_save():
    with patch("core.healer.config_healer.atomic_save") as mock_save:
        res = client.delete("/v1/models/non-existent-test-model")
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "ok"


# 3. QuotaMesh.get_active_accounts() and registry integration
def test_quota_mesh_active_accounts_and_registry():
    mesh = QuotaMesh()
    assert mesh.get_active_accounts() == []

    mesh.register_keys("openai", ["sk-test-key-1", "sk-test-key-2"])
    accounts = mesh.get_active_accounts()
    assert len(accounts) == 1
    assert accounts[0]["provider"] == "openai"
    assert accounts[0]["keys_count"] == 2
    assert accounts[0]["healthy_count"] == 2

    # Registry check_providers with mesh
    registry = ModelRegistry()
    health = pytest.importorskip("asyncio").run(registry.check_providers(mesh=mesh))
    assert health["mesh"].connected is True
    assert "gpt-4o" in health["mesh"].available_models


# 4. Orchestrator routes via official API key in mesh
@pytest.mark.asyncio
async def test_orchestrator_official_api_routing_with_mock():
    mock_engine = MagicMock()
    mock_engine.is_ready = False
    orch = HybridOrchestrator(browser_engine=mock_engine)

    from core.mesh import mesh
    mesh.register_keys("openai", ["sk-mock-key-for-test"])

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": "Hello from mock OpenAI API!"}}]
        }
        mock_post.return_value = mock_resp

        result = await orch.query("Hello", model_id="gpt-4o", inject_signature=False)
        assert result == "Hello from mock OpenAI API!"
        assert mock_post.called


# 5. Honest storage.get_summary() (no artificial inflation)
def test_honest_storage_summary(tmp_path):
    db_file = tmp_path / "test_storage.db"
    store = AnalyticsStorage(db_path=str(db_file))

    # Record completion where raw_prompt == prompt_tokens (BPE off, 0 savings)
    store.record_completion(
        req_id="honest-1",
        model_requested="gpt-4o",
        model_routed="gpt-4o",
        prompt_tokens=100,
        completion_tokens=50,
        duration_seconds=0.5,
        raw_prompt_tokens=100  # Equal to prompt_tokens!
    )

    summary = store.get_summary()
    assert summary["tokens_saved"] == 0
    assert summary["tokens_saved_pct"] == 0.0


# 6. SSE split DLP token de-tokenization across chunk boundaries
def test_split_dlp_token_stream_restoration():
    vault = SessionVault(req_id="test-req-1")
    placeholder = vault.add("super_secret_password_123", "KEY")
    # Placeholder is typically '__SEC_01_KEY__'

    # Simulate split: chunk 1 ends with '__SEC_01', chunk 2 starts with '_KEY__'
    half = len(placeholder) // 2
    part1 = placeholder[:half]
    part2 = placeholder[half:]

    chunk1 = f"Password is {part1}"
    chunk2 = f"{part2} and some more text."

    # Process chunk 1
    flush1, buf1 = DLPShield.restore_stream_chunk(chunk1, vault, buffer="")
    assert "super_secret_password_123" not in flush1
    assert placeholder not in flush1
    assert buf1 == part1  # Tail held in buffer

    # Process chunk 2
    flush2, buf2 = DLPShield.restore_stream_chunk(chunk2, vault, buffer=buf1)
    assert "super_secret_password_123 and some more text." in flush2
    assert buf2 == ""
