import json
import pytest
from pathlib import Path
from core.ide_setup import HistoryManager, safe_update_json

def test_history_backup_and_rollback(tmp_path):
    mgr = HistoryManager(backup_dir=tmp_path / "backups")
    test_file = tmp_path / "sample_config.json"
    test_file.write_text(json.dumps({"key": "original_val"}), encoding="utf-8")

    # Record backup
    bak = mgr.record_backup("test_target", test_file)
    assert bak.exists()

    # Modify original
    test_file.write_text(json.dumps({"key": "modified_val"}), encoding="utf-8")
    assert json.loads(test_file.read_text(encoding="utf-8"))["key"] == "modified_val"

    # Rollback
    results = mgr.rollback("test_target")
    assert any("Восстановлен" in r for r in results)
    assert json.loads(test_file.read_text(encoding="utf-8"))["key"] == "original_val"

def test_safe_update_json_preserves_keys(tmp_path):
    test_file = tmp_path / "mcp_config.json"
    initial_content = {
        "mcpServers": {
            "homelab-brain-orchestrator": {
                "command": "python",
                "args": ["server.py"]
            }
        }
    }
    test_file.write_text(json.dumps(initial_content), encoding="utf-8")

    def good_updater(data):
        data["mcpServers"]["zero-copy-agent"] = {"url": "http://127.0.0.1:8000/mcp"}
        return data

    ok, msg = safe_update_json(test_file, good_updater, target="antigravity")
    assert ok is True
    updated = json.loads(test_file.read_text(encoding="utf-8"))
    assert "homelab-brain-orchestrator" in updated["mcpServers"]
    assert "zero-copy-agent" in updated["mcpServers"]

def test_safe_update_json_auto_rollback_on_corruption(tmp_path):
    test_file = tmp_path / "mcp_config.json"
    initial_content = {
        "mcpServers": {
            "homelab-brain-orchestrator": {"command": "python"}
        }
    }
    test_file.write_text(json.dumps(initial_content), encoding="utf-8")

    # Updater that illegally attempts to drop homelab-brain-orchestrator
    def malicious_updater(data):
        return {"mcpServers": {"zero-copy-agent": {"url": "http://127.0.0.1:8000/mcp"}}}

    ok, msg = safe_update_json(test_file, malicious_updater, target="antigravity")
    assert ok is False
    assert "АВТООТКАТ" in msg

    # Verify that file was rolled back to original content
    restored = json.loads(test_file.read_text(encoding="utf-8"))
    assert "homelab-brain-orchestrator" in restored["mcpServers"]
    assert "zero-copy-agent" not in restored["mcpServers"]
