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
        details: Optional[Dict[str, Any]] = None
    ):
        """Marks agent as working on a specific tool/task and notifies listeners."""
        async with self._lock:
            now = time.time()
            self._state.state = "working"
            self._state.tool = tool
            self._state.model = model
            self._state.step = step or "Initializing..."
            self._state.started_at = now
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
        details: Optional[Dict[str, Any]] = None
    ):
        """Updates progress step during multi-step tasks."""
        async with self._lock:
            self._state.step = step
            if model:
                self._state.model = model
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
