"""
Release Verification Gate for zero-copy-agent v1.6.0.
Verifies all release blockers, diff-audit features, syntax gates,
security checks, savings calculations, and version consistency.
"""
import pytest
import shutil
import time
from pathlib import Path

from core.version import __version__
from core.config import config
from core.updater import CURRENT_VERSION
from core.audit import ArchitectAudit, LocalHardGate
from core.storage import AnalyticsStorage
from core.prompt_compressor import PromptCompressorManager, CompressionDraft
from core.ide_sync import RULE_THROUGH_ZCA


def test_v160_version_consistency():
    """Ensures version is unified across all core components."""
    assert __version__ == "1.6.0"
    assert config.version == "1.6.0"
    assert CURRENT_VERSION == "1.6.0"


def test_architect_audit_defaults_to_reject_on_invalid_output():
    """Verifies that non-JSON or corrupt auditor output is rejected and never defaults to PASS."""
    audit = ArchitectAudit()

    # 1. Plain text / corrupt response is not a valid audit response
    assert audit.is_valid_audit_response("This looks okay to me, approved!") is False
    with pytest.raises(ValueError):
        audit.parse_audit_response("This looks okay to me, approved!")

    # 2. Empty response is not valid
    assert audit.is_valid_audit_response("") is False

    # 3. Valid JSON with PASS
    valid_json = '{"verdict": "PASS", "issues": []}'
    assert audit.is_valid_audit_response(valid_json) is True
    res3 = audit.parse_audit_response(valid_json)
    assert res3["verdict"] == "PASS"
    assert res3["issues"] == []

    # 4. Valid JSON with REJECT
    reject_json = '{"verdict": "REJECT", "issues": [{"file": "core/app.py", "description": "Bug"}]}'
    assert audit.is_valid_audit_response(reject_json) is True
    res4 = audit.parse_audit_response(reject_json)
    assert res4["verdict"] == "REJECT"

    # 5. Non-standard verdict never defaults to PASS, strictly REJECT
    other_json = '{"verdict": "MAYBE", "issues": []}'
    res5 = audit.parse_audit_response(other_json)
    assert res5["verdict"] == "REJECT"


def test_local_hard_gate_powershell_bracket_parity():
    """Verifies PowerShell bracket parity validation."""
    gate = LocalHardGate()

    good_ps1 = 'function Test-Script { if ($true) { Write-Host "Hello" [int]$x = (1 + 2) } }'
    issues_good = gate._validate_powershell("good.ps1", good_ps1)
    assert len(issues_good) == 0

    bad_ps1 = 'function Broken { if ($true) { Write-Host "Missing brace" }'
    issues_bad = gate._validate_powershell("bad.ps1", bad_ps1)
    assert len(issues_bad) > 0
    assert "PowerShellBracketMismatch" in [i["type"] for i in issues_bad]


def test_local_hard_gate_javascript_validation():
    """Verifies JS syntax check when node is available, or clean skip if not."""
    gate = LocalHardGate()
    node_available = shutil.which("node") is not None

    good_js = "const sum = (a, b) => a + b; console.log(sum(1, 2));"
    issues_good = gate._validate_javascript("valid.js", good_js)
    assert len(issues_good) == 0

    if node_available:
        bad_js = "const x = ; function { broken"
        issues_bad = gate._validate_javascript("invalid.js", bad_js)
        assert len(issues_bad) > 0
        assert "JavaScriptSyntaxError" in [i["type"] for i in issues_bad]


