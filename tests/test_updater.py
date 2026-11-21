import pytest
from pathlib import Path
from core.updater import get_current_exe_path, get_update_state, check_github_updates

def test_get_current_exe_path():
    p = get_current_exe_path()
    assert isinstance(p, Path)
    assert p.name.endswith(".exe") or "python" in p.name.lower()

def test_get_update_state():
    state = get_update_state()
    assert isinstance(state, dict)
    assert "status" in state
    assert "percent" in state
    assert "message" in state

def test_check_github_updates_structure():
    info = check_github_updates("1.0.0")
    assert isinstance(info, dict)
    assert info["status"] in ("ok", "offline")
    assert "has_update" in info
    assert "version" in info
    assert "download_url" in info
