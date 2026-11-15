import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import jobs, webhooks
from ..config import settings
from ..db import get_db
from ..models import BrowserSession, Job
from ..schemas import JobOut, TTSRequest
from ..services import InsufficientCreditError, resolve_session, usable_sessions

router = APIRouter(tags=["tts"])


def job_out(j: Job) -> JobOut:
    return JobOut(
        id=j.id,
        session_id=j.session_id,
        voice=j.voice,
        voice_id=j.voice_id,
        model_id=j.model_id,
        source=j.source,
        status=j.status,
        error=j.error,
        error_code=j.error_code,
        created_at=j.created_at,
        started_at=j.started_at,
        finished_at=j.finished_at,
        download_url=f"/jobs/{j.id}/download" if j.status == "done" else None,
        webhook_url=j.webhook_url,
        webhook_status=j.webhook_status,
        webhook_attempts=j.webhook_attempts,
        webhook_error=j.webhook_error,
    )


@router.post("/tts", response_model=JobOut, status_code=202)
async def create_tts(body: TTSRequest, db: Session = Depends(get_db)):
    """Buat voice over. Proses jalan di background; cek GET /jobs/{id}."""
    if body.session_id is not None:
        s = resolve_session(db, body.session_id)
        if s.status == "exhausted":
            raise InsufficientCreditError(f"credit session {s.id} habis")
    else:
        usable = usable_sessions(db)
        if not usable:
            if db.scalar(select(BrowserSession.id).where(BrowserSession.status == "exhausted").limit(1)):
                raise InsufficientCreditError("credit semua session sudah habis")
            s = resolve_session(db, None)  # 400 / session expired ditangani seperti biasa
        else:
            s = usable[0]
    voice_settings = {
        "stability": body.stability if body.stability is not None else settings.default_stability,
        "similarity": body.similarity if body.similarity is not None else settings.default_similarity,
        "text_focus": False,
    }
    voice, voice_id = body.voice, body.voice_id
    if not voice and not voice_id:
        voice, voice_id = settings.default_voice_name, settings.default_voice_id
    job = Job(
        session_id=s.id,
        session_pinned=body.session_id is not None,
        voice=voice or voice_id,
        voice_id=voice_id,
        model_id=body.model_id or settings.default_model_id,
        options_json=json.dumps(voice_settings),
        text=body.text,
        webhook_url=body.webhook_url or settings.webhook_url or None,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    jobs.enqueue(job.id)
    return job_out(job)


@router.get("/jobs", response_model=list[JobOut])
def list_jobs(
    status_: str | None = Query(default=None, alias="status"),
    limit: int = 50,
    db: Session = Depends(get_db),
):
    q = select(Job).order_by(Job.created_at.desc()).limit(min(limit, 200))
    if status_:
        q = q.where(Job.status == status_)
    return [job_out(j) for j in db.scalars(q)]


@router.get("/jobs/{job_id}", response_model=JobOut)
def get_job(job_id: str, db: Session = Depends(get_db)):
    j = db.get(Job, job_id)
    if not j:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "job tidak ditemukan")
    return job_out(j)


@router.post("/jobs/{job_id}/webhook", response_model=JobOut, status_code=202)
async def resend_webhook(
    job_id: str,
    webhook_url: str | None = Query(
        default=None, pattern=r"^https?://", description="Opsional: kirim ke URL lain (juga disimpan)"
    ),
    db: Session = Depends(get_db),
):
    """Kirim ulang webhook untuk job yang sudah selesai/gagal."""
    j = db.get(Job, job_id)
    if not j:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "job tidak ditemukan")
    if j.status not in ("done", "failed"):
        raise HTTPException(status.HTTP_409_CONFLICT, f"job belum selesai (status: {j.status})")
    if webhook_url:
        j.webhook_url = webhook_url
    if not j.webhook_url:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "job tidak punya webhook_url; kirim lewat query")
    j.webhook_status, j.webhook_attempts, j.webhook_error = "sending", 0, None
    db.commit()
    db.refresh(j)
    jobs.spawn(webhooks.notify(j.id))
    return job_out(j)


@router.get("/jobs/{job_id}/download")
def download(job_id: str, db: Session = Depends(get_db)):
    j = db.get(Job, job_id)
    if not j:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "job tidak ditemukan")
    if j.status != "done" or not j.file_path or not Path(j.file_path).exists():
        raise HTTPException(status.HTTP_409_CONFLICT, f"file belum tersedia (status: {j.status})")
    path = Path(j.file_path)
    return FileResponse(path, filename=path.name)
