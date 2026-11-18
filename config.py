"""
Application configuration.

Loads configuration from environment variables / a .env file and exposes
it through a single, *validated* and *immutable-by-convention* Settings
object.

Dependencies (add to requirements.txt):
    pydantic>=2.0
    pydantic-settings>=2.0

Usage:
    from config import settings

    print(settings.APP_NAME)
    print(settings.MODEL)
    api_key = settings.api_key_for()          # key for the active provider
"""

from __future__ import annotations

import logging
import warnings
from enum import Enum
from functools import lru_cache
from pathlib import Path
from typing import Literal, Optional

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent

# نستخدم logger مستقل هنا (وليس الـ root logger) حتى يظهر ضمن نفس تسلسل
# اللوج المستخدم في باقي المشروع (مثال: "llm.orchestrator" في chatbot.py).
logger = logging.getLogger("app.config")


class Provider(str, Enum):
    OPENAI = "openai"
    GEMINI = "gemini"
    OLLAMA = "ollama"
    CLAUDE = "claude"


# Sensible default model per provider.
DEFAULT_MODELS: dict[Provider, str] = {
    Provider.OPENAI: "gpt-4o-mini",
    Provider.GEMINI: "gemini-2.5-flash",
    Provider.OLLAMA: "llama3",
    Provider.CLAUDE: "claude-sonnet-4-6",
}

# Which settings field holds the API key for each provider.
# Ollama runs locally and needs no key.
_API_KEY_FIELD: dict[Provider, str] = {
    Provider.OPENAI: "OPENAI_API_KEY",
    Provider.GEMINI: "GEMINI_API_KEY",
    Provider.CLAUDE: "CLAUDE_API_KEY",
}


class Settings(BaseSettings):
    """Centralized, validated application settings."""

    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    # --- App metadata -------------------------------------------------
    APP_NAME: str = "AI ChatBot"
    APP_VERSION: str = "1.0.0"
    ENVIRONMENT: Literal["development", "staging", "production"] = "development"
    LOG_LEVEL: str = "INFO"

    # --- Provider / model selection ------------------------------------
    PROVIDER: Provider = Provider.GEMINI
    MODEL: Optional[str] = None
    # If True, a missing API key for PROVIDER raises instead of silently
    # falling back to another provider. Forced on automatically in prod.
    STRICT_PROVIDER: bool = False

    # --- API credentials (SecretStr keeps them out of logs / repr) -----
    OPENAI_API_KEY: SecretStr = SecretStr("")
    GEMINI_API_KEY: SecretStr = SecretStr("")
    CLAUDE_API_KEY: SecretStr = SecretStr("")
    OLLAMA_URL: str = "http://127.0.0.1:11434"

    # --- Generation parameters ------------------------------------------
    TEMPERATURE: float = Field(default=0.7, ge=0.0, le=2.0)
    MAX_TOKENS: int = Field(default=1000, gt=0, le=200_000)
    REQUEST_TIMEOUT_SECONDS: int = Field(default=30, gt=0, le=300)

    # --- Security / session ----------------------------------------------
    SECRET_KEY: SecretStr = SecretStr("change-me")
    SESSION_COOKIE_HTTPONLY: bool = True
    SESSION_COOKIE_SAMESITE: Literal["Strict", "Lax", "None"] = "Lax"

    # --- Uploads (كانت مبعثرة كأرقام ثابتة داخل app.py — مركزية الآن) ------
    MAX_UPLOAD_MB: int = Field(default=15, gt=0, le=200)
    MAX_FILE_TEXT_CHARS: int = Field(default=3000, gt=0)
    MAX_MESSAGE_LENGTH: int = Field(default=8000, gt=0)

    # --- Rate limiting (يُستخدم عبر Flask-Limiter في app.py) --------------
    RATE_LIMIT_CHAT: str = "20 per minute"
    RATE_LIMIT_UPLOAD: str = "10 per minute"
    RATE_LIMIT_DEFAULT: str = "200 per day, 50 per hour"

    # --- Storage -----------------------------------------------------------
    DATABASE_PATH: str = "chat_history.db"

    # ------------------------------------------------------------------
    # Field-level validation
    # ------------------------------------------------------------------

    @field_validator("LOG_LEVEL")
    @classmethod
    def _validate_log_level(cls, v: str) -> str:
        valid = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        v = v.upper()
        if v not in valid:
            raise ValueError(f"LOG_LEVEL must be one of {sorted(valid)}, got {v!r}")
        return v

    @field_validator("OLLAMA_URL")
    @classmethod
    def _validate_ollama_url(cls, v: str) -> str:
        if not (v.startswith("http://") or v.startswith("https://")):
            raise ValueError(f"OLLAMA_URL must start with http:// or https://, got {v!r}")
        return v

    # ------------------------------------------------------------------
    # Cross-field validation (runs after every field is parsed)
    # ------------------------------------------------------------------

    @model_validator(mode="after")
    def _enforce_production_safety(self) -> "Settings":
        if self.ENVIRONMENT == "production":
            self.STRICT_PROVIDER = True
            if self.SECRET_KEY.get_secret_value() == "change-me":
                raise ValueError(
                    "SECRET_KEY must be changed from its default before "
                    "running with ENVIRONMENT=production."
                )
        return self

    @model_validator(mode="after")
    def _resolve_provider_and_model(self) -> "Settings":
        """
        Ensure the active provider actually has credentials. In non-strict
        mode, fall back to the first provider that does (Ollama always
        counts, since it needs no key). Then fill in a default MODEL if
        the caller didn't specify one, or if the selected model belongs to
        the previous provider after a fallback.
        """
        original_provider = self.PROVIDER
        original_model = self.MODEL

        if not self._has_key(self.PROVIDER):
            if self.STRICT_PROVIDER:
                raise ValueError(
                    f"No API key configured for PROVIDER={self.PROVIDER.value!r} "
                    f"(expected env var {_API_KEY_FIELD.get(self.PROVIDER, 'N/A')})."
                )
            fallback = self._first_available_provider()
            if fallback is not None and fallback != self.PROVIDER:
                msg = (
                    f"No API key for provider '{self.PROVIDER.value}'; "
                    f"falling back to '{fallback.value}'."
                )
                warnings.warn(msg, stacklevel=2)
                logger.warning("provider_fallback", extra={
                    "requested_provider": self.PROVIDER.value,
                    "fallback_provider": fallback.value,
                })
                self.PROVIDER = fallback

        if self.MODEL is None:
            self.MODEL = DEFAULT_MODELS[self.PROVIDER]
        elif self.PROVIDER != original_provider and self._model_looks_like_provider_model(original_provider, original_model):
            self.MODEL = DEFAULT_MODELS[self.PROVIDER]

        return self

    @staticmethod
    def _model_looks_like_provider_model(provider: Provider, model: Optional[str]) -> bool:
        if not model:
            return False
        model = model.lower()
        if provider == Provider.OPENAI:
            return model.startswith("gpt-") or model.startswith("gpt4") or "gpt" in model
        if provider == Provider.GEMINI:
            return model.startswith("gemini-")
        if provider == Provider.OLLAMA:
            return model.startswith("llama")
        if provider == Provider.CLAUDE:
            return model.startswith("claude-")
        return False

    @model_validator(mode="after")
    def _warn_on_insecure_secret(self) -> "Settings":
        if self.SECRET_KEY.get_secret_value() == "change-me":
            msg = (
                "SECRET_KEY is set to its insecure default value. "
                "Set a strong SECRET_KEY in your .env before deploying."
            )
            warnings.warn(msg, stacklevel=2)
            # مهم عمليًا: warnings.warn لا تظهر في أنظمة تجميع اللوج (log aggregators)
            # التي تراقب logging فقط — لذلك نسجّلها أيضًا كتحذير لوج حقيقي.
            logger.warning("insecure_secret_key_default")
        return self

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _has_key(self, provider: Provider) -> bool:
        if provider == Provider.OLLAMA:
            return True
        field_name = _API_KEY_FIELD[provider]
        return bool(getattr(self, field_name).get_secret_value())

    def _first_available_provider(self) -> Optional[Provider]:
        for provider in Provider:
            if self._has_key(provider):
                return provider
        return None

    def api_key_for(self, provider: Optional[Provider] = None) -> str:
        """Return the plaintext API key for the given (or active) provider."""
        provider = provider or self.PROVIDER
        if provider == Provider.OLLAMA:
            return ""
        return getattr(self, _API_KEY_FIELD[provider]).get_secret_value()

    def __repr__(self) -> str:  # never leak secrets via logs/debuggers
        return (
            f"Settings(ENVIRONMENT={self.ENVIRONMENT!r}, "
            f"PROVIDER={self.PROVIDER.value!r}, MODEL={self.MODEL!r}, "
            f"APP_NAME={self.APP_NAME!r})"
        )

    __str__ = __repr__


@lru_cache
def get_settings() -> Settings:
    """
    Return a cached Settings singleton.

    Using a function (instead of importing the module-level `settings`
    directly) makes it trivial to override configuration in tests via
    `get_settings.cache_clear()` + monkeypatched env vars, and is the
    pattern FastAPI's dependency-injection docs recommend.
    """
    return Settings()


settings = get_settings()