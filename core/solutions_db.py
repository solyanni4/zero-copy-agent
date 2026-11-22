"""
Solutions Knowledge Base for zero-copy-agent.
Stores solved patterns, architectural blueprints, bug fixes, and best practices.
Features FTS5 full-text indexing and hybrid semantic search via Ollama (nomic-embed-text)
with automatic fallback to FTS5 BM25 when embeddings are offline.
"""
import os
import sys
import json
import math
import struct
import sqlite3
import logging
import uuid
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional, Tuple
import httpx

from core.config import get_runtime_cwd

logger = logging.getLogger("zcao.solutions")


def get_solutions_db_path() -> Path:
    """Returns persistent path to solutions SQLite database."""
    base_dir = get_runtime_cwd() / "data"
    base_dir.mkdir(parents=True, exist_ok=True)
    return base_dir / "solutions.db"


def serialize_embedding(vec: List[float]) -> bytes:
    """Packs float vector into binary BLOB."""
    return struct.pack(f"{len(vec)}f", *vec)


def deserialize_embedding(blob: Optional[bytes]) -> Optional[List[float]]:
    """Unpacks binary BLOB back to list of floats."""
    if not blob:
        return None
    count = len(blob) // 4
    return list(struct.unpack(f"{count}f", blob))


def cosine_similarity(v1: List[float], v2: List[float]) -> float:
    """Calculates cosine similarity between two float vectors."""
    if not v1 or not v2 or len(v1) != len(v2):
        return 0.0
    dot = 0.0
    norm1 = 0.0
    norm2 = 0.0
    for a, b in zip(v1, v2):
        dot += a * b
        norm1 += a * a
        norm2 += b * b
    if norm1 <= 0.0 or norm2 <= 0.0:
        return 0.0
    return dot / (math.sqrt(norm1) * math.sqrt(norm2))


