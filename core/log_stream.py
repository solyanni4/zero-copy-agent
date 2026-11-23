"""
zero-copy-agent Log Streamer & Unified Orchestrator Logger.
Provides standardized [ORCHESTRATOR] logging format, suppresses noisy background polls,
and broadcasts log lines over Server-Sent Events (SSE).
"""
import logging
import time
import asyncio
from typing import Dict, Any, List, Optional, Set
from collections import deque

logger = logging.getLogger("zcao.orchestrator")

# In-memory circular buffer of recent logs
LOG_BUFFER: deque = deque(maxlen=500)
_LOG_LISTENERS: Set[asyncio.Queue] = set()


class BackgroundPollFilter(logging.Filter):
    """
    Suppresses repetitive background health and tag polling logs from INFO to DEBUG
    to keep the live log console clean and readable.
    """
    IGNORED_PATHS = ("/api/tags", "/health", "/api/activity/stream", "/api/activity/status", "/api/logs/stream")

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        if any(p in msg for p in self.IGNORED_PATHS):
            if "GET " in msg and " 200" in msg:
                # Lower level to DEBUG so it doesn't appear in default INFO console
                record.levelno = logging.DEBUG
                record.levelname = "DEBUG"
        return True


class SSELogHandler(logging.Handler):
    """Logging handler that captures formatted log records for SSE streaming."""
    def emit(self, record: logging.LogRecord):
        try:
            msg = self.format(record)
            entry = {
                "time": time.strftime("%H:%M:%S", time.localtime(record.created)),
                "level": record.levelname,
                "name": record.name,
                "message": msg
            }
            LOG_BUFFER.append(entry)

            # Broadcast to active SSE listeners
            dead = []
            for q in list(_LOG_LISTENERS):
                try:
                    q.put_nowait(entry)
                except Exception:
                    dead.append(q)
            for q in dead:
                _LOG_LISTENERS.discard(q)
        except Exception:
            pass


def subscribe_logs() -> asyncio.Queue:
    """Subscribes an SSE listener queue for live log streaming."""
    q: asyncio.Queue = asyncio.Queue()
    _LOG_LISTENERS.add(q)
    return q


def unsubscribe_logs(q: asyncio.Queue):
    """Unsubscribes a log SSE listener."""
    _LOG_LISTENERS.discard(q)


def get_recent_log_entries(limit: int = 150) -> List[Dict[str, Any]]:
    """Returns the most recent in-memory log entries."""
    return list(LOG_BUFFER)[-limit:]


# ==============================================================================
# Unified [ORCHESTRATOR] Event Logging Format
# ==============================================================================

def log_orchestrator_start(mode: str, task: str):
    """Logs standardized combat start event."""
    task_clean = (task or "").strip().replace("\n", " ")
    if len(task_clean) > 80:
        task_clean = task_clean[:77] + "..."
    msg = f"[ORCHESTRATOR] ▶ СТАРТ | Режим: {mode} | Задача: {task_clean}"
    logger.info(msg)


def log_orchestrator_stage(stage: str, model: str, account: str = "", status: str = "", source: str = ""):
    """Logs standardized stage execution event."""
    icons = {
        "architect": "🧠 Архитектор",
        "planner": "🧠 Архитектор",
        "planning": "🧠 Архитектор",
        "multi_architect": "🧠 Мульти-Архитектор",
        "consensus": "🤝 Консенсус",
        "coder": "💻 Исполнитель",
        "executor": "💻 Исполнитель",
        "coding": "💻 Исполнитель",
        "gate": "⚙️ Hard Gate",
        "audit": "🛡️ Аудит",
        "reviewer": "🛡️ Аудит"
    }
    stage_name = icons.get(stage.lower(), f"⚡ {stage.capitalize()}")
    acc_str = f" ({account})" if account else ""
    if source and not account:
        acc_str = f" ({source})"
    status_str = f" | Статус: {status}" if status else ""
    msg = f"[ORCHESTRATOR] {stage_name} | Модель: {model}{acc_str}{status_str}"
    logger.info(msg)


def log_orchestrator_done(duration_sec: float, savings_pct: float = 0.0, summary: str = ""):
    """Logs standardized completion event."""
    savings_str = f" | Экономия: {int(savings_pct)}%" if savings_pct > 0 else ""
    sum_str = f" | Результат: {summary}" if summary else ""
    msg = f"[ORCHESTRATOR] ✅ ГОТОВО | Затрачено: {duration_sec:.1f}s{savings_str}{sum_str}"
    logger.info(msg)
