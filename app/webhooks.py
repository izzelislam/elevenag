"""Kirim webhook saat job selesai / gagal.

Request ke penerima:
  POST <webhook_url>
  Content-Type: application/json
  X-Elevenag-Event: job.completed | job.failed
  X-Elevenag-Delivery: <uuid unik per percobaan kirim>
  X-Elevenag-Timestamp: <unix detik>
  X-Elevenag-Signature: sha256=<hex>   (hanya kalau WEBHOOK_SECRET diisi)

Signature = HMAC-SHA256(WEBHOOK_SECRET, "<timestamp>.<raw body>").
Penerima sebaiknya memverifikasi signature dan menolak timestamp yang terlalu lama.
"""

import asyncio
import hashlib
import hmac
import json
import logging
import time
import urllib.error
import urllib.request
import uuid

from .config import settings
from .db import SessionLocal
from .models import Job, utcnow

log = logging.getLogger(__name__)

# jeda sebelum tiap percobaan (detik); jumlah elemen = jumlah percobaan maksimal
RETRY_DELAYS = (0, 5, 30, 120)
TIMEOUT_S = 10


def sign(secret: str, timestamp: str, body: bytes) -> str:
    mac = hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256)
    return "sha256=" + mac.hexdigest()


def build_payload(job: Job) -> dict:
    done = job.status == "done"
    return {
        "event": "job.completed" if done else "job.failed",
        "sent_at": utcnow().isoformat() + "Z",
        "job": {
            "id": job.id,
            "status": job.status,
            "session_id": job.session_id,
            "voice": job.voice,
            "voice_id": job.voice_id,
            "model_id": job.model_id,
            "source": job.source,
            "error": job.error,
            "text_length": len(job.text or ""),
            "created_at": job.created_at.isoformat() + "Z" if job.created_at else None,
            "started_at": job.started_at.isoformat() + "Z" if job.started_at else None,
            "finished_at": job.finished_at.isoformat() + "Z" if job.finished_at else None,
            # perlu header X-API-Key untuk mengunduh
            "download_url": f"{settings.public_base_url.rstrip('/')}/jobs/{job.id}/download" if done else None,
        },
    }


def _post(url: str, event: str, body: bytes) -> int:
    ts = str(int(time.time()))
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "elevenag-webhook/1",
        "X-Elevenag-Event": event,
        "X-Elevenag-Delivery": str(uuid.uuid4()),
        "X-Elevenag-Timestamp": ts,
    }
    if settings.webhook_secret:
        headers["X-Elevenag-Signature"] = sign(settings.webhook_secret, ts, body)
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
            return resp.status
    except urllib.error.HTTPError as e:
        return e.code


def _update(job_id: str, **values) -> None:
    with SessionLocal() as db:
        job = db.get(Job, job_id)
        if job:
            for k, v in values.items():
                setattr(job, k, v)
            db.commit()


async def notify(job_id: str) -> None:
    """Kirim webhook untuk job ini (kalau punya webhook_url), dengan retry."""
    with SessionLocal() as db:
        job = db.get(Job, job_id)
        if not job or not job.webhook_url or job.status not in ("done", "failed"):
            return
        url = job.webhook_url
        payload = build_payload(job)

    body = json.dumps(payload).encode()
    _update(job_id, webhook_status="sending", webhook_error=None)
    error = None
    for attempt, delay in enumerate(RETRY_DELAYS, start=1):
        if delay:
            await asyncio.sleep(delay)
        try:
            status = await asyncio.to_thread(_post, url, payload["event"], body)
            if 200 <= status < 300:
                _update(job_id, webhook_status="sent", webhook_attempts=attempt, webhook_error=None, webhook_sent_at=utcnow())
                log.info("webhook job %s terkirim (HTTP %s, percobaan %d)", job_id, status, attempt)
                return
            error = f"HTTP {status}"
        except Exception as e:
            error = f"{type(e).__name__}: {e}"[:500]
        log.warning("webhook job %s gagal (percobaan %d/%d): %s", job_id, attempt, len(RETRY_DELAYS), error)
        _update(job_id, webhook_attempts=attempt, webhook_error=error)

    _update(job_id, webhook_status="failed")
