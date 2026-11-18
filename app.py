import os
import uuid
import json
import logging
import threading
from pathlib import Path

from flask import Flask, render_template, request, jsonify, Response, stream_with_context
from werkzeug.utils import secure_filename

from chatbot import ChatBot
from config import settings
from utils.db import (
    get_last_messages, init_db, save_message, get_user_persona, set_user_persona, 
    get_user_memories, add_user_memory, create_thread, get_threads, update_thread, delete_thread,
    log_event, get_analytics_summary, save_rating,
    get_messages_for_embedding, mark_messages_embedded,
    get_facts_for_embedding, mark_facts_embedded,
)
from utils.file_parser import extract_text_from_file
from utils.chart_extractor import extract_chart_data

# --- Hybrid Memory Service (Vector RAG) ---
try:
    from services.memory_service.memory_manager import (
        get_context as mem_get_context,
        background_index_message,
        background_index_document,
        background_index_fact,
    )
    _HAS_VECTOR_MEMORY = True
except ImportError as _ve:
    _HAS_VECTOR_MEMORY = False
    logger_pre = logging.getLogger(__name__)
    logger_pre.warning(f"ChromaDB unavailable — vector memory disabled. ({_ve})")

# محاولة استخدام python-magic لفحص MIME الحقيقي (وليس فقط الامتداد).
# إن لم تكن المكتبة مثبتة، نستمر بفحص الامتداد فقط مع تحذير في اللوج.
try:
    import magic  # pip install python-magic  (يحتاج libmagic على النظام)
    _HAS_MAGIC = True
except ImportError:
    _HAS_MAGIC = False

# محاولة استخدام Flask-Limiter لتحديد معدل الطلبات.
try:
    from flask_limiter import Limiter
    from flask_limiter.util import get_remote_address
    _HAS_LIMITER = True
except ImportError:
    _HAS_LIMITER = False

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# --- إعدادات الأمان الأساسية ---
# SECRET_KEY يُتحقق منه ويُفرض بالفعل داخل config.py (يرفض التشغيل في production
# إن كان لا يزال "change-me"). هنا فقط نمرره لـ Flask، ونولّد قيمة عشوائية مؤقتة
# في التطوير إن تُرك افتراضيًا (config.py يكتفي بتحذير، لا يمنع التشغيل في dev).
_secret = settings.SECRET_KEY.get_secret_value()
if _secret == 'change-me' and settings.ENVIRONMENT != 'production':
    _secret = os.urandom(32).hex()
app.secret_key = _secret

BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

ALLOWED_EXTENSIONS = {'.pdf', '.docx', '.txt', '.xlsx', '.xls', '.csv', '.png', '.jpg', '.jpeg', '.gif', '.bmp', '.tiff'}

# خرائط الامتداد -> أنواع MIME المقبولة (لفحص حقيقي وليس فقط الاسم)
ALLOWED_MIME_TYPES = {
    '.pdf': {'application/pdf'},
    '.docx': {'application/vnd.openxmlformats-officedocument.wordprocessingml.document'},
    '.txt': {'text/plain'},
    '.xlsx': {'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'},
    '.xls': {'application/vnd.ms-excel'},
    '.csv': {'text/csv', 'text/plain'},
    '.png': {'image/png'},
    '.jpg': {'image/jpeg'},
    '.jpeg': {'image/jpeg'},
    '.gif': {'image/gif'},
    '.bmp': {'image/bmp', 'image/x-ms-bmp'},
    '.tiff': {'image/tiff'},
}

MAX_FILE_TEXT_LENGTH = settings.MAX_FILE_TEXT_CHARS
MAX_UPLOAD_SIZE_MB = settings.MAX_UPLOAD_MB
app.config['MAX_CONTENT_LENGTH'] = MAX_UPLOAD_SIZE_MB * 1024 * 1024  # يرفض Flask تلقائيًا أي طلب أكبر (413)

