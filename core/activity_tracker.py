"""
zero-copy-agent Activity Tracker
Tracks live MCP execution state, DAG planning progress, and broadcasts SSE events.
"""
import asyncio
import time
import logging
from typing import Optional, Dict, Any, Set
from dataclasses import dataclass, field

logger = logging.getLogger("zero-copy-agent.activity")


@dataclass
class ActivityState:
    state: str = "idle"  # "idle" | "working"
    tool: Optional[str] = None
    step: Optional[str] = None
    model: Optional[str] = None
    started_at: Optional[float] = None
    stage: Optional[str] = None         # "planning" | "coding" | "audit" | "consensus"
    stage_label: Optional[str] = None   # "Планирование" | "Кодинг" | "Аудит" | "Синтез"
    source: Optional[str] = None        # "API Key" | "Web" | "Local GPU"
    account: Optional[str] = None       # e.g. "gemini-2"
    fallback_active: bool = False
    fallback_reason: Optional[str] = None
    parallel_architects: Optional[list] = None
    task_summary: Optional[str] = None
    last_completed: Optional[Dict[str, Any]] = None
    history: list = field(default_factory=list)  # Recent 10 human-friendly transitions
    trace: str = ""                              # e.g. "Анализ: gemini-4-neon → Код: qwen2.5-coder:14b"

    def to_dict(self) -> Dict[str, Any]:
        elapsed = 0.0
        if self.started_at and self.state == "working":
            elapsed = round(time.time() - self.started_at, 1)
        return {
            "state": self.state,
            "tool": self.tool,
            "step": self.step,
            "model": self.model,
            "started_at": self.started_at,
            "elapsed_seconds": elapsed,
            "stage": self.stage,
            "stage_label": self.stage_label,
            "source": self.source,
            "account": self.account,
            "fallback_active": self.fallback_active,
            "fallback_reason": self.fallback_reason,
            "parallel_architects": self.parallel_architects,
            "task_summary": self.task_summary,
            "last_completed": self.last_completed,
            "history": list(self.history)[-10:],
            "trace": self.trace,
        }


