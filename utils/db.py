import logging
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator, List, Dict, Any

from config import settings

logger = logging.getLogger("app.db")

BASE_DIR = Path(__file__).resolve().parent.parent

# نستخدم settings.DATABASE_PATH كمصدر الحقيقة الوحيد (قابل للضبط عبر .env)،
# مع دعم مسار مطلق أو نسبي لمجلد data/ داخل المشروع للحفاظ على التوافق القديم.
_configured_path = Path(settings.DATABASE_PATH)
if _configured_path.is_absolute():
    DB_PATH = _configured_path
else:
    DATA_DIR = BASE_DIR / "data"
    DB_PATH = DATA_DIR / _configured_path.name if _configured_path.name.endswith(".db") else DATA_DIR / "messages.db"

DB_PATH.parent.mkdir(parents=True, exist_ok=True)

# مهلة الانتظار قبل رمي "database is locked" عند تزامن الكتابة (بالمللي ثانية)
BUSY_TIMEOUT_MS = 5000

# مدة الاحتفاظ الافتراضية بالرسائل (أيام) — سياسة خصوصية بسيطة، قابلة للاستدعاء دوريًا
DEFAULT_RETENTION_DAYS = 90


@contextmanager
def _get_connection() -> Iterator[sqlite3.Connection]:
    """
    نقطة اتصال موحّدة تُفعّل WAL mode + busy_timeout على كل اتصال،
    بدل تكرار هذا الإعداد في كل دالة على حدة.
    """
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS};")
        conn.execute("PRAGMA foreign_keys=ON;")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    with _get_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                thread_id TEXT DEFAULT 'default',
                role TEXT NOT NULL,
                message TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        try:
            conn.execute("ALTER TABLE messages ADD COLUMN thread_id TEXT DEFAULT 'default'")
        except sqlite3.OperationalError:
            pass
        # Migration: add is_embedded flag for Hybrid Memory (safe — ignored if already exists)
        try:
            conn.execute("ALTER TABLE messages ADD COLUMN is_embedded INTEGER DEFAULT 0")
        except sqlite3.OperationalError:
            pass
        # فهرس مركّب لتسريع get_last_messages بشكل كبير مع نمو الجدول
        # (بدون هذا الفهرس، كل استعلام هو full table scan).
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_messages_user_id_id ON messages (user_id, id DESC)"
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS user_settings (
                user_id TEXT PRIMARY KEY,
                persona TEXT DEFAULT 'assistant'
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS user_memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                memory TEXT NOT NULL,
                is_embedded INTEGER DEFAULT 0,
                UNIQUE(user_id, memory)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS threads (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                title TEXT NOT NULL,
                is_pinned INTEGER DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS analytics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT,
                event_type TEXT NOT NULL,
                provider TEXT,
                response_time_ms INTEGER,
                details TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS ratings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                message_id TEXT NOT NULL,
                rating INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(user_id, message_id)
            )
            """
        )
    logger.info("db_initialized", extra={"path": str(DB_PATH)})


def save_message(user_id: str, thread_id: str, role: str, message: str) -> None:
    created_at = datetime.now(timezone.utc).isoformat()
    try:
        with _get_connection() as conn:
            conn.execute(
                "INSERT INTO messages (user_id, thread_id, role, message, created_at) VALUES (?, ?, ?, ?, ?)",
                (user_id, thread_id, role, message, created_at),
            )
            # تحديث وقت آخر نشاط في الغرفة
            conn.execute(
                "UPDATE threads SET updated_at = ? WHERE id = ?",
                (created_at, thread_id)
            )
    except sqlite3.Error:
        logger.exception("save_message_failed", extra={"user_id": user_id, "thread_id": thread_id})
        raise


def get_last_messages(user_id: str, thread_id: str = 'default', limit: int = 50) -> List[Dict[str, Any]]:
    try:
        with _get_connection() as conn:
            cursor = conn.execute(
                "SELECT role, message, created_at FROM messages WHERE user_id = ? AND thread_id = ? ORDER BY id DESC LIMIT ?",
                (user_id, thread_id, limit),
            )
            rows = cursor.fetchall()
    except sqlite3.Error:
        logger.exception("get_last_messages_failed", extra={"user_id": user_id})
        raise

    messages = []
    for role, message, created_at in reversed(rows):
        messages.append({
            "role": role,
            "parts": [message],
            "created_at": created_at,
        })
    return messages


def set_user_persona(user_id: str, persona: str) -> None:
    try:
        with _get_connection() as conn:
            conn.execute(
                "INSERT INTO user_settings (user_id, persona) VALUES (?, ?) "
                "ON CONFLICT(user_id) DO UPDATE SET persona=excluded.persona",
                (user_id, persona),
            )
    except sqlite3.Error:
        logger.exception("set_user_persona_failed", extra={"user_id": user_id})
        raise


def get_user_persona(user_id: str) -> str:
    try:
        with _get_connection() as conn:
            cursor = conn.execute("SELECT persona FROM user_settings WHERE user_id = ?", (user_id,))
            row = cursor.fetchone()
            return row[0] if row else "assistant"
    except sqlite3.Error:
        logger.exception("get_user_persona_failed", extra={"user_id": user_id})
        return "assistant"


def delete_user_messages(user_id: str) -> int:
    """
    يحذف كل رسائل مستخدم معيّن (right-to-be-forgotten). يُرجع عدد الصفوف المحذوفة.
    مفيد لتلبية طلبات حذف البيانات الشخصية (GDPR وما شابه).
    """
    with _get_connection() as conn:
        cursor = conn.execute("DELETE FROM messages WHERE user_id = ?", (user_id,))
        deleted = cursor.rowcount
    logger.info("user_messages_deleted", extra={"user_id": user_id, "count": deleted})
    return deleted


def purge_old_messages(retention_days: int = DEFAULT_RETENTION_DAYS) -> int:
    """
    سياسة احتفاظ بسيطة: يحذف الرسائل الأقدم من retention_days.
    يُفضّل استدعاؤها من مهمة مجدولة (cron / APScheduler) بدل تركها بلا حد أقصى للأبد.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=retention_days)).isoformat()
    with _get_connection() as conn:
        cursor = conn.execute("DELETE FROM messages WHERE created_at < ?", (cutoff,))
        deleted = cursor.rowcount
    logger.info("old_messages_purged", extra={"deleted": deleted, "retention_days": retention_days})
    return deleted


