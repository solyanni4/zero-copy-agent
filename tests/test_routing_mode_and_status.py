import pytest
import asyncio
from pathlib import Path
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient

from core.config import config
from core.ide_sync import apply_rule_to_file, sync_routing_rules, RULE_THROUGH_ZCA, RULE_BYPASS, ZCA_ROUTING_START, ZCA_ROUTING_END
from core.activity_tracker import ActivityTracker
from core.mcp_server import execute_tool
from main import app


def test_apply_rule_preserves_surrounding_content(tmp_path):
    target_file = tmp_path / "GEMINI.md"
    existing_content = "# User Custom Instructions\n\nAlways use tabs instead of spaces.\n"
    target_file.write_text(existing_content, encoding="utf-8")

    # 1. Apply through_zca
    updated = apply_rule_to_file(target_file, RULE_THROUGH_ZCA)
    assert updated is True
    content1 = target_file.read_text(encoding="utf-8")
    assert ZCA_ROUTING_START in content1
    assert ZCA_ROUTING_END in content1
    assert "ВСЕ задачи на проектирование" in content1
    assert "Always use tabs instead of spaces." in content1

    # 2. Re-applying the same rule should be idempotent (no-op)
    updated_noop = apply_rule_to_file(target_file, RULE_THROUGH_ZCA)
    assert updated_noop is False

    # 3. Switch to bypass
    updated_bypass = apply_rule_to_file(target_file, RULE_BYPASS)
    assert updated_bypass is True
    content2 = target_file.read_text(encoding="utf-8")
    assert ZCA_ROUTING_START in content2
    assert ZCA_ROUTING_END in content2
    assert "ZCA находится в режиме обхода (Bypass)" in content2
    assert "Always use tabs instead of spaces." in content2
    # Ensure no duplicate markers
    assert content2.count(ZCA_ROUTING_START) == 1
    assert content2.count(ZCA_ROUTING_END) == 1


def test_sync_routing_rules(tmp_path):
    subproject = tmp_path / "my_project"
    subproject.mkdir()
    res = sync_routing_rules("through_zca", project_dirs=[subproject])
    assert "updated" in res
    assert "skipped" in res
    assert res["mode"] == "through_zca"

    # Verify project-level files created
    assert (subproject / "GEMINI.md").exists()
    assert (subproject / ".cursorrules").exists()


@pytest.mark.asyncio
async def test_mcp_server_bypass_mode():
    orig_mode = getattr(config, "ide_routing_mode", "through_zca")
    try:
        config.ide_routing_mode = "bypass"
        resp = await execute_tool("cascade_architect", {"task": "test task"})
        assert "[⏭️ ZCA Bypass]" in resp
        assert "Режим обхода активен" in resp

        resp2 = await execute_tool("local_gpu_code", {"prompt": "write hello world"})
        assert "[⏭️ ZCA Bypass]" in resp2

        # In through_zca mode, it does not decline with bypass
        config.ide_routing_mode = "through_zca"
        with patch("core.mcp_server._orchestrator", None):
            resp3 = await execute_tool("cascade_architect", {"task": "test"})
            assert "[ERROR]" in resp3
            assert "[⏭️ ZCA Bypass]" not in resp3
    finally:
        config.ide_routing_mode = orig_mode


@pytest.mark.asyncio
async def test_activity_tracker_russian_transitions_and_trace():
    tracker = ActivityTracker()

    # 1. Start planning
    await tracker.start_activity(
        tool="cascade_architect",
        model="gemini-4-neon",
        stage="planning"
    )
    status1 = tracker.get_status()
    assert status1["state"] == "working"
    assert status1["model"] == "gemini-4-neon"
    assert "🧠 Выполняется анализ запроса — модель: gemini-4-neon" in status1["step"]
    assert any("🧠 Выполняется анализ запроса — модель: gemini-4-neon" in h["message"] for h in status1["history"])

    # 2. Update step: coding
    await tracker.update_step(
        step="Кодинг",
        model="qwen2.5-coder:14b",
        stage="coding"
    )
    status2 = tracker.get_status()
    assert any("💻 Генерация кода — модель: qwen2.5-coder:14b" in h["message"] for h in status2["history"])

    # 3. Failover / Fallback
    await tracker.record_fallback(
        failed_model="gemini-4-neon",
        next_model="deepseek-coder",
        reason="429"
    )
    status3 = tracker.get_status()
    assert status3["fallback_active"] is True
    assert status3["model"] == "deepseek-coder"
    assert any("⚠️ Модель gemini-4-neon недоступна (429) → переключено на deepseek-coder" in h["message"] for h in status3["history"])

    # 4. Update step: audit
    await tracker.update_step(
        step="Аудит",
        model="gemini-4-neon",
        stage="audit"
    )
    status4 = tracker.get_status()
    assert any("🔍 Аудит — модель: gemini-4-neon" in h["message"] for h in status4["history"])

    # 5. Check trace formatting
    badge_trace = tracker.get_trace(as_badge=True)
    assert "Анализ: gemini-4-neon" in badge_trace
    assert "Код: qwen2.5-coder:14b" in badge_trace
    assert "Аудит: gemini-4-neon" in badge_trace

    human_trace = tracker.get_trace(as_badge=False)
    assert "Архитектор: gemini-4-neon" in human_trace
    assert "Код: qwen2.5-coder:14b" in human_trace
    assert "Аудит: gemini-4-neon" in human_trace

    # 6. Finish activity
    await tracker.finish_activity(success=True)
    status5 = tracker.get_status()
    assert status5["state"] == "idle"
    assert any("✅ Выполнено за" in h["message"] and "Архитектор: gemini-4-neon" in h["message"] for h in status5["history"])


def test_routing_mode_api_endpoints():
    client = TestClient(app)
    orig_mode = getattr(config, "ide_routing_mode", "through_zca")

    try:
        # GET current mode
        res_get = client.get("/api/routing-mode")
        assert res_get.status_code == 200
        data_get = res_get.json()
        assert data_get["status"] == "ok"
        assert "mode" in data_get

        # POST invalid mode
        res_bad = client.post("/api/routing-mode", json={"mode": "unknown_mode"})
        assert res_bad.status_code == 400

        # POST bypass
        res_bypass = client.post("/api/routing-mode", json={"mode": "bypass"})
        assert res_bypass.status_code == 200
        data_bypass = res_bypass.json()
        assert data_bypass["status"] == "ok"
        assert data_bypass["mode"] == "bypass"
        assert config.ide_routing_mode == "bypass"

        # POST through_zca
        res_zca = client.post("/api/routing-mode", json={"mode": "through_zca"})
        assert res_zca.status_code == 200
        data_zca = res_zca.json()
        assert data_zca["status"] == "ok"
        assert data_zca["mode"] == "through_zca"
        assert config.ide_routing_mode == "through_zca"

    finally:
        config.ide_routing_mode = orig_mode
