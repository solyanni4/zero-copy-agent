import pytest
from fastapi.testclient import TestClient
from main import app

client = TestClient(app)

def test_ui_root_and_static_serving():
    # 1. Root /
    res_root = client.get("/")
    assert res_root.status_code == 200
    assert "text/html" in res_root.headers.get("content-type", "")
    assert "zero-copy-agent" in res_root.text
    assert "app.js" in res_root.text

    # 2. /ui alias
    res_ui = client.get("/ui")
    assert res_ui.status_code == 200
    assert "text/html" in res_ui.headers.get("content-type", "")
    assert "zero-copy-agent" in res_ui.text

    # 3. Static styles.css
    res_css = client.get("/static/styles.css")
    assert res_css.status_code == 200
    assert "--bg-app" in res_css.text

    # 4. Static app.js
    res_js = client.get("/static/app.js")
    assert res_js.status_code == 200
    assert "fetchSystemStatus" in res_js.text

def test_ui_status_endpoint():
    res = client.get("/v1/ui/status")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "ok"
    assert data["gateway_status"] == "online"
    assert "uptime_seconds" in data
    assert "policies" in data
    assert "savings" in data
    assert "ollama" in data

def test_ui_policies_update_endpoint():
    # Update policies with max_rework_iterations = 0
    payload = {
        "dlp_enabled": True,
        "air_gapped": False,
        "compression_enabled": True,
        "mode": "cascade",
        "breakpoint_enabled": True,
        "max_rework_iterations": 0
    }
    res = client.post("/v1/ui/policies", json=payload)
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "ok"
    policies = data["policies"]
    assert policies["mode"] == "cascade"
    assert policies["breakpoint_enabled"] is True
    assert policies["max_rework_iterations"] == 0

    # Reset mode back to auto
    reset_res = client.post("/v1/ui/policies", json={"mode": "auto", "breakpoint_enabled": False, "max_rework_iterations": 2})
    assert reset_res.status_code == 200
    assert reset_res.json()["policies"]["mode"] == "auto"
    assert reset_res.json()["policies"]["max_rework_iterations"] == 2

def test_ui_dlp_sandbox_test_endpoint():
    payload = {
        "text": "Check secret: OPENAI_KEY=sk-proj-9481948194819481948194819481 and server IP 192.168.1.100"
    }
    res = client.post("/v1/ui/dlp/test", json=payload)
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "ok"
    assert data["detected_count"] >= 1
    assert "__SEC_" in data["sanitized"]
    assert len(data["mappings"]) >= 1

def test_ui_healer_and_discovery_endpoints(monkeypatch):
    # Mock config_healer so it doesn't touch local subscriptions.yaml
    import core.ui_api
    from core.healer import SubscriptionsConfig
    monkeypatch.setattr(
        core.ui_api.config_healer,
        "load_or_create",
        lambda: (SubscriptionsConfig(schema_version=2, accounts=[]), ["Mock fix"])
    )

    # 1. Config Healer
    res_healer = client.post("/v1/ui/healer/run")
    assert res_healer.status_code == 200
    data_healer = res_healer.json()
    assert data_healer["status"] == "ok"
    assert "schema_version" in data_healer

    # 2. Discovery
    res_disc = client.post("/v1/ui/discovery")
    assert res_disc.status_code == 200
    data_disc = res_disc.json()
    assert data_disc["status"] == "ok"
    assert "models" in data_disc

def test_ui_logs_and_history_endpoints():
    # 1. DLP Logs
    res_logs = client.get("/v1/ui/dlp/logs")
    assert res_logs.status_code == 200
    assert "logs" in res_logs.json()

    # 2. Backups
    res_backups = client.get("/v1/ui/backups")
    assert res_backups.status_code == 200
    assert "backups" in res_backups.json()

    # 3. History
    res_hist = client.get("/v1/ui/history")
    assert res_hist.status_code == 200
    assert "history" in res_hist.json()
