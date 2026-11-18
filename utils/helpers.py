"""
Utility helper functions.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional, Sequence, Union

logger = logging.getLogger(__name__)

PathLike = Union[str, Path]

# Refuse to load absurdly large files into memory by default.
_DEFAULT_MAX_SIZE_BYTES = 50 * 1024 * 1024  # 50 MB

# ترتيب مهم: utf-8-sig يلتقط ملفات UTF-8 مع BOM (شائعة من محرري ويندوز)،
# cp1256 هو الترميز التقليدي للعربية على ويندوز (شائع لمشاريع كهذه).
# latin-1 يبقى الملاذ الأخير لأنه لا يفشل أبدًا في فك الترميز — لكن هذا
# بالتحديد يجعله خطيرًا: قد "ينجح" على بايتات ليست فعليًا latin-1 وينتج
# نصًا مشوّهًا (mojibake) بصمت. لذلك نضعه آخر الترتيب فقط، ونحذّر بقوة
# عند الاعتماد عليه فعليًا.
_DEFAULT_FALLBACK_ENCODINGS: tuple[str, ...] = ("utf-8-sig", "cp1256", "latin-1")


class FileReadError(Exception):
    """Raised when a file cannot be read as text (only when strict=True)."""


def read_text_file(
    path: PathLike,
    *,
    encoding: str = "utf-8",
    fallback_encodings: Optional[Sequence[str]] = _DEFAULT_FALLBACK_ENCODINGS,
    max_size_bytes: Optional[int] = _DEFAULT_MAX_SIZE_BYTES,
    strict: bool = False,
) -> str:
    """
    Read a text file and return its contents.

    Args:
        path: Path to the file (str or Path).
        encoding: Primary encoding to try (default: "utf-8").
        fallback_encodings: Ordered encodings to retry with if decoding with
            `encoding` fails (e.g. a file saved as Windows-1256/Latin-1).
            Set to None or an empty sequence to disable the retry.
            NOTE: "latin-1" never raises UnicodeDecodeError (it maps every
            byte value to a character), so if it's in this list it will
            always "succeed" as a last resort — even on genuinely wrong
            bytes, producing garbled (mojibake) text. A warning is logged
            whenever it is actually the encoding that was used.
        max_size_bytes: Refuse to read files larger than this many bytes,
            to avoid accidentally loading a huge file into memory.
            Set to None to disable the check.
        strict: If True, raise FileReadError on any failure (missing file,
            too large, undecodable). Use strict=True for files whose
            content is critical to correct operation (e.g. a system
            prompt) so failures are loud instead of silently returning "".
            If False (default), return "" and log a warning/error instead.

    Returns:
        The file contents as a string, or "" on failure when strict=False.

    Raises:
        FileReadError: If strict=True and the file is missing, too large,
            or cannot be decoded/read.
    """
    file_path = Path(path)

    def _fail(message: str, exc: Optional[Exception] = None) -> str:
        logger.warning(message)
        if strict:
            raise FileReadError(message) from exc
        return ""

    # is_file() (unlike the original exists()) correctly rejects
    # directories and other non-regular files instead of letting
    # read_text() blow up with IsADirectoryError.
    if not file_path.is_file():
        return _fail(f"File not found: {file_path}")

    if max_size_bytes is not None:
        try:
            size = file_path.stat().st_size
        except OSError as exc:
            return _fail(f"Could not stat file {file_path}: {exc}", exc)

        if size > max_size_bytes:
            return _fail(
                f"File {file_path} is {size} bytes, exceeding the "
                f"{max_size_bytes}-byte limit."
            )

    try:
        return file_path.read_text(encoding=encoding)
    except UnicodeDecodeError as exc:
        encodings_to_try = list(fallback_encodings or ())
        last_exc: Exception = exc

        for i, enc in enumerate(encodings_to_try):
            logger.info(
                "Failed to decode %s as %s, retrying with %s.",
                file_path, encoding if i == 0 else encodings_to_try[i - 1], enc,
            )
            try:
                text = file_path.read_text(encoding=enc)
            except (UnicodeDecodeError, OSError) as exc2:
                last_exc = exc2
                continue

            if enc.lower() in {"latin-1", "latin1", "iso-8859-1"}:
                # وصلنا لملاذ لا يفشل أبدًا — هذا لا يعني أن الترميز صحيح فعليًا.
                logger.warning(
                    "Decoded %s using %s as a last-resort fallback; this "
                    "encoding never raises errors, so the result may be "
                    "garbled (mojibake) if the file wasn't actually %s.",
                    file_path, enc, enc,
                )
            return text

        return _fail(f"Could not decode {file_path} (tried {encoding}, {encodings_to_try}): {last_exc}", last_exc)
    except OSError as exc:
        return _fail(f"Could not read {file_path}: {exc}", exc)