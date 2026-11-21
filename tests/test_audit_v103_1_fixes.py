"""
Tests verifying fixes for audit v1.0.3.1:
1. check_github_updates() handles unreachable GitHub cleanly (no NameError, valid download_url).
2. Key loading into mesh on startup, and removal from mesh._pools on DELETE and toggle.
3. Execution of cascade:architect via API key in mesh (httpx mocked, browser_engine is_ready=False).
4. Policy flags filtering in /v1/ui/dlp/test (mask_keys, mask_ips, mask_hosts).
"""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from fastapi.testclient import TestClient

from main import app, sync_mesh_from_subscriptions
from core.mesh import mesh
from core.updater import check_github_updates, GITHUB_REPO
from core.orchestrator import HybridOrchestrator
from core.healer import AccountSubscription, SubscriptionsConfig, config_healer

client = TestClient(app)


# 1. check_github_updates() with unreachable GitHub
def test_updater_unreachable_github_no_name_error():
    with patch("urllib.request.build_opener") as mock_opener:
        mock_instance = MagicMock()
        mock_instance.open.side_effect = Exception("Connection refused")
        mock_opener.return_value = mock_instance

        res = check_github_updates(current_version="1.0.6")
        assert res["status"] == "offline"
        assert res["has_update"] is False
        assert "Нет связи с GitHub" in res["message"]
        assert f"https://github.com/{GITHUB_REPO}/releases" in res["download_url"]
        assert "PUBLIC_REPO" not in res["download_url"]


# 2. Key loading into mesh on startup and removal from mesh._pools on DELETE / toggle
def test_mesh_sync_on_startup_and_mutation():
    acc1 = AccountSubscription(
        id="acc-test-openai",
        provider="openai",
        api_key="sk-test-secret-key-12345",
        status="active"
    )
    acc2 = AccountSubscription(
        id="acc-test-anthropic",
        provider="anthropic",
        api_key="sk-ant-test-secret-key-67890",
        status="inactive"
    )
    cfg = SubscriptionsConfig(accounts=[acc1, acc2])

    # Startup sync: only active keys are registered
    sync_mesh_from_subscriptions(cfg)
    assert mesh.get_active_key("openai") == "sk-test-secret-key-12345"
    assert mesh.get_active_key("anthropic") is None

    # Toggle acc1 to inactive
    acc1.status = "inactive"
    sync_mesh_from_subscriptions(cfg)
    assert mesh.get_active_key("openai") is None

    # Delete acc1
    cfg.accounts = [acc2]
    sync_mesh_from_subscriptions(cfg)
    assert mesh.get_active_key("openai") is None
    assert "openai" not in mesh._pools or len(mesh._pools["openai"]) == 0


# 3. Execution of cascade:architect via API key in mesh
@pytest.mark.asyncio
async def test_cascade_architect_via_mesh_api_key():
    mock_engine = MagicMock()
    mock_engine.is_ready = False
    mock_engine.query = AsyncMock(side_effect=Exception("Browser not available"))

    orch = HybridOrchestrator(browser_engine=mock_engine)
    orch.enable_local_gate = False
    orch.enable_architect_audit = False
    orch.breakpoint_enabled = False

    mesh.clear()
    mesh.register_keys("gemini", ["AIzaSyTestKeyForCascadeMock12345678"])

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        # Mock Google Gemini OpenAI-compatible API response
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "choices": [{
                "message": {"content": "### Plan: 1. Mock API Plan\n### Implementation:\ndef solve(): return 42"}
            }]
        }
        mock_post.return_value = mock_resp

        result = await orch._run_cascade_architect("Реализуй функцию solve()", inject_signature=False)
        assert "Mock API Plan" in result or "solve" in result
        assert mock_post.called


# 4. Policy flags in /v1/ui/dlp/test
def test_dlp_policy_flags_filtering():
    sample = "sk-proj-1234567890abcdef1234567890abcdef and 192.168.1.55 on database.internal"

    # All enabled: masks keys, IPs, and hosts
    res1 = client.post("/v1/ui/dlp/test", json={
        "text": sample,
        "mask_keys": True,
        "mask_ips": True,
        "mask_hosts": True
    })
    assert res1.status_code == 200
    d1 = res1.json()
    assert "sk-proj" not in d1["sanitized"]
    assert "192.168.1.55" not in d1["sanitized"]
    assert "database.internal" not in d1["sanitized"]

    # Keys disabled: key stays, IP and host masked
    res2 = client.post("/v1/ui/dlp/test", json={
        "text": sample,
        "mask_keys": False,
        "mask_ips": True,
        "mask_hosts": True
    })
    assert res2.status_code == 200
    d2 = res2.json()
    assert "sk-proj" in d2["sanitized"]
    assert "192.168.1.55" not in d2["sanitized"]

    # IPs disabled: IP stays, key and host masked
    res3 = client.post("/v1/ui/dlp/test", json={
        "text": sample,
        "mask_keys": True,
        "mask_ips": False,
        "mask_hosts": True
    })
    assert res3.status_code == 200
    d3 = res3.json()
    assert "sk-proj" not in d3["sanitized"]
    assert "192.168.1.55" in d3["sanitized"]