def test_analytics_savings_local_vs_paid_api(tmp_path: Path):
    """
    Verifies that:
    - Local GPU and Web sessions compute 100% of equivalent API savings.
    - Paid API keys compute savings purely from compressed tokens difference.
    """
    store = AnalyticsStorage(db_path=tmp_path / "test_v160_savings.db")

    # Local GPU (Ollama)
    local_saved = store.calculate_savings(
        model_id="gpt-4o",
        prompt_tokens=500,
        completion_tokens=200,
        raw_prompt_tokens=1000,
        model_routed="ollama:qwen2.5-coder:14b"
    )
    # 1000 raw prompt @ $2.50/M + 200 completion @ $10.00/M = 0.0025 + 0.002 = 0.0045
    assert local_saved == 0.0045

    # Web Session
    web_saved = store.calculate_savings(
        model_id="gpt-4o",
        prompt_tokens=500,
        completion_tokens=200,
        raw_prompt_tokens=1000,
        model_routed="chatgpt-web-primary"
    )
    assert web_saved == 0.0045

    # Paid API Key (only prompt token compression diff is saved)
    # raw_prompt (1000) - prompt (500) = 500 saved tokens @ $2.50/M = 0.00125
    paid_saved = store.calculate_savings(
        model_id="gpt-4o",
        prompt_tokens=500,
        completion_tokens=200,
        raw_prompt_tokens=1000,
        model_routed="openai-api_key"
    )
    assert paid_saved == 0.00125


def test_prompt_compressor_cleanup_old_drafts():
    """Verifies that expired drafts (> 1800s) are cleanly pruned."""
    mgr = PromptCompressorManager()

    # Draft 1: fresh
    d1 = mgr.create_draft("Fresh prompt for testing")
    assert d1.draft_id in mgr._drafts

    # Draft 2: old approved draft
    d2 = mgr.create_draft("Old completed prompt")
    mgr.approve(d2.draft_id)
    # Simulate age > 30 minutes
    mgr._drafts[d2.draft_id].created_at = time.time() - 2000.0

    # Prune
    deleted = mgr.cleanup_old_drafts(max_age_seconds=1800.0)
    assert deleted == 1
    assert d2.draft_id not in mgr._drafts
    assert d1.draft_id in mgr._drafts


def test_ide_routing_rule_contains_background_daemon_guidelines():
    """Verifies that RULE_THROUGH_ZCA instructs agents to run background servers via Start-Process."""
    assert "Start-Process" in RULE_THROUGH_ZCA
    assert "--headless" in RULE_THROUGH_ZCA


def test_all_modules_import_cleanly():
    """Verifies that all core, desktop, and entrypoint modules import without errors."""
    import importlib
    import pkgutil
    import core

    for m in pkgutil.walk_packages(core.__path__, "core."):
        mod = importlib.import_module(m.name)
        assert mod is not None

    for mod_name in ("main", "desktop.app", "desktop.tray_app"):
        mod = importlib.import_module(mod_name)
        assert mod is not None


def test_no_mojibake_in_source_files():
    """Verifies no mojibake corruption sequences exist across repo source files."""
    import re
    root_dir = Path(__file__).resolve().parent.parent
    mojibake_pattern = re.compile(r"ð\x9f|â\x9a|â\x9c")

    scanned_files = 0
    for ext in ("*.py", "*.js", "*.html", "*.css", "*.md"):
        for file_path in root_dir.rglob(ext):
            path_str = str(file_path).replace("\\", "/")
            if any(skip in path_str for skip in ["/venv/", "/.git/", "/dist/", "/build/", "/.pytest_cache/", "scripts/check_emojis.py"]):
                continue
            scanned_files += 1
            content = file_path.read_text(encoding="utf-8", errors="replace")
            matches = mojibake_pattern.findall(content)
            assert not matches, f"Mojibake found in {file_path}: {matches[:5]}"

    assert scanned_files > 20


@pytest.mark.asyncio
async def test_orchestrator_compression_works_without_approval():
    """Verifies that prompt compression is preserved when human approval is disabled."""
    from unittest.mock import AsyncMock, MagicMock
    from core.orchestrator import HybridOrchestrator
    from core.compression import compressor

    # Mock browser engine to capture the exact prompt passed
    mock_browser = MagicMock()
    mock_browser.is_ready = True
    mock_browser.query = AsyncMock(return_value="mock answer")

    orch = HybridOrchestrator(browser_engine=mock_browser)
    orch.active_model = "mock-test"
    orch.mode = "manual"
    orch.compression_enabled = True
    orch.dlp_enabled = False
    orch.air_gapped = False
    config.cascade.require_compression_approval = False

    raw_prompt = """
    # This is a sample code file with comments and whitespace
    # Another comment that can be compressed
    def calculate_total(items):
        total = 0
        for item in items:
            total += item
        return total
    """

    expected_compressed, _, _ = compressor.compress_text(raw_prompt)
    assert len(expected_compressed) < len(raw_prompt)

    # Execute query with require_compression_approval disabled
    resp = await orch.query(raw_prompt, model_id="mock-test", inject_signature=False)
    assert resp == "mock answer"

    # Verify that query received the compressed prompt, NOT the raw prompt
    mock_browser.query.assert_called_once()
    called_prompt = mock_browser.query.call_args[0][0]
    assert called_prompt == expected_compressed
    assert len(called_prompt) < len(raw_prompt)


