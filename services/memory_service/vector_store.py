"""
services/memory_service/vector_store.py
-----------------------------------------
Responsibility:
    Thin, typed wrapper around ChromaDB.  Provides collection-level
    operations (upsert, query, delete) with full error-handling and
    structured logging.  No business logic lives here.

Collections used:
    - chat_history   : individual chat messages
    - documents      : uploaded-file content chunks
    - user_facts     : long-term memory facts about the user
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, List, Optional, Any

import chromadb
from chromadb.config import Settings as ChromaSettings

logger = logging.getLogger("memory.vector_store")

# ─── Constants ────────────────────────────────────────────────────────────────

BASE_DIR = Path(__file__).resolve().parent.parent.parent  # project root
CHROMA_PATH = BASE_DIR / "data" / "chroma_db"
CHROMA_PATH.mkdir(parents=True, exist_ok=True)

COLLECTION_CHAT = "chat_history"
COLLECTION_DOCS = "documents"
COLLECTION_FACTS = "user_facts"

ALL_COLLECTIONS = [COLLECTION_CHAT, COLLECTION_DOCS, COLLECTION_FACTS]


# ─── Client singleton ─────────────────────────────────────────────────────────

_client: Optional[chromadb.PersistentClient] = None


def _get_client() -> chromadb.PersistentClient:
    global _client
    if _client is None:
        _client = chromadb.PersistentClient(
            path=str(CHROMA_PATH),
            settings=ChromaSettings(anonymized_telemetry=False),
        )
        logger.info("chroma_client_initialized", extra={"path": str(CHROMA_PATH)})
    return _client


def _get_or_create_collection(name: str) -> chromadb.Collection:
    return _get_client().get_or_create_collection(
        name=name,
        metadata={"hnsw:space": "cosine"},
    )


# ─── Public API ───────────────────────────────────────────────────────────────


def upsert_vectors(
    collection_name: str,
    ids: List[str],
    embeddings: List[List[float]],
    documents: List[str],
    metadatas: Optional[List[Dict[str, Any]]] = None,
) -> None:
    """
    Insert or update vectors in a named collection.

    Args:
        collection_name: One of COLLECTION_CHAT / COLLECTION_DOCS / COLLECTION_FACTS.
        ids:             Unique stable IDs for each document (e.g., "msg_42_chunk_0").
        embeddings:      Pre-computed float vectors (one per document).
        documents:       Raw text that corresponds to each vector.
        metadatas:       Optional per-document metadata dicts.
    """
    try:
        col = _get_or_create_collection(collection_name)
        col.upsert(
            ids=ids,
            embeddings=embeddings,
            documents=documents,
            metadatas=metadatas or [{} for _ in ids],
        )
        logger.debug(
            "vectors_upserted",
            extra={"collection": collection_name, "count": len(ids)},
        )
    except Exception:
        logger.exception(
            "vector_upsert_failed",
            extra={"collection": collection_name, "count": len(ids)},
        )
        raise


def query_vectors(
    collection_name: str,
    query_embedding: List[float],
    top_k: int = 5,
    where: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """
    Semantic nearest-neighbour search.

    Returns:
        List of dicts, each containing:
            - ``text``     : the stored document string
            - ``id``       : the document id
            - ``distance`` : cosine distance (lower = more similar)
            - ``metadata`` : stored metadata dict
    """
    try:
        col = _get_or_create_collection(collection_name)
        results = col.query(
            query_embeddings=[query_embedding],
            n_results=min(top_k, col.count() or 1),
            where=where or None,
            include=["documents", "distances", "metadatas"],
        )
        hits: List[Dict[str, Any]] = []
        if results and results.get("ids"):
            for i, doc_id in enumerate(results["ids"][0]):
                hits.append(
                    {
                        "id": doc_id,
                        "text": results["documents"][0][i],
                        "distance": results["distances"][0][i],
                        "metadata": results["metadatas"][0][i],
                    }
                )
        return hits
    except Exception:
        logger.exception("vector_query_failed", extra={"collection": collection_name})
        return []


def delete_by_metadata(
    collection_name: str,
    where: Dict[str, Any],
) -> None:
    """Delete all vectors matching a metadata filter."""
    try:
        col = _get_or_create_collection(collection_name)
        col.delete(where=where)
        logger.info("vectors_deleted", extra={"collection": collection_name, "filter": where})
    except Exception:
        logger.exception("vector_delete_failed", extra={"collection": collection_name})
        raise


def collection_count(collection_name: str) -> int:
    """Return total vectors stored in a collection (0 on error)."""
    try:
        return _get_or_create_collection(collection_name).count()
    except Exception:
        return 0
