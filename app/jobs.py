import asyncio
import json
import logging

from sqlalchemy import update

from . import webhooks
from .browser import browser_manager
from .config import settings
from .db import SessionLocal
from .elevenlabs import client as el
from .models import BrowserSession, Job, utcnow
from .services import mark_exhausted, mark_status, usable_sessions

log = logging.getLogger(__name__)

_tasks: set[asyncio.Task] = set()


def spawn(coro) -> None:
    """Jalankan coroutine di background (referensi task disimpan supaya tidak di-GC)."""
    task = asyncio.create_task(coro)
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


def enqueue(job_id: str) -> None:
    spawn(_run_and_notify(job_id))


async def _run_and_notify(job_id: str) -> None:
    try:
        await _run(job_id)
    finally:
        # webhook dikirim setelah browser dilepas, jadi retry-nya tidak menahan antrean
        try:
            await webhooks.notify(job_id)
        except Exception:
            log.exception("webhook job %s error", job_id)


def fail_stale_jobs() -> None:
    """Job yang masih queued/running saat server mati tidak akan lanjut."""
    with SessionLocal() as db:
        db.execute(
            update(Job)
            .where(Job.status.in_(["queued", "running"]))
            .values(status="failed", error="server restarted", finished_at=utcnow())
        )
        db.commit()


def _finish(job_id: str, **values) -> None:
    with SessionLocal() as db:
        db.execute(update(Job).where(Job.id == job_id).values(finished_at=utcnow(), **values))
        db.commit()


async def _run(job_id: str) -> None:
    with SessionLocal() as db:
        job = db.get(Job, job_id)
        if not job:
            return
        pinned = bool(job.session_pinned)
        if pinned:
            ids = [job.session_id] if job.session_id else []
        else:
            ids = [u.id for u in usable_sessions(db)]
            # session yang dipilih saat POST /tts tetap diprioritaskan kalau masih usable
            if job.session_id in ids:
                ids.remove(job.session_id)
                ids.insert(0, job.session_id)
        text, voice_id, model_id = job.text, job.voice_id, job.model_id or settings.default_model_id
        # kalau user cuma kirim voice_id, kolom voice berisi id itu juga -> bukan nama
        voice = job.voice if job.voice != job.voice_id else None
        voice_settings = json.loads(job.options_json) if job.options_json else {
            "stability": settings.default_stability,
            "similarity": settings.default_similarity,
            "text_focus": False,
        }

    if not ids:
        _finish(job_id, status="failed", error_code="insufficient_credit",
                error="insufficient credit: tidak ada session yang bisa dipakai")
        return

    last_error: str | None = None
    only_credit = True  # semua kegagalan sejauh ini karena credit habis
    for sid in ids:
        with SessionLocal() as db:
            sess = db.get(BrowserSession, sid)
            if not sess:
                continue
            cookies, ua, storage = sess.cookies, sess.user_agent, sess.storage
        try:
            async with browser_manager.session_context(sid, cookies, ua, storage) as ctx:
                with SessionLocal() as db:
                    db.execute(
                        update(Job).where(Job.id == job_id)
                        .values(status="running", started_at=utcnow(), session_id=sid)
                    )
                    db.commit()
                result = await el.generate_tts(
                    ctx,
                    text=text,
                    voice=voice,
                    voice_id=voice_id,
                    model_id=model_id,
                    voice_settings=voice_settings,
                    out_base=settings.download_dir / job_id,
                    debug_png=settings.debug_dir / f"{job_id}.png",
                )
            _finish(
                job_id,
                status="done",
                file_path=str(result["path"]),
                voice_id=result["voice_id"] or voice_id,
                source=result["source"],
                error=None,
                error_code=None,
            )
            return
        except el.InsufficientCredit as e:
            new_default = mark_exhausted(sid)
            log.warning("session %s credit habis (%s); default baru: %s", sid, str(e)[:100], new_default)
            last_error = f"session {sid}: credit habis"
        except el.NotLoggedIn:
            mark_status(sid, "expired")
            only_credit = False
            last_error = f"session {sid} expired / belum login; kirim ulang lewat PUT /sessions/{{id}}/login"
            if pinned:
                break
        except Exception as e:
            log.exception("job %s gagal di session %s", job_id, sid)
            _finish(job_id, status="failed", error=f"{type(e).__name__}: {e}"[:2000])
            return

    if only_credit:
        _finish(job_id, status="failed", error_code="insufficient_credit",
                error="insufficient credit: credit semua session sudah habis")
    else:
        _finish(job_id, status="failed", error=last_error)
