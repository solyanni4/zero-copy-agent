import json
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from core.ide_setup import (
    detect_jetbrains,
    configure_jetbrains,
    rollback_target_clean,
    print_config_snippet,
    scan_all,
    configure_targets,
    sync_ide_if_needed,
    history_manager
)


def test_detect_jetbrains_basic():
    """Verify detect_jetbrains returns expected keys and structure."""
    res = detect_jetbrains()
    assert isinstance(res, dict)
    assert "name" in res
    assert "JetBrains" in res["name"]
    assert "installed" in res
    assert "configured" in res
    assert "path" in res
    assert "config_dirs" in res
    assert "details" in res


def test_detect_jetbrains_mock_installed(tmp_path):
    """Test detect_jetbrains when an installation is found."""
    mock_app_data = tmp_path / "AppData" / "Roaming"
    jb_dir = mock_app_data / "JetBrains" / "PyCharm2024.1"
    jb_dir.mkdir(parents=True)

    with patch.dict("os.environ", {"APPDATA": str(mock_app_data), "LOCALAPPDATA": str(tmp_path)}):
        with patch("sys.platform", "win32"):
            res = detect_jetbrains()
            assert res["installed"] is True
            assert any("PyCharm" in d for d in res["detected_ides"])


def test_detect_jetbrains_when_not_installed(tmp_path):
    """Test detect_jetbrains when no JetBrains paths or binaries exist."""
    empty_appdata = tmp_path / "empty_appdata"
    empty_appdata.mkdir()

    with patch.dict("os.environ", {"APPDATA": str(empty_appdata), "LOCALAPPDATA": str(empty_appdata), "ProgramFiles": str(empty_appdata)}):
        with patch("shutil.which", return_value=None):
            with patch("pathlib.Path.home", return_value=tmp_path / "home"):
                res = detect_jetbrains()
                # snippet doesn't exist, no binaries or folders
                assert res["configured"] is False


def test_configure_jetbrains_creates_snippet(tmp_path):
    """Verify configure_jetbrains writes valid JSON snippet with OpenAI & MCP configs."""
    fake_home = tmp_path / "user_home"
    fake_home.mkdir()

    with patch("pathlib.Path.home", return_value=fake_home):
        ok, msg = configure_jetbrains(dry_run=False, port=8888)
        assert ok is True
        snippet_file = fake_home / ".zero-copy-agent" / "jetbrains-setup.json"
        assert snippet_file.exists()

        data = json.loads(snippet_file.read_text(encoding="utf-8"))
        assert data["openai_endpoint"]["url"] == "http://127.0.0.1:8888/v1"
        assert data["openai_endpoint"]["api_key"] == "sk-zero-copy"
        assert data["mcp_server"]["url"] == "http://127.0.0.1:8888/mcp"
        assert data["mcp_server"]["sse_url"] == "http://127.0.0.1:8888/sse"
        assert "cascade_architect" in data["mcp_server"]["tools"]


def test_configure_jetbrains_dry_run(tmp_path):
    """Dry-run should not create files on disk."""
    fake_home = tmp_path / "user_home"
    fake_home.mkdir()

    with patch("pathlib.Path.home", return_value=fake_home):
        ok, msg = configure_jetbrains(dry_run=True, port=8000)
        assert ok is True
        assert "[DRY-RUN]" in msg
        snippet_file = fake_home / ".zero-copy-agent" / "jetbrains-setup.json"
        assert not snippet_file.exists()


