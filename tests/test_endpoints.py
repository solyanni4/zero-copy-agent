import pytest
from fastapi.testclient import TestClient
from main import app

client = TestClient(app)

def test_get_models_endpoint():
    res = client.get("/v1/models")
    assert res.status_code == 200
    data = res.json()
    assert "data" in data
    # mock-test is always active
    model_ids = [m["id"] for m in data["data"]]
    assert "mock-test" in model_ids

def test_get_models_with_inactive():
    res = client.get("/v1/models?include_inactive=true")
    assert res.status_code == 200
    data = res.json()
    model_ids = [m["id"] for m in data["data"]]
    assert "gpt-4o" in model_ids or "mock-test" in model_ids

def test_integrations_scan_endpoint():
    res = client.get("/v1/integrations/scan")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "ok"
    assert "antigravity" in data["integrations"]
    assert "vscode" in data["integrations"]

def test_integrations_snippet_endpoint():
    res = client.get("/v1/integrations/snippet/vscode")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "ok"
    assert "tier:local-fast" in data["snippet"]
    assert "cli_command" in data

def test_subscriptions_endpoint():
    res = client.get("/v1/subscriptions")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "ok"
    assert data["subscriptions"]["schema_version"] == 2
    assert len(data["subscriptions"]["accounts"]) >= 1

def test_chat_completions_headers_and_signature_suppression():
    # 1. Normal chat should include headers
    payload = {
        "model": "mock-test",
        "messages": [{"role": "user", "content": "Привет"}],
        "stream": False
    }
    res = client.post("/v1/chat/completions", json=payload)
    assert res.status_code == 200
    assert "x-zca-model" in res.headers
    body = res.json()
    content = body["choices"][0]["message"]["content"]
    assert "⚡ **[zero-copy-agent" in content

    # 2. JSON format request must suppress text badge to keep parseable
    json_payload = {
        "model": "mock-test",
        "messages": [{"role": "user", "content": '{"action": "test"}'}],
        "response_format": {"type": "json_object"},
        "stream": False
    }
    json_res = client.post("/v1/chat/completions", json=json_payload)
    assert json_res.status_code == 200
    assert "x-zca-model" in json_res.headers
    json_body = json_res.json()
    json_content = json_body["choices"][0]["message"]["content"]
    # Badge should NOT be in the body content!
    assert "⚡ **[zero-copy-agent" not in json_content

def test_provider_keys_endpoints():
    # 1. Get keys
    res = client.get("/v1/providers/keys")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "ok"
    assert "accounts" in data

    # 2. Add key
    add_payload = {
        "provider": "openai",
        "api_key": "sk-proj-1234567890abcdef1234567890",
        "alias": "Test Key"
    }
    add_res = client.post("/v1/providers/keys", json=add_payload)
    assert add_res.status_code == 200
    acc_id = add_res.json()["account_id"]
    assert acc_id

    # 3. Verify key is in list and masked
    list_res = client.get("/v1/providers/keys")
    acc = next(a for a in list_res.json()["accounts"] if a["id"] == acc_id)
    assert "..." in acc["api_key_masked"]

    # 4. Delete key
    del_res = client.delete(f"/v1/providers/keys/{acc_id}")
    assert del_res.status_code == 200

def test_playground_run_endpoint():
    payload = {
        "prompt": "Привет, это тестовый промпт для песочницы!",
        "model": "mock-test"
    }
    res = client.post("/v1/playground/run", json=payload)
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "ok"
    assert "content" in data
    assert "duration_ms" in data
    assert data["tokens"] > 0

def test_onboarding_status_endpoint():
    res = client.get("/v1/onboarding/status")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "ok"
    assert "progress_pct" in data
    assert "completed_count" in data
    assert "total_steps" in data
    assert data["total_steps"] == 4
    assert len(data["steps"]) == 4
    step_ids = [s["id"] for s in data["steps"]]
    assert "step_local" in step_ids
    assert "step_cloud" in step_ids
    assert "step_ide" in step_ids
    assert "step_test" in step_ids

