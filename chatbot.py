"""
chatbot.py - Enterprise LLM Orchestration Layer

Responsibility:
- Provider orchestration (NO UI / NO DB / NO Flask)
- Unified LLM interface
- Retry + fallback + observability hooks
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Protocol, Iterable, Callable
import time
import random
import logging
import requests

from config import settings, Provider
from utils.helpers import read_text_file


# =========================================================
# Logging (Structured, Safe)
# =========================================================

logger = logging.getLogger("llm.orchestrator")


# =========================================================
# Error Model (Unified Hierarchy)
# =========================================================

class ProviderError(Exception): ...
class ProviderNotConfiguredError(ProviderError): ...
class AuthenticationError(ProviderError): ...
class RateLimitError(ProviderError): ...
class ProviderTimeoutError(ProviderError): ...
class NetworkError(ProviderError): ...
class InvalidResponseError(ProviderError): ...
class ProviderUnavailableError(ProviderError): ...


# =========================================================
# Core Data Models
# =========================================================

@dataclass(frozen=True)
class Message:
    role: str
    content: str


@dataclass
class LLMRequest:
    system: str
    messages: List[Message]


@dataclass
class LLMResponse:
    text: str
    provider: str
    model: str
    latency: float
    retries: int


# =========================================================
# Conversation Manager (Stateless ChatBot Design)
# =========================================================

class ConversationManager:
    """
    Converts external history into unified Message format.
    No state stored here.
    """

    def normalize(self, history: List[Dict[str, Any]]) -> List[Message]:
        normalized: List[Message] = []

        for h in history:
            role = (h.get("role") or "user").lower()
            role = "user" if role in {"user", "file"} else "assistant"

            parts = h.get("parts", [])
            content = "\n".join(parts)

            normalized.append(Message(role=role, content=content))

        return normalized


# =========================================================
# Prompt Loader (Cached + Reloadable)
# =========================================================

class PromptLoader:
    _cache: Dict[str, str] = {}

    @classmethod
    def load(cls, path: str, *, strict: bool = True) -> str:
        # strict=True افتراضيًا: ملف system prompt حرج لعمل الشات بوت بشكل صحيح.
        # فشل صامت هنا (إرجاع "") أخطر بكثير من فشل واضح عند الإقلاع، لأنه
        # يعني تشغيل الشات بوت بدون أي تعليمات/persona دون أن ينتبه أحد.
        if path not in cls._cache:
            cls._cache[path] = read_text_file(path, strict=strict)
        return cls._cache[path]

    @classmethod
    def reload(cls, path: str, *, strict: bool = True) -> str:
        cls._cache.pop(path, None)
        return cls.load(path, strict=strict)


# =========================================================
# Prompt Builder (Provider-Agnostic Translation Layer)
# =========================================================

class PromptBuilder:

    def openai(self, req: LLMRequest) -> List[Dict[str, str]]:
        out = []
        if req.system:
            out.append({"role": "system", "content": req.system})

        for m in req.messages:
            out.append({"role": m.role, "content": m.content})
        return out

    def claude(self, req: LLMRequest) -> str:
        text = req.system + "\n\n" if req.system else ""

        for m in req.messages:
            prefix = "Human: " if m.role == "user" else "Assistant: "
            text += prefix + m.content + "\n"

        return text + "Assistant:"

    def ollama(self, req: LLMRequest) -> str:
        parts = []
        if req.system:
            parts.append(req.system)

        for m in req.messages:
            parts.append(f"{m.role.upper()}: {m.content}")

        return "\n\n".join(parts)

    def gemini(self, req: LLMRequest) -> List[Dict]:
        contents = []

        for m in req.messages:
            role = "USER" if m.role == "user" else "MODEL"
            contents.append({
                "role": role,
                "parts": [{"text": m.content}]
            })

        return contents


# =========================================================
# Retry Engine (Selective + Exponential Backoff + Jitter)
# =========================================================

class RetryEngine:

    def __init__(self, max_retries: int = 3, base_delay: float = 0.5, max_delay: float = 8.0):
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.max_delay = max_delay

    def _backoff(self, attempt: int) -> float:
        # exponential backoff + jitter عشوائي لمنع "thundering herd" عند فشل جماعي،
        # مع سقف أقصى (max_delay) لمنع تجمّد الطلب لفترة طويلة جدًا.
        delay = self.base_delay * (2 ** attempt)
        delay = min(delay, self.max_delay)
        return delay + random.uniform(0, delay * 0.1)

    def execute(self, fn: Callable[[], str]) -> str:
        last_error = None

        for attempt in range(self.max_retries):
            try:
                return fn()
            except AuthenticationError:
                # خطأ لن يُصلح نفسه بإعادة المحاولة — أوقف فورًا
                raise
            except InvalidResponseError:
                raise
            except RateLimitError as e:
                last_error = e
                delay = self._backoff(attempt)
                logger.warning(
                    "retry_event",
                    extra={"attempt": attempt, "delay": delay, "error_type": type(e).__name__},
                )
                time.sleep(delay)
            except (ProviderTimeoutError, NetworkError, ProviderUnavailableError) as e:
                last_error = e
                delay = self._backoff(attempt)
                logger.warning(
                    "retry_event",
                    extra={"attempt": attempt, "delay": delay, "error_type": type(e).__name__},
                )
                time.sleep(delay)
            except Exception as e:
                # أخطاء غير متوقعة/غير مصنّفة: لا تستحق إعادة محاولة عمياء
                logger.error("unclassified_error", extra={"error_type": type(e).__name__, "error": str(e)})
                raise

        raise last_error


# =========================================================
# Provider Interface (Strategy Pattern)
# =========================================================

class LLMProvider(Protocol):
    name: str

    def generate(self, req: LLMRequest) -> str:
        ...


# =========================================================
# Base Provider Utilities
# =========================================================

class BaseProvider:
    name: str = "base"

    def _log(self, model: str, latency: float, retries: int = 0):
        logger.info(
            "llm_call",
            extra={
                "provider": self.name,
                "model": model,
                "latency": latency,
                "retries": retries,
            }
        )


# قيمة مركزية قابلة للضبط عبر .env (REQUEST_TIMEOUT_SECONDS) بدل ثابت مبعثر هنا
REQUEST_TIMEOUT_SECONDS = settings.REQUEST_TIMEOUT_SECONDS


# =========================================================
# OpenAI Provider
# =========================================================

try:
    from openai import OpenAI
    import openai as openai_sdk
except Exception:
    OpenAI = None
    openai_sdk = None


class OpenAIProvider(BaseProvider):

    name = "openai"

    def __init__(self):
        if OpenAI is None:
            raise ProviderNotConfiguredError("openai not installed")

        key = settings.OPENAI_API_KEY.get_secret_value()
        if not key:
            raise AuthenticationError("missing openai key")

        # عميل واحد يُعاد استخدامه لكل الطلبات بدل إنشائه من جديد كل مرة
        self.client = OpenAI(api_key=key, timeout=REQUEST_TIMEOUT_SECONDS)
        self.model = settings.MODEL
        self.builder = PromptBuilder()

    def generate(self, req: LLMRequest) -> str:
        messages = self.builder.openai(req)
        start = time.monotonic()

        try:
            resp = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=settings.TEMPERATURE,
                max_tokens=settings.MAX_TOKENS,
            )
        except openai_sdk.AuthenticationError as e:
            raise AuthenticationError(str(e)) from e
        except openai_sdk.RateLimitError as e:
            raise RateLimitError(str(e)) from e
        except openai_sdk.APITimeoutError as e:
            raise ProviderTimeoutError(str(e)) from e
        except openai_sdk.APIConnectionError as e:
            raise NetworkError(str(e)) from e
        except openai_sdk.APIError as e:
            raise InvalidResponseError(str(e)) from e

        self._log(self.model, time.monotonic() - start)
        return resp.choices[0].message.content

    def stream_generate(self, req: LLMRequest) -> Iterable[str]:
        """يُولّد الرد حرفًا بحرف عبر OpenAI streaming."""
        messages = self.builder.openai(req)
        try:
            resp = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=settings.TEMPERATURE,
                max_tokens=settings.MAX_TOKENS,
                stream=True,
            )
            for chunk in resp:
                delta = chunk.choices[0].delta.content
                if delta:
                    yield delta
        except openai_sdk.AuthenticationError as e:
            raise AuthenticationError(str(e)) from e
        except openai_sdk.RateLimitError as e:
            raise RateLimitError(str(e)) from e
        except openai_sdk.APITimeoutError as e:
            raise ProviderTimeoutError(str(e)) from e
        except openai_sdk.APIConnectionError as e:
            raise NetworkError(str(e)) from e
        except openai_sdk.APIError as e:
            raise InvalidResponseError(str(e)) from e


# =========================================================
# Gemini Provider
# =========================================================

try:
    from google import genai
    from google.genai import types
    from google.genai import errors as genai_errors
except Exception:
    genai = None
    genai_errors = None


class GeminiProvider(BaseProvider):

    name = "gemini"

    def __init__(self):
        if genai is None:
            raise ProviderNotConfiguredError("gemini not installed")

        key = settings.GEMINI_API_KEY.get_secret_value()
        if not key:
            raise AuthenticationError("missing gemini key")

        self.client = genai.Client(api_key=key)
        self.model = settings.MODEL
        self.builder = PromptBuilder()

    def generate(self, req: LLMRequest) -> str:
        contents = self.builder.gemini(req)
        start = time.monotonic()

        config = types.GenerateContentConfig(
            temperature=settings.TEMPERATURE,
            max_output_tokens=settings.MAX_TOKENS,
            system_instruction=req.system or None,
            http_options=types.HttpOptions(timeout=REQUEST_TIMEOUT_SECONDS * 1000),
        )

        try:
            res = self.client.models.generate_content(
                model=self.model,
                contents=contents,
                config=config,
            )
        except Exception as e:
            # مكتبة google-genai ترمي ClientError/ServerError بأكواد HTTP داخل الرسالة؛
            # نميّز حسب الكود المتاح إن وُجد لتفادي معاملة كل خطأ كـ "غير قابل للتصنيف".
            status = getattr(e, "status_code", None) or getattr(e, "code", None)
            if status == 401 or status == 403:
                raise AuthenticationError(str(e)) from e
            if status == 429:
                raise RateLimitError(str(e)) from e
            if status == 504 or "timeout" in str(e).lower():
                raise ProviderTimeoutError(str(e)) from e
            raise NetworkError(str(e)) from e

        self._log(self.model, time.monotonic() - start)
        return res.text

    def stream_generate(self, req: LLMRequest) -> Iterable[str]:
        """يُولّد الرد حرفًا بحرف عبر Gemini streaming."""
        contents = self.builder.gemini(req)
        config = types.GenerateContentConfig(
            temperature=settings.TEMPERATURE,
            max_output_tokens=settings.MAX_TOKENS,
            system_instruction=req.system or None,
            http_options=types.HttpOptions(timeout=REQUEST_TIMEOUT_SECONDS * 1000),
        )
        try:
            for chunk in self.client.models.generate_content_stream(
                model=self.model,
                contents=contents,
                config=config,
            ):
                if chunk.text:
                    yield chunk.text
        except Exception as e:
            status = getattr(e, "status_code", None) or getattr(e, "code", None)
            if status == 401 or status == 403:
                raise AuthenticationError(str(e)) from e
            if status == 429:
                raise RateLimitError(str(e)) from e
            if status == 504 or "timeout" in str(e).lower():
                raise ProviderTimeoutError(str(e)) from e
            raise NetworkError(str(e)) from e


# =========================================================
# Claude Provider
# =========================================================

try:
    from anthropic import Anthropic
    import anthropic as anthropic_sdk
except Exception:
    Anthropic = None
    anthropic_sdk = None


class ClaudeProvider(BaseProvider):

    name = "claude"

    def __init__(self):
        if Anthropic is None:
            raise ProviderNotConfiguredError("anthropic not installed")

        key = settings.CLAUDE_API_KEY.get_secret_value()
        if not key:
            raise AuthenticationError("missing claude key")

        self.client = Anthropic(api_key=key, timeout=REQUEST_TIMEOUT_SECONDS)
        self.model = settings.MODEL
        self.builder = PromptBuilder()

    def generate(self, req: LLMRequest) -> str:
        prompt = self.builder.claude(req)
        start = time.monotonic()

        try:
            resp = self.client.messages.create(
                model=self.model,
                max_tokens=settings.MAX_TOKENS,
                temperature=settings.TEMPERATURE,
                messages=[{"role": "user", "content": prompt}],
            )
        except anthropic_sdk.AuthenticationError as e:
            raise AuthenticationError(str(e)) from e
        except anthropic_sdk.RateLimitError as e:
            raise RateLimitError(str(e)) from e
        except anthropic_sdk.APITimeoutError as e:
            raise ProviderTimeoutError(str(e)) from e
        except anthropic_sdk.APIConnectionError as e:
            raise NetworkError(str(e)) from e
        except anthropic_sdk.APIError as e:
            raise InvalidResponseError(str(e)) from e

        self._log(self.model, time.monotonic() - start)
        return resp.content[0].text

    def stream_generate(self, req: LLMRequest) -> Iterable[str]:
        """يُولّد الرد حرفًا بحرف عبر Claude streaming."""
        prompt = self.builder.claude(req)
        try:
            with self.client.messages.stream(
                model=self.model,
                max_tokens=settings.MAX_TOKENS,
                temperature=settings.TEMPERATURE,
                messages=[{"role": "user", "content": prompt}],
            ) as stream:
                for text in stream.text_stream:
                    yield text
        except anthropic_sdk.AuthenticationError as e:
            raise AuthenticationError(str(e)) from e
        except anthropic_sdk.RateLimitError as e:
            raise RateLimitError(str(e)) from e
        except anthropic_sdk.APITimeoutError as e:
            raise ProviderTimeoutError(str(e)) from e
        except anthropic_sdk.APIConnectionError as e:
            raise NetworkError(str(e)) from e
        except anthropic_sdk.APIError as e:
            raise InvalidResponseError(str(e)) from e


# =========================================================
# Ollama Provider
# =========================================================

class OllamaProvider(BaseProvider):

    name = "ollama"

    def __init__(self):
        self.url = settings.OLLAMA_URL.rstrip("/")
        self.builder = PromptBuilder()
        self.model = settings.MODEL

    def generate(self, req: LLMRequest) -> str:
        prompt = self.builder.ollama(req)
        start = time.monotonic()

        try:
            r = requests.post(
                f"{self.url}/api/generate",
                json={
                    "model": self.model,
                    "prompt": prompt,
                    "temperature": settings.TEMPERATURE,
                    "max_tokens": settings.MAX_TOKENS,
                },
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
            r.raise_for_status()
        except requests.Timeout:
            raise ProviderTimeoutError("ollama timeout")
        except requests.RequestException as e:
            raise NetworkError(str(e))

        data = r.json()
        self._log(self.model, time.monotonic() - start)
        return data.get("response") or data.get("text") or r.text

    def stream_generate(self, req: LLMRequest) -> Iterable[str]:
        """يُولّد الرد حرفًا بحرف عبر Ollama streaming."""
        import json as _json
        prompt = self.builder.ollama(req)
        try:
            r = requests.post(
                f"{self.url}/api/generate",
                json={
                    "model": self.model,
                    "prompt": prompt,
                    "temperature": settings.TEMPERATURE,
                    "stream": True,
                },
                timeout=REQUEST_TIMEOUT_SECONDS,
                stream=True,
            )
            r.raise_for_status()
            for line in r.iter_lines():
                if line:
                    chunk = _json.loads(line)
                    token = chunk.get("response", "")
                    if token:
                        yield token
                    if chunk.get("done"):
                        break
        except requests.Timeout:
            raise ProviderTimeoutError("ollama timeout")
        except requests.RequestException as e:
            raise NetworkError(str(e))


# =========================================================
# Provider Factory (caches instances — عميل واحد لكل مزوّد طوال عمر العملية)
# =========================================================

class ProviderFactory:

    registry = {
        "openai": OpenAIProvider,
        "gemini": GeminiProvider,
        "claude": ClaudeProvider,
        "ollama": OllamaProvider,
    }

    _instances: Dict[str, LLMProvider] = {}
    _failed: Dict[str, ProviderError] = {}

    @classmethod
    def create(cls, name: str) -> LLMProvider:
        if name not in cls.registry:
            raise ProviderUnavailableError(name)

        if name in cls._instances:
            return cls._instances[name]

        # إن فشلت تهيئة المزوّد سابقًا (مثلاً مفتاح مفقود)، لا داعي لإعادة المحاولة
        # في كل رسالة — لكن نسمح بإعادة المحاولة كل عملية تشغيل جديدة للعملية (process).
        if name in cls._failed:
            raise cls._failed[name]

        try:
            instance = cls.registry[name]()
        except ProviderError as e:
            cls._failed[name] = e
            raise

        cls._instances[name] = instance
        return instance

    @classmethod
    def reset(cls):
        """مفيد للاختبارات أو عند تحديث مفاتيح API بدون إعادة تشغيل العملية بالكامل."""
        cls._instances.clear()
        cls._failed.clear()


# =========================================================
# Provider Chain (Fallback System)
# =========================================================

class ProviderChain:

    order = ["openai", "gemini", "claude", "ollama"]

    def __init__(self):
        preferred = (settings.PROVIDER.value if settings.PROVIDER else "gemini").lower()
        self.order = [preferred] + [name for name in self.order if name != preferred]

    def execute(self, req: LLMRequest) -> str:
        errors = {}

        for name in self.order:
            try:
                provider = ProviderFactory.create(name)
            except ProviderError as e:
                errors[name] = e
                logger.warning(
                    "provider_failed",
                    extra={
                        "provider": name,
                        "error": type(e).__name__,
                        "details": str(e)
                    },
                )
                continue

            try:
                logger.info("provider_attempt", extra={"provider": name})
                return provider.generate(req), name
            except ProviderError as e:
                errors[name] = e
                logger.warning(
                    "provider_failed",
                    extra={
                        "provider": name,
                        "error": type(e).__name__,
                        "details": str(e)
                    },
                )

        # If we got here, all providers failed.
        primary_provider = self.order[0]
        primary_error = errors.get(primary_provider)
        
        if primary_error:
            raise ProviderUnavailableError(f"Primary provider '{primary_provider}' failed: {primary_error}. Fallbacks also failed.")
        else:
            raise ProviderUnavailableError(f"All providers failed to respond. Errors: {errors}")

    def stream(self, req: LLMRequest) -> tuple:
        """
        يُحاول المزوّد المفضل أولاً ثم يتراجع للتالي.
        يُعيد (generator, provider_name) — الـ generator ينتج tokens نصية.
        """
        for name in self.order:
            try:
                provider = ProviderFactory.create(name)
            except ProviderError:
                continue
            if not hasattr(provider, 'stream_generate'):
                continue
            # نُعيد generator factory + الاسم؛ المستدعي مسؤول عن التكرار
            return provider.stream_generate, name
        raise ProviderUnavailableError("No streaming-capable provider available")


# =========================================================
# ChatBot Orchestrator (PURE CORE)
# =========================================================

PERSONAS = {
    "assistant": "أنت مساعد ذكي رسمي ومهني، تجيب بدقة واختصار وتحافظ على نبرة محايدة.",
    "friend": "أنت صديق ودود تتحدث بعفوية ومرح، وتستخدم بعض التعابير الدارجة اللطيفة، وتظهر تعاطفاً ودعماً مستمراً.",
    "tech": "أنت خبير تقني مبرمج ومهندس نظم، تناقش المواضيع التقنية بتفصيل، وتطرح حلولاً برمجية معمقة.",
    "teacher": "أنت معلم صبور ومبسط للمعلومات، تشرح المفاهيم المعقدة بأمثلة بسيطة وتطرح أسئلة للتأكد من الفهم.",
    "writer": "أنت كاتب إبداعي متمكن من اللغة العربية، تستخدم تعابير بلاغية وتكتب بأسلوب مشوق وجذاب."
}

class ChatBot:

    def __init__(self):
        self.conv = ConversationManager()
        self.builder = PromptBuilder()
        self.retry = RetryEngine()

        self.system_prompt = PromptLoader.load(
            "prompts/system_prompt.txt", strict=False
        ) or PERSONAS["assistant"]

        self.chain = ProviderChain()

    def ask(self, history: List[Dict[str, Any]], persona: str = "assistant",
             memories: List[str] = None, user_id: str = "anonymous",
             vector_context: str = "") -> str:

        messages = self.conv.normalize(history)
        
        system_text = PERSONAS.get(persona, self.system_prompt)
        if vector_context:
            system_text += "\n\n### السياق الدلالي ذو الصلة (مُسترجع تلقائياً):\n" + vector_context
        if memories:
            memory_context = "\n\nمعلومات سابقة عن المستخدم لتخصيص الرد:\n" + "\n".join(f"- {m}" for m in memories)
            system_text += memory_context
        
        req = LLMRequest(system=system_text, messages=messages)

        def _call():
            return self.chain.execute(req)

        import time
        from utils.db import log_event
        start_time = time.time()
        try:
            reply, provider_name = self.retry.execute(_call)
            latency = int((time.time() - start_time) * 1000)
            log_event(user_id, "chat", provider=provider_name, response_time_ms=latency)
            return reply
        except Exception as e:
            log_event(user_id, "error", details=str(e)[:200])
            raise e

    def ask_stream(
        self,
        history: List[Dict[str, Any]],
        persona: str = "assistant",
        memories: List[str] = None,
        user_id: str = "anonymous",
        vector_context: str = "",
    ):
        """
        Generator: ينتج tuples (token: str, provider: str | None).
        آخر tuple يكون ("", provider_name) للإشارة إلى الانتهاء.
        """
        import time as _time
        from utils.db import log_event

        messages = self.conv.normalize(history)
        system_text = PERSONAS.get(persona, self.system_prompt)
        # Inject vector context (semantic memory) first, then SQLite memories
        if vector_context:
            system_text += "\n\n### السياق الدلالي ذو الصلة (مُسترجَع تلقائيّاً):\n" + vector_context
        if memories:
            system_text += "\n\nمعلومات سابقة عن المستخدم لتخصيص الرد:\n" + "\n".join(f"- {m}" for m in memories)

        req = LLMRequest(system=system_text, messages=messages)
        stream_fn, provider_name = self.chain.stream(req)

        start = _time.time()
        try:
            for token in stream_fn(req):
                yield token, provider_name
            latency = int((_time.time() - start) * 1000)
            log_event(user_id, "chat", provider=provider_name, response_time_ms=latency)
        except Exception as e:
            log_event(user_id, "error", details=str(e)[:200])
            raise

    def ask_provider(self, provider_name: str, history: List[Dict[str, Any]], persona: str = "assistant") -> str:
        """استدعاء مزود محدد مباشرةً للمقارنة بين النماذج."""
        messages = self.conv.normalize(history)
        system_text = PERSONAS.get(persona, self.system_prompt)
        req = LLMRequest(system=system_text, messages=messages)
        try:
            provider = ProviderFactory.create(provider_name)
            result = provider.generate(req)
            # generate returns a plain string (not a tuple) when called directly
            if isinstance(result, tuple):
                return result[0]
            return result
        except Exception as e:
            raise e

    def generate_suggestions(self, last_bot_reply: str) -> List[str]:
        """توليد 3 أسئلة متابعة مقترحة بناءً على آخر رد من البوت."""
        import json as _json
        system_text = (
            "أنت مساعد ذكي. بناءً على الرد التالي من البوت، اقترح 3 أسئلة متابعة قصيرة ومثيرة للاهتمام يمكن أن يسألها المستخدم. "
            "أعد الإجابة بصيغة JSON فقط كمصفوفة من النصوص، مثال: [\"سؤال 1\", \"سؤال 2\", \"سؤال 3\"]. "
            "لا تضف أي نص إضافي خارج JSON. الأسئلة يجب أن تكون قصيرة (أقل من 10 كلمات) ومرتبطة بالسياق."
        )
        req = LLMRequest(
            system=system_text,
            messages=[Message(role="user", content=f"رد البوت: {last_bot_reply[:1000]}")]
        )
        def _call():
            return self.chain.execute(req)
        try:
            resp, _ = self.retry.execute(_call)
            # استخراج JSON من الرد
            resp = resp.strip()
            # إزالة code fences إن وُجدت
            if resp.startswith("```"):
                resp = resp.split("```")[1]
                if resp.startswith("json"):
                    resp = resp[4:]
            suggestions = _json.loads(resp.strip())
            if isinstance(suggestions, list):
                return [str(s).strip() for s in suggestions[:3] if str(s).strip()]
            return []
        except Exception as e:
            logger.warning(f"فشل توليد الاقتراحات: {e}")
            return []

    def extract_memory(self, user_message: str) -> List[str]:
        system_text = (
            "أنت محلل بيانات. مهمتك استخراج معلومات شخصية صريحة وثابتة عن المستخدم (اسمه، عمره، عمله، اهتماماته الواضحة، تفضيلاته). "
            "لا تستخرج المشاعر المؤقتة أو الأسئلة. اكتب كل معلومة في سطر منفصل. إذا لم تجد أي معلومة هامة، أرجع الكلمة NONE فقط."
        )
        req = LLMRequest(system=system_text, messages=[Message(role="user", content=user_message)])
        
        def _call():
            return self.chain.execute(req)
            
        try:
            resp, _ = self.retry.execute(_call)
            if "NONE" in resp.upper():
                return []
            lines = [line.strip("- *").strip() for line in resp.split("\n") if line.strip("- *").strip()]
            return [line for line in lines if line]
        except Exception as e:
            logger.warning(f"فشل استخراج الذاكرة: {e}")
            return []