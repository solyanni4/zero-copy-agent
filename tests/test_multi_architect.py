import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from core.orchestrator import HybridOrchestrator


@pytest.mark.asyncio
async def test_multi_architect_parallel_success():
    """Verify parallel execution of 2+ architects and consensus synthesis."""
    orch = HybridOrchestrator(browser_engine=MagicMock())
    orch.architect_models = ["gemini-4-neon", "deepseek-reasoner"]

    # Mock query candidate responses
    async def mock_query_candidate(role, candidate, prompt, req_id=""):
        if candidate == "gemini-4-neon":
            return "Gemini Plan: Clean Microservices Architecture", "gemini-4-neon"
        elif candidate == "deepseek-reasoner":
            return "DeepSeek Plan: High Performance Async Queue", "deepseek-reasoner"
        return "Default Plan", candidate

    orch._query_candidate_for_role = AsyncMock(side_effect=mock_query_candidate)
    orch.query_role_with_fallback = AsyncMock(
        return_value=("Synthesized Plan: Unified Async Microservices", "gemini-4-neon")
    )

    plan, used_model = await orch._run_multi_architect_analysis(
        orch.architect_models,
        "Build scalable event bus"
    )

    assert "Synthesized Plan" in plan
    assert "multi-architect" in used_model
    assert "gemini-4-neon" in used_model
    assert "deepseek-reasoner" in used_model
    assert orch._query_candidate_for_role.call_count == 2
    assert orch.query_role_with_fallback.call_count == 1


@pytest.mark.asyncio
async def test_multi_architect_partial_failure_tolerance():
    """Verify that if 1 of 2 models times out or fails, the remaining model's plan is used."""
    orch = HybridOrchestrator(browser_engine=MagicMock())
    orch.architect_models = ["gemini-4-neon", "failing-model"]

    async def mock_query_candidate(role, candidate, prompt, req_id=""):
        if candidate == "gemini-4-neon":
            return "Gemini Standalone Plan", "gemini-4-neon"
        elif candidate == "failing-model":
            raise asyncio.TimeoutError("Model timed out")
        return "Default Plan", candidate

    orch._query_candidate_for_role = AsyncMock(side_effect=mock_query_candidate)
    orch.query_role_with_fallback = AsyncMock()

    plan, used_model = await orch._run_multi_architect_analysis(
        orch.architect_models,
        "Build scalable event bus"
    )

    assert plan == "Gemini Standalone Plan"
    assert used_model == "gemini-4-neon"
    # Consensus synthesis should NOT be called when only 1 succeeded
    assert orch.query_role_with_fallback.call_count == 0


@pytest.mark.asyncio
async def test_multi_architect_all_failed_fallback():
    """Verify that if all multi-architect models fail, it falls back to standard planner cascade."""
    orch = HybridOrchestrator(browser_engine=MagicMock())
    orch.architect_models = ["fail-1", "fail-2"]

    orch._query_candidate_for_role = AsyncMock(side_effect=RuntimeError("Quota exceeded"))
    orch.query_role_with_fallback = AsyncMock(
        return_value=("Emergency Cascade Plan", "ollama/qwen2.5-coder:14b")
    )

    plan, used_model = await orch._run_multi_architect_analysis(
        orch.architect_models,
        "Build scalable event bus"
    )

    assert plan == "Emergency Cascade Plan"
    assert used_model == "ollama/qwen2.5-coder:14b"
    assert orch.query_role_with_fallback.call_count == 1


@pytest.mark.asyncio
async def test_cascade_architect_triggers_multi_architect():
    """Verify that _run_cascade_architect uses _run_multi_architect_analysis when 2+ models are configured."""
    orch = HybridOrchestrator(browser_engine=MagicMock())
    orch.architect_models = ["gemini-4-neon", "deepseek-reasoner"]
    orch.enable_local_gate = False
    orch.enable_architect_audit = False

    orch._run_multi_architect_analysis = AsyncMock(
        return_value=("Multi Blueprint", "multi-architect(gemini-4-neon+deepseek-reasoner)")
    )
    orch.ollama = MagicMock()
    orch.ollama.is_authenticated = AsyncMock(return_value=True)
    orch.ollama.list_models = AsyncMock(return_value=["qwen2.5-coder:14b"])
    orch.ollama.send_prompt = AsyncMock(return_value="def solution(): pass")

    res = await orch._run_cascade_architect("Test user task", inject_signature=False)

    assert orch._run_multi_architect_analysis.call_count == 1
    assert "Multi Blueprint" in res
    assert "def solution(): pass" in res