def add_user_memory(user_id: str, memory: str) -> None:
    try:
        with _get_connection() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO user_memories (user_id, memory) VALUES (?, ?)",
                (user_id, memory),
            )
    except sqlite3.Error:
        logger.exception("add_user_memory_failed", extra={"user_id": user_id})


def get_user_memories(user_id: str) -> List[str]:
    try:
        with _get_connection() as conn:
            cursor = conn.execute("SELECT memory FROM user_memories WHERE user_id = ?", (user_id,))
            rows = cursor.fetchall()
            return [row[0] for row in rows]
    except sqlite3.Error:
        logger.exception("get_user_memories_failed", extra={"user_id": user_id})
        return []


def create_thread(user_id: str, title: str = "محادثة جديدة") -> str:
    thread_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    try:
        with _get_connection() as conn:
            conn.execute(
                "INSERT INTO threads (id, user_id, title, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                (thread_id, user_id, title, now, now)
            )
        return thread_id
    except sqlite3.Error:
        logger.exception("create_thread_failed", extra={"user_id": user_id})
        raise


def get_threads(user_id: str) -> List[Dict]:
    try:
        with _get_connection() as conn:
            cursor = conn.execute(
                "SELECT id, title, is_pinned, updated_at FROM threads WHERE user_id = ? ORDER BY is_pinned DESC, updated_at DESC",
                (user_id,)
            )
            return [{"id": r[0], "title": r[1], "is_pinned": bool(r[2]), "updated_at": r[3]} for r in cursor.fetchall()]
    except sqlite3.Error:
        logger.exception("get_threads_failed", extra={"user_id": user_id})
        return []


def update_thread(thread_id: str, title: str = None, is_pinned: bool = None) -> None:
    now = datetime.now(timezone.utc).isoformat()
    try:
        with _get_connection() as conn:
            if title is not None:
                conn.execute("UPDATE threads SET title = ?, updated_at = ? WHERE id = ?", (title, now, thread_id))
            if is_pinned is not None:
                conn.execute("UPDATE threads SET is_pinned = ?, updated_at = ? WHERE id = ?", (int(is_pinned), now, thread_id))
    except sqlite3.Error:
        logger.exception("update_thread_failed", extra={"thread_id": thread_id})
        raise


def delete_thread(thread_id: str) -> None:
    try:
        with _get_connection() as conn:
            conn.execute("DELETE FROM threads WHERE id = ?", (thread_id,))
            conn.execute("DELETE FROM messages WHERE thread_id = ?", (thread_id,))
    except sqlite3.Error:
        logger.exception("delete_thread_failed", extra={"thread_id": thread_id})
        raise


def log_event(user_id: str, event_type: str, provider: str = None, response_time_ms: int = None, details: str = None) -> None:
    created_at = datetime.now(timezone.utc).isoformat()
    try:
        with _get_connection() as conn:
            conn.execute(
                "INSERT INTO analytics (user_id, event_type, provider, response_time_ms, details, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (user_id, event_type, provider, response_time_ms, details, created_at)
            )
    except sqlite3.Error:
        logger.exception("log_event_failed", extra={"user_id": user_id, "event_type": event_type})


