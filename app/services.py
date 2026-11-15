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


class InsufficientCreditError(HTTPException):
    def __init__(self, message: str):
        super().__init__(
            status.HTTP_402_PAYMENT_REQUIRED,
            {"code": "insufficient_credit", "message": message},
        )


def reactivate_due(db: Session) -> None:
    """Session 'exhausted' yang sudah lewat credits_reset_at dianggap punya credit lagi."""
    db.execute(
        update(BrowserSession)
        .where(
            BrowserSession.status == "exhausted",
            BrowserSession.credits_reset_at.is_not(None),
            BrowserSession.credits_reset_at <= utcnow(),
        )
        .values(status="unknown")
    )
    db.commit()


def usable_sessions(db: Session) -> list[BrowserSession]:
    """Session yang masih boleh dipakai (bukan expired/exhausted); default di urutan pertama."""
    reactivate_due(db)
    rows = db.scalars(
        select(BrowserSession)
        .where(BrowserSession.status.in_(["unknown", "ok"]))
        .order_by(BrowserSession.is_default.desc(), BrowserSession.id)
    ).all()
    return list(rows)


def mark_exhausted(session_id: int) -> int | None:
    """Tandai credit session habis. Kalau session ini default, default pindah ke session
    berikutnya yang masih usable. Return id default yang baru (atau None kalau tidak berubah)."""
    with SessionLocal() as db:
        s = db.get(BrowserSession, session_id)
        if not s:
            return None
        s.status = "exhausted"
        if s.credits_limit is not None:
            s.credits_used = s.credits_limit
        s.last_checked_at = utcnow()
        was_default = s.is_default
        db.commit()
        if not was_default:
            return None
        nxt = next((u for u in usable_sessions(db) if u.id != session_id), None)
        if not nxt:
            return None
        set_default(db, nxt.id)
        db.commit()
        return nxt.id


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
        s.status = "exhausted" if sub.get("remaining") is not None and sub["remaining"] <= 0 else "ok"
        s.tier = sub.get("tier")
        s.credits_used = sub.get("used")
        s.credits_limit = sub.get("limit")
        reset = sub.get("reset_unix")
        s.credits_reset_at = (
            datetime.fromtimestamp(reset, timezone.utc).replace(tzinfo=None) if reset else None
        )
        s.last_checked_at = utcnow()
        exhausted = s.status == "exhausted"
        db.commit()
    if exhausted:
        mark_exhausted(session_id)  # kalau ini default, default pindah ke session lain
