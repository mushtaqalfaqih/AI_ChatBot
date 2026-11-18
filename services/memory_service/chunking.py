"""
services/memory_service/chunking.py
------------------------------------
Responsibility: Split arbitrary text into overlapping chunks suitable for
embedding and semantic search.

Design decisions:
- Pure functions; no state, no I/O.
- Tries to honour sentence/paragraph boundaries before falling back to
  character-level splitting (avoids cutting a sentence mid-word).
- Exported API: chunk_text() — the only function callers need.
"""

from __future__ import annotations
import re
import logging
from typing import List

logger = logging.getLogger("memory.chunking")

# Sentence-boundary markers (covers Arabic + Latin punctuation)
_SENTENCE_ENDINGS = re.compile(r"(?<=[.!?؟।\n])\s+")


def _split_on_boundaries(text: str) -> List[str]:
    """Split text at sentence/paragraph boundaries."""
    parts = _SENTENCE_ENDINGS.split(text.strip())
    return [p.strip() for p in parts if p.strip()]


def chunk_text(
    text: str,
    chunk_size: int = 500,
    overlap: int = 80,
) -> List[str]:
    """
    Split *text* into overlapping chunks of at most *chunk_size* characters.

    Algorithm:
    1. Split at sentence boundaries to keep semantic units intact.
    2. Greedily accumulate sentences until chunk_size is reached.
    3. Backtrack by *overlap* characters to create the next chunk window.

    Args:
        text:       Raw text to chunk.
        chunk_size: Max characters per chunk (default 500).
        overlap:    Number of trailing characters carried into the next chunk
                    for context continuity (default 80).

    Returns:
        List[str] of text chunks.  Empty list if *text* is empty.
    """
    if not text or not text.strip():
        return []

    sentences = _split_on_boundaries(text)
    if not sentences:
        return []

    chunks: List[str] = []
    current: List[str] = []
    current_len = 0

    for sentence in sentences:
        s_len = len(sentence)

        # A single sentence larger than chunk_size → hard split it
        if s_len >= chunk_size:
            # First flush what we have
            if current:
                chunks.append(" ".join(current))
                current, current_len = [], 0
            # Hard-split the oversized sentence
            for i in range(0, s_len, chunk_size - overlap):
                chunks.append(sentence[i : i + chunk_size])
            continue

        if current_len + s_len + 1 > chunk_size and current:
            chunks.append(" ".join(current))
            # Overlap: keep sentences from the tail until overlap chars covered
            tail: List[str] = []
            tail_len = 0
            for s in reversed(current):
                if tail_len + len(s) + 1 <= overlap:
                    tail.insert(0, s)
                    tail_len += len(s) + 1
                else:
                    break
            current, current_len = tail, tail_len

        current.append(sentence)
        current_len += s_len + 1

    if current:
        chunks.append(" ".join(current))

    logger.debug("chunked_text", extra={"input_len": len(text), "num_chunks": len(chunks)})
    return chunks
