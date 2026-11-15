from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .db import SessionLocal
from .models import BrowserSession, utcnow
from .schemas import SessionOut


def session_out(s: BrowserSession) -> SessionOut:
    remaining = None
    if s.credits_limit is not None and s.credits_used is not None:
        remaining = max(s.credits_limit - s.credits_used, 0)
    return SessionOut(
        id=s.id,
        name=s.name,
        is_default=s.is_default,
        status=s.status,
        user_agent=s.user_agent,
        cookie_count=len(s.cookies),
        has_login_storage=s.has_login_storage,
        tier=s.tier,
        credits_used=s.credits_used,
        credits_limit=s.credits_limit,
        credits_remaining=remaining,
        credits_reset_at=s.credits_reset_at,
        last_checked_at=s.last_checked_at,
        created_at=s.created_at,
    )


def resolve_session(db: Session, session_id: int | None) -> BrowserSession:
    """Pakai session_id kalau dikirim, kalau tidak pakai session default."""
    if session_id is not None:
        s = db.get(BrowserSession, session_id)
        if not s:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"session {session_id} tidak ditemukan")
        return s
    s = db.scalar(select(BrowserSession).where(BrowserSession.is_default.is_(True)))
    if not s:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "belum ada session default; kirim session_id atau set default")
    return s


def set_default(db: Session, session_id: int) -> None:
    db.execute(update(BrowserSession).values(is_default=False))
    db.execute(update(BrowserSession).where(BrowserSession.id == session_id).values(is_default=True))


# --- dipanggil dari kode browser (di luar request DB session) ---

def save_cookies(session_id: int, cookies: list[dict]) -> None:
    with SessionLocal() as db:
        s = db.get(BrowserSession, session_id)
        if s and cookies:
            s.cookies = cookies
            db.commit()


def save_storage(session_id: int, storage: dict) -> None:
    with SessionLocal() as db:
        s = db.get(BrowserSession, session_id)
        if s and storage:
            s.storage = storage
            db.commit()


def load_session(session_id: int | None) -> tuple[int, list[dict], str | None, dict | None]:
    """Ambil data yang dibutuhkan browser: (id, cookies, user_agent, storage)."""
    with SessionLocal() as db:
        s = resolve_session(db, session_id)
        return s.id, s.cookies, s.user_agent, s.storage


def mark_status(session_id: int, new_status: str) -> None:
    with SessionLocal() as db:
        s = db.get(BrowserSession, session_id)
        if s:
            s.status = new_status
            s.last_checked_at = utcnow()
            db.commit()


def save_credits(session_id: int, sub: dict) -> None:
    with SessionLocal() as db:
        s = db.get(BrowserSession, session_id)
        if not s:
            return
        s.status = "ok"
        s.tier = sub.get("tier")
        s.credits_used = sub.get("used")
        s.credits_limit = sub.get("limit")
        reset = sub.get("reset_unix")
        s.credits_reset_at = (
            datetime.fromtimestamp(reset, timezone.utc).replace(tzinfo=None) if reset else None
        )
        s.last_checked_at = utcnow()
        db.commit()
