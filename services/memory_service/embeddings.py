"""
services/memory_service/embeddings.py
---------------------------------------
Responsibility:
    Load and cache the sentence-transformer embedding model.
    Provide a single embed() function that converts text(s) into float vectors.

Why sentence-transformers / all-MiniLM-L6-v2?
    - Free, local, no API key required.
    - 384-dimensional vectors: fast + accurate enough for chat context.
    - Works well with Arabic text (multilingual variant available if needed).
    - The model is downloaded once and cached by HuggingFace automatically.

Thread-safety:
    SentenceTransformer is *not* thread-safe by default during the first
    load. We use a threading.Lock to serialise the first initialisation;
    subsequent calls are lock-free reads.
"""

from __future__ import annotations

import logging
import threading
from typing import List, Union

logger = logging.getLogger("memory.embeddings")

# Default model — fast, lightweight, works for Arabic + English
_DEFAULT_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"

_model = None
_model_lock = threading.Lock()


def _get_model():
    global _model
    if _model is None:
        with _model_lock:
            if _model is None:
                try:
                    from sentence_transformers import SentenceTransformer
                    logger.info("loading_embedding_model", extra={"model": _DEFAULT_MODEL})
                    _model = SentenceTransformer(_DEFAULT_MODEL)
                    logger.info("embedding_model_loaded", extra={"model": _DEFAULT_MODEL})
                except Exception:
                    logger.exception("embedding_model_load_failed")
                    raise
    return _model


def embed(texts: Union[str, List[str]]) -> List[List[float]]:
    """
    Embed one or more texts into float vectors.

    Args:
        texts: A single string or list of strings.

    Returns:
        List of float lists (one per input text).
        Returns an empty list on failure (for graceful degradation).
    """
    if isinstance(texts, str):
        texts = [texts]

    if not texts:
        return []

    try:
        model = _get_model()
        vectors = model.encode(texts, convert_to_numpy=True, show_progress_bar=False)
        return [v.tolist() for v in vectors]
    except Exception:
        logger.exception("embed_failed", extra={"count": len(texts)})
        return []


def embed_single(text: str) -> List[float]:
    """
    Convenience: embed a single string and return a single vector.
    Returns an empty list on failure.
    """
    results = embed([text])
    return results[0] if results else []
