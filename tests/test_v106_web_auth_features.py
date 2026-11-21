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


def test_get_runtime_cwd_pyinstaller_frozen(tmp_path, monkeypatch):
    """Verifies that in PyInstaller frozen mode, get_runtime_cwd returns sys.executable.parent and never _MEIPASS."""
    from core.config import get_runtime_cwd

    exe_dir = tmp_path / "installed_app"
    exe_dir.mkdir()
    fake_exe = exe_dir / "zero-copy-agent.exe"
    fake_exe.touch()

    temp_meipass = tmp_path / "Temp" / "_MEI123456"
    temp_meipass.mkdir(parents=True)

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(fake_exe))
    monkeypatch.setattr(sys, "_MEIPASS", str(temp_meipass), raising=False)

    resolved = get_runtime_cwd()
    assert resolved == exe_dir
    assert resolved != temp_meipass


def test_model_registry_check_disk_updates():
    """Verifies that ModelRegistry.check_disk_updates detects file modification times."""
    from core.registry import ModelRegistry

    reg = ModelRegistry()
    assert reg._subscriptions_mtime == 0.0

    mock_stat = MagicMock()
    mock_stat.st_mtime = 123456.78

    mock_path = MagicMock()
    mock_path.exists.return_value = True
    mock_path.stat.return_value = mock_stat

    with patch("core.healer.config_healer.target_path", mock_path):
        changed = reg.check_disk_updates()
        assert changed is True
        assert reg._subscriptions_mtime == 123456.78

        # Calling again with same mtime should return False
        changed_second = reg.check_disk_updates()
        assert changed_second is False

        # Changing mtime triggers invalidation again
        mock_stat.st_mtime = 123499.99
        changed_third = reg.check_disk_updates()
        assert changed_third is True
        assert reg._subscriptions_mtime == 123499.99


def test_models_endpoint_refresh_param():
    """Verifies GET /v1/models with refresh=true query parameter."""
    with patch("main.orchestrator.discover_models") as mock_discover:
        mock_discover.return_value = [{"id": "gemini-4-neon", "object": "model"}]
        resp = client.get("/v1/models?refresh=true")
        assert resp.status_code == 200
        mock_discover.assert_called_with(force=True, include_inactive=False)