def get_analytics_summary() -> Dict:
    try:
        with _get_connection() as conn:
            cursor = conn.execute("SELECT COUNT(*) FROM messages")
            total_messages = cursor.fetchone()[0]

            cursor = conn.execute("SELECT COUNT(*) FROM analytics WHERE event_type = 'upload'")
            total_uploads = cursor.fetchone()[0]
            
            cursor = conn.execute("SELECT AVG(response_time_ms) FROM analytics WHERE event_type = 'chat' AND response_time_ms IS NOT NULL")
            avg_resp = cursor.fetchone()[0]
            avg_response_time = int(avg_resp) if avg_resp else 0

            cursor = conn.execute("SELECT provider, COUNT(*) FROM analytics WHERE event_type = 'chat' GROUP BY provider")
            providers_usage = {row[0]: row[1] for row in cursor.fetchall() if row[0]}

            seven_days_ago = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
            cursor = conn.execute(
                "SELECT date(created_at), COUNT(*) FROM messages WHERE created_at >= ? GROUP BY date(created_at) ORDER BY date(created_at) ASC",
                (seven_days_ago,)
            )
            daily_messages = {row[0]: row[1] for row in cursor.fetchall()}

            cursor = conn.execute("SELECT details, COUNT(*) FROM analytics WHERE event_type = 'error' GROUP BY details ORDER BY COUNT(*) DESC LIMIT 5")
            top_errors = [{"error": row[0], "count": row[1]} for row in cursor.fetchall()]

            return {
                "total_messages": total_messages,
                "total_uploads": total_uploads,
                "avg_response_time": avg_response_time,
                "providers_usage": providers_usage,
                "daily_messages": daily_messages,
                "top_errors": top_errors
            }
    except sqlite3.Error:
        logger.exception("get_analytics_summary_failed")
        return {}


def get_messages_for_embedding(limit: int = 200) -> List[Dict[str, Any]]:
    """
    Return messages not yet embedded into ChromaDB.
    Called once at startup to back-fill the vector store.
    """
    try:
        with _get_connection() as conn:
            cursor = conn.execute(
                "SELECT id, user_id, thread_id, role, message FROM messages "
                "WHERE is_embedded = 0 ORDER BY id ASC LIMIT ?",
                (limit,),
            )
            return [
                {"id": r[0], "user_id": r[1], "thread_id": r[2], "role": r[3], "message": r[4]}
                for r in cursor.fetchall()
            ]
    except sqlite3.Error:
        logger.exception("get_messages_for_embedding_failed")
        return []


def mark_messages_embedded(message_ids: List[int]) -> None:
    """Mark messages as embedded after successful vector store upsert."""
    if not message_ids:
        return
    try:
        placeholders = ",".join("?" * len(message_ids))
        with _get_connection() as conn:
            conn.execute(
                f"UPDATE messages SET is_embedded = 1 WHERE id IN ({placeholders})",
                message_ids,
            )
    except sqlite3.Error:
        logger.exception("mark_messages_embedded_failed")


def get_facts_for_embedding() -> List[Dict[str, Any]]:
    """Return user memory facts not yet embedded into ChromaDB."""
    try:
        with _get_connection() as conn:
            cursor = conn.execute(
                "SELECT id, user_id, memory FROM user_memories WHERE is_embedded = 0"
            )
            return [{"id": r[0], "user_id": r[1], "memory": r[2]} for r in cursor.fetchall()]
    except sqlite3.Error:
        logger.exception("get_facts_for_embedding_failed")
        return []


def mark_facts_embedded(fact_ids: List[int]) -> None:
    """Mark facts as embedded after successful vector store upsert."""
    if not fact_ids:
        return
    try:
        placeholders = ",".join("?" * len(fact_ids))
        with _get_connection() as conn:
            conn.execute(
                f"UPDATE user_memories SET is_embedded = 1 WHERE id IN ({placeholders})",
                fact_ids,
            )
    except sqlite3.Error:
        logger.exception("mark_facts_embedded_failed")


def save_rating(user_id: str, message_id: str, rating: int) -> None:
    """حفظ أو تحديث تقييم المستخدم لرسالة بعينها (INSERT OR REPLACE)."""
    created_at = datetime.now(timezone.utc).isoformat()
    try:
        with _get_connection() as conn:
            conn.execute(
                "INSERT INTO ratings (user_id, message_id, rating, created_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(user_id, message_id) DO UPDATE SET rating=excluded.rating, created_at=excluded.created_at",
                (user_id, message_id, rating, created_at)
            )
    except sqlite3.Error:
        logger.exception("save_rating_failed", extra={"user_id": user_id, "message_id": message_id})