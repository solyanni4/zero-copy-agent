"""
Unit tests for core/orchestration_presets.py (Presets Engine, built-ins and persistence).
"""
import pytest
import tempfile
import shutil
from pathlib import Path

from core.orchestration_presets import PresetsManager, OrchestrationPreset
from core.orchestrator import HybridOrchestrator


@pytest.fixture
def temp_presets_manager():
    temp_dir = tempfile.mkdtemp()
    cfg_path = Path(temp_dir) / "presets.yaml"
    mgr = PresetsManager(config_path=cfg_path)
    yield mgr
    shutil.rmtree(temp_dir, ignore_errors=True)


def test_builtin_presets_loaded(temp_presets_manager):
    mgr = temp_presets_manager
    presets = mgr.list_presets()
    ids = [p["id"] for p in presets]
    assert "auto" in ids
    assert "basic" in ids
    assert "thinker" in ids

    active = mgr.get_active_preset()
    assert active.id == "auto"


def test_switch_active_preset(temp_presets_manager):
    mgr = temp_presets_manager
    ok = mgr.set_active_preset("thinker")
    assert ok is True
    assert mgr.get_active_preset().id == "thinker"


def test_create_update_and_protect_builtin(temp_presets_manager):
    mgr = temp_presets_manager

    # 1. Protection: cannot delete built-in (raises ValueError)
    with pytest.raises(ValueError):
        mgr.delete_preset("auto")

    # 2. Create custom preset
    custom_preset, created = mgr.create_or_update_preset({
        "id": "my-custom-setup",
        "name": "My Custom Setup",
        "description": "Fast GPU coding without review",
        "coder_model": "qwen2.5-coder:14b",
        "strict_ast_test_gates": False
    })
    assert created is True
    assert custom_preset.name == "My Custom Setup"

    # Verify present in list
    assert any(p["id"] == "my-custom-setup" for p in mgr.list_presets())

    # 3. Delete custom preset
    del_ok = mgr.delete_preset("my-custom-setup")
    assert del_ok is True
    assert not any(p["id"] == "my-custom-setup" for p in mgr.list_presets())


def test_orchestrator_apply_preset(temp_presets_manager):
    mgr = temp_presets_manager
    thinker = mgr.get_preset("thinker")
    assert thinker is not None

    orch = HybridOrchestrator(browser_engine=None)
    orch.apply_preset(thinker)

    assert orch.enable_local_gate == thinker.strict_ast_test_gates
    assert orch.enable_architect_audit == (thinker.cross_review_depth > 0)
