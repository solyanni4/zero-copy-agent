"""
Unit tests for core/solutions_db.py (SQLite RAG + FTS5 + Nomic vector embeddings).
"""
import pytest
import tempfile
import shutil
from pathlib import Path

from core.solutions_db import SolutionsDB, solutions_db


@pytest.fixture
def temp_solutions_db():
    temp_dir = tempfile.mkdtemp()
    db_path = Path(temp_dir) / "test_solutions.db"
    s_db = SolutionsDB(db_path=db_path)
    yield s_db
    shutil.rmtree(temp_dir, ignore_errors=True)


def test_solutions_db_crud(temp_solutions_db):
    s_db = temp_solutions_db

    # 1. Add solution
    sol_id = s_db.add_solution(
        title="FastAPI Lifespan Context Manager",
        problem_description="How to initialize async database connections cleanly in FastAPI",
        solution_code="@asynccontextmanager\nasync def lifespan(app: FastAPI):\n    yield",
        tags=["fastapi", "asyncio", "lifespan"],
        language="python"
    )
    assert sol_id is not None

    # 2. Get solution
    sol = s_db.get_solution(sol_id)
    assert sol is not None
    assert sol["title"] == "FastAPI Lifespan Context Manager"
    assert "fastapi" in sol["tags"]
    assert sol["usage_count"] == 1

    # 3. Duplicate detection bumps usage count
    dup_id = s_db.add_solution(
        title="FastAPI Lifespan Context Manager",
        problem_description="Any description",
        solution_code="@asynccontextmanager\nasync def lifespan(app: FastAPI):\n    yield",
        tags=["fastapi"]
    )
    assert dup_id == sol_id
    sol_updated = s_db.get_solution(sol_id)
    assert sol_updated["usage_count"] == 2

    # 4. Update solution
    ok = s_db.update_solution(
        solution_id=sol_id,
        title="FastAPI Modern Lifespan Handler",
        tags=["fastapi", "lifespan", "v2"]
    )
    assert ok is True
    sol_renamed = s_db.get_solution(sol_id)
    assert sol_renamed["title"] == "FastAPI Modern Lifespan Handler"
    assert "v2" in sol_renamed["tags"]

    # 5. Delete solution
    del_ok = s_db.delete_solution(sol_id)
    assert del_ok is True
    assert s_db.get_solution(sol_id) is None


def test_solutions_fts5_and_list(temp_solutions_db):
    s_db = temp_solutions_db

    s_db.add_solution(
        title="Pytest SQLite Fixture",
        problem_description="Setting up isolated in-memory test databases in pytest",
        solution_code="def db():\n    return sqlite3.connect(':memory:')",
        tags=["pytest", "sqlite"],
        language="python"
    )
    s_db.add_solution(
        title="Docker Compose Healthcheck",
        problem_description="Configuring health check intervals for Postgres in docker compose",
        solution_code="healthcheck:\n  test: ['CMD', 'pg_isready']",
        tags=["docker", "postgres"],
        language="yaml"
    )

    # List with tag
    items = s_db.list_solutions(tag="docker")
    assert len(items) == 1
    assert items[0]["title"] == "Docker Compose Healthcheck"

    # Search with FTS5
    results = s_db.search_solutions("healthcheck", top_k=5)
    assert len(results) >= 1
    assert any("Docker" in r["title"] for r in results)

    # Prompt formatting
    prompt_ctx = s_db.format_context_prompt(results)
    assert "База готовых решений" in prompt_ctx
    assert "Docker Compose Healthcheck" in prompt_ctx