class ActivityTracker:
    def __init__(self):
        self._state = ActivityState()
        self._listeners: Set[asyncio.Queue] = set()
        self._lock = asyncio.Lock()
        self._trace_map = {"analysis": "", "code": "", "audit": ""}

    def get_status(self) -> Dict[str, Any]:
        """Returns snapshot of current state dictionary."""
        return self._state.to_dict()

    def get_trace(self, as_badge: bool = True) -> str:
        """Returns current accumulated execution trace."""
        return self._build_trace(as_badge=as_badge)

    def _build_trace(self, as_badge: bool = True) -> str:
        parts = []
        arch_label = "Анализ" if as_badge else "Архитектор"
        if self._trace_map.get("analysis"):
            parts.append(f"{arch_label}: {self._trace_map['analysis']}")
        if self._trace_map.get("code"):
            parts.append(f"Код: {self._trace_map['code']}")
        if self._trace_map.get("audit"):
            parts.append(f"Аудит: {self._trace_map['audit']}")
        return " → ".join(parts)

    def _add_history_entry(self, message: str, event_type: str = "info"):
        now_str = time.strftime("%H:%M:%S")
        entry = {
            "time": now_str,
            "message": message,
            "type": event_type
        }
        self._state.history.append(entry)
        if len(self._state.history) > 10:
            self._state.history = self._state.history[-10:]

    def set_stage_model(self, stage: str, model: str):
        """Directly sets the actual resolved model for a pipeline stage."""
        stage_key = "analysis" if stage in ("planner", "planning") else ("code" if stage in ("coder", "coding") else ("audit" if stage in ("reviewer", "audit") else stage))
        if stage_key in self._trace_map and model:
            self._trace_map[stage_key] = model
            self._state.trace = self._build_trace()

    async def record_fallback(
        self,
        failed_model: str,
        next_model: str,
        reason: str = "",
        stage: Optional[str] = None
    ):
        """Records a failover/fallback transition in history and broadcasts event."""
        reason_str = f" ({reason})" if reason else ""
        msg = f"⚠️ Модель {failed_model} недоступна{reason_str} → переключено на {next_model}"
        async with self._lock:
            self._state.fallback_active = True
            self._state.fallback_reason = reason or "fallback"
            self._state.model = next_model
            if stage:
                stage_key = "analysis" if stage in ("planner", "planning") else ("code" if stage in ("coder", "coding") else ("audit" if stage in ("reviewer", "audit") else stage))
                if stage_key in self._trace_map:
                    self._trace_map[stage_key] = next_model
            self._state.trace = self._build_trace()
            self._add_history_entry(msg, "fallback")
            event_data = self._state.to_dict()
            event = {
                "event": "fallback",
                "data": event_data
            }
        logger.warning(f"[Activity] Fallback: {msg}")
        await self._broadcast(event)

    async def start_activity(
        self,
        tool: str,
        model: Optional[str] = None,
        step: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
        stage: Optional[str] = None,
        stage_label: Optional[str] = None,
        source: Optional[str] = None,
        account: Optional[str] = None
    ):
        """Marks agent as working on a specific tool/task and notifies listeners."""
        async with self._lock:
            now = time.time()
            self._state.state = "working"
            self._state.tool = tool
            self._state.model = model
            self._state.started_at = now
            effective_stage = stage or (details.get("stage") if details else None)
            self._state.stage = effective_stage
            self._state.stage_label = stage_label or (details.get("stage_label") if details else None)
            self._state.source = source or (details.get("source") if details else None)
            self._state.account = account or (details.get("account") if details else None)
            self._state.fallback_active = False
            self._state.fallback_reason = None
            if details and "parallel_architects" in details:
                self._state.parallel_architects = details["parallel_architects"]

            # Initialize / update trace map
            self._trace_map = {"analysis": "", "code": "", "audit": ""}

            # Build human-friendly step & history entry
            if details and details.get("parallel_architects"):
                archs = details["parallel_architects"]
                if isinstance(archs, list):
                    arch_names = [a.get("model", str(a)) if isinstance(a, dict) else str(a) for a in archs]
                    archs_str = ", ".join(arch_names)
                else:
                    archs_str = str(archs)
                msg = f"🧠 Параллельный анализ — модели: {archs_str}"
                self._trace_map["analysis"] = archs_str
                self._state.step = msg
                self._add_history_entry(msg, "planning")
            elif effective_stage == "planning" or tool in ("cascade_architect", "plan_and_execute_task"):
                m_str = model or "автовыбор"
                msg = f"🧠 Выполняется анализ запроса — модель: {m_str}"
                if model:
                    self._trace_map["analysis"] = model
                self._state.step = step or msg
                self._add_history_entry(msg, "planning")
            elif effective_stage == "coding" or tool == "local_gpu_code":
                m_str = model or "локальная модель"
                msg = f"💻 Генерация кода — модель: {m_str}"
                if model:
                    self._trace_map["code"] = model
                self._state.step = step or msg
                self._add_history_entry(msg, "coding")
            else:
                self._state.step = step or "Инициализация..."
                self._add_history_entry(self._state.step, "start")

            self._state.trace = self._build_trace()

            event_data = self._state.to_dict()
            if details:
                event_data["details"] = details
            event = {
                "event": "start",
                "data": event_data
            }
        logger.info(f"[Activity] Started: {tool} ({model}) - {self._state.step}")
        await self._broadcast(event)

    async def update_step(
        self,
        step: str,
        model: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
        stage: Optional[str] = None,
        stage_label: Optional[str] = None,
        source: Optional[str] = None,
        account: Optional[str] = None,
        fallback_active: Optional[bool] = None,
        fallback_reason: Optional[str] = None
    ):
        """Updates progress step during multi-step tasks."""
        async with self._lock:
            if model:
                self._state.model = model
            effective_stage = stage or (details.get("stage") if details else self._state.stage)
            if effective_stage:
                self._state.stage = effective_stage
            if stage_label:
                self._state.stage_label = stage_label
            elif details and "stage_label" in details:
                self._state.stage_label = details["stage_label"]
            if source:
                self._state.source = source
            elif details and "source" in details:
                self._state.source = details["source"]
            if account:
                self._state.account = account
            elif details and "account" in details:
                self._state.account = details["account"]
            if fallback_active is not None:
                self._state.fallback_active = fallback_active
            elif details and "fallback_active" in details:
                self._state.fallback_active = details["fallback_active"]
            if fallback_reason is not None:
                self._state.fallback_reason = fallback_reason
            elif details and "fallback_reason" in details:
                self._state.fallback_reason = details["fallback_reason"]
            if details and "parallel_architects" in details:
                self._state.parallel_architects = details["parallel_architects"]

            # Map stage to human-friendly Russian messages
            if details and details.get("parallel_architects"):
                archs = details["parallel_architects"]
                if isinstance(archs, list):
                    arch_names = [a.get("model", str(a)) if isinstance(a, dict) else str(a) for a in archs]
                    archs_str = ", ".join(arch_names)
                else:
                    archs_str = str(archs)
                msg = f"🧠 Параллельный анализ — модели: {archs_str}"
                self._trace_map["analysis"] = archs_str
                self._state.step = msg
                self._add_history_entry(msg, "planning")
            elif effective_stage == "planning":
                curr_model = model or self._state.model or "автовыбор"
                msg = f"🧠 Выполняется анализ запроса — модель: {curr_model}"
                if model:
                    self._trace_map["analysis"] = model
                self._state.step = step or msg
                self._add_history_entry(msg, "planning")
            elif effective_stage == "coding":
                curr_model = model or self._state.model or "локальная модель"
                msg = f"💻 Генерация кода — модель: {curr_model}"
                if model:
                    self._trace_map["code"] = model
                self._state.step = step or msg
                self._add_history_entry(msg, "coding")
            elif effective_stage == "audit":
                curr_model = model or self._state.model or "локальная модель"
                msg = f"🔍 Аудит — модель: {curr_model}"
                if model:
                    self._trace_map["audit"] = model
                self._state.step = step or msg
                self._add_history_entry(msg, "audit")
            else:
                self._state.step = step
                self._add_history_entry(step, "progress")

            self._state.trace = self._build_trace()

            event_data = self._state.to_dict()
            if details:
                event_data["details"] = details
            event = {
                "event": "progress",
                "data": event_data
            }
        logger.info(f"[Activity] Step: {self._state.tool} -> {self._state.step}")
        await self._broadcast(event)

    async def finish_activity(
        self,
        success: bool = True,
        summary: Optional[str] = None,
        error: Optional[str] = None,
        tokens: int = 0
    ):
        """Reverts state back to idle, records last completion metadata, and notifies listeners."""
        async with self._lock:
            now = time.time()
            started = self._state.started_at or now
            elapsed = round(max(0.0, now - started), 2)
            trace_desc = self._build_trace(as_badge=False)

            if success:
                trace_part = f" ({trace_desc})" if trace_desc else ""
                finish_msg = f"✅ Выполнено за {elapsed}s{trace_part}"
                self._add_history_entry(finish_msg, "done")
            else:
                finish_msg = f"❌ Ошибка: {error or summary or 'Сбой выполнения'}"
                self._add_history_entry(finish_msg, "error")

            last_comp = {
                "tool": self._state.tool,
                "model": self._state.model,
                "step": self._state.step,
                "elapsed_seconds": elapsed,
                "tokens": tokens,
                "success": success,
                "summary": summary or finish_msg,
                "error": error,
                "completed_at": now,
                "trace": trace_desc,
            }
            self._state.last_completed = last_comp
            self._state.state = "idle"
            self._state.tool = None
            self._state.step = None
            self._state.model = None
            self._state.started_at = None

            event_type = "done" if success else "error"
            event = {
                "event": event_type,
                "data": {
                    **self._state.to_dict(),
                    "completed": last_comp
                }
            }
        logger.info(f"[Activity] Finished: {last_comp['tool']} (took {elapsed}s, success={success})")
        await self._broadcast(event)

    def subscribe(self) -> asyncio.Queue:
        """Subscribes an SSE listener queue."""
        q: asyncio.Queue = asyncio.Queue()
        self._listeners.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue):
        """Unsubscribes a listener queue."""
        self._listeners.discard(q)

    async def _broadcast(self, event: Dict[str, Any]):
        """Pushes event to all active listener queues."""
        dead = []
        for q in list(self._listeners):
            try:
                q.put_nowait(event)
            except Exception:
                dead.append(q)
        for q in dead:
            self._listeners.discard(q)


# Global singleton instance
activity_tracker = ActivityTracker()
