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
