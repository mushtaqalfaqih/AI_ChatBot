"""
services/memory_service/memory_manager.py
-------------------------------------------
Responsibility:
    High-level Memory Manager — the ONLY entry-point for the rest of the app.

    Provides:
        index_message()   — embed & store a chat message in ChromaDB
        index_document()  — embed & store uploaded-file chunks
        index_fact()      — embed & store a user fact (long-term memory)
        get_context()     — semantic search across all collections,
                            rank results, and return inject-ready text
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any

from .chunking import chunk_text
from .embeddings import embed_single, embed
from .vector_store import (
    COLLECTION_CHAT,
    COLLECTION_DOCS,
    COLLECTION_FACTS,
    upsert_vectors,
    query_vectors,
    collection_count,
)

logger = logging.getLogger("memory.manager")


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _make_id(*parts: str) -> str:
    """Build a stable, URL-safe ID from parts."""
    return "_".join(str(p).replace(" ", "_").replace("/", "_")[:40] for p in parts)


# ─── Indexing API ─────────────────────────────────────────────────────────────

def index_message(
    message_id: str,
    user_id: str,
    thread_id: str,
    role: str,
    text: str,
) -> None:
    """
    Embed and store a single chat message.
    Safe to call from a background thread.
    """
    if not text or not text.strip():
        return
    try:
        vector = embed_single(text)
        if not vector:
            return
        upsert_vectors(
            COLLECTION_CHAT,
            ids=[message_id],
            embeddings=[vector],
            documents=[text],
            metadatas=[{
                "user_id": user_id,
                "thread_id": thread_id,
                "role": role,
                "timestamp": _now_iso(),
            }],
        )
    except Exception:
        logger.exception("index_message_failed", extra={"message_id": message_id})


def index_document(
    document_id: str,
    user_id: str,
    filename: str,
    text: str,
) -> None:
    """
    Chunk, embed, and store an uploaded document.
    Safe to call from a background thread.
    """
    if not text or not text.strip():
        return
    try:
        chunks = chunk_text(text, chunk_size=600, overlap=100)
        if not chunks:
            return

        vectors = embed(chunks)
        if not vectors:
            return

        ids = [_make_id(document_id, str(i)) for i in range(len(chunks))]
        metadatas = [
            {
                "user_id": user_id,
                "filename": filename,
                "chunk_index": i,
                "timestamp": _now_iso(),
            }
            for i in range(len(chunks))
        ]
        upsert_vectors(COLLECTION_DOCS, ids=ids, embeddings=vectors,
                       documents=chunks, metadatas=metadatas)
        logger.info("document_indexed", extra={
            "document_id": document_id, "chunks": len(chunks), "filename": filename
        })
    except Exception:
        logger.exception("index_document_failed", extra={"document_id": document_id})


def index_fact(
    user_id: str,
    fact: str,
) -> None:
    """
    Embed and store a single user fact extracted by extract_memory().
    Uses a deterministic ID so re-indexing the same fact is a no-op.
    """
    if not fact or not fact.strip():
        return
    try:
        fact_id = _make_id("fact", user_id, fact[:60])
        vector = embed_single(fact)
        if not vector:
            return
        upsert_vectors(
            COLLECTION_FACTS,
            ids=[fact_id],
            embeddings=[vector],
            documents=[fact],
            metadatas=[{"user_id": user_id, "timestamp": _now_iso()}],
        )
    except Exception:
        logger.exception("index_fact_failed", extra={"user_id": user_id})


# ─── Retrieval API ────────────────────────────────────────────────────────────

def get_context(
    user_id: str,
    query: str,
    top_k: int = 5,
    max_chars: int = 1800,
) -> str:
    """
    Run semantic search across all three collections filtered by user_id,
    rank results by distance, deduplicate, and return a ready-to-inject
    context string.

    Args:
        user_id:   Filter results to this user only.
        query:     The current user message used as the query vector.
        top_k:     Max results to pull from each collection.
        max_chars: Hard cap on total returned characters.

    Returns:
        A formatted context string, or "" if nothing was found.
    """
    if not query or not query.strip():
        return ""

    try:
        q_vector = embed_single(query)
        if not q_vector:
            return ""

        where_filter = {"user_id": {"$eq": user_id}}

        chat_hits  = query_vectors(COLLECTION_CHAT,  q_vector, top_k, where_filter)
        doc_hits   = query_vectors(COLLECTION_DOCS,  q_vector, top_k, where_filter)
        fact_hits  = query_vectors(COLLECTION_FACTS, q_vector, top_k, where_filter)

        # Merge and rank by cosine distance (lower = better)
        all_hits: List[Dict[str, Any]] = (
            [{"source": "chat",     **h} for h in chat_hits] +
            [{"source": "document", **h} for h in doc_hits] +
            [{"source": "fact",     **h} for h in fact_hits]
        )
        all_hits.sort(key=lambda h: h["distance"])

        # Deduplicate by text
        seen: set[str] = set()
        ranked: List[Dict[str, Any]] = []
        for hit in all_hits:
            text_key = hit["text"][:80]
            if text_key not in seen:
                seen.add(text_key)
                ranked.append(hit)

        if not ranked:
            return ""

        # Build inject string with source labels
        sections: List[str] = []
        total = 0
        for hit in ranked:
            label = {
                "chat": "محادثة سابقة",
                "document": f"مستند: {hit['metadata'].get('filename', 'ملف')}",
                "fact": "معلومة عن المستخدم",
            }.get(hit["source"], "سياق")

            entry = f"[{label}]\n{hit['text']}"
            if total + len(entry) > max_chars:
                break
            sections.append(entry)
            total += len(entry)

        if not sections:
            return ""

        context = "\n\n---\n\n".join(sections)
        logger.debug("context_retrieved", extra={
            "user_id": user_id, "hits": len(sections), "chars": total
        })
        return context

    except Exception:
        logger.exception("get_context_failed", extra={"user_id": user_id})
        return ""


# ─── Background helpers ───────────────────────────────────────────────────────

def background_index_message(
    message_id: str,
    user_id: str,
    thread_id: str,
    role: str,
    text: str,
) -> None:
    """Fire-and-forget wrapper — spawns a daemon thread."""
    t = threading.Thread(
        target=index_message,
        args=(message_id, user_id, thread_id, role, text),
        daemon=True,
        name=f"embed-msg-{message_id}",
    )
    t.start()


def background_index_document(
    document_id: str,
    user_id: str,
    filename: str,
    text: str,
) -> None:
    """Fire-and-forget wrapper — spawns a daemon thread."""
    t = threading.Thread(
        target=index_document,
        args=(document_id, user_id, filename, text),
        daemon=True,
        name=f"embed-doc-{document_id}",
    )
    t.start()


def background_index_fact(user_id: str, fact: str) -> None:
    """Fire-and-forget wrapper — spawns a daemon thread."""
    t = threading.Thread(
        target=index_fact,
        args=(user_id, fact),
        daemon=True,
        name=f"embed-fact-{user_id[:8]}",
    )
    t.start()


def stats() -> Dict[str, int]:
    """Return vector counts per collection (for dashboards / health checks)."""
    return {
        "chat_history": collection_count(COLLECTION_CHAT),
        "documents":    collection_count(COLLECTION_DOCS),
        "user_facts":   collection_count(COLLECTION_FACTS),
    }
