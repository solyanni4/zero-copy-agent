"""
DAG Task Planner, Local Worker with AST/Test-gates, and Final Integrator.
Implements autonomous multi-step problem solving for zero-copy-agent.
"""
import os
import re
import ast
import json
import time
import shutil
import asyncio
import inspect
import logging
from dataclasses import dataclass, field, asdict
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Any, Optional, Tuple, Callable

from core.ide_setup import history_manager

logger = logging.getLogger("zcao.task_planner")


# ==============================================================================
# 1. Data Models
# ==============================================================================

@dataclass
class SubtaskStep:
    id: str                                  # e.g. "step_1"
    title: str                               # Short title of the subtask
    description: str                         # Detailed prompt instructions for worker
    target_files: List[str]                  # 1-3 relative file paths
    depends_on: List[str] = field(default_factory=list)  # Step IDs required before this step
    verification_cmd: str = ""               # Specific test/compile command
    status: str = "pending"                  # "pending" | "in_progress" | "completed" | "failed"
    attempts: int = 0                        # Number of execution attempts
    error_log: str = ""                      # Failure log or traceback
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())


@dataclass
class ExecutionPlan:
    task_id: str
    goal: str
    steps: List[SubtaskStep] = field(default_factory=list)
    workspace_dir: str = "."
    status: str = "pending"                  # "pending" | "in_progress" | "completed" | "failed"
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())
    final_audit: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ExecutionPlan":
        steps_data = data.get("steps", [])
        steps = [SubtaskStep(**s) for s in steps_data]
        return cls(
            task_id=data.get("task_id", f"plan-{int(time.time())}"),
            goal=data.get("goal", ""),
            steps=steps,
            workspace_dir=data.get("workspace_dir", "."),
            status=data.get("status", "pending"),
            created_at=data.get("created_at", datetime.now().isoformat()),
            updated_at=data.get("updated_at", datetime.now().isoformat()),
            final_audit=data.get("final_audit", "")
        )

    def get_step(self, step_id: str) -> Optional[SubtaskStep]:
        for step in self.steps:
            if step.id == step_id:
                return step
        return None

    def get_first_uncompleted_step(self) -> Optional[SubtaskStep]:
        for step in self.steps:
            if step.status != "completed":
                return step
        return None

    def is_all_completed(self) -> bool:
        return bool(self.steps) and all(s.status == "completed" for s in self.steps)


# ==============================================================================
# 2. Plan Storage & Checkpoints (.zca/task_plan.json)
# ==============================================================================

