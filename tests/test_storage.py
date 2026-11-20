"""
Tests for AnalyticsStorage, SQLite WAL persistence, and connection lifecycle.
"""
import pytest
from pathlib import Path
from core.storage import AnalyticsStorage

def test_storage_lifecycle_and_connection_closure(tmp_path: Path):
    db_file = tmp_path / "test_analytics.db"
    store = AnalyticsStorage(db_path=db_file)
    
    # 1. Record completion
    cost = store.record_completion(
        req_id="test-req-1",
        model_requested="gemini-4-neon",
        model_routed="gemini-4-neon",
        prompt_tokens=1000,
        completion_tokens=500,
        duration_seconds=1.25
    )
    assert cost > 0.0
    
    # 2. Record cognitive event
    store.record_event(
        req_id="test-req-1",
        stage="planner",
        model="gemini-4-neon",
        message="Decomposing task",
        metadata={"subtasks": 2}
    )
    
    # 3. Retrieve summary
    summary = store.get_summary()
    assert summary["total_requests"] == 1
    assert summary["total_tokens"] == 1500
    assert "tokens_without_zc" in summary
    assert "tokens_saved" in summary
    assert "tokens_saved_pct" in summary
    assert summary["tokens_without_zc"] >= summary["total_tokens"]
    assert len(summary["models_breakdown"]) == 1
    assert summary["models_breakdown"][0]["model_requested"] == "gemini-4-neon"
    
    # 4. Retrieve recent completions and events
    recent = store.get_recent(limit=5)
    assert len(recent) == 1
    assert recent[0]["id"] == "test-req-1"
    
    events = store.get_recent_events(limit=5)
    assert len(events) == 1
    assert events[0]["req_id"] == "test-req-1"
    assert events[0]["metadata"]["subtasks"] == 2
    
    # 5. Verify repeated calls do not exhaust or lock file
    for i in range(50):
        store.record_event(f"req-{i}", "step", "mock", f"msg {i}")
        store.get_recent_events(limit=5)
        store.get_summary()
        
    summary_final = store.get_summary()
    assert summary_final["total_requests"] == 1
