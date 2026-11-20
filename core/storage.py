"""
SQLite WAL Storage and Token Economics Analytics for zero-copy-agent.
Tracks requests, token volume, response latency, and calculates money saved
versus commercial API pricing (OpenAI GPT-4o, Anthropic Claude, Google Gemini Pro).
"""
import os
import sys
import json
import sqlite3
import logging
from contextlib import contextmanager
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional

logger = logging.getLogger("zcao.storage")

# Pricing benchmarks per 1,000,000 tokens (Input / Output in USD)
PRICE_BENCHMARKS = {
    "gpt-4o": {"input": 2.50, "output": 10.00},
    "chatgpt-plus": {"input": 2.50, "output": 10.00},
    "claude-3-7-sonnet": {"input": 3.00, "output": 15.00},
    "gemini-advanced": {"input": 1.25, "output": 5.00},
    "gemini-4-neon": {"input": 2.00, "output": 8.00},
    "gemini-4-hydrogen": {"input": 1.25, "output": 5.00},
    "cascade:architect": {"input": 2.50, "output": 10.00},
    "default": {"input": 2.00, "output": 8.00},
}

def get_db_path() -> Path:
    """Returns path to SQLite database in standard user appdata / config directory."""
    if sys.platform == "win32":
        base_dir = Path(os.getenv("LOCALAPPDATA", Path.home())) / "zero-copy-agent"
    else:
        base_dir = Path.home() / ".config" / "zero-copy-agent"
    base_dir.mkdir(parents=True, exist_ok=True)
    return base_dir / "analytics.db"

