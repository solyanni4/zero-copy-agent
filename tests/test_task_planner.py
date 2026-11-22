import json
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from core.task_planner import (
    SubtaskStep,
    ExecutionPlan,
    PlanStorage,
    TaskPlanner,
    StepExecutor,
    TaskIntegrator,
    run_dag_pipeline,
    build_compact_project_tree
)
from core.mcp_server import execute_tool, init_mcp


def test_plan_storage_save_load_clear(tmp_path):
    """Verify atomic saving, loading and clearing of .zca/task_plan.json."""
    ws = tmp_path / "workspace"
    ws.mkdir()

    step1 = SubtaskStep(
        id="step_1",
        title="Init DB",
        description="Create tables",
        target_files=["models.py"],
        verification_cmd="python -m py_compile models.py"
    )
    plan = ExecutionPlan(
        task_id="plan-test-123",
        goal="Setup Database",
        steps=[step1],
        workspace_dir=str(ws)
    )

    # Save
    saved_path = PlanStorage.save_plan(plan, ws)
    assert saved_path.exists()
    assert saved_path == ws / ".zca" / "task_plan.json"

    # Load
    loaded = PlanStorage.load_plan(ws)
    assert loaded is not None
    assert loaded.task_id == "plan-test-123"
    assert loaded.goal == "Setup Database"
    assert len(loaded.steps) == 1
    assert loaded.steps[0].id == "step_1"

    # Clear
    PlanStorage.clear_plan(ws)
    assert not saved_path.exists()
    assert PlanStorage.load_plan(ws) is None


def test_dag_topological_sort():
    """Verify steps are topologically sorted by dependency order."""
    step3 = SubtaskStep(id="step_3", title="Finalize", description="", target_files=[], depends_on=["step_2"])
    step1 = SubtaskStep(id="step_1", title="Setup", description="", target_files=[], depends_on=[])
    step2 = SubtaskStep(id="step_2", title="Implement", description="", target_files=[], depends_on=["step_1"])

    steps = [step3, step1, step2]
    sorted_steps = TaskPlanner._topological_sort(steps)
    order_ids = [s.id for s in sorted_steps]
    assert order_ids == ["step_1", "step_2", "step_3"]


@pytest.mark.asyncio
async def test_task_planner_create_plan_with_orchestrator(tmp_path):
    """Test DAG creation via architect mock returning valid JSON."""
    ws = tmp_path / "project"
    ws.mkdir()
    (ws / "main.py").write_text("print('hello')", encoding="utf-8")

    mock_orch = MagicMock()
    mock_plan_json = {
        "task_id": "plan-abc",
        "steps": [
            {
                "id": "step_1",
                "title": "Define User Model",
                "description": "Create User dataclass",
                "target_files": ["models.py"],
                "depends_on": [],
                "verification_cmd": "python -m py_compile models.py"
            },
            {
                "id": "step_2",
                "title": "Add Auth Service",
                "description": "Implement login logic",
                "target_files": ["auth.py"],
                "depends_on": ["step_1"],
                "verification_cmd": "python -m py_compile auth.py"
            }
        ]
    }
    mock_orch.query = AsyncMock(return_value=f"```json\n{json.dumps(mock_plan_json)}\n```")

    plan = await TaskPlanner.create_plan(
        goal="Add authentication system",
        workspace_dir=ws,
        orchestrator=mock_orch
    )

    assert plan.task_id == "plan-abc"
    assert len(plan.steps) == 2
    assert plan.steps[0].id == "step_1"
    assert plan.steps[1].id == "step_2"
    assert PlanStorage.get_plan_path(ws).exists()


@pytest.mark.asyncio
async def test_task_planner_create_plan_fallback(tmp_path):
    """Test fallback DAG when architect returns malformed or non-JSON response."""
    ws = tmp_path / "project"
    ws.mkdir()
    (ws / "app.py").write_text("# app", encoding="utf-8")

    mock_orch = MagicMock()
    mock_orch.query = AsyncMock(return_value="I cannot format this as JSON right now.")

    plan = await TaskPlanner.create_plan(
        goal="Implement caching layer",
        workspace_dir=ws,
        orchestrator=mock_orch
    )

    assert plan is not None
    assert len(plan.steps) >= 2
    assert plan.steps[0].status == "pending"


