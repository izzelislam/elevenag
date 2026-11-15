import json

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import PlainTextResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..browser import browser_manager
from ..cookies import CookieFormatError, normalize_cookies
from ..db import get_db
from ..models import BrowserSession
from ..schemas import CookiesUpdate, SessionCreate, SessionOut
from ..services import session_out, set_default
from ..storage_js import CONSOLE_SNIPPET, build_payload_snippet, has_firebase_auth

router = APIRouter(prefix="/sessions", tags=["sessions"])


def _get(db: Session, session_id: int) -> BrowserSession:
    s = db.get(BrowserSession, session_id)
    if not s:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "session tidak ditemukan")
    return s


def _parse_cookies(raw) -> list[dict]:
    try:
        return normalize_cookies(raw)
    except (CookieFormatError, ValueError) as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"cookies: {e}")


def _parse_storage(raw) -> dict:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError as e:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"storage bukan JSON valid: {e}")
    if not isinstance(raw, dict) or not isinstance(raw.get("indexedDB", []), list):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "format storage tidak dikenali")
    if not has_firebase_auth(raw):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "storage tidak berisi token login (firebase:authUser); "
            "jalankan snippet di tab elevenlabs.io yang sudah login",
        )
    return raw


def _apply(s: BrowserSession, body: CookiesUpdate | SessionCreate) -> None:
    if body.cookies:
        s.cookies = _parse_cookies(body.cookies)
    if body.storage:
        s.storage = _parse_storage(body.storage)
    if body.user_agent is not None:
        s.user_agent = body.user_agent


@router.get("/snippet", response_class=PlainTextResponse)
def payload_snippet(
    name: str | None = Query(
        default=None,
        max_length=100,
        description="Isi untuk membuat session BARU (body POST /sessions). "
        "Kosongkan untuk memperbarui session yang ada (body PUT /sessions/{id}/login).",
    ),
    set_default: bool = Query(default=False, description="Hanya untuk session baru"),
):
    """Snippet Console Chrome yang menghasilkan body request lengkap
    (storage login + cookies + user_agent [+ name]).

    Cara pakai:
    1. Buka tab elevenlabs.io yang sudah login, DevTools > Console.
    2. Paste snippet ini, Enter.
    3. Klik tombol **Salin payload** di pojok kanan bawah halaman.
    4. Paste sebagai body `POST /sessions` (kalau `name` diisi) atau `PUT /sessions/{id}/login`."""
    hint = "POST /sessions" if name else "PUT /sessions/{id}/login"
    return build_payload_snippet(name, set_default, hint)


@router.get("/export-script", response_class=PlainTextResponse, include_in_schema=False)
def export_script():
    """(lama) Snippet yang hanya mengekspor storage."""
    return CONSOLE_SNIPPET


@router.post("", response_model=SessionOut, status_code=201)
def create_session(body: SessionCreate, db: Session = Depends(get_db)):
    if db.scalar(select(BrowserSession).where(BrowserSession.name == body.name)):
        raise HTTPException(status.HTTP_409_CONFLICT, "nama session sudah dipakai")
    s = BrowserSession(name=body.name, cookies_json="[]")
    _apply(s, body)
    db.add(s)
    db.flush()
    is_first = db.scalar(select(func.count()).select_from(BrowserSession)) == 1
    if body.set_default or is_first:
        set_default(db, s.id)
    db.commit()
    db.refresh(s)
    return session_out(s)


@router.get("", response_model=list[SessionOut])
def list_sessions(db: Session = Depends(get_db)):
    return [session_out(s) for s in db.scalars(select(BrowserSession).order_by(BrowserSession.id))]


@router.get("/{session_id}", response_model=SessionOut)
def get_session(session_id: int, db: Session = Depends(get_db)):
    return session_out(_get(db, session_id))


@router.put("/{session_id}/login", response_model=SessionOut)
@router.put("/{session_id}/cookies", response_model=SessionOut, include_in_schema=False)
async def update_login(session_id: int, body: CookiesUpdate, db: Session = Depends(get_db)):
    """Perbarui data login (storage dan/atau cookies). Field yang tidak dikirim tidak diubah."""
    s = _get(db, session_id)
    _apply(s, body)
    s.status = "unknown"
    db.commit()
    db.refresh(s)
    # context browser lama masih memakai data login lama -> tutup
    await browser_manager.invalidate(session_id)
    return session_out(s)


@router.post("/{session_id}/default", response_model=SessionOut)
def make_default(session_id: int, db: Session = Depends(get_db)):
    s = _get(db, session_id)
    set_default(db, s.id)
    db.commit()
    db.refresh(s)
    return session_out(s)


@router.delete("/{session_id}", status_code=204)
async def delete_session(session_id: int, db: Session = Depends(get_db)):
    db.delete(_get(db, session_id))
    db.commit()
    await browser_manager.invalidate(session_id, forget=True)