# --- Rate Limiting ---
if _HAS_LIMITER:
    limiter = Limiter(
        get_remote_address,
        app=app,
        default_limits=[settings.RATE_LIMIT_DEFAULT],
        storage_uri="memory://",  # للإنتاج استخدم Redis: redis://localhost:6379
    )
else:
    logger.warning('flask-limiter غير مثبت — لا يوجد حماية من الاستخدام المفرط. ثبّت: pip install flask-limiter')
    limiter = None


def _apply_limit(rule):
    """ديكوريتر آمن: يطبّق الحد فقط إذا كانت المكتبة متوفرة."""
    def decorator(func):
        return limiter.limit(rule)(func) if limiter else func
    return decorator


def allowed_file(filename: str) -> str | None:
    """يرجع الامتداد (بنقطة) إن كان مسموحًا، أو None."""
    if '.' not in filename:
        return None
    ext = '.' + filename.rsplit('.', 1)[1].lower()
    return ext if ext in ALLOWED_EXTENSIONS else None


def validate_mime(filepath: Path, ext: str) -> bool:
    """يتحقق أن المحتوى الفعلي للملف يطابق الامتداد المُعلن، لمنع تزييف نوع الملف."""
    if not _HAS_MAGIC:
        return True  # تراجع صامت إن لم تتوفر المكتبة (تم تسجيل تحذير عند الإقلاع)
    try:
        detected = magic.from_file(str(filepath), mime=True)
    except Exception as e:
        logger.error(f'فشل فحص MIME: {e}')
        return False
    allowed = ALLOWED_MIME_TYPES.get(ext, set())
    return detected in allowed


def truncate_text(text: str, max_chars: int = MAX_FILE_TEXT_LENGTH) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "\n\n[...تم الاقتصاص لتناسب حدود ذاكرة النموذج]"


@app.errorhandler(413)
def file_too_large(e):
    return jsonify({'reply': f'حجم الملف يتجاوز الحد المسموح ({MAX_UPLOAD_SIZE_MB}MB).'}), 413


@app.errorhandler(429)
def rate_limit_exceeded(e):
    # بدون هذا الـ handler، ردّ flask-limiter الافتراضي HTML وليس JSON،
    # فتفشل الواجهة الأمامية بمحاولة تفسير جسم الاستجابة كـ JSON.
    return jsonify({'reply': 'لقد أرسلت عددًا كبيرًا من الطلبات خلال وقت قصير. حاول مجددًا بعد قليل.'}), 429


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/set_persona', methods=['POST'])
def set_persona():
    data = request.get_json(silent=True) or {}
    user_id = data.get('userId', 'anonymous')
    persona = data.get('persona', 'assistant')
    try:
        set_user_persona(user_id, persona)
        return jsonify({'status': 'success'})
    except Exception as e:
        logger.exception('فشل حفظ الشخصية')
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/threads', methods=['GET'])
def get_user_threads():
    user_id = request.args.get('userId', 'anonymous')
    threads = get_threads(user_id)
    return jsonify({'threads': threads})


@app.route('/threads', methods=['POST'])
def new_thread():
    data = request.get_json(silent=True) or {}
    user_id = data.get('userId', 'anonymous')
    title = data.get('title', 'محادثة جديدة')
    thread_id = create_thread(user_id, title)
    return jsonify({'threadId': thread_id})


@app.route('/threads/<thread_id>', methods=['PUT'])
def edit_thread(thread_id):
    data = request.get_json(silent=True) or {}
    title = data.get('title')
    is_pinned = data.get('is_pinned')
    update_thread(thread_id, title=title, is_pinned=is_pinned)
    return jsonify({'status': 'success'})


@app.route('/threads/<thread_id>', methods=['DELETE'])
def remove_thread(thread_id):
    delete_thread(thread_id)
    return jsonify({'status': 'success'})


@app.route('/threads/<thread_id>/messages', methods=['GET'])
def get_messages(thread_id):
    user_id = request.args.get('userId', 'anonymous')
    history = get_last_messages(user_id, thread_id, limit=50)
    formatted = []
    for msg in history:
        sender = 'user' if msg['role'].lower() == 'user' else 'bot'
        formatted.append({'sender': sender, 'text': msg['parts'][0]})
    return jsonify({'messages': formatted})


