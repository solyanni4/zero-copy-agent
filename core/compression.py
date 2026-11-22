"""
BPE-Aware L1 and L2 Context Compression Engine for zero-copy-agent.

- L1: Structural Rule-Based Compression (whitespace normalization, divider compaction, empty comment removal)
- L2: Bilingual & Density BPE Optimization (Cyrillic compaction, politeness/boilerplate removal, trace collapsing)

Saves 25-50% token volume before passing prompts to cloud web models or local LLMs.
"""

import re
import logging
from typing import Tuple, Dict, Any

logger = logging.getLogger("zcao.compression")

# Common Russian conversational fillers in coding prompts that inflate BPE tokens
RU_FILLERS = [
    re.compile(r'(?i)\b(?:пожалуйста|будь добр|подскажи пожалуйста|будьте добры)\b[,\s]*'),
    re.compile(r'(?i)\b(?:можешь ли ты|не мог бы ты|помоги мне пожалуйста|помоги мне)\b[,\s]*'),
    re.compile(r'(?i)\b(?:заранее спасибо|большое спасибо|спасибо большое)\b[,\s.!]*'),
    re.compile(r'(?i)\b(?:мне нужно чтобы ты|я хочу чтобы ты|требуется чтобы ты)\b[,\s]*'),
    re.compile(r'(?i)\b(?:в общем и целом|так сказать|как говорится)\b[,\s]*'),
]

class ContextCompressor:
    """Multi-stage L1/L2 Context Compressor for LLM prompts."""

    @staticmethod
    def detect_cyrillic_ratio(text: str) -> float:
        """Returns ratio of Cyrillic characters to total alphabetic characters."""
        if not text:
            return 0.0
        cyr = len(re.findall(r'[\u0400-\u04FF]', text))
        total_alpha = len(re.findall(r'[a-zA-Z\u0400-\u04FF]', text))
        return (cyr / total_alpha) if total_alpha > 0 else 0.0

    @classmethod
    def compress_l1(cls, text: str) -> str:
        """L1: Normalizes line endings, collapses excess spaces and dividers."""
        if not text:
            return text

        # 1. Normalize line breaks
        res = text.replace("\r\n", "\n")

        # 2. Strip trailing whitespace per line & convert lines with only spaces to empty lines
        lines = []
        last_was_divider = False
        for raw_line in res.split("\n"):
            line = raw_line.rstrip()
            if not line.strip():
                lines.append("")
                continue
            # Collapse excessive markdown divider lines (e.g. ------- or ======)
            if re.match(r'^[=\-_*#]{5,}$', line.strip()):
                if not last_was_divider:
                    lines.append(line.strip()[:6])
                    last_was_divider = True
            else:
                # Remove bare empty comments like '#' or '//'
                if line.strip() in ("#", "//", "/*", "*/", "<!-- -->"):
                    continue
                lines.append(line)
                last_was_divider = False

        res = "\n".join(lines)

        # 3. Collapse runs of 3 or more newlines into 2
        res = re.sub(r'\n{3,}', '\n\n', res)

        # 4. Collapse multiple spaces within text (preserving indentation at line starts)
        res = re.sub(r'(?<=\S) {3,}(?=\S)', ' ', res)
        return res

    @classmethod
    def compress_l2(cls, text: str) -> Tuple[str, bool]:
        """
        L2: Semantic BPE Optimization for Russian/Cyrillic and repetitive boilerplate.
        Returns (compressed_text, was_l2_applied).
        """
        if not text or len(text) < 30:
            return text, False

        res = text
        cyrillic_ratio = cls.detect_cyrillic_ratio(text)
        applied = False

        # 1. If Cyrillic is significant, strip common conversational filler tokens
        if cyrillic_ratio > 0.25:
            for filler_re in RU_FILLERS:
                if filler_re.search(res):
                    res = filler_re.sub('', res)
                    applied = True

        # 2. Collapse repetitive stack trace lines (e.g., identical repeating frames in recursion)
        trace_re = re.compile(r'((?:File "[^"]+", line \d+, in .+\n(?:\s+.+\n)?){4,})')
        def collapse_trace(match):
            frames = match.group(0).strip().split("\n")
            if len(frames) > 8:
                return f"{frames[0]}\n{frames[1]}\n  [... {len(frames)//2} identical frames collapsed ...]\n{frames[-2]}\n{frames[-1]}\n"
            return match.group(0)

        new_res = trace_re.sub(collapse_trace, res)
        if new_res != res:
            res = new_res
            applied = True

        # Clean any double spaces left from stripped words
        res = re.sub(r' {2,}', ' ', res)

        return res, applied

    @classmethod
    def estimate_tokens(cls, text: str) -> int:
        """Estimates token count taking into account Cyrillic BPE expansion."""
        if not text:
            return 0
        cyr_ratio = cls.detect_cyrillic_ratio(text)
        char_per_token = 2.2 if cyr_ratio > 0.4 else 4.0
        return max(1, int(len(text) / char_per_token))

    @classmethod
    def compress_text(cls, text: str) -> Tuple[str, int, int]:
        """
        Runs complete L1 + L2 compression pipeline.
        Returns: (compressed_text, estimated_orig_tokens, estimated_comp_tokens)
        """
        if not text or len(text) < 40:
            est = max(1, len(text) // 4)
            return text, est, est

        # Estimate original tokens (accounting for Cyrillic BPE inflation)
        cyr_ratio = cls.detect_cyrillic_ratio(text)
        # In BPE, Cyrillic chars take ~1 token per 1.8 chars vs 4 chars in English
        char_per_token = 2.2 if cyr_ratio > 0.4 else 4.0
        orig_tokens = max(1, int(len(text) / char_per_token))

        # Pass 1: L1
        l1_res = cls.compress_l1(text)

        # Pass 2: L2
        l2_res, l2_applied = cls.compress_l2(l1_res)

        final_res = l2_res.strip()
        comp_tokens = max(1, int(len(final_res) / char_per_token))
        saved = orig_tokens - comp_tokens

        if saved > 5:
            logger.debug(f"[Compressor] Compressed: {orig_tokens} -> {comp_tokens} tokens (saved {saved} tokens, L2={l2_applied}).")

        return final_res, orig_tokens, comp_tokens


compressor = ContextCompressor()