class SolutionsDB:
    def __init__(self, db_path: Optional[Path] = None, ollama_url: str = "http://127.0.0.1:11434"):
        self.db_path = db_path or get_solutions_db_path()
        # Clean local ollama url
        clean_url = (ollama_url or "http://127.0.0.1:11434").rstrip("/")
        if not clean_url.startswith("http://") and not clean_url.startswith("https://"):
            clean_url = f"http://{clean_url}"
        clean_url = clean_url.replace("://0.0.0.0", "://127.0.0.1")
        self.ollama_url = clean_url
        self.embed_model = "nomic-embed-text"
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        return conn

    def _init_db(self):
        """Initializes tables, FTS5 virtual table, and sync triggers."""
        with self._get_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS solutions (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    problem_pattern TEXT NOT NULL,
                    solution_summary TEXT NOT NULL,
                    code_snippet TEXT DEFAULT '',
                    tags TEXT DEFAULT '[]',
                    stack TEXT DEFAULT '',
                    source_model TEXT DEFAULT 'architect',
                    usage_count INTEGER DEFAULT 0,
                    created_at TEXT NOT NULL,
                    embedding BLOB
                );
            """)

            # Create FTS5 virtual table for full-text search
            conn.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS solutions_fts USING fts5(
                    id UNINDEXED,
                    title,
                    problem_pattern,
                    solution_summary,
                    code_snippet,
                    tags,
                    stack,
                    content='solutions',
                    content_rowid='rowid'
                );
            """)

            # Triggers to keep FTS index synchronized with table
            conn.execute("""
                CREATE TRIGGER IF NOT EXISTS solutions_ai AFTER INSERT ON solutions BEGIN
                    INSERT INTO solutions_fts(rowid, id, title, problem_pattern, solution_summary, code_snippet, tags, stack)
                    VALUES (new.rowid, new.id, new.title, new.problem_pattern, new.solution_summary, new.code_snippet, new.tags, new.stack);
                END;
            """)
            conn.execute("""
                CREATE TRIGGER IF NOT EXISTS solutions_ad AFTER DELETE ON solutions BEGIN
                    INSERT INTO solutions_fts(solutions_fts, rowid, id, title, problem_pattern, solution_summary, code_snippet, tags, stack)
                    VALUES ('delete', old.rowid, old.id, old.title, old.problem_pattern, old.solution_summary, old.code_snippet, old.tags, old.stack);
                END;
            """)
            conn.execute("""
                CREATE TRIGGER IF NOT EXISTS solutions_au AFTER UPDATE ON solutions BEGIN
                    INSERT INTO solutions_fts(solutions_fts, rowid, id, title, problem_pattern, solution_summary, code_snippet, tags, stack)
                    VALUES ('delete', old.rowid, old.id, old.title, old.problem_pattern, old.solution_summary, old.code_snippet, old.tags, old.stack);
                    INSERT INTO solutions_fts(rowid, id, title, problem_pattern, solution_summary, code_snippet, tags, stack)
                    VALUES (new.rowid, new.id, new.title, new.problem_pattern, new.solution_summary, new.code_snippet, new.tags, new.stack);
                END;
            """)
            conn.commit()

    def compute_embedding(self, text: str) -> Optional[List[float]]:
        """
        Synchronously calculates embedding via local Ollama.
        Returns 768-dim float vector or None if Ollama/nomic-embed-text is offline.
        """
        if not text or not text.strip():
            return None
        try:
            with httpx.Client(timeout=4.0, trust_env=False) as client:
                res = client.post(
                    f"{self.ollama_url}/api/embeddings",
                    json={"model": self.embed_model, "prompt": text[:2000]}
                )
                if res.status_code == 200:
                    data = res.json()
                    emb = data.get("embedding")
                    if isinstance(emb, list) and len(emb) > 0:
                        return emb
        except Exception as e:
            logger.debug(f"[SolutionsDB] Embedding generation skipped (fallback to FTS5): {e}")
        return None

    def add_solution(
        self,
        title: str,
        problem_pattern: str = "",
        solution_summary: str = "",
        code_snippet: str = "",
        tags: Optional[List[str]] = None,
        stack: str = "",
        source_model: str = "architect",
        allow_duplicate: bool = False,
        **kwargs
    ) -> str:
        """
        Stores solution with duplicate detection.
        If an identical problem/title already exists, bumps usage_count instead of duplicating.
        """
        problem_pattern = problem_pattern or kwargs.get("problem_description", "")
        solution_summary = solution_summary or kwargs.get("description", "") or kwargs.get("solution_code", "")
        code_snippet = code_snippet or kwargs.get("solution_code", "") or kwargs.get("code", "")
        stack = stack or kwargs.get("language", "")
        tags_list = tags or []
        tags_json = json.dumps(tags_list, ensure_ascii=False)
        clean_title = title.strip()
        clean_prob = problem_pattern.strip()
        clean_sol = solution_summary.strip()
        clean_stack = stack.strip().lower()

        # Check for near duplicates by title & pattern
        if not allow_duplicate:
            with self._get_connection() as conn:
                existing = conn.execute(
                    "SELECT id, usage_count FROM solutions WHERE LOWER(title) = ? OR LOWER(problem_pattern) = ?",
                    (clean_title.lower(), clean_prob.lower())
                ).fetchone()
                if existing:
                    ex_id = existing["id"]
                    conn.execute("UPDATE solutions SET usage_count = usage_count + 1 WHERE id = ?", (ex_id,))
                    conn.commit()
                    logger.info(f"[SolutionsDB] Duplicate pattern recognized. Incremented usage for '{ex_id}'")
                    return ex_id

        # Compute embedding over title + problem + solution
        embed_text = f"{clean_title}\n{clean_prob}\n{clean_sol}"
        emb = self.compute_embedding(embed_text)
        blob = serialize_embedding(emb) if emb else None

        sol_id = str(uuid.uuid4())[:8]
        now_iso = datetime.now(timezone.utc).isoformat()

        with self._get_connection() as conn:
            conn.execute("""
                INSERT INTO solutions (
                    id, title, problem_pattern, solution_summary, code_snippet,
                    tags, stack, source_model, usage_count, created_at, embedding
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?);
            """, (
                sol_id, clean_title, clean_prob, clean_sol, code_snippet,
                tags_json, clean_stack, source_model, now_iso, blob
            ))
            conn.commit()

        logger.info(f"[SolutionsDB] Saved new solution '{clean_title}' (id: {sol_id}, stack: {clean_stack})")
        return sol_id

    def get_solution(self, solution_id: str) -> Optional[Dict[str, Any]]:
        """Retrieves single solution by ID."""
        with self._get_connection() as conn:
            row = conn.execute("SELECT * FROM solutions WHERE id = ?", (solution_id,)).fetchone()
            if not row:
                return None
            return self._row_to_dict(row)

    def update_solution(self, solution_id: str, updates: Optional[Dict[str, Any]] = None, **kwargs) -> bool:
        """Updates fields of an existing solution."""
        updates_dict = dict(updates or {})
        updates_dict.update(kwargs)
        if "problem_description" in updates_dict:
            updates_dict["problem_pattern"] = updates_dict.pop("problem_description")
        if "solution_code" in updates_dict:
            updates_dict["code_snippet"] = updates_dict.pop("solution_code")
        if "description" in updates_dict:
            updates_dict["solution_summary"] = updates_dict.pop("description")
        if "language" in updates_dict:
            updates_dict["stack"] = updates_dict.pop("language")

        allowed_fields = {"title", "problem_pattern", "solution_summary", "code_snippet", "tags", "stack"}
        set_clauses = []
        params = []

        recompute_embed = False
        for k, v in updates_dict.items():
            if k in allowed_fields:
                if k == "tags" and isinstance(v, list):
                    v = json.dumps(v, ensure_ascii=False)
                set_clauses.append(f"{k} = ?")
                params.append(v)
                if k in ("title", "problem_pattern", "solution_summary"):
                    recompute_embed = True

        if not set_clauses:
            return False

        if recompute_embed:
            # Recompute embedding
            sol = self.get_solution(solution_id)
            if sol:
                t = updates_dict.get("title", sol["title"])
                p = updates_dict.get("problem_pattern", sol["problem_pattern"])
                s = updates_dict.get("solution_summary", sol["solution_summary"])
                emb = self.compute_embedding(f"{t}\n{p}\n{s}")
                set_clauses.append("embedding = ?")
                params.append(serialize_embedding(emb) if emb else None)

        params.append(solution_id)
        with self._get_connection() as conn:
            res = conn.execute(f"UPDATE solutions SET {', '.join(set_clauses)} WHERE id = ?", params)
            conn.commit()
            return res.rowcount > 0

    def delete_solution(self, solution_id: str) -> bool:
        """Deletes solution from database and FTS index."""
        with self._get_connection() as conn:
            res = conn.execute("DELETE FROM solutions WHERE id = ?", (solution_id,))
            conn.commit()
            return res.rowcount > 0

    def increment_usage(self, solution_id: str) -> bool:
        """Increments usage counter for solution."""
        with self._get_connection() as conn:
            res = conn.execute("UPDATE solutions SET usage_count = usage_count + 1 WHERE id = ?", (solution_id,))
            conn.commit()
            return res.rowcount > 0

    def list_solutions(
        self,
        limit: int = 50,
        offset: int = 0,
        stack: Optional[str] = None,
        tag: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Lists solutions with optional stack and tag filters."""
        query = "SELECT * FROM solutions WHERE 1=1"
        params: List[Any] = []
        if stack:
            query += " AND LOWER(stack) = ?"
            params.append(stack.strip().lower())
        if tag:
            query += " AND tags LIKE ?"
            params.append(f'%"{tag.strip()}"%')

        query += " ORDER BY usage_count DESC, created_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        with self._get_connection() as conn:
            rows = conn.execute(query, params).fetchall()
            return [self._row_to_dict(r) for r in rows]

    def search_solutions(
        self,
        query: str,
        top_k: int = 3,
        stack: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Hybrid semantic and full-text search.
        1. Queries local Ollama for query embedding.
        2. Queries SQLite FTS5 index.
        3. Fuses scores with weighting and usage_count reinforcement.
        """
        clean_query = query.strip()
        if not clean_query:
            return self.list_solutions(limit=top_k, stack=stack)

        target_stack = stack.strip().lower() if stack else None

        # 1. Full-text search candidates
        fts_scores: Dict[str, float] = {}
        with self._get_connection() as conn:
            # Clean fts query to alphanumeric terms
            terms = [t for t in "".join(c if c.isalnum() else " " for c in clean_query).split() if len(t) > 1]
            if terms:
                fts_expr = " OR ".join(f'"{t}"*' for t in terms)
                try:
                    fts_rows = conn.execute("""
                        SELECT id, rank FROM solutions_fts
                        WHERE solutions_fts MATCH ?
                        ORDER BY rank
                        LIMIT 20
                    """, (fts_expr,)).fetchall()
                    for r in fts_rows:
                        # FTS5 rank is negative (lower = better)
                        fts_scores[r["id"]] = 1.0 / (1.0 + abs(float(r["rank"])))
                except Exception as e:
                    logger.debug(f"[SolutionsDB] FTS search warning: {e}")

        # 2. Semantic search with embeddings
        query_emb = self.compute_embedding(clean_query)
        semantic_scores: Dict[str, float] = {}

        with self._get_connection() as conn:
            all_rows = conn.execute("SELECT id, stack, usage_count, embedding FROM solutions").fetchall()

            for row in all_rows:
                sid = row["id"]
                # Stack filter pre-check
                row_stack = (row["stack"] or "").lower()
                if target_stack and row_stack and row_stack != target_stack:
                    continue

                if query_emb and row["embedding"]:
                    stored_vec = deserialize_embedding(row["embedding"])
                    if stored_vec:
                        sim = cosine_similarity(query_emb, stored_vec)
                        if sim > 0.15:
                            semantic_scores[sid] = sim

        # 3. Combine scores
        candidate_ids = set(fts_scores.keys()) | set(semantic_scores.keys())
        if not candidate_ids:
            # Fallback to recent stack solutions if no exact match
            return self.list_solutions(limit=top_k, stack=stack)

        scored_items: List[Tuple[str, float]] = []
        for sid in candidate_ids:
            sem_s = semantic_scores.get(sid, 0.0)
            fts_s = fts_scores.get(sid, 0.0)

            if query_emb and sem_s > 0:
                base_score = 0.65 * sem_s + 0.35 * fts_s
            else:
                base_score = fts_s

            scored_items.append((sid, base_score))

        # Sort by score descending
        scored_items.sort(key=lambda x: x[1], reverse=True)
        top_ids = [sid for sid, _ in scored_items[:top_k]]

        # Fetch full data and annotate scores
        results: List[Dict[str, Any]] = []
        score_map = dict(scored_items)
        with self._get_connection() as conn:
            for sid in top_ids:
                row = conn.execute("SELECT * FROM solutions WHERE id = ?", (sid,)).fetchone()
                if row:
                    d = self._row_to_dict(row)
                    d["relevance_score"] = round(score_map.get(sid, 0.0), 3)
                    results.append(d)

        return results

    def format_context_prompt(self, solutions: List[Dict[str, Any]]) -> str:
        """Formats retrieved solutions into concise system markdown context for LLM."""
        if not solutions:
            return ""
        lines = [
            "### База готовых решений Архитектора (Релевантный опыт)",
            "Используй проверенные паттерны из прошлых успешных решений для ускорения и избежания повторных ошибок:"
        ]
        for idx, sol in enumerate(solutions, 1):
            title = sol.get("title", "")
            prob = sol.get("problem_pattern", "")
            ans = sol.get("solution_summary", "")
            code = sol.get("code_snippet", "")
            stack = sol.get("stack", "")
            lines.append(f"\n#### Паттерн {idx}: {title} (Стек: {stack or 'универсальный'})")
            lines.append(f"- **Проблема / Контекст:** {prob}")
            lines.append(f"- **Решение:** {ans}")
            if code and code.strip():
                lines.append(f"```\n{code.strip()[:600]}\n```")
        lines.append("")
        return "\n".join(lines)

    def _row_to_dict(self, row: sqlite3.Row) -> Dict[str, Any]:
        """Converts sqlite Row into public dictionary."""
        tags = []
        try:
            tags = json.loads(row["tags"] or "[]")
        except Exception:
            pass
        return {
            "id": row["id"],
            "title": row["title"],
            "problem_pattern": row["problem_pattern"],
            "solution_summary": row["solution_summary"],
            "code_snippet": row["code_snippet"] or "",
            "tags": tags,
            "stack": row["stack"] or "",
            "source_model": row["source_model"] or "architect",
            "usage_count": row["usage_count"] or 0,
            "created_at": row["created_at"],
            "has_embedding": bool(row["embedding"])
        }


# Global singleton instance
solutions_db = SolutionsDB()