@app.route('/dashboard')
def dashboard():
    return render_template('dashboard.html')


@app.route('/api/analytics')
def api_analytics():
    summary = get_analytics_summary()
    return jsonify(summary)


@app.route('/compare', methods=['POST'])
def compare():
    """يُرسل نفس السؤال لمزودين بالتوازي ويُعيد كلا الردين."""
    if bot is None:
        return jsonify({'error': init_error or 'البوت غير مهيأ'}), 503

    data = request.get_json(silent=True) or {}
    message = (data.get('message') or '').strip()
    provider1 = (data.get('provider1') or 'gemini').lower()
    provider2 = (data.get('provider2') or 'openai').lower()
    persona = data.get('persona', 'assistant')
    user_id = data.get('userId', 'anonymous')
    thread_id = data.get('threadId', 'default')

    if not message:
        return jsonify({'error': 'الرسالة فارغة'}), 400

    history = get_last_messages(user_id, thread_id, limit=20)
    history.append({'role': 'user', 'parts': [message]})

    from concurrent.futures import ThreadPoolExecutor, as_completed
    results = {}

    def call_provider(name):
        try:
            return name, bot.ask_provider(name, history, persona=persona)
        except Exception as e:
            return name, f'⚠️ خطأ: {str(e)}'

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = {executor.submit(call_provider, p): p for p in [provider1, provider2]}
        for future in as_completed(futures):
            name, reply = future.result()
            results[name] = reply

    return jsonify({
        'provider1': {'name': provider1, 'reply': results.get(provider1, '⚠️ لم يتم استلام رد')},
        'provider2': {'name': provider2, 'reply': results.get(provider2, '⚠️ لم يتم استلام رد')},
    })


@app.route('/rate', methods=['POST'])
def rate_message():
    """يحفظ تقييم المستخدم لرسالة بعينها."""
    data = request.get_json(silent=True) or {}
    user_id = data.get('userId', 'anonymous')
    message_id = data.get('messageId', '')
    rating = data.get('rating', 0)
    if not message_id or rating not in (1, -1):
        return jsonify({'error': 'بيانات غير صالحة'}), 400
    save_rating(user_id, message_id, rating)
    return jsonify({'status': 'ok'})


@app.route('/translate', methods=['POST'])
def translate_message():
    """يترجم نصاً باستخدام الـ LLM إلى اللغة المحددة."""
    if bot is None:
        return jsonify({'error': 'البوت غير مهيأ'}), 503
    data = request.get_json(silent=True) or {}
    text = (data.get('text') or '').strip()
    target_lang = (data.get('targetLang') or 'English').strip()
    if not text:
        return jsonify({'error': 'النص فارغ'}), 400
    try:
        from chatbot import LLMRequest, Message
        req = LLMRequest(
            system=f"أنت مترجم محترف. ترجم النص التالي إلى {target_lang} فقط، دون أي شرح أو إضافة.",
            messages=[Message(role='user', content=text)]
        )
        translation, _ = bot.retry.execute(lambda: bot.chain.execute(req))
        return jsonify({'translation': translation})
    except Exception as e:
        logger.exception('translation_failed')
        return jsonify({'error': str(e)}), 500


@app.route('/suggestions', methods=['POST'])
def get_suggestions():
    """يولّد 3 أسئلة متابعة مقترحة بناءً على آخر رد من البوت."""
    if bot is None:
        return jsonify({'suggestions': []}), 200
    data = request.get_json(silent=True) or {}
    reply = (data.get('reply') or '').strip()
    if not reply:
        return jsonify({'suggestions': []}), 200
    try:
        suggestions = bot.generate_suggestions(reply)
        return jsonify({'suggestions': suggestions})
    except Exception as e:
        logger.exception('suggestions_failed')
        return jsonify({'suggestions': []}), 200


