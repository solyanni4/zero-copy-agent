"""
Tests for ActivityTracker and MCP badge signature injection.
"""
import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from core.activity_tracker import ActivityTracker, ActivityState
from core.mcp_server import execute_tool, init_mcp, WORKING_TOOLS, _is_mcp_badge_enabled


@pytest.mark.asyncio
async def test_activity_tracker_state_transitions():
    tracker = ActivityTracker()
    assert tracker.get_status()["state"] == "idle"

    # Subscribe queue
    q = tracker.subscribe()

    # Start
    await tracker.start_activity(
        tool="local_gpu_code",
        model="ollama/qwen2.5-coder:14b",
        step="Writing code"
    )
    status = tracker.get_status()
    assert status["state"] == "working"
    assert status["tool"] == "local_gpu_code"
    assert status["model"] == "ollama/qwen2.5-coder:14b"
    assert status["step"] == "Writing code"

    # Check event
    evt_start = await asyncio.wait_for(q.get(), timeout=1.0)
    assert evt_start["event"] == "start"
    assert evt_start["data"]["tool"] == "local_gpu_code"

    # Progress
    await tracker.update_step("Step 2/3: Running tests")
    assert tracker.get_status()["step"] == "Step 2/3: Running tests"
    evt_prog = await asyncio.wait_for(q.get(), timeout=1.0)
    assert evt_prog["event"] == "progress"
    assert evt_prog["data"]["step"] == "Step 2/3: Running tests"

    # Finish
    await tracker.finish_activity(success=True, tokens=150)
    status_done = tracker.get_status()
    assert status_done["state"] == "idle"
    assert status_done["last_completed"]["success"] is True
    assert status_done["last_completed"]["tokens"] == 150

    evt_done = await asyncio.wait_for(q.get(), timeout=1.0)
    assert evt_done["event"] == "done"
    assert evt_done["data"]["completed"]["tokens"] == 150

    tracker.unsubscribe(q)


@pytest.mark.asyncio
async def test_mcp_server_badge_injection():
    mock_orchestrator = MagicMock()
    mock_orchestrator.active_preset = MagicMock(id="thinker", name="МЫСЛИТЕЛЬ")
    mock_orchestrator.query = AsyncMock(return_value="def solution():\n    return 42\n")
    mock_orchestrator.plan_and_execute_task = AsyncMock(return_value="Plan executed successfully")
    mock_orchestrator.cascade_architect_model = "gemini-4-neon"
    mock_orchestrator.cascade_executor_model = "ollama/qwen2.5-coder:14b"

    init_mcp(mock_orchestrator)

    # Test local_gpu_code
    res = await execute_tool("local_gpu_code", {"prompt": "write solution", "model": "qwen2.5-coder:14b"})
    assert "[⚡ ZCA | Tool: local_gpu_code | Preset: thinker | Model: ollama/qwen2.5-coder:14b" in res
    assert "Local tokens:" in res
    assert "Time:" in res
    assert "def solution():" in res

    # Test cascade_architect
    res_arch = await execute_tool("cascade_architect", {"task": "design architecture"})
    assert "[⚡ ZCA | Tool: cascade_architect | Preset: thinker | Model: gemini-4-neon" in res_arch
    assert "Time:" in res_arch

    # Test plan_and_execute_task
    res_plan = await execute_tool("plan_and_execute_task", {"goal": "build feature"})
    assert "[⚡ ZCA | Tool: plan_and_execute_task | Preset: thinker | Model: ollama/qwen2.5-coder:14b" in res_plan
    assert "Plan executed successfully" in res_plan


@pytest.mark.asyncio
async def test_mcp_badge_toggle_off():
    mock_orchestrator = MagicMock()
    mock_orchestrator.active_preset = MagicMock(id="basic", name="Базовый")
    mock_orchestrator.query = AsyncMock(return_value="pure code without badge")
    init_mcp(mock_orchestrator)

    with patch("core.mcp_server._is_mcp_badge_enabled", return_value=False):
        res = await execute_tool("local_gpu_code", {"prompt": "write solution"})
        assert "[⚡ ZCA" not in res
        assert res == "pure code without badge"