def test_save_config_never_writes_to_meipass(tmp_path: Path, monkeypatch):
    """Verifies save_config writes to get_runtime_cwd / config.yaml and never to _MEIPASS."""
    import sys
    from core.config import save_config, CascadeConfig, AppConfig

    # 1. CascadeConfig defaults to require_compression_approval=False
    assert CascadeConfig().require_compression_approval is False

    # 2. Simulate PyInstaller frozen runtime with _MEIPASS
    fake_meipass = tmp_path / "fake_meipass"
    fake_meipass.mkdir()
    monkeypatch.setattr(sys, "_MEIPASS", str(fake_meipass), raising=False)

    fake_cwd = tmp_path / "runtime_cwd"
    fake_cwd.mkdir()
    monkeypatch.delenv("CONFIG_PATH", raising=False)
    monkeypatch.setattr("core.config.get_runtime_cwd", lambda: fake_cwd)

    test_cfg = AppConfig()
    success = save_config(test_cfg)
    assert success is True

    # Written to fake_cwd / config.yaml
    expected_file = fake_cwd / "config.yaml"
    assert expected_file.exists()

    # Never written to _MEIPASS
    meipass_file = fake_meipass / "config.yaml"
    assert not meipass_file.exists()


@pytest.mark.asyncio
async def test_wait_for_smart_response_thinking_model_pause():
    """
    Verifies that wait_for_smart_response does NOT cut off after stability_window (3.5s)
    while the model is in thinking/reasoning phase (is_generating_fn=True).
    Simulates a 5s thinking pause before JSON output is returned.
    """
    from core.drivers.base import BaseDriver

    class ConcreteDriver(BaseDriver):
        async def is_authenticated(self) -> bool:
            return True
        async def send_prompt(self, prompt: str, timeout: float = 60.0) -> str:
            return ""
        async def send_prompt_stream(self, prompt: str, timeout: float = 60.0):
            yield ""

    driver = ConcreteDriver(page=None)
    t0 = time.monotonic()

    async def mock_extract():
        elapsed = time.monotonic() - t0
        # First 5 seconds: thinking phase, no output text yet
        if elapsed < 5.0:
            return ""
        return '{"verdict": "PASS", "issues": []}'

    async def mock_is_generating():
        elapsed = time.monotonic() - t0
        # Generating is True during 5s thinking phase and 1s after text begins
        return elapsed < 6.0

    res = await driver.wait_for_smart_response(
        extract_fn=mock_extract,
        is_generating_fn=mock_is_generating,
        ttft_timeout=10.0,
        max_generation_time=20.0,
        stability_window=3.5,
        baseline_text=""
    )

    assert '"verdict": "PASS"' in res
    # Ensure it waited past the 5s thinking threshold
    total_elapsed = time.monotonic() - t0
    assert total_elapsed >= 5.0


@pytest.mark.asyncio
async def test_wait_for_smart_response_baseline_ignored():
    """
    Verifies that baseline_text from previous turns is ignored during Stage 1 and Stage 2
    until new content is produced.
    """
    from core.drivers.base import BaseDriver

    class ConcreteDriver(BaseDriver):
        async def is_authenticated(self) -> bool:
            return True
        async def send_prompt(self, prompt: str, timeout: float = 60.0) -> str:
            return ""
        async def send_prompt_stream(self, prompt: str, timeout: float = 60.0):
            yield ""

    driver = ConcreteDriver(page=None)
    t0 = time.monotonic()
    baseline = "Previous turn completed response"

    async def mock_extract():
        elapsed = time.monotonic() - t0
        if elapsed < 1.0:
            return baseline
        return "Updated new response"

    async def mock_is_generating():
        elapsed = time.monotonic() - t0
        return elapsed < 2.0

    res = await driver.wait_for_smart_response(
        extract_fn=mock_extract,
        is_generating_fn=mock_is_generating,
        ttft_timeout=5.0,
        max_generation_time=10.0,
        stability_window=1.5,
        baseline_text=baseline
    )

    assert res == "Updated new response"


def test_chrome_extension_manifest_validation():
    """Validates chrome_extension/manifest.json for Manifest V3 conformance and required permissions."""
    import json
    from core.config import get_runtime_cwd

    manifest_path = Path(__file__).resolve().parent.parent / "chrome_extension" / "manifest.json"
    assert manifest_path.exists(), f"manifest.json not found at {manifest_path}"

    with open(manifest_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    assert data["manifest_version"] == 3
    assert "cookies" in data["permissions"]
    assert "alarms" in data["permissions"]
    assert "storage" in data["permissions"]

    hosts = data.get("host_permissions", [])
    expected_hosts = [
        "https://*.google.com/*",
        "https://gemini.google.com/*",
        "https://chatgpt.com/*",
        "https://claude.ai/*",
        "https://chat.deepseek.com/*",
        "https://chat.qwen.ai/*",
        "https://grok.com/*",
        "http://127.0.0.1:8000/*"
    ]
    for h in expected_hosts:
        assert h in hosts, f"Expected host permission '{h}' missing from manifest"

    assert data.get("background", {}).get("service_worker") == "background.js"


def test_ensure_chrome_extension_ready(tmp_path: Path, monkeypatch):
    """Verifies ensure_chrome_extension_ready unpacks extension and writes defaults.json with syncToken."""
    import json
    from core.auth import ensure_chrome_extension_ready
    from core.config import AppConfig

    fake_runtime = tmp_path / "runtime"
    fake_runtime.mkdir()
    monkeypatch.setattr("core.config.get_runtime_cwd", lambda: fake_runtime)

    test_cfg = AppConfig()
    test_cfg.cookie_sync_token = "test_secret_sync_token_12345"

    target_dir = ensure_chrome_extension_ready(test_cfg)
    assert target_dir.exists()
    assert target_dir == fake_runtime / "chrome_extension"

    defaults_path = target_dir / "defaults.json"
    assert defaults_path.exists()
    with open(defaults_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    assert data["syncToken"] == "test_secret_sync_token_12345"
    assert "127.0.0.1" in data["zcaUrl"]


def test_sync_cookies_endpoint(tmp_path: Path, monkeypatch):
    """Verifies POST /v1/auth/sync-cookies auth check, conversion of Chrome cookies to Playwright, and file persistence."""
    import json
    from starlette.testclient import TestClient
    from main import app
    from core.config import config

    fake_runtime = tmp_path / "runtime_cookies"
    fake_runtime.mkdir()
    monkeypatch.setattr("core.config.get_runtime_cwd", lambda: fake_runtime)
    monkeypatch.setattr("main.get_runtime_cwd", lambda: fake_runtime)

    client = TestClient(app)
    expected_token = getattr(config, "cookie_sync_token", "default_test_token")

    payload = {
        "provider": "gemini",
        "account_id": "gemini-web-primary",
        "cookies": [
            {
                "name": "__Secure-1PSID",
                "value": "mock_cookie_val_xyz",
                "domain": ".google.com",
                "path": "/",
                "expirationDate": 1799999999.5,
                "httpOnly": True,
                "secure": False,
                "sameSite": "no_restriction",
                "hostOnly": True,
                "session": False
            }
        ]
    }

    # 1. Reject invalid sync token with 401
    bad_res = client.post(
        "/v1/auth/sync-cookies",
        headers={"x-zca-sync-token": "wrong_token"},
        json=payload
    )
    assert bad_res.status_code == 401

    # 2. Accept valid sync token
    good_res = client.post(
        "/v1/auth/sync-cookies",
        headers={"x-zca-sync-token": expected_token},
        json=payload
    )
    assert good_res.status_code == 200
    res_data = good_res.json()
    assert res_data["status"] == "ok"
    assert res_data["cookies_synced"] == 1

    # 3. Verify file saved to browser_profiles/gemini-web-primary/synced_cookies.json
    saved_file = fake_runtime / "browser_profiles" / "gemini-web-primary" / "synced_cookies.json"
    assert saved_file.exists()

    with open(saved_file, "r", encoding="utf-8") as f:
        file_data = json.load(f)

    assert file_data["provider"] == "gemini"
    assert file_data["account_id"] == "gemini-web-primary"
    assert "updated_at" in file_data
    assert len(file_data["cookies"]) == 1

    cookie = file_data["cookies"][0]
    assert cookie["name"] == "__Secure-1PSID"
    assert cookie["value"] == "mock_cookie_val_xyz"
    assert cookie["domain"] == ".google.com"
    assert cookie["expires"] == 1799999999.5
    assert cookie["sameSite"] == "None"
    assert cookie["secure"] is True
    assert "hostOnly" not in cookie
    assert "session" not in cookie


def test_auth_patchright_avoids_add_init_script():
    """Verifies that when using_patchright is True, add_init_script is not called in core/auth.py."""
    import inspect
    import core.auth as auth_mod

    # Inspect source of authenticate and authenticate_desktop
    src_auth = inspect.getsource(auth_mod.authenticate)
    src_desktop = inspect.getsource(auth_mod.authenticate_desktop)

    # Both must check if not using_patchright before add_init_script
    assert "if not using_patchright:" in src_auth
    assert "if not using_patchright:" in src_desktop
    # Neither should call add_init_script unconditionally
    lines_auth = [l.strip() for l in src_auth.splitlines()]
    lines_desktop = [l.strip() for l in src_desktop.splitlines()]

    for i, line in enumerate(lines_auth):
        if "context.add_init_script" in line:
            assert "if not using_patchright:" in lines_auth[i - 1]

    for i, line in enumerate(lines_desktop):
        if "context.add_init_script" in line:
            assert "if not using_patchright:" in lines_desktop[i - 1]


def test_pyinstaller_datas_contain_static_and_chrome_extension():
    """Verifies that PyInstaller spec and build script bundle static, chrome_extension, and ide_config."""
    from pathlib import Path
    spec_path = Path(__file__).resolve().parent.parent / "zero-copy-agent.spec"
    build_path = Path(__file__).resolve().parent.parent / "desktop" / "build.py"

    assert spec_path.exists(), "zero-copy-agent.spec must exist"
    assert build_path.exists(), "desktop/build.py must exist"

    spec_text = spec_path.read_text(encoding="utf-8")
    build_text = build_path.read_text(encoding="utf-8")

    # Check spec datas
    assert "'static'" in spec_text or '"static"' in spec_text
    assert "'chrome_extension'" in spec_text or '"chrome_extension"' in spec_text
    assert "'ide_config'" in spec_text or '"ide_config"' in spec_text

    # Check build.py datas
    assert "static" in build_text
    assert "chrome_extension" in build_text
    assert "ide_config" in build_text


def test_root_dashboard_resolves_html():
    """Verifies that GET / returns the dashboard HTML and does not show UI loading error."""
    from starlette.testclient import TestClient
    from main import app

    client = TestClient(app)
    res = client.get("/")
    assert res.status_code == 200
    assert "UI loading error" not in res.text
    assert "<html" in res.text.lower()


def test_claude_resolves_strictly_to_claude_driver():
    """Verifies that provider 'claude' resolves strictly to ClaudeDriver (claude.ai) and never falls back to GeminiDriver."""
    import pytest
    from core.browser_session import DRIVER_MAP, PROVIDER_URLS, get_driver_for_provider
    from core.drivers.claude import ClaudeDriver
    from core.drivers.gemini import GeminiDriver

    assert "claude" in DRIVER_MAP
    assert DRIVER_MAP["claude"] == ClaudeDriver
    assert ClaudeDriver.URL == "https://claude.ai"
    assert PROVIDER_URLS["claude"] == "https://claude.ai"

    driver_inst = get_driver_for_provider("claude", None)
    assert isinstance(driver_inst, ClaudeDriver)
    assert not isinstance(driver_inst, GeminiDriver)

    # Unknown provider must raise ValueError, not silently substitute GeminiDriver
    with pytest.raises(ValueError) as exc_info:
        get_driver_for_provider("unknown_provider_xyz", None)
    assert "Неподдерживаемый или неизвестный провайдер" in str(exc_info.value)


def test_repeated_sync_cookies_returns_unchanged_and_avoids_rewrite(tmp_path: Path, monkeypatch):
    """Verifies that repeating POST /v1/auth/sync-cookies with unchanged cookies returns status 'unchanged'."""
    from starlette.testclient import TestClient
    from main import app
    from core.config import config

    fake_runtime = tmp_path / "runtime_repeat_cookies"
    fake_runtime.mkdir()
    monkeypatch.setattr("core.config.get_runtime_cwd", lambda: fake_runtime)
    monkeypatch.setattr("main.get_runtime_cwd", lambda: fake_runtime)

    client = TestClient(app)
    expected_token = getattr(config, "cookie_sync_token", "default_test_token")

    payload = {
        "provider": "claude",
        "account_id": "claude-web-primary",
        "cookies": [
            {
                "name": "sessionKey",
                "value": "sk-ant-sid-test-val",
                "domain": ".claude.ai",
                "path": "/",
                "secure": True,
                "httpOnly": True,
                "sameSite": "lax"
            }
        ]
    }

    # 1. First sync -> status ok
    res1 = client.post(
        "/v1/auth/sync-cookies",
        headers={"x-zca-sync-token": expected_token},
        json=payload
    )
    assert res1.status_code == 200
    assert res1.json()["status"] == "ok"

    # 2. Second sync with identical cookies -> status unchanged
    res2 = client.post(
        "/v1/auth/sync-cookies",
        headers={"x-zca-sync-token": expected_token},
        json=payload
    )
    assert res2.status_code == 200
    assert res2.json()["status"] == "unchanged"


def test_gemini_expanded_cookie_whitelist_in_background_js():
    """Verifies that chrome_extension/background.js includes __Secure-1PAPISID, __Secure-3PAPISID, and SIDCC."""
    from pathlib import Path
    bg_path = Path(__file__).resolve().parent.parent / "chrome_extension" / "background.js"
    assert bg_path.exists(), "chrome_extension/background.js must exist"
    bg_code = bg_path.read_text(encoding="utf-8")

    # Critical Google cookie tokens
    assert "SIDCC" in bg_code
    assert "__Secure-1P" in bg_code
    assert "__Secure-3P" in bg_code
    assert "__Host-" in bg_code
    assert "gemini.google.com" in bg_code


def test_cookie_conversion_session_and_host_prefix():
    """Verifies that session cookies lack 'expires' and __Host- cookies use url instead of domain."""
    from main import convert_chrome_cookie_to_playwright

    # 1. Session cookie (no expirationDate)
    c_session = {
        "name": "session_tok",
        "value": "xyz123",
        "domain": "chatgpt.com",
        "path": "/"
    }
    pw_session = convert_chrome_cookie_to_playwright(c_session)
    assert pw_session is not None
    assert "expires" not in pw_session

    # 2. Negative/zero expirationDate -> session cookie
    c_zero_exp = {
        "name": "zero_exp_tok",
        "value": "xyz123",
        "domain": "chatgpt.com",
        "path": "/",
        "expirationDate": -1
    }
    pw_zero = convert_chrome_cookie_to_playwright(c_zero_exp)
    assert pw_zero is not None
    assert "expires" not in pw_zero

    # 3. __Host- prefixed cookie
    c_host = {
        "name": "__Host-auth",
        "value": "sec_val_456",
        "domain": ".google.com",
        "path": "/subpath"
    }
    pw_host = convert_chrome_cookie_to_playwright(c_host)
    assert pw_host is not None
    assert pw_host["secure"] is True
    assert pw_host["path"] == "/"
    assert "domain" not in pw_host
    assert pw_host["url"] == "https://google.com/"


def test_load_config_saves_to_runtime_cwd_and_persists_token(tmp_path, monkeypatch):
    """Verifies that load_config() saves config.yaml to get_runtime_cwd() and persists the token."""
    import yaml
    from core.config import load_config

    template_dir = tmp_path / "bundle_template"
    template_dir.mkdir()
    template_cfg = template_dir / "config.yaml"
    template_cfg.write_text("version: '1.6.0'\nshow_mcp_badge: true\n", encoding="utf-8")

    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()

    monkeypatch.setattr("core.config.get_runtime_cwd", lambda: runtime_dir)
    monkeypatch.setattr("core.config.get_config_path", lambda: template_cfg)
    monkeypatch.delenv("CONFIG_PATH", raising=False)

    cfg1 = load_config()
    saved_runtime_cfg = runtime_dir / "config.yaml"
    assert saved_runtime_cfg.exists(), "config.yaml must be saved into runtime_cwd"

    raw_saved = yaml.safe_load(saved_runtime_cfg.read_text(encoding="utf-8"))
    assert "cookie_sync_token" in raw_saved
    assert raw_saved["cookie_sync_token"] == cfg1.cookie_sync_token

    monkeypatch.setattr("core.config.get_config_path", lambda: saved_runtime_cfg)
    cfg2 = load_config()
    assert cfg2.cookie_sync_token == cfg1.cookie_sync_token


def test_sync_cookies_rejects_missing_key_auth_cookies(tmp_path, monkeypatch):
    """Verifies POST /v1/auth/sync-cookies returns ignored when missing key auth cookies."""
    from starlette.testclient import TestClient
    from main import app
    from core.config import config

    fake_runtime = tmp_path / "runtime_ignored"
    fake_runtime.mkdir()
    monkeypatch.setattr("core.config.get_runtime_cwd", lambda: fake_runtime)
    monkeypatch.setattr("main.get_runtime_cwd", lambda: fake_runtime)

    client = TestClient(app)
    expected_token = getattr(config, "cookie_sync_token", "default_test_token")

    payload = {
        "provider": "claude",
        "account_id": "claude-web-primary",
        "cookies": [
            {
                "name": "test-c",
                "value": "junk_val_123",
                "domain": "claude.ai",
                "path": "/"
            }
        ]
    }

    res = client.post(
        "/v1/auth/sync-cookies",
        headers={"X-ZCA-Sync-Token": expected_token},
        json=payload
    )
    assert res.status_code == 200
    data = res.json()
    assert data.get("status") == "ignored"
    assert data.get("reason") == "no_auth_cookies"

    prof_dir = fake_runtime / "browser_profiles" / "claude-web-primary"
    assert not prof_dir.exists(), "Profile dir must not be created for ignored cookie sync"


def test_prune_browser_profile_caches(tmp_path):
    """Verifies prune_browser_profile_caches removes transient caches while preserving cookies."""
    from core.auth import prune_browser_profile_caches

    profile_dir = tmp_path / "mock_profile"
    default_dir = profile_dir / "Default"
    default_dir.mkdir(parents=True)

    (profile_dir / "OptGuideOnDeviceModel").mkdir()
    (profile_dir / "GrShaderCache").mkdir()
    (default_dir / "Cache").mkdir()
    (default_dir / "Code Cache").mkdir()
    (default_dir / "GPUCache").mkdir()

    network_dir = default_dir / "Network"
    network_dir.mkdir()
    cookies_file = network_dir / "Cookies"
    cookies_file.write_bytes(b"SQLITE_COOKIES_DATABASE_MOCK")

    local_storage = default_dir / "Local Storage"
    local_storage.mkdir()
    (local_storage / "leveldb").mkdir()

    preferences_file = default_dir / "Preferences"
    preferences_file.write_text('{"profile":{"name":"Default"}}', encoding="utf-8")

    prune_browser_profile_caches(profile_dir)

    assert not (profile_dir / "OptGuideOnDeviceModel").exists()
    assert not (profile_dir / "GrShaderCache").exists()
    assert not (default_dir / "Cache").exists()
    assert not (default_dir / "Code Cache").exists()
    assert not (default_dir / "GPUCache").exists()

    assert cookies_file.exists()
    assert cookies_file.read_bytes() == b"SQLITE_COOKIES_DATABASE_MOCK"
    assert local_storage.exists()
    assert preferences_file.exists()