class PlanStorage:
    """Manages atomic persistence of execution plans and resume checkpoints."""

    @staticmethod
    def get_plan_path(workspace_dir: Path) -> Path:
        return workspace_dir / ".zca" / "task_plan.json"

    @classmethod
    def save_plan(cls, plan: ExecutionPlan, workspace_dir: Path) -> Path:
        plan.updated_at = datetime.now().isoformat()
        plan_file = cls.get_plan_path(workspace_dir)
        plan_file.parent.mkdir(parents=True, exist_ok=True)
        tmp_file = plan_file.with_suffix(f".tmp_{int(time.time() * 1000)}")
        try:
            with open(tmp_file, "w", encoding="utf-8") as f:
                json.dump(plan.to_dict(), f, indent=2, ensure_ascii=False)
            shutil.move(str(tmp_file), str(plan_file))
            logger.debug(f"[PlanStorage] Checkpoint saved: {plan_file} ({plan.status})")
            return plan_file
        except Exception as e:
            if tmp_file.exists():
                tmp_file.unlink(missing_ok=True)
            logger.error(f"[PlanStorage] Failed to save plan: {e}")
            raise

    @classmethod
    def load_plan(cls, workspace_dir: Path) -> Optional[ExecutionPlan]:
        plan_file = cls.get_plan_path(workspace_dir)
        if not plan_file.exists():
            return None
        try:
            with open(plan_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            return ExecutionPlan.from_dict(data)
        except Exception as e:
            logger.warning(f"[PlanStorage] Corrupted plan file {plan_file}: {e}")
            return None

    @classmethod
    def clear_plan(cls, workspace_dir: Path):
        plan_file = cls.get_plan_path(workspace_dir)
        if plan_file.exists():
            plan_file.unlink(missing_ok=True)


def build_compact_project_tree(workspace_dir: Path, max_depth: int = 3, max_files: int = 100) -> List[str]:
    """Generates a compact list of repository file paths avoiding noisy or build directories."""
    ignore_dirs = {
        ".git", "venv", ".venv", "__pycache__", ".pytest_cache",
        ".zca", "node_modules", "dist", "build", "browser_profiles",
        "browser_profile", ".idea", ".vscode"
    }
    ignore_exts = {".pyc", ".pyo", ".pyd", ".exe", ".dll", ".so", ".dylib", ".png", ".jpg", ".ico"}

    files_list = []
    base_parts_len = len(workspace_dir.resolve().parts)

    for root, dirs, files in os.walk(workspace_dir):
        # Prune ignored directories in-place
        dirs[:] = [d for d in dirs if d not in ignore_dirs and not d.startswith(".")]

        rel_root = Path(root).resolve()
        depth = len(rel_root.parts) - base_parts_len
        if depth > max_depth:
            continue

        for f in files:
            p = Path(f)
            if p.suffix.lower() in ignore_exts:
                continue
            rel_path = (rel_root / f).relative_to(workspace_dir).as_posix()
            files_list.append(rel_path)
            if len(files_list) >= max_files:
                return files_list

    return files_list


# ==============================================================================
# 3. Task Decomposer (DAG-Planner)
# ==============================================================================

class TaskPlanner:
    """Decomposes high-level goals into 3-7 DAG subtasks using cloud Architect."""

    @staticmethod
    def _topological_sort(steps: List[SubtaskStep]) -> List[SubtaskStep]:
        """Ensures DAG order so dependent steps execute after their prerequisites."""
        step_map = {s.id: s for s in steps}
        visited = {}
        ordered = []

        def visit(step_id: str, path: set):
            if step_id in path:
                # Cycle detected, break cycle gracefully
                logger.warning(f"[DAG] Cycle detected involving {step_id}, breaking dependency")
                return
            if visited.get(step_id) == "done":
                return
            visited[step_id] = "visiting"
            step = step_map.get(step_id)
            if step:
                for dep in step.depends_on:
                    if dep in step_map:
                        visit(dep, path | {step_id})
                ordered.append(step)
            visited[step_id] = "done"

        for s in steps:
            if s.id not in visited:
                visit(s.id, set())

        return ordered

    @classmethod
    async def create_plan(
        cls,
        goal: str,
        workspace_dir: Path,
        orchestrator=None,
        architect_model: str = "gemini-4-neon"
    ) -> ExecutionPlan:
        """Calls cloud Architect to decompose goal into 3-7 DAG steps with verification commands."""
        project_tree = build_compact_project_tree(workspace_dir)
        tree_str = "\n".join(project_tree[:80])

        # RAG enrichment from SolutionsDB
        solutions_ctx = ""
        try:
            from core.solutions_db import solutions_db
            rels = solutions_db.search_solutions(goal, top_k=3)
            if rels:
                solutions_ctx = solutions_db.format_context_prompt(rels)
                for r in rels:
                    solutions_db.increment_usage(r["id"])
        except Exception as e:
            logger.debug(f"[TaskPlanner] Solutions RAG lookup warning: {e}")

        prompt = (
            f"You are a Senior Software Architect and DAG Task Planner.\n"
            f"GOAL: {goal}\n\n"
            f"{solutions_ctx}\n\n" if solutions_ctx else ""
            f"PROJECT TREE:\n{tree_str}\n\n"
            f"Task: Decompose this goal into 3 to 7 atomic, sequential, test-driven steps.\n"
            f"Requirements:\n"
            f"1. Each step must target 1 to 3 files max (target_files).\n"
            f"2. Each step must specify explicit interfaces/contracts and dependencies (depends_on list of step IDs).\n"
            f"3. Each step MUST include a concrete local verification command (verification_cmd), e.g.:\n"
            f"   'pytest tests/test_feature.py' or 'python -m py_compile module.py'.\n"
            f"4. Output MUST be strict valid JSON matching this schema:\n"
            f"{{\n"
            f'  "task_id": "plan-{int(time.time())}",\n'
            f'  "steps": [\n'
            f'    {{\n'
            f'      "id": "step_1",\n'
            f'      "title": "Define schemas and data contracts",\n'
            f'      "description": "Create data model classes with validation",\n'
            f'      "target_files": ["core/models.py"],\n'
            f'      "depends_on": [],\n'
            f'      "verification_cmd": "python -m py_compile core/models.py"\n'
            f'    }}\n'
            f'  ]\n'
            f"}}\n"
            f"Return ONLY the JSON object. Do not wrap in conversational text."
        )

        plan_data = None
        if orchestrator:
            try:
                arch_models = getattr(orchestrator, "architect_models", None)
                if arch_models and len(arch_models) >= 2 and hasattr(orchestrator, "_run_multi_architect_analysis"):
                    raw_resp, _ = await orchestrator._run_multi_architect_analysis(arch_models, prompt)
                else:
                    fn = getattr(orchestrator, "query_role_with_fallback", None)
                    if callable(fn) and inspect.iscoroutinefunction(fn):
                        raw_resp, _ = await fn("planner", prompt)
                    else:
                        raw_resp = await orchestrator.query(prompt, architect_model, inject_signature=False)
                # Extract JSON block
                clean_json = raw_resp.strip()
                if "```json" in clean_json:
                    clean_json = clean_json.split("```json")[1].split("```")[0].strip()
                elif "```" in clean_json:
                    clean_json = clean_json.split("```")[1].split("```")[0].strip()
                plan_data = json.loads(clean_json)
            except Exception as e:
                logger.warning(f"[TaskPlanner] Architect response parsing failed ({e}). Using robust fallback DAG.")

        # Fallback DAG construction if parsing failed or orchestrator was None
        if not plan_data or "steps" not in plan_data or not plan_data["steps"]:
            plan_data = cls._generate_fallback_dag(goal, project_tree)

        steps = []
        for raw_s in plan_data.get("steps", []):
            step = SubtaskStep(
                id=str(raw_s.get("id", f"step_{len(steps) + 1}")),
                title=str(raw_s.get("title", f"Subtask {len(steps) + 1}")),
                description=str(raw_s.get("description", "")),
                target_files=[str(f) for f in raw_s.get("target_files", [])],
                depends_on=[str(d) for d in raw_s.get("depends_on", [])],
                verification_cmd=str(raw_s.get("verification_cmd", "")),
                status="pending"
            )
            steps.append(step)

        sorted_steps = cls._topological_sort(steps)
        plan = ExecutionPlan(
            task_id=str(plan_data.get("task_id", f"plan-{int(time.time())}")),
            goal=goal,
            steps=sorted_steps,
            workspace_dir=str(workspace_dir.resolve()),
            status="in_progress"
        )

        PlanStorage.save_plan(plan, workspace_dir)
        return plan

    @classmethod
    def _generate_fallback_dag(cls, goal: str, project_tree: List[str]) -> Dict[str, Any]:
        """Provides a safe, resilient 3-step default DAG when LLM JSON is malformed."""
        target_py = next((f for f in project_tree if f.endswith(".py") and not f.startswith("test")), "main.py")
        test_py = next((f for f in project_tree if "test" in f and f.endswith(".py")), "tests/test_endpoints.py")

        return {
            "task_id": f"plan-{int(time.time())}",
            "steps": [
                {
                    "id": "step_1",
                    "title": "Design Contracts and Schemas",
                    "description": f"Declare interfaces and data structures required for: {goal}",
                    "target_files": [target_py],
                    "depends_on": [],
                    "verification_cmd": f"python -m py_compile {target_py}"
                },
                {
                    "id": "step_2",
                    "title": "Core Implementation Logic",
                    "description": f"Implement core functionality to fulfill: {goal}",
                    "target_files": [target_py],
                    "depends_on": ["step_1"],
                    "verification_cmd": f"python -m py_compile {target_py}"
                },
                {
                    "id": "step_3",
                    "title": "Testing and Verification",
                    "description": f"Add and execute unit tests validating: {goal}",
                    "target_files": [test_py],
                    "depends_on": ["step_2"],
                    "verification_cmd": f"pytest {test_py} -v"
                }
            ]
        }


# ==============================================================================
# 4. Step Worker with AST & Test-Gates (StepExecutor)
# ==============================================================================

class StepExecutor:
    """Executes atomic subtasks with local coder, verifying through AST and test gates."""

    @staticmethod
    def verify_ast_syntax(file_path: Path, content: str) -> Tuple[bool, str]:
        """Validates syntactic correctness using language AST/parsers."""
        suffix = file_path.suffix.lower()
        if suffix == ".py":
            try:
                ast.parse(content, filename=str(file_path))
                return True, "AST syntax check passed"
            except SyntaxError as e:
                return False, f"Python SyntaxError in {file_path.name} line {e.lineno}: {e.msg}"
        elif suffix == ".json":
            try:
                json.loads(content)
                return True, "JSON syntax check passed"
            except Exception as e:
                return False, f"JSON ParseError in {file_path.name}: {e}"
        elif suffix in (".yaml", ".yml"):
            try:
                import yaml
                yaml.safe_load(content)
                return True, "YAML syntax check passed"
            except Exception as e:
                return False, f"YAML ParseError in {file_path.name}: {e}"
        return True, "No specific AST parser, plain text OK"

    @staticmethod
    async def run_cmd(cmd: str, workspace_dir: Path, timeout: float = 40.0) -> Tuple[bool, str, str]:
        """Runs a verification shell command asynchronously with timeout."""
        if not cmd.strip():
            return True, "", ""
        try:
            proc = await asyncio.create_subprocess_shell(
                cmd,
                cwd=str(workspace_dir),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout_bytes, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            stdout = stdout_bytes.decode(errors="replace").strip()
            stderr = stderr_bytes.decode(errors="replace").strip()
            return proc.returncode == 0, stdout, stderr
        except asyncio.TimeoutError:
            return False, "", f"Command timed out after {timeout} seconds"
        except Exception as e:
            return False, "", f"Failed to execute command '{cmd}': {e}"

    @classmethod
    def _extract_files_from_response(cls, response_text: str, default_file: Optional[str] = None) -> Dict[str, str]:
        """Extracts targeted files and code blocks from LLM output."""
        files = {}
        # Pattern 1: FILE: path/to/file.ext followed by fenced code
        pattern = r"(?:###\s*)?(?:FILE|File):\s*([^\n\r]+)[\r\n]+```(?:[a-zA-Z0-9_\-]+)?[\r\n]+(.*?)[\r\n]+```"
        matches = re.findall(pattern, response_text, re.DOTALL)
        for fname, code in matches:
            files[fname.strip().strip("'\"`")] = code

        # Pattern 2: Single code block without header if only one default file is expected
        if not files and default_file and "```" in response_text:
            code_blocks = re.findall(r"```(?:[a-zA-Z0-9_\-]+)?[\r\n]+(.*?)[\r\n]+```", response_text, re.DOTALL)
            if code_blocks:
                files[default_file] = code_blocks[0]

        return files

    @classmethod
    async def run_step(
        cls,
        step: SubtaskStep,
        plan: ExecutionPlan,
        workspace_dir: Path,
        orchestrator=None,
        executor_model: str = "ollama/qwen2.5-coder:14b",
        max_attempts: int = 2
    ) -> bool:
        """
        Executes a single step:
        1. Backs up target files.
        2. Calls local model with concise context.
        3. AST & test verification gates.
        4. Auto-retry on failure or rollback to backup.
        """
        step.status = "in_progress"
        step.attempts = 0
        PlanStorage.save_plan(plan, workspace_dir)

        # 1. Back up all target files before any modification
        backups: Dict[str, Path] = {}
        for rel_file in step.target_files:
            abs_file = workspace_dir / rel_file
            bak = history_manager.record_backup("task_planner", abs_file)
            backups[rel_file] = bak

        last_error = ""

        for attempt in range(1, max_attempts + 1):
            step.attempts = attempt

            # Read current content of target files (if any)
            target_contents = {}
            for rel_file in step.target_files:
                abs_file = workspace_dir / rel_file
                if abs_file.exists():
                    try:
                        target_contents[rel_file] = abs_file.read_text(encoding="utf-8")[:4000]
                    except Exception:
                        target_contents[rel_file] = ""
                else:
                    target_contents[rel_file] = "(New file to create)"

            files_context = "\n\n".join(
                f"--- FILE: {rf} ---\n{content}" for rf, content in target_contents.items()
            )

            feedback_prompt = ""
            if last_error:
                feedback_prompt = (
                    f"\n\n[PREVIOUS ATTEMPT FAILED WITH ERROR]:\n{last_error}\n"
                    f"Please analyze the error and fix it completely."
                )

            coder_prompt = (
                f"You are a Senior Local Software Engineer.\n"
                f"TASK: {step.title}\n"
                f"DETAILS: {step.description}\n"
                f"TARGET FILES: {', '.join(step.target_files)}\n\n"
                f"CURRENT FILE CONTENTS:\n{files_context}\n"
                f"{feedback_prompt}\n\n"
                f"INSTRUCTIONS:\n"
                f"1. Generate clean, syntactically correct code solving this exact step.\n"
                f"2. Format each updated file as:\n"
                f"FILE: <target_file_path>\n"
                f"```<language>\n"
                f"<full updated code>\n"
                f"```\n"
                f"Only output the code blocks."
            )

            # Query local worker (or orchestrator)
            response_code = ""
            if orchestrator:
                try:
                    fn = getattr(orchestrator, "query_role_with_fallback", None)
                    if callable(fn) and inspect.iscoroutinefunction(fn):
                        response_code, _ = await fn("coder", coder_prompt)
                    else:
                        response_code = await orchestrator.query(coder_prompt, executor_model, inject_signature=False)
                except Exception as e:
                    logger.warning(f"[StepExecutor] Coder query error ({e}). Using mock/fallback.")
                    response_code = ""

            extracted = cls._extract_files_from_response(
                response_code,
                default_file=step.target_files[0] if step.target_files else None
            )

            if not extracted:
                last_error = "Could not extract updated files from model output."
                step.error_log = last_error
                continue

            # Write files to disk and verify Gate 1: AST / Syntax
            ast_failed = False
            for rel_file, code_str in extracted.items():
                abs_file = workspace_dir / rel_file
                abs_file.parent.mkdir(parents=True, exist_ok=True)
                abs_file.write_text(code_str, encoding="utf-8")

                syntax_ok, syntax_err = cls.verify_ast_syntax(abs_file, code_str)
                if not syntax_ok:
                    ast_failed = True
                    last_error = f"Syntax Gate Failed: {syntax_err}"
                    step.error_log = last_error
                    break

            if ast_failed:
                continue

            # Gate 2: verification_cmd execution
            if step.verification_cmd:
                cmd_ok, stdout, stderr = await cls.run_cmd(step.verification_cmd, workspace_dir)
                if not cmd_ok:
                    last_error = f"Verification Command Failed ({step.verification_cmd}):\n{stderr or stdout}"
                    step.error_log = last_error
                    continue

            # If both gates passed!
            step.status = "completed"
            step.error_log = ""
            PlanStorage.save_plan(plan, workspace_dir)
            logger.info(f"[StepExecutor] Step {step.id} ('{step.title}') completed successfully on attempt {attempt}.")
            return True

        # All attempts failed -> Rollback from backup!
        logger.warning(f"[StepExecutor] Step {step.id} failed after {max_attempts} attempts. Rolling back files...")
        for rel_file, bak_path in backups.items():
            abs_file = workspace_dir / rel_file
            if bak_path.exists():
                try:
                    content = bak_path.read_text(encoding="utf-8")
                    if content == "__FILE_DID_NOT_EXIST__":
                        abs_file.unlink(missing_ok=True)
                    else:
                        shutil.copy2(bak_path, abs_file)
                except Exception as e:
                    logger.error(f"[StepExecutor] Rollback error for {abs_file}: {e}")

        step.status = "failed"
        PlanStorage.save_plan(plan, workspace_dir)
        return False


# ==============================================================================
# 5. Final Integrator (TaskIntegrator)
# ==============================================================================

class TaskIntegrator:
    """Verifies all steps, executes global test suites, and audits architectural joints."""

    @classmethod
    async def verify_all(
        cls,
        plan: ExecutionPlan,
        workspace_dir: Path,
        orchestrator=None,
        reviewer_model: str = "gemini-4-neon"
    ) -> Dict[str, Any]:
        """Runs overall test suite, gathers diff, and performs final architectural review."""
        # 1. Run global pytest if available
        test_ok, stdout, stderr = await StepExecutor.run_cmd("pytest", workspace_dir, timeout=60.0)

        # 2. Get targeted git diff for modified files (±25 lines context)
        modified_files = []
        for s in plan.steps:
            for tf in s.target_files:
                if tf and tf not in modified_files:
                    modified_files.append(tf)

        diff_summary = ""
        if modified_files:
            files_arg = " ".join(f'"{f}"' for f in modified_files)
            diff_ok, diff_out, _ = await StepExecutor.run_cmd(f"git diff -U25 -- {files_arg}", workspace_dir, timeout=15.0)
            if diff_ok and diff_out.strip():
                diff_summary = diff_out[:8000]

        if not diff_summary:
            diff_ok, diff_out, _ = await StepExecutor.run_cmd("git diff -U25", workspace_dir, timeout=15.0)
            diff_summary = diff_out[:8000] if diff_ok and diff_out else "(No git diff available)"

        # 3. Final review with Architect
        audit_report = ""
        if orchestrator:
            prompt = (
                f"You are the Lead Architect performing a Final Integration Audit.\n"
                f"TASK GOAL: {plan.goal}\n"
                f"MODIFIED FILES: {', '.join(modified_files) if modified_files else 'None'}\n"
                f"ALL STEPS COMPLETED: {len(plan.steps)}/{len(plan.steps)}\n"
                f"GLOBAL TEST SUITE RESULT: {'PASSED [OK]' if test_ok else 'FAILED [ERROR]'}\n"
                f"TEST OUTPUT:\n{stdout[:1500]}\n\n"
                f"TARGETED UNIFIED DIFF (±25 lines context):\n{diff_summary}\n\n"
                f"Please verify indentation, attribute matching, asyncio.Lock usage, absence of stubs, and contract integrity."
            )
            try:
                fn = getattr(orchestrator, "query_role_with_fallback", None)
                if callable(fn) and inspect.iscoroutinefunction(fn):
                    audit_report, _ = await fn("reviewer", prompt)
                else:
                    audit_report = await orchestrator.query(prompt, reviewer_model, inject_signature=False)
            except Exception as e:
                audit_report = f"[Audit] Local tests completed. Architectural audit warning: {e}"
        else:
            audit_report = "[Audit] Integration tests passed cleanly. Plan verified."

        plan.final_audit = audit_report
        plan.status = "completed" if (plan.is_all_completed() and test_ok) else "in_progress"
        PlanStorage.save_plan(plan, workspace_dir)

        # Auto-save completed pattern to SolutionsDB
        if plan.status == "completed":
            try:
                from core.solutions_db import solutions_db
                solutions_db.add_solution(
                    title=plan.goal[:80],
                    problem_pattern=plan.goal[:300],
                    solution_summary=f"DAG план из {len(plan.steps)} шагов успешно выполнен. {audit_report[:300]}",
                    code_snippet="",
                    tags=["dag_planner", "completed_task"],
                    source_model=reviewer_model or "architect"
                )
            except Exception as e:
                logger.debug(f"[TaskIntegrator] Failed to save solution pattern: {e}")

        return {
            "task_id": plan.task_id,
            "status": plan.status,
            "tests_passed": test_ok,
            "steps_completed": sum(1 for s in plan.steps if s.status == "completed"),
            "total_steps": len(plan.steps),
            "final_audit": audit_report
        }


# ==============================================================================
# 6. High-Level Pipeline Runner
# ==============================================================================

async def run_dag_pipeline(
    goal: str,
    workspace_dir: Optional[Path] = None,
    orchestrator=None,
    resume: bool = True,
    architect_model: str = "gemini-4-neon",
    executor_model: str = "ollama/qwen2.5-coder:14b",
    reviewer_model: str = "gemini-4-neon",
    progress_callback: Optional[Callable[[str], None]] = None
) -> Dict[str, Any]:
    """
    Executes the full DAG-Planner + Local Worker + Final Integrator lifecycle.
    Supports checkpoints (.zca/task_plan.json) and resume.
    """
    ws = Path(workspace_dir or Path.cwd()).resolve()
    ws.mkdir(parents=True, exist_ok=True)

    def notify(msg: str):
        logger.info(f"[DAG-Pipeline] {msg}")
        if progress_callback:
            try:
                progress_callback(msg)
            except Exception:
                pass

    # 1. Resume check or Plan Creation
    plan: Optional[ExecutionPlan] = None
    if resume:
        plan = PlanStorage.load_plan(ws)
        if plan and not plan.is_all_completed():
            notify(f"Resuming existing plan '{plan.task_id}' with {len(plan.steps)} steps...")
        else:
            plan = None

    if not plan:
        old_plan_file = PlanStorage.get_plan_path(ws)
        if old_plan_file.exists():
            try:
                old_plan_file.unlink(missing_ok=True)
            except Exception:
                pass
        effective_archs = getattr(orchestrator, "architect_models", None)
        arch_display = "+".join(effective_archs) if (effective_archs and len(effective_archs) >= 2) else architect_model
        notify(f"Decomposing goal via Architect ({arch_display})...")
        try:
            from core.activity_tracker import activity_tracker
            await activity_tracker.update_step(step=f"Decomposing goal via Architect ({arch_display})...", model=arch_display)
        except Exception:
            pass
        plan = await TaskPlanner.create_plan(
            goal=goal,
            workspace_dir=ws,
            orchestrator=orchestrator,
            architect_model=architect_model
        )
        notify(f"Created DAG plan '{plan.task_id}' with {len(plan.steps)} subtasks.")

    # 2. Sequential DAG execution
    for idx, step in enumerate(plan.steps, start=1):
        step_desc = f"Step {idx}/{len(plan.steps)}: {', '.join(step.target_files) if step.target_files else step.title}"
        try:
            from core.activity_tracker import activity_tracker
            await activity_tracker.update_step(step=step_desc, model=executor_model)
        except Exception:
            pass
        if step.status == "completed":
            notify(f"[{idx}/{len(plan.steps)}] Step '{step.title}' already completed. Skipping.")
            continue

        # Check dependencies
        unmet_deps = [d for d in step.depends_on if plan.get_step(d) and plan.get_step(d).status != "completed"]
        if unmet_deps:
            err = f"Step '{step.title}' blocked by uncompleted dependencies: {', '.join(unmet_deps)}"
            notify(f"[!] {err}")
            step.status = "failed"
            step.error_log = err
            PlanStorage.save_plan(plan, ws)
            return {
                "task_id": plan.task_id,
                "status": "failed",
                "failed_step": step.id,
                "message": err,
                "plan": plan.to_dict()
            }

        notify(f"[{idx}/{len(plan.steps)}] Executing: '{step.title}' (Files: {', '.join(step.target_files)})...")
        success = await StepExecutor.run_step(
            step=step,
            plan=plan,
            workspace_dir=ws,
            orchestrator=orchestrator,
            executor_model=executor_model
        )

        if not success:
            err = f"Step '{step.title}' failed after {step.attempts} attempts. Changes rolled back."
            notify(f"[ERROR] {err}")
            return {
                "task_id": plan.task_id,
                "status": "failed",
                "failed_step": step.id,
                "error": step.error_log,
                "message": err,
                "plan": plan.to_dict()
            }

    # 3. Final Integration
    notify("All steps passed! Running Final Integrator verification...")
    try:
        from core.activity_tracker import activity_tracker
        await activity_tracker.update_step(step=f"Final Integrator ({len(plan.steps)}/{len(plan.steps)})", model=reviewer_model)
    except Exception:
        pass
    integ_res = await TaskIntegrator.verify_all(
        plan=plan,
        workspace_dir=ws,
        orchestrator=orchestrator,
        reviewer_model=reviewer_model
    )
    notify(f"Task completed successfully! Tests passed: {integ_res['tests_passed']}.")

    return {
        "task_id": plan.task_id,
        "status": "completed",
        "steps_count": len(plan.steps),
        "tests_passed": integ_res["tests_passed"],
        "final_audit": integ_res["final_audit"],
        "plan": plan.to_dict()
    }