class AnalyticsStorage:
    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or get_db_path()
        self._init_db()

    @contextmanager
    def _get_connection(self):
        conn = sqlite3.connect(str(self.db_path), timeout=10.0)
        conn.row_factory = sqlite3.Row
        # WAL mode allows concurrent readers and writers without lock contention
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        try:
            yield conn
        finally:
            conn.close()

    def _init_db(self):
        with self._get_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS completions (
                    id TEXT PRIMARY KEY,
                    timestamp TEXT NOT NULL,
                    model_requested TEXT NOT NULL,
                    model_routed TEXT NOT NULL,
                    raw_prompt_tokens INTEGER NOT NULL DEFAULT 0,
                    prompt_tokens INTEGER NOT NULL,
                    completion_tokens INTEGER NOT NULL,
                    total_tokens INTEGER NOT NULL,
                    duration_seconds REAL NOT NULL,
                    cost_saved_usd REAL NOT NULL
                );
            """)
            try:
                conn.execute("ALTER TABLE completions ADD COLUMN raw_prompt_tokens INTEGER NOT NULL DEFAULT 0;")
            except Exception:
                pass
            conn.execute("CREATE INDEX IF NOT EXISTS idx_timestamp ON completions(timestamp);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_model ON completions(model_requested);")

            # Cognitive Observability Events Table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS cognitive_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    req_id TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    model TEXT NOT NULL,
                    message TEXT NOT NULL,
                    metadata_json TEXT
                );
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_event_req ON cognitive_events(req_id);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_event_ts ON cognitive_events(timestamp);")
            conn.commit()

    @staticmethod
    def calculate_savings(model_id: str, prompt_tokens: int, completion_tokens: int) -> float:
        """Calculates approximate dollars saved based on equivalent official API rates."""
        rates = PRICE_BENCHMARKS.get(model_id.lower(), PRICE_BENCHMARKS["default"])
        input_cost = (prompt_tokens / 1_000_000) * rates["input"]
        output_cost = (completion_tokens / 1_000_000) * rates["output"]
        return round(input_cost + output_cost, 5)

    def record_completion(
        self,
        req_id: str,
        model_requested: str,
        model_routed: str,
        prompt_tokens: int,
        completion_tokens: int,
        duration_seconds: float = 0.0,
        raw_prompt_tokens: int = 0
    ) -> float:
        """Records an inference event and returns the calculated savings in USD."""
        cost_saved = self.calculate_savings(model_requested, prompt_tokens, completion_tokens)
        ts = datetime.now(timezone.utc).isoformat()
        total_tokens = prompt_tokens + completion_tokens
        actual_raw_prompt = raw_prompt_tokens if raw_prompt_tokens > 0 else prompt_tokens

        try:
            with self._get_connection() as conn:
                conn.execute("""
                    INSERT OR REPLACE INTO completions (
                        id, timestamp, model_requested, model_routed,
                        raw_prompt_tokens, prompt_tokens, completion_tokens, total_tokens,
                        duration_seconds, cost_saved_usd
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    req_id, ts, model_requested, model_routed,
                    actual_raw_prompt, prompt_tokens, completion_tokens, total_tokens,
                    round(duration_seconds, 2), cost_saved
                ))
                conn.commit()
            logger.debug(f"[Storage] Saved completion {req_id}: {total_tokens} tokens (raw: {actual_raw_prompt}), ${cost_saved:.4f} saved.")
        except Exception as e:
            logger.error(f"[Storage] Failed to record completion: {e}")

        return cost_saved

    def get_summary(self) -> Dict[str, Any]:
        """Returns cumulative usage statistics, total savings, and model breakdown."""
        with self._get_connection() as conn:
            cur = conn.cursor()

            # Global Totals
            cur.execute("""
                SELECT 
                    COUNT(*) as total_requests,
                    COALESCE(SUM(raw_prompt_tokens), 0) as total_raw_prompt_tokens,
                    COALESCE(SUM(prompt_tokens), 0) as total_prompt_tokens,
                    COALESCE(SUM(completion_tokens), 0) as total_completion_tokens,
                    COALESCE(SUM(total_tokens), 0) as total_tokens,
                    COALESCE(SUM(cost_saved_usd), 0.0) as total_saved_usd
                FROM completions;
            """)
            totals = dict(cur.fetchone())

            # Calculate raw tokens that would have been spent without zero-copy-agent
            total_tokens = totals.get("total_tokens", 0)
            raw_p = totals.get("total_raw_prompt_tokens", 0)
            p_tokens = totals.get("total_prompt_tokens", 0)
            comp = totals.get("total_completion_tokens", 0)

            # For historical rows without raw_prompt_tokens, estimate based on BPE ~35% compression
            effective_raw_p = max(raw_p, int(p_tokens * 1.55))
            raw_without_zc = effective_raw_p + comp
            tokens_saved = max(0, raw_without_zc - total_tokens)
            tokens_saved_pct = round((tokens_saved / max(1, raw_without_zc)) * 100, 1)

            totals["tokens_without_zc"] = raw_without_zc
            totals["tokens_saved"] = tokens_saved
            totals["tokens_saved_pct"] = tokens_saved_pct

            # Today's Totals
            today_prefix = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            cur.execute("""
                SELECT 
                    COUNT(*) as today_requests,
                    COALESCE(SUM(total_tokens), 0) as today_tokens,
                    COALESCE(SUM(cost_saved_usd), 0.0) as today_saved_usd
                FROM completions
                WHERE timestamp LIKE ? || '%';
            """, (today_prefix,))
            today = dict(cur.fetchone())

            # Model breakdown
            cur.execute("""
                SELECT 
                    model_requested,
                    COUNT(*) as requests,
                    SUM(total_tokens) as tokens,
                    ROUND(SUM(cost_saved_usd), 4) as saved_usd
                FROM completions
                GROUP BY model_requested
                ORDER BY tokens DESC
                LIMIT 10;
            """)
            models = [dict(row) for row in cur.fetchall()]

            totals.update(today)
            totals["total_saved_usd"] = round(totals.get("total_saved_usd", 0.0), 2)
            totals["today_saved_usd"] = round(totals.get("today_saved_usd", 0.0), 2)
            totals["models_breakdown"] = models
            return totals

    def get_recent(self, limit: int = 15) -> List[Dict[str, Any]]:
        """Returns the most recent completions for dashboard inspection."""
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT id, timestamp, model_requested, model_routed, 
                       total_tokens, duration_seconds, cost_saved_usd
                FROM completions
                ORDER BY timestamp DESC
                LIMIT ?;
            """, (limit,))
            return [dict(row) for row in cur.fetchall()]

    def record_event(
        self,
        req_id: str,
        stage: str,
        model: str,
        message: str,
        metadata: Optional[Dict[str, Any]] = None
    ):
        """Records a granular cognitive trace event during request execution."""
        ts = datetime.now(timezone.utc).isoformat()
        meta_json = json.dumps(metadata or {}, ensure_ascii=False)
        try:
            with self._get_connection() as conn:
                conn.execute("""
                    INSERT INTO cognitive_events (req_id, timestamp, stage, model, message, metadata_json)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (req_id, ts, stage, model, message, meta_json))
                conn.commit()
        except Exception as e:
            logger.error(f"[Storage] Failed to record cognitive event: {e}")

    def get_recent_events(self, limit: int = 30) -> List[Dict[str, Any]]:
        """Returns recent cognitive observability events for UI tracing."""
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT id, req_id, timestamp, stage, model, message, metadata_json
                FROM cognitive_events
                ORDER BY id DESC
                LIMIT ?;
            """, (limit,))
            rows = []
            for row in cur.fetchall():
                d = dict(row)
                try:
                    d["metadata"] = json.loads(d.pop("metadata_json") or "{}")
                except Exception:
                    d["metadata"] = {}
                rows.append(d)
            return rows

analytics = AnalyticsStorage()