@app.route('/chat/stream', methods=['POST'])
def chat_stream():
    """
    SSE endpoint: يبثّ tokens نصية حرفًا بحرف عبر text/event-stream.
    يتوقع العميل حدثين:
      data: {"token": "..."}   -- كل token فردي
      data: {"done": true, "provider": "...", "full": "..."}  -- إشارة الانتهاء
    """
    if bot is None:
        def _err():
            yield f"data: {json.dumps({'error': init_error or 'Bot not ready'})}\n\n"
        return Response(stream_with_context(_err()), mimetype='text/event-stream')

    data = request.get_json(silent=True) or {}
    message   = (data.get('message') or '').strip()
    user_id   = data.get('userId', 'anonymous')
    thread_id = data.get('threadId', 'default')

    if not message:
        def _bad():
            yield f"data: {json.dumps({'error': 'empty message'})}\n\n"
        return Response(stream_with_context(_bad()), mimetype='text/event-stream')

    # Save user message & load history
    try:
        save_message(user_id, thread_id, 'User', message)
        history = get_last_messages(user_id, thread_id, limit=50)
    except Exception as e:
        def _dberr():
            yield f"data: {json.dumps({'error': str(e)})}\n\n"
        return Response(stream_with_context(_dberr()), mimetype='text/event-stream')

    persona  = get_user_persona(user_id)
    memories = get_user_memories(user_id)

    # --- Hybrid Memory: semantic context injection (graceful degradation) ---
    vector_context = ""
    if _HAS_VECTOR_MEMORY:
        try:
            vector_context = mem_get_context(user_id, message, top_k=4)
        except Exception:
            logger.warning("vector_context_retrieval_failed — falling back to SQLite only")

    threading.Thread(
        target=background_extract_memory, args=(user_id, message, bot)
    ).start()

    # Fire-and-forget: index this user message in the vector store
    if _HAS_VECTOR_MEMORY:
        background_index_message(
            message_id=f"msg_{user_id}_{thread_id}_{len(history)}",
            user_id=user_id, thread_id=thread_id, role="user", text=message,
        )

    @stream_with_context
    def generate():
        full_reply = []
        provider_name = 'unknown'
        try:
            for token, pname in bot.ask_stream(
                    history, persona=persona, memories=memories,
                    user_id=user_id, vector_context=vector_context
                ):
                    provider_name = pname
                    full_reply.append(token)
                    yield f"data: {json.dumps({'token': token})}\n\n"
        except Exception as e:
            logger.exception('stream_chat_failed')
            yield f"data: {json.dumps({'error': str(e)})}\n\n"
            return

        full_text = ''.join(full_reply)
        try:
            save_message(user_id, thread_id, 'AI', full_text)
        except Exception:
            logger.exception('stream_save_reply_failed')

        yield f"data: {json.dumps({'done': True, 'provider': provider_name, 'full': full_text})}\n\n"

    return Response(
        generate(),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no',   # disable Nginx buffering if behind proxy
            'Connection': 'keep-alive',
        }
    )


bot = None
init_error = None

try:
    init_db()
    bot = ChatBot()
except Exception as e:
    init_error = str(e)
    logger.exception('فشل تهيئة ChatBot أو قاعدة البيانات')


def _startup_vector_sync():
    """
    Background startup task: back-fills ChromaDB with existing SQLite messages and facts
    that were not yet embedded. Runs once at startup and is a no-op if all are already synced.
    """
    if not _HAS_VECTOR_MEMORY:
        return
    try:
        msgs = get_messages_for_embedding(limit=500)
        if msgs:
            logger.info(f"vector_sync: embedding {len(msgs)} messages...")
            for m in msgs:
                background_index_message(
                    message_id=f"msg_{m['id']}",
                    user_id=m['user_id'],
                    thread_id=m['thread_id'],
                    role=m['role'],
                    text=m['message'],
                )
            mark_messages_embedded([m['id'] for m in msgs])

        facts = get_facts_for_embedding()
        if facts:
            logger.info(f"vector_sync: embedding {len(facts)} facts...")
            for f in facts:
                background_index_fact(f['user_id'], f['memory'])
            mark_facts_embedded([f['id'] for f in facts])
    except Exception:
        logger.exception("startup_vector_sync_failed")


