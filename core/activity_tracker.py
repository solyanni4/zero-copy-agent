"""
zero-copy-agent Activity Tracker
Tracks live MCP execution state, DAG planning progress, and broadcasts SSE events.
"""
import asyncio
import time
import logging
from typing import Optional, Dict, Any, Set
from dataclasses import dataclass

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
        }


class ActivityTracker:
    def __init__(self):
        self._state = ActivityState()
        self._listeners: Set[asyncio.Queue] = set()
        self._lock = asyncio.Lock()

    def get_status(self) -> Dict[str, Any]:
        """Returns snapshot of current state dictionary."""
        return self._state.to_dict()

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
            self._state.step = step or "Инициализация..."
            self._state.started_at = now
            self._state.stage = stage or (details.get("stage") if details else None)
            self._state.stage_label = stage_label or (details.get("stage_label") if details else None)
            self._state.source = source or (details.get("source") if details else None)
            self._state.account = account or (details.get("account") if details else None)
            self._state.fallback_active = False
            self._state.fallback_reason = None
            if details and "parallel_architects" in details:
                self._state.parallel_architects = details["parallel_architects"]
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
            self._state.step = step
            if model:
                self._state.model = model
            if stage:
                self._state.stage = stage
            elif details and "stage" in details:
                self._state.stage = details["stage"]
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

            event_data = self._state.to_dict()
            if details:
                event_data["details"] = details
            event = {
                "event": "progress",
                "data": event_data
            }
        logger.info(f"[Activity] Step: {self._state.tool} -> {step}")
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
            last_comp = {
                "tool": self._state.tool,
                "model": self._state.model,
                "step": self._state.step,
                "elapsed_seconds": elapsed,
                "tokens": tokens,
                "success": success,
                "summary": summary or ("Завершено успешно" if success else "Ошибка выполнения"),
                "error": error,
                "completed_at": now
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
