import sys
from pathlib import Path
from unittest.mock import patch, MagicMock
import pytest
from fastapi.testclient import TestClient

from main import app
from core.updater import check_github_updates
from core.registry import model_registry
from core.healer import AccountSubscription, SubscriptionModel, SubscriptionsConfig, config_healer

client = TestClient(app)


def test_api_web_auth_valid_providers():
    with patch("subprocess.Popen") as mock_popen:
        for prov in ["gemini", "chatgpt", "claude"]:
            resp = client.post(f"/v1/providers/web-auth/{prov}")
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "started"
            assert data["provider"] == prov
            assert f"Окно авторизации для {prov.upper()}" in data["message"]

        assert mock_popen.call_count == 3
        # Check command args passed to Popen
        args, kwargs = mock_popen.call_args
        cmd = args[0]
        assert "--auth" in cmd
        assert "claude" in cmd


def test_api_web_auth_invalid_provider():
    resp = client.post("/v1/providers/web-auth/unsupported_vendor")
    assert resp.status_code == 400
    assert "Недопустимый провайдер" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_registry_recognizes_active_browser_sessions():
    test_acc = AccountSubscription(
        id="gemini-web-primary",
        provider="gemini",
        auth_type="browser_session",
        status="active",
        alias="Gemini Web Session",
        models=[
            SubscriptionModel(id="gemini-4-neon", display_name="Gemini 4 Neon", status="active")
        ]
    )
    fake_cfg = SubscriptionsConfig(schema_version=2, accounts=[test_acc], user_aliases={})

    with patch.object(config_healer, "load_or_create", return_value=(fake_cfg, [])):
        cards = await model_registry.get_models(force_refresh=True)
        active_ids = [c.id for c in cards]
        assert "gemini-4-neon" in active_ids


def test_updater_returns_offline_when_unreachable():
    with patch("urllib.request.build_opener") as mock_opener:
        mock_instance = MagicMock()
        mock_instance.open.side_effect = Exception("Network unreachable")
        mock_opener.return_value = mock_instance

        res = check_github_updates(current_version="1.0.6")
        assert res["status"] == "offline"
        assert res["has_update"] is False
        assert "Нет связи с GitHub" in res["message"]