threading.Thread(target=_startup_vector_sync, daemon=True, name="startup-vector-sync").start()


def background_extract_memory(user_id: str, message: str, bot_instance: ChatBot):
    try:
        new_mems = bot_instance.extract_memory(message)
        for m in new_mems:
            add_user_memory(user_id, m)
            if _HAS_VECTOR_MEMORY:
                background_index_fact(user_id, m)
    except Exception as e:
        logger.error(f"background memory extraction failed: {e}")


@app.route('/chat', methods=['POST'])
@_apply_limit(settings.RATE_LIMIT_CHAT)
def chat():
    data = request.get_json(silent=True) or {}
    message = data.get('message', '').strip()
    user_id = data.get('userId', 'anonymous')
    thread_id = data.get('threadId', 'default')

    if not message:
        return jsonify({'reply': 'يرجى إرسال رسالة صحيحة.'}), 400

    if len(message) > settings.MAX_MESSAGE_LENGTH:
        return jsonify({'reply': 'الرسالة طويلة جدًا.'}), 400

    if bot is None:
        return jsonify({'reply': f'الخادم غير مُهيأ: {init_error}'}), 500

    try:
        save_message(user_id, thread_id, 'User', message)
        history = get_last_messages(user_id, thread_id, limit=50)
    except Exception as e:
        logger.exception('فشل الوصول لقاعدة البيانات')
        return jsonify({'reply': 'تعذّر حفظ/قراءة سجل المحادثة حاليًا، حاول مجددًا.'}), 500

    try:
        persona = get_user_persona(user_id)
        memories = get_user_memories(user_id)

        # --- Hybrid Memory: semantic context injection ---
        vector_context = ""
        if _HAS_VECTOR_MEMORY:
            try:
                vector_context = mem_get_context(user_id, message, top_k=4)
            except Exception:
                logger.warning("vector_context_retrieval_failed — falling back to SQLite only")
            background_index_message(
                message_id=f"msg_{user_id}_{thread_id}_{len(history)}",
                user_id=user_id, thread_id=thread_id, role="user", text=message,
            )

        # استخراج الذكريات في الخلفية حتى لا يبطئ رد البوت
        threading.Thread(target=background_extract_memory, args=(user_id, message, bot)).start()
        try:
            reply = bot.ask(history, persona=persona, memories=memories, user_id=user_id, vector_context=vector_context)
        except Exception as e:
            logger.exception('فشل الاتصال بمزود AI')
            return jsonify({'reply': f'عذراً، الخدمة غير متوفرة حالياً بسبب مشكلة في الاتصال بمزود الذكاء الاصطناعي. يرجى التأكد من صحة مفتاح API والمحاولة لاحقاً.\n\n(تفاصيل الخطأ: {e})'}), 500

    except Exception as e:
        logger.exception('فشل جلب سياق المستخدم')
        return jsonify({'reply': 'حدث خطأ داخلي.'}), 500

    try:
        save_message(user_id, thread_id, 'AI', reply)
    except Exception:
        logger.exception('فشل حفظ رد الذكاء الاصطناعي (سيُعرض للمستخدم رغم ذلك)')
    return jsonify({'reply': reply})


