"""
Unit tests for core/prompt_compressor.py (Human-in-the-Loop review and approval).
"""
import pytest
import asyncio
from core.prompt_compressor import PromptCompressorManager


@pytest.fixture
def manager():
    return PromptCompressorManager()


def test_create_draft_and_pending_list(manager):
    draft = manager.create_draft(
        original_prompt="Please write a python script that connects to sqlite and performs select * from users where active = 1",
        compressed_prompt="write python script: connect sqlite, select * from users where active=1",
        req_id="test-req-1"
    )
    assert draft.id is not None
    assert draft.status == "pending"
    assert draft.savings_pct > 0

    pending = manager.get_pending_drafts()
    assert any(d["id"] == draft.id for d in pending)


def test_approve_draft(manager):
    draft = manager.create_draft(
        original_prompt="Long verbose query",
        compressed_prompt="Short query",
        req_id="test-req-2"
    )
    ok = manager.approve(draft.id, edited_prompt="Custom edited prompt")
    assert ok is True
    assert draft.status == "approved"
    assert draft.compressed_prompt == "Custom edited prompt"


def test_rework_draft_feedback(manager):
    draft = manager.create_draft(
        original_prompt="Long verbose prompt with many details",
        compressed_prompt="Short prompt",
        req_id="test-req-3"
    )
    reworked = manager.rework(draft.id, user_feedback="мягче")
    assert reworked is not None
    assert reworked.status == "pending"
    assert reworked.rework_count == 1
    assert "мягче" in reworked.rework_feedback


def test_cancel_draft(manager):
    draft = manager.create_draft(
        original_prompt="Some prompt",
        compressed_prompt="Some prompt",
        req_id="test-req-4"
    )
    ok = manager.cancel(draft.id)
    assert ok is True
    assert draft.status == "cancelled"


@pytest.mark.asyncio
async def test_wait_for_decision_async(manager):
    draft = manager.create_draft(
        original_prompt="Original request",
        compressed_prompt="Compressed request",
        req_id="test-req-async"
    )

    async def user_decision_task():
        await asyncio.sleep(0.05)
        manager.approve(draft.id)

    asyncio.create_task(user_decision_task())
    prompt_text, status = await manager.wait_for_decision(draft.id, timeout=2.0)
    assert status == "approved"
    assert prompt_text == "Compressed request"
