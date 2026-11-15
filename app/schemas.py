from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, model_validator

# Cookie bisa dikirim sebagai:
#  - array JSON hasil export extension Chrome (Cookie-Editor / EditThisCookie)
#  - string header "Cookie: a=b; c=d" (copy dari DevTools > Network)
CookieInput = list[dict[str, Any]] | str


# Hasil snippet console (GET /sessions/export-script): {"localStorage": {...}, "indexedDB": [...]}.
# Boleh dikirim sebagai object JSON atau string JSON (langsung paste dari clipboard).
StorageInput = dict[str, Any] | str


class _LoginData(BaseModel):
    cookies: CookieInput | None = None
    storage: StorageInput | None = None
    user_agent: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def _need_something(self):
        if not self.cookies and not self.storage and self.user_agent is None:
            raise ValueError("isi 'storage' (disarankan) dan/atau 'cookies'")
        if self.user_agent and not self.user_agent.startswith("Mozilla/"):
            raise ValueError(
                "user_agent harus dari navigator.userAgent (diawali 'Mozilla/'), "
                "atau string kosong untuk pakai bawaan Chromium"
            )
        return self


class SessionCreate(_LoginData):
    name: str = Field(min_length=1, max_length=100)
    set_default: bool = False


class CookiesUpdate(_LoginData):
    """Ganti data login. Field yang tidak dikirim tidak diubah."""


class SessionOut(BaseModel):
    id: int
    name: str
    is_default: bool
    status: str
    user_agent: str | None
    cookie_count: int
    has_login_storage: bool
    tier: str | None
    credits_used: int | None
    credits_limit: int | None
    credits_remaining: int | None
    credits_reset_at: datetime | None
    last_checked_at: datetime | None
    created_at: datetime


class Credits(BaseModel):
    session_id: int
    tier: str | None
    used: int | None
    limit: int | None
    remaining: int | None
    reset_at: datetime | None
    source: str | None = None  # "network" atau "dom"


class Voice(BaseModel):
    voice_id: str | None
    name: str
    category: str | None = None
    gender: str | None = None
    age: str | None = None
    accent: str | None = None
    language: str | None = None
    locale: str | None = None
    use_case: str | None = None
    descriptive: str | None = None
    description: str | None = None
    preview_url: str | None = None
    cloned_by_count: int | None = None
    free_users_allowed: bool | None = None
    is_added_by_user: bool | None = None


class VoiceList(BaseModel):
    voices: list[Voice]
    has_more: bool
    total_count: int | None
    source: str  # "network" atau "dom"


class TTSRequest(BaseModel):
    text: str = Field(min_length=1, max_length=10_000)
    voice: str | None = Field(
        default=None,
        max_length=200,
        description="Nama voice (awalan boleh), mis. 'Zan'. Kosongkan voice & voice_id untuk pakai DEFAULT_VOICE_*",
    )
    voice_id: str | None = Field(
        default=None, max_length=64, description="voice_id dari GET /voices (lebih pasti dari nama)"
    )
    model_id: str | None = Field(default=None, max_length=100, description="Kosongkan untuk DEFAULT_MODEL_ID")
    stability: float | None = Field(default=None, ge=0, le=1)
    similarity: float | None = Field(default=None, ge=0, le=1)
    session_id: int | None = Field(default=None, description="Kosongkan untuk pakai session default")
    webhook_url: str | None = Field(
        default=None,
        max_length=1000,
        pattern=r"^https?://",
        description="Dipanggil (POST) saat job selesai/gagal. Kosongkan untuk pakai WEBHOOK_URL di .env",
    )

    model_config = {"json_schema_extra": {"examples": [{"text": "Selamat pagi, ini tes suara."}]}}


class JobOut(BaseModel):
    id: str
    session_id: int | None
    voice: str
    voice_id: str | None
    model_id: str | None
    source: str | None
    status: str
    error: str | None
    error_code: str | None = None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    download_url: str | None
    webhook_url: str | None = None
    webhook_status: str | None = None
    webhook_attempts: int | None = None
    webhook_error: str | None = None