@app.route('/upload', methods=['POST'])
@_apply_limit(settings.RATE_LIMIT_UPLOAD)
def upload():
    user_id = request.form.get('userId', 'anonymous')
    thread_id = request.form.get('threadId', 'default')
    if 'file' not in request.files:
        return jsonify({'reply': 'يرجى اختيار ملف.'}), 400

    file = request.files['file']
    if not file or file.filename == '':
        return jsonify({'reply': 'يرجى اختيار ملف صحيح.'}), 400

    original_filename = secure_filename(file.filename) or 'file'
    ext = os.path.splitext(original_filename)[1].lower()

    log_event(user_id, "upload", details=original_filename)
    if not ext:
        return jsonify({'reply': 'نوع الملف غير مدعوم. استخدم PDF أو DOCX أو TXT أو Excel أو صورة.'}), 400

    # اسم فريد لمنع تعارض الملفات وهجمات تخمين المسار / الكتابة فوق ملفات أخرى
    unique_name = f"{uuid.uuid4().hex}{ext}"
    saved_path = UPLOAD_DIR / unique_name
    file.save(saved_path)

    try:
        # فحص MIME الفعلي بعد الحفظ (لا يمكن فحص المحتوى قبل حفظه على القرص)
        if not validate_mime(saved_path, ext):
            return jsonify({'reply': 'محتوى الملف لا يطابق نوعه المُعلن. تم رفض الملف.'}), 400

        try:
            file_text = extract_text_from_file(saved_path)
        except Exception as e:
            logger.exception('فشل استخراج النص من الملف')
            return jsonify({'reply': f'فشل قراءة الملف: {e}'}), 500

        if not file_text.strip():
            return jsonify({'reply': 'الملف لا يحتوي على نص قابل للقراءة.'}), 400

        truncated_text = truncate_text(file_text)
        save_message(user_id, thread_id, 'User', f'ملف مرفق: {original_filename}\n\n{truncated_text}')

        if bot is None:
            return jsonify({
                'reply': f'تم رفع الملف "{original_filename}" وقراءة محتواه، '
                         f'ولكن الخادم غير مُهيأ لتحليل المحتوى: {init_error}'
            }), 500

        history = get_last_messages(user_id, thread_id, limit=50)
        analysis_instruction = (
            'قم بتحليل محتوى الصورة المرفقة ووصف ما تراه.'
            if ext in {'.png', '.jpg', '.jpeg'}
            else 'قم بتحليل محتوى الملف المرفق وأعطني ملخصًا موجزًا.'
        )
        analysis_history = history + [
            {'role': 'User', 'parts': [f'ملف مرفق: {original_filename}\n\n{truncated_text}']},
            {'role': 'User', 'parts': [analysis_instruction]}
        ]

        try:
            persona = get_user_persona(user_id)
            memories = get_user_memories(user_id)
            analysis_reply = bot.ask(analysis_history, persona=persona, memories=memories, user_id=user_id)
        except Exception as e:
            logger.exception('فشل تحليل محتوى الملف')
            return jsonify({'reply': f'تم رفع الملف "{original_filename}"، لكن فشل تحليل المحتوى: {e}'}), 500

        save_message(user_id, thread_id, 'AI', analysis_reply)

        # Auto chart: extract numeric chart data for spreadsheets
        chart_data = None
        if ext in {'.csv', '.xlsx', '.xls'}:
            try:
                chart_data = extract_chart_data(saved_path)
            except Exception:
                logger.warning('chart_extraction_failed (non-critical)')

        # --- Hybrid Memory: index document in ChromaDB for future semantic retrieval ---
        if _HAS_VECTOR_MEMORY and file_text.strip():
            doc_id = f"doc_{user_id}_{uuid.uuid4().hex[:8]}"
            background_index_document(
                document_id=doc_id,
                user_id=user_id,
                filename=original_filename,
                text=file_text,
            )

        response_payload = {
            'reply': f'تم رفع الملف "{original_filename}" وقراءة محتواه بنجاح. تحليل مبدئي:\n{analysis_reply}'
        }
        if chart_data:
            response_payload['chart_data'] = chart_data
        return jsonify(response_payload)

    finally:
        # حذف الملف من القرص بعد المعالجة — لا حاجة للاحتفاظ به بعد استخراج النص
        try:
            saved_path.unlink(missing_ok=True)
        except Exception as e:
            logger.error(f'فشل حذف الملف المؤقت {saved_path}: {e}')


if __name__ == '__main__':
    debug_mode = settings.ENVIRONMENT != 'production'
    app.run(host='127.0.0.1', port=5000, debug=debug_mode, use_reloader=False)