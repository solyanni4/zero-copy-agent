"""
Interactive Human-in-the-Loop Prompt Compression Manager for zero-copy-agent.
Allows developers and users to inspect, approve, edit, or request rework
on compressed prompts before sending them to cloud web sessions or external APIs.
"""
import asyncio
import logging
import uuid
import time
from dataclasses import dataclass, asdict
from typing import Dict, Any, List, Optional, Tuple

from core.compression import ContextCompressor

logger = logging.getLogger("zcao.compressor_manager")


@dataclass
class CompressionDraft:
    draft_id: str
    original_prompt: str
    compressed_prompt: str
    original_tokens: int
    compressed_tokens: int
    savings_percent: float
    status: str  # "pending" | "approved" | "reworking" | "cancelled"
    feedback: Optional[str] = None
    created_at: float = 0.0
    edited_prompt: Optional[str] = None
    rework_count: int = 0

    @property
    def id(self) -> str:
        return self.draft_id

    @property
    def savings_pct(self) -> float:
        return self.savings_percent

    @property
    def rework_feedback(self) -> Optional[str]:
        return self.feedback

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["id"] = self.draft_id
        d["savings_pct"] = self.savings_percent
        return d


class PromptCompressorManager:
    """Manages prompt compression drafts and asynchronous human approval workflows."""

    def __init__(self):
        self._drafts: Dict[str, CompressionDraft] = {}
        self._events: Dict[str, asyncio.Event] = {}
        self._results: Dict[str, Tuple[str, str]] = {}  # draft_id -> (final_prompt, status)

    def create_draft(
        self,
        original_prompt: str,
        compressed_prompt: Optional[str] = None,
        req_id: Optional[str] = None,
        **kwargs
    ) -> CompressionDraft:
        """Creates a pending compression draft with token savings calculations."""
        draft_id = str(uuid.uuid4())[:8]

        # Use ContextCompressor if compressed_prompt not supplied
        if compressed_prompt is None:
            comp_text, orig_tokens, comp_tokens = ContextCompressor.compress_text(original_prompt)
        else:
            comp_text = compressed_prompt
            orig_tokens = ContextCompressor.estimate_tokens(original_prompt)
            comp_tokens = ContextCompressor.estimate_tokens(comp_text)

        savings = 0.0
        if orig_tokens > 0:
            savings = round(max(0.0, (1.0 - (comp_tokens / orig_tokens)) * 100.0), 1)

        draft = CompressionDraft(
            draft_id=draft_id,
            original_prompt=original_prompt,
            compressed_prompt=comp_text,
            original_tokens=orig_tokens,
            compressed_tokens=comp_tokens,
            savings_percent=savings,
            status="pending",
            feedback=None,
            created_at=time.time(),
            edited_prompt=None
        )

        self._drafts[draft_id] = draft
        self._events[draft_id] = asyncio.Event()
        self.cleanup_old_drafts()
        logger.info(f"[CompressorManager] Created draft '{draft_id}': {orig_tokens}->{comp_tokens} tokens ({savings}% saved)")
        return draft

    def cleanup_old_drafts(self, max_age_seconds: float = 1800.0) -> int:
        """Prunes completed, approved or cancelled drafts older than max_age_seconds (default: 30 mins)."""
        now = time.time()
        to_delete = [
            d_id for d_id, d in self._drafts.items()
            if d.status in ("approved", "cancelled", "timeout_auto_approved")
            and (now - d.created_at) > max_age_seconds
        ]
        for d_id in to_delete:
            self._drafts.pop(d_id, None)
            self._events.pop(d_id, None)
            self._results.pop(d_id, None)
        if to_delete:
            logger.debug(f"[CompressorManager] Cleaned up {len(to_delete)} old drafts (> {max_age_seconds}s)")
        return len(to_delete)

    def get_draft(self, draft_id: str) -> Optional[CompressionDraft]:
        return self._drafts.get(draft_id)

    def get_pending_drafts(self) -> List[Dict[str, Any]]:
        """Returns list of all drafts awaiting user approval."""
        self.cleanup_old_drafts()
        return [
            d.to_dict()
            for d in sorted(self._drafts.values(), key=lambda x: x.created_at, reverse=True)
            if d.status in ("pending", "reworking")
        ]

    def approve(self, draft_id: str, edited_prompt: Optional[str] = None) -> bool:
        """Approves draft with optional manual user edits."""
        draft = self._drafts.get(draft_id)
        if not draft:
            return False

        final_text = edited_prompt if (edited_prompt is not None and edited_prompt.strip()) else draft.compressed_prompt
        draft.status = "approved"
        draft.edited_prompt = edited_prompt
        draft.compressed_prompt = final_text

        self._results[draft_id] = (final_text, "approved")
        if draft_id in self._events:
            self._events[draft_id].set()

        logger.info(f"[CompressorManager] Draft '{draft_id}' APPROVED by user.")
        return True

    def rework(self, draft_id: str, user_feedback: str) -> Optional[CompressionDraft]:
        """
        Re-compresses prompt incorporating user feedback.
        Example feedback: 'сожми мягче', 'не удаляй комментарии', 'сожми жестче'.
        """
        draft = self._drafts.get(draft_id)
        if not draft:
            return None

        draft.status = "reworking"
        draft.feedback = user_feedback.strip()
        fb_lower = user_feedback.lower()

        orig = draft.original_prompt
        # Adjust compression heuristic based on user feedback
        if any(w in fb_lower for w in ("мягче", "soft", "не удаляй", "keep")):
            # Only L1 structural cleanup, preserve all text and fillers
            new_compressed = ContextCompressor.compress_l1(orig)
        elif any(w in fb_lower for w in ("жестче", "сильнее", "hard", "aggressive")):
            # Aggressive compression: L1 + L2 + collapse multiple whitespace
            l1 = ContextCompressor.compress_l1(orig)
            l2, _ = ContextCompressor.compress_l2(l1)
            lines = [line.strip() for line in l2.split("\n") if line.strip()]
            new_compressed = "\n".join(lines)
        else:
            # Default re-run with guidance note prepended
            c_text, _, _ = ContextCompressor.compress_text(orig)
            new_compressed = c_text

        new_tokens = ContextCompressor.estimate_tokens(new_compressed)
        savings = 0.0
        if draft.original_tokens > 0:
            savings = round(max(0.0, (1.0 - (new_tokens / draft.original_tokens)) * 100.0), 1)

        draft.compressed_prompt = new_compressed
        draft.compressed_tokens = new_tokens
        draft.savings_percent = savings
        draft.status = "pending"
        draft.rework_count += 1

        logger.info(f"[CompressorManager] Draft '{draft_id}' reworked: {draft.original_tokens}->{new_tokens} tokens ({savings}% saved)")
        return draft

    def cancel(self, draft_id: str) -> bool:
        """Cancels compression; instructs caller to send original uncompressed prompt."""
        draft = self._drafts.get(draft_id)
        if not draft:
            return False

        draft.status = "cancelled"
        self._results[draft_id] = (draft.original_prompt, "cancelled")
        if draft_id in self._events:
            self._events[draft_id].set()

        logger.info(f"[CompressorManager] Draft '{draft_id}' CANCELLED. Falling back to original prompt.")
        return True

    async def wait_for_decision(self, draft_id: str, timeout: float = 180.0) -> Tuple[str, str]:
        """
        Suspends coroutine until user approves, edits, reworks or cancels draft.
        If timeout expires, defaults to sending the compressed prompt.
        Returns: (final_prompt_text, final_status)
        """
        event = self._events.get(draft_id)
        draft = self._drafts.get(draft_id)
        if not event or not draft:
            return ("", "error")

        try:
            await asyncio.wait_for(event.wait(), timeout=timeout)
            return self._results.get(draft_id, (draft.compressed_prompt, "approved"))
        except asyncio.TimeoutError:
            logger.warning(f"[CompressorManager] Draft '{draft_id}' timed out after {timeout}s. Auto-approving.")
            draft.status = "approved"
            return (draft.compressed_prompt, "timeout_auto_approved")
        finally:
            self._events.pop(draft_id, None)


# Global singleton manager
compressor_manager = PromptCompressorManager()
prompt_compressor = compressor_manager
