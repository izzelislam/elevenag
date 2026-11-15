import json
import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.mysql import MEDIUMTEXT
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base

LongText = Text().with_variant(MEDIUMTEXT(), "mysql")


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class BrowserSession(Base):
    """Satu akun ElevenLabs = satu set cookie."""

    __tablename__ = "browser_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    cookies_json: Mapped[str] = mapped_column(LongText)
    # localStorage + IndexedDB login (Firebase); login ElevenLabs disimpan di sini, bukan di cookie
    storage_json: Mapped[str | None] = mapped_column(LongText, nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(500), nullable=True)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    # unknown | ok | expired | exhausted (credit habis; aktif lagi otomatis setelah credits_reset_at)
    status: Mapped[str] = mapped_column(String(20), default="unknown")

    tier: Mapped[str | None] = mapped_column(String(50), nullable=True)
    credits_used: Mapped[int | None] = mapped_column(Integer, nullable=True)
    credits_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    credits_reset_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    @property
    def cookies(self) -> list[dict]:
        return json.loads(self.cookies_json)

    @cookies.setter
    def cookies(self, value: list[dict]) -> None:
        self.cookies_json = json.dumps(value)

    @property
    def storage(self) -> dict | None:
        return json.loads(self.storage_json) if self.storage_json else None

    @storage.setter
    def storage(self, value: dict | None) -> None:
        self.storage_json = json.dumps(value) if value else None

    @property
    def has_login_storage(self) -> bool:
        from .storage_js import has_firebase_auth

        return has_firebase_auth(self.storage)


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    session_id: Mapped[int | None] = mapped_column(
        ForeignKey("browser_sessions.id", ondelete="SET NULL"), nullable=True, index=True
    )
    voice: Mapped[str] = mapped_column(String(200))
    voice_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    model_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    options_json: Mapped[str | None] = mapped_column(Text, nullable=True)  # voice settings
    source: Mapped[str | None] = mapped_column(String(20), nullable=True)  # network | dom
    text: Mapped[str] = mapped_column(LongText)
    # queued | running | done | failed
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    file_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # kode error mesin-baca, mis. "insufficient_credit"
    error_code: Mapped[str | None] = mapped_column(String(40), nullable=True)
    # True = client memilih session_id sendiri -> tidak boleh pindah session otomatis
    session_pinned: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    # webhook saat job selesai/gagal
    webhook_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    webhook_status: Mapped[str | None] = mapped_column(String(20), nullable=True)  # sending|sent|failed
    webhook_attempts: Mapped[int | None] = mapped_column(Integer, nullable=True)
    webhook_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    webhook_sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