def test_step_executor_ast_gate(tmp_path):
    """Verify AST gate validates correct Python/JSON and flags syntax errors."""
    py_ok = tmp_path / "good.py"
    py_bad = tmp_path / "bad.py"
    json_ok = tmp_path / "data.json"
    json_bad = tmp_path / "bad.json"

    ok, _ = StepExecutor.verify_ast_syntax(py_ok, "def foo():\n    return 42\n")
    assert ok is True

    ok, err = StepExecutor.verify_ast_syntax(py_bad, "def foo(:\n    broken syntax")
    assert ok is False
    assert "SyntaxError" in err

    ok, _ = StepExecutor.verify_ast_syntax(json_ok, '{"key": "value"}')
    assert ok is True

    ok, err = StepExecutor.verify_ast_syntax(json_bad, '{"unclosed": ')
    assert ok is False
    assert "ParseError" in err


@pytest.mark.asyncio
async def test_step_executor_success(tmp_path):
    """Verify StepExecutor runs step, verifies AST, and marks as completed."""
    ws = tmp_path / "ws"
    ws.mkdir()

    step = SubtaskStep(
        id="step_1",
        title="Add helper",
        description="Write helper function",
        target_files=["helper.py"],
        verification_cmd=""
    )
    plan = ExecutionPlan(task_id="plan-1", goal="Test", steps=[step], workspace_dir=str(ws))

    mock_orch = MagicMock()
    mock_orch.query = AsyncMock(return_value="""
FILE: helper.py
```python
def add(a: int, b: int) -> int:
    return a + b
```
""")

    success = await StepExecutor.run_step(
        step=step,
        plan=plan,
        workspace_dir=ws,
        orchestrator=mock_orch
    )

    assert success is True
    assert step.status == "completed"
    assert (ws / "helper.py").exists()
    assert "def add" in (ws / "helper.py").read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_step_executor_rollback_on_failure(tmp_path):
    """Verify that when verification fails repeatedly, changes are rolled back to original."""
    ws = tmp_path / "ws"
    ws.mkdir()
    target_file = ws / "critical.py"
    original_code = "ORIGINAL_STATE = True\n"
    target_file.write_text(original_code, encoding="utf-8")

    step = SubtaskStep(
        id="step_fail",
        title="Broken step",
        description="Fails verification",
        target_files=["critical.py"],
        verification_cmd="python -c \"import sys; sys.exit(1)\""  # Command that always fails
    )
    plan = ExecutionPlan(task_id="plan-1", goal="Test", steps=[step], workspace_dir=str(ws))

    mock_orch = MagicMock()
    mock_orch.query = AsyncMock(return_value="""
FILE: critical.py
```python
CORRUPTED_STATE = True
```
""")

    success = await StepExecutor.run_step(
        step=step,
        plan=plan,
        workspace_dir=ws,
        orchestrator=mock_orch,
        max_attempts=2
    )

    assert success is False
    assert step.status == "failed"
    assert step.attempts == 2

    # Verification of rollback: original content must be restored!
    restored_code = target_file.read_text(encoding="utf-8")
    assert restored_code == original_code


@pytest.mark.asyncio
async def test_task_runner_resume_checkpoint(tmp_path):
    """Verify run_dag_pipeline resumes from first uncompleted step if plan exists."""
    ws = tmp_path / "ws"
    ws.mkdir()

    step1 = SubtaskStep(
        id="step_1",
        title="Already done",
        description="",
        target_files=[],
        status="completed"
    )
    step2 = SubtaskStep(
        id="step_2",
        title="Needs execution",
        description="Generate helper",
        target_files=["resumed.py"],
        status="pending"
    )
    existing_plan = ExecutionPlan(
        task_id="plan-resume-42",
        goal="Multi-step goal",
        steps=[step1, step2],
        workspace_dir=str(ws)
    )
    PlanStorage.save_plan(existing_plan, ws)

    mock_orch = MagicMock()
    mock_orch.query = AsyncMock(return_value="""
FILE: resumed.py
```python
RESUMED = True
```
""")

    with patch("core.task_planner.TaskIntegrator.verify_all", return_value={"tests_passed": True, "final_audit": "Clean"}):
        result = await run_dag_pipeline(
            goal="Multi-step goal",
            workspace_dir=ws,
            orchestrator=mock_orch,
            resume=True
        )

        assert result["status"] == "completed"
        assert result["task_id"] == "plan-resume-42"
        assert (ws / "resumed.py").exists()


@pytest.mark.asyncio
async def test_mcp_plan_and_execute_tool():
    """Verify MCP tool 'plan_and_execute_task' dispatches correctly through orchestrator."""
    mock_orch = MagicMock()
    mock_orch.plan_and_execute_task = AsyncMock(return_value="[OK] План исполнен успешно")
    init_mcp(mock_orch)

    res = await execute_tool("plan_and_execute_task", {"goal": "Build full auth system"})
    assert "[OK]" in res
    assert "План исполнен" in res
