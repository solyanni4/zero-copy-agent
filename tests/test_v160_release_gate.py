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

