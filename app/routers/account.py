from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query, status
from playwright.async_api import Error as PWError
from playwright.async_api import TimeoutError as PWTimeout

from ..browser import EgressError, browser_manager
from ..elevenlabs import client as el
from ..schemas import Credits, VoiceList
from ..services import load_session, mark_status, save_credits

router = APIRouter(tags=["account"])


EXPIRED_MSG = (
    "session expired / belum login. Kirim ulang data login lewat PUT /sessions/{id}/login "
    "(field 'storage' dari GET /sessions/export-script)"
)


async def _run(session_id: int | None, fn):
    sid, cookies, ua, storage = load_session(session_id)
    try:
        async with browser_manager.session_context(sid, cookies, ua, storage) as ctx:
            return sid, await fn(ctx)
    except el.NotLoggedIn:
        mark_status(sid, "expired")
        raise HTTPException(status.HTTP_409_CONFLICT, EXPIRED_MSG)
    except el.SelectorNotConfigured as e:
        raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, str(e))
    except PWTimeout as e:
        raise HTTPException(
            status.HTTP_504_GATEWAY_TIMEOUT, f"timeout menunggu ElevenLabs: {e.message[:300]}"
        )
    except PWError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"browser error: {e.message[:300]}")


@router.get("/egress")
async def egress():
    """IP keluar yang terlihat oleh ElevenLabs. Kalau PROXY_URL diisi (mis. Cloudflare WARP),
    `ip` harus IP Cloudflare dan `warp` bernilai "on" (bukan IP VPS)."""
    try:
        return await browser_manager.probe_egress()
    except EgressError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(e))
    except PWError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"gagal mengecek IP keluar: {e.message[:300]}")


@router.get("/credits", response_model=Credits)
async def get_credits(session_id: int | None = None):
    """Cek sisa & pemakaian credit secara live (juga memperbarui data di DB)."""
    sid, sub = await _run(session_id, el.get_subscription)
    save_credits(sid, sub)
    reset = sub.get("reset_unix")
    return Credits(
        session_id=sid,
        tier=sub["tier"],
        used=sub["used"],
        limit=sub["limit"],
        remaining=sub["remaining"],
        reset_at=datetime.fromtimestamp(reset, timezone.utc) if reset else None,
        source=sub.get("source"),
    )


@router.get("/voices", response_model=VoiceList)
async def get_voices(
    search: str | None = Query(default=None, max_length=100, description="Nama voice, mis. 'zan'"),
    language: str | None = Query(default="id", description="Kode bahasa; kosongkan untuk semua"),
    sort: str = Query(default="trending"),
    page_size: int = Query(default=30, ge=1, le=100),
    page: int = Query(default=0, ge=0),
    session_id: int | None = None,
):
    """Cari voice di voice library ElevenLabs."""

    async def fn(ctx):
        return await el.search_voices(ctx, search, language or None, sort, page_size, page)

    _, result = await _run(session_id, fn)
    return result