def test_browser_html_rendering_for_endpoints():
    html_header = {"Accept": "text/html,application/xhtml+xml"}

    # 1. Health endpoint in browser
    res_health = client.get("/health", headers=html_header)
    assert res_health.status_code == 200
    assert "text/html" in res_health.headers.get("content-type", "")
    assert "Health Check" in res_health.text

    # 2. Models endpoint in browser
    res_models = client.get("/v1/models", headers=html_header)
    assert res_models.status_code == 200
    assert "text/html" in res_models.headers.get("content-type", "")
    assert "Каталог моделей" in res_models.text

    # 3. Savings endpoint in browser
    res_savings = client.get("/v1/analytics/savings", headers=html_header)
    assert res_savings.status_code == 200
    assert "text/html" in res_savings.headers.get("content-type", "")
    assert "Метрики экономии" in res_savings.text

    # 4. Short /savings alias
    res_short = client.get("/savings", headers=html_header)
    assert res_short.status_code == 200
    assert "text/html" in res_short.headers.get("content-type", "")

def test_root_dashboard_desktop_ui():
    res = client.get("/")
    assert res.status_code == 200
    assert "text/html" in res.headers.get("content-type", "")
    assert "zero-copy-agent" in res.text
    assert "Мониторинг и токены" in res.text
    assert "Провайдеры и веб-сессии" in res.text
    assert "Фактически обработано" in res.text

def test_auth_status_endpoint():
    res = client.get("/v1/auth/status")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "ok"
    assert "providers" in data
    assert "gemini" in data["providers"]
    assert "chatgpt" in data["providers"]
    assert "claude" in data["providers"]

def test_auth_login_validation():
    # Invalid provider
    res_err = client.post("/v1/auth/login", json={"provider": "unknown_ai"})
    assert res_err.status_code == 400

    # Valid provider format
    res_ok = client.post("/v1/auth/login", json={"provider": "gemini"})
    assert res_ok.status_code == 200
    assert res_ok.json()["status"] == "ok"

def test_app_minimize_endpoint():
    res = client.post("/v1/app/minimize")
    assert res.status_code == 200
    assert res.json()["status"] == "ok"

    # Test with mock window attached
    class MockWindow:
        def __init__(self):
            self.hidden = False
        def hide(self):
            self.hidden = True

    mock_win = MockWindow()
    app.state.desktop_window = mock_win
    res2 = client.post("/v1/app/minimize")
    assert res2.status_code == 200
    assert mock_win.hidden is True
    app.state.desktop_window = None

def test_app_quit_endpoint():
    class MockDesktopApp:
        def __init__(self):
            self.stopped = False
        def stop(self):
            self.stopped = True

    mock_app = MockDesktopApp()
    app.state.desktop_app = mock_app
    res = client.post("/v1/app/quit")
    assert res.status_code == 200
    assert res.json()["status"] == "ok"
    app.state.desktop_app = None

def test_hardware_profile_endpoint():
    res = client.get("/v1/system/hardware")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "ok"
    assert "hardware" in data
    hw = data["hardware"]
    assert "gpu_name" in hw
    assert "total_ram_gb" in hw
    assert "recommended_tier" in hw
    assert isinstance(hw["recommended_models"], list)

def test_updates_check_endpoint():
    res = client.get("/v1/ui/updates/check")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "ok"
    assert "version" in data

def test_dlp_test_endpoint():
    payload = {"text": "API_KEY = 'sk-live-1234567890abcdef' host = '192.168.1.50'"}
    res = client.post("/v1/ui/dlp/test", json=payload)
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "ok"
    assert data["total_masked"] >= 1 or data["detected_count"] >= 1
    assert "192.168.1.50" not in data["sanitized"]

def test_orchestration_config_endpoints():
    # GET
    res_get = client.get("/v1/orchestration/config")
    assert res_get.status_code == 200
    cfg = res_get.json()["config"]
    assert "architect_model" in cfg

    # POST update
    payload = {
        "architect_model": "gemini-1.5-pro",
        "coder_model": "qwen2.5-coder:14b",
        "auditor_model": "gemini-1.5-pro",
        "hard_gate_cmd": "python -m pytest tests/",
        "mode": "auto",
        "max_rework_cycles": 3
    }
    res_post = client.post("/v1/orchestration/config", json=payload)
    assert res_post.status_code == 200
    assert res_post.json()["status"] == "ok"

    # Verify updated
    res_verify = client.get("/v1/orchestration/config")
    assert res_verify.json()["config"]["hard_gate_cmd"] == "python -m pytest tests/"

def test_uninstall_endpoint_validation():
    # Without confirm
    res_bad = client.post("/v1/system/uninstall", json={"confirm": False})
    assert res_bad.status_code == 400

def test_update_progress_and_install_endpoints():
    res_prog = client.get("/v1/system/update/progress")
    assert res_prog.status_code == 200
    data = res_prog.json()
    assert "status" in data
    assert "percent" in data