def test_configure_jetbrains_preserves_existing_mcp(tmp_path):
    """Verify that configuring JetBrains does not wipe existing MCP servers in mcp.json."""
    fake_home = tmp_path / "user_home"
    fake_app_data = tmp_path / "AppData" / "Roaming"
    jb_cfg = fake_app_data / "JetBrains" / "PhpStorm2024.1"
    jb_cfg.mkdir(parents=True)

    # Existing mcp.json with custom server
    existing_mcp = jb_cfg / "mcp.json"
    existing_mcp.write_text(json.dumps({
        "mcpServers": {
            "custom-team-tool": {"url": "http://10.0.0.5:9000/sse"}
        }
    }), encoding="utf-8")

    with patch("pathlib.Path.home", return_value=fake_home):
        with patch.dict("os.environ", {"APPDATA": str(fake_app_data), "LOCALAPPDATA": str(tmp_path)}):
            with patch("sys.platform", "win32"):
                ok, msg = configure_jetbrains(dry_run=False, port=8000)
                assert ok is True

                updated_mcp = json.loads(existing_mcp.read_text(encoding="utf-8"))
                assert "custom-team-tool" in updated_mcp["mcpServers"]
                assert "zero-copy-agent" in updated_mcp["mcpServers"]
                assert updated_mcp["mcpServers"]["zero-copy-agent"]["url"] == "http://127.0.0.1:8000/sse"


def test_print_config_snippet_jetbrains():
    """Verify print_config_snippet returns expected JSON for jetbrains and pycharm."""
    snippet_raw = print_config_snippet("jetbrains", port=8000)
    data = json.loads(snippet_raw)
    assert "openai_endpoint" in data
    assert data["openai_endpoint"]["url"] == "http://127.0.0.1:8000/v1"
    assert "mcp_server" in data

    snippet_pycharm = print_config_snippet("pycharm", port=8000)
    assert "zero-copy-agent" in snippet_pycharm


def test_rollback_target_clean_jetbrains(tmp_path):
    """Test clean rollback of JetBrains snippet."""
    fake_home = tmp_path / "user_home"
    snippet_file = fake_home / ".zero-copy-agent" / "jetbrains-setup.json"
    snippet_file.parent.mkdir(parents=True)
    snippet_file.write_text("{}", encoding="utf-8")

    with patch("pathlib.Path.home", return_value=fake_home):
        ok, msg = rollback_target_clean("jetbrains")
        assert ok is True
        assert not snippet_file.exists()


def test_sync_ide_if_needed_on_version_change(tmp_path):
    """When version is different from stored, sync_ide_if_needed must trigger sync."""
    fake_home = tmp_path / "user_home"
    state_file = fake_home / ".zero-copy-agent" / "ide_sync_state.json"
    state_file.parent.mkdir(parents=True)
    state_file.write_text(json.dumps({
        "version": "1.0.6",
        "configured_paths": {}
    }), encoding="utf-8")

    with patch("pathlib.Path.home", return_value=fake_home):
        with patch("core.ide_setup.scan_all", return_value={
            "jetbrains": {"name": "JetBrains", "installed": True, "configured": False, "path": "p"}
        }):
            with patch("core.ide_setup.configure_targets", return_value=[("JetBrains IDEs", True, "ok")]):
                res = sync_ide_if_needed(current_version="1.0.7", port=8000)
                assert res["status"] == "synced"
                assert "1.0.6 -> 1.0.7" in res["reason"]

                # Verify new state was written
                new_state = json.loads(state_file.read_text(encoding="utf-8"))
                assert new_state["version"] == "1.0.7"


def test_sync_ide_if_needed_skips_when_unchanged(tmp_path):
    """When version is identical and all installed IDEs are configured, sync must be skipped."""
    fake_home = tmp_path / "user_home"
    state_file = fake_home / ".zero-copy-agent" / "ide_sync_state.json"
    state_file.parent.mkdir(parents=True)
    state_file.write_text(json.dumps({
        "version": "1.0.7",
        "configured_paths": {"jetbrains": ["path1"]}
    }), encoding="utf-8")

    with patch("pathlib.Path.home", return_value=fake_home):
        with patch("core.ide_setup.scan_all", return_value={
            "jetbrains": {"name": "JetBrains", "installed": True, "configured": True, "path": "path1"}
        }):
            res = sync_ide_if_needed(current_version="1.0.7", port=8000)
            assert res["status"] == "skipped"
            assert "Конфигурации актуальны" in res["reason"]
