import logging
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from playwright.async_api import APIResponse, BrowserContext, Locator, Page, Request, Response, expect
from playwright.async_api import TimeoutError as PWTimeout

from ..config import settings
from . import selectors as S

log = logging.getLogger(__name__)


class NotLoggedIn(Exception):
    """Data login session sudah expired / tidak valid."""


class SelectorNotConfigured(Exception):
    pass


class VoiceNotFound(Exception):
    def __str__(self) -> str:
        return f"voice tidak ditemukan: {self.args[0] if self.args else ''}"


def _sel(name: str) -> str:
    value = getattr(S, name)
    if not value or value.startswith("TODO"):
        raise SelectorNotConfigured(f"selector {name} belum diisi di app/elevenlabs/selectors.py")
    return value


def _check_login(page: Page) -> None:
    if any(m in page.url for m in S.LOGIN_URL_MARKERS):
        raise NotLoggedIn(page.url)


async def _goto(page: Page, url: str) -> None:
    await page.goto(url, wait_until="domcontentloaded")
    _check_login(page)


async def _debug_shot(page: Page, name: str) -> str | None:
    path = settings.debug_dir / f"{name}-{datetime.now():%Y%m%d-%H%M%S}.png"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        await page.screenshot(path=str(path), full_page=True)
        return str(path)
    except Exception:
        return None


async def _open_app(page: Page, url: str, ready: Locator, what: str) -> None:
    """Buka halaman aplikasi dan tunggu sampai `ready` terlihat (aplikasi ElevenLabs berat;
    saat cache kosong bisa ~45 detik). NotLoggedIn kalau dilempar ke halaman login."""
    await _goto(page, url)
    deadline = time.monotonic() + settings.app_load_timeout_ms / 1000
    while time.monotonic() < deadline:
        _check_login(page)
        try:
            if await ready.first.is_visible():
                return
        except Exception:
            pass
        await page.wait_for_timeout(500)
    _check_login(page)
    shot = await _debug_shot(page, "app-load")
    raise PWTimeout(
        f"aplikasi tidak siap dalam {settings.app_load_timeout_ms // 1000} detik "
        f"(menunggu {what}); screenshot: {shot}"
    )


# ---------------------------------------------------------------------------
# Auth: header yang dipakai web app untuk memanggil API internalnya.
# Ditangkap sekali per context, dipakai ulang sampai AUTH_CACHE_MINUTES.
# ---------------------------------------------------------------------------

# header yang diatur sendiri oleh HTTP client / spesifik ke request aslinya
_SKIP_HEADERS = {"host", "content-length", "content-type", "cookie", "connection", "accept-encoding"}
_AUTH_ATTR = "_elevenag_auth"


class ApiAuth:
    def __init__(self, base: str, headers: dict[str, str]) -> None:
        self.base, self.headers = base, headers
        self.created = time.monotonic()

    @property
    def fresh(self) -> bool:
        return time.monotonic() - self.created < settings.auth_cache_minutes * 60


async def _capture_auth(ctx: BrowserContext, url: str) -> ApiAuth | None:
    """Buka aplikasi dan tangkap header authorization dari request API pertama.
    Menunggu sampai tertangkap, dilempar ke login (NotLoggedIn), atau timeout."""
    found: list[ApiAuth] = []

    async def on_request(req: Request) -> None:
        if found:
            return
        m = S.API_URL_RE.match(req.url)
        if not m:
            return
        try:
            headers = await req.all_headers()
        except Exception:
            return
        if headers.get("authorization") and not found:
            # salin header asli browser (origin, referer, user-agent, x-*, authorization)
            # supaya panggilan langsung identik dengan request web app
            keep = {k: v for k, v in headers.items() if k.lower() not in _SKIP_HEADERS and not k.startswith(":")}
            keep.setdefault("origin", S.BASE_URL)
            keep.setdefault("referer", S.BASE_URL + "/")
            found.append(ApiAuth(m.group(1), keep))

    page = await ctx.new_page()
    page.on("request", on_request)
    started = time.monotonic()
    try:
        await _goto(page, url)
        deadline = started + settings.app_load_timeout_ms / 1000
        while not found and time.monotonic() < deadline:
            _check_login(page)
            await page.wait_for_timeout(250)
        _check_login(page)
        if found:
            log.info("auth tertangkap (%s) setelah %.0f detik", found[0].base, time.monotonic() - started)
        else:
            shot = await _debug_shot(page, "auth")
            log.warning("auth TIDAK tertangkap dalam %.0f detik; screenshot: %s", time.monotonic() - started, shot)
        return found[0] if found else None
    finally:
        await page.close()


async def get_auth(ctx: BrowserContext, force: bool = False) -> ApiAuth | None:
    cached: ApiAuth | None = getattr(ctx, _AUTH_ATTR, None)
    if cached and cached.fresh and not force:
        return cached
    auth = await _capture_auth(ctx, S.TTS_URL)
    setattr(ctx, _AUTH_ATTR, auth)
    return auth


async def _api_request(
    ctx: BrowserContext,
    method: str,
    path: str,
    *,
    params: dict | None = None,
    body: Any = None,
    timeout_ms: int | None = None,
) -> APIResponse | None:
    """Panggil API internal langsung pakai auth web app. Kalau dapat 401/403, auth
    diambil ulang lalu dicoba sekali lagi. None kalau auth tidak tersedia / request error."""
    for attempt in range(2):
        auth = await get_auth(ctx, force=attempt > 0)
        if not auth:
            return None
        headers = dict(auth.headers)
        if body is not None:
            headers["content-type"] = "application/json"
        try:
            resp = await ctx.request.fetch(
                auth.base + path,
                method=method,
                params=params,
                data=body,
                headers=headers,
                timeout=timeout_ms or settings.nav_timeout_ms,
            )
        except Exception:
            log.warning("api %s %s gagal", method, path, exc_info=True)
            return None
        if resp.status in (401, 403) and attempt == 0:
            log.info("api %s %s -> HTTP %s, ambil ulang auth", method, path, resp.status)
            continue
        return resp
    return None


async def _api_json(ctx: BrowserContext, path: str, params: dict | None = None) -> Any | None:
    resp = await _api_request(ctx, "GET", path, params=params)
    if resp is None:
        return None
    if not resp.ok:
        try:
            text = (await resp.text())[:200]
        except Exception:
            text = ""
        log.warning("api GET %s -> HTTP %s %s", path, resp.status, text)
        return None
    try:
        return await resp.json()
    except Exception:
        log.warning("api GET %s -> bukan JSON", path)
        return None


# ---------------------------------------------------------------------------
# Credit
# ---------------------------------------------------------------------------

def _to_int(text: str) -> int | None:
    digits = re.sub(r"[^\d]", "", text)
    return int(digits) if digits else None


def _extract_sub(data: Any) -> dict | None:
    """Ambil objek subscription dari response /v1/user/subscription atau /v1/user."""
    if not isinstance(data, dict):
        return None
    if data.get("character_limit") is not None:
        return data
    sub = data.get("subscription")
    if isinstance(sub, dict) and sub.get("character_limit") is not None:
        return sub
    return None


async def get_subscription(ctx: BrowserContext) -> dict:
    """Cek credit: API subscription langsung (pakai auth web app);
    fallback baca kartu Balance di menu avatar."""
    # 1. network
    for path in (S.SUBSCRIPTION_PATH, S.USER_PATH):
        sub = _extract_sub(await _api_json(ctx, path))
        if sub:
            used, limit = sub.get("character_count"), sub["character_limit"]
            return {
                "tier": sub.get("tier"),
                "used": used,
                "limit": limit,
                "remaining": max(limit - used, 0) if used is not None else None,
                "reset_unix": sub.get("next_character_count_reset_unix"),
                "source": "network",
            }

    # 2. fallback DOM: klik avatar, baca kartu Balance
    log.info("credits: jalur network gagal, coba baca kartu Balance (DOM)")
    page = await ctx.new_page()
    avatar = page.locator(_sel("AVATAR_TRIGGER"))
    step = "aplikasi siap (avatar terlihat)"
    try:
        await _open_app(page, S.HOME_URL, avatar, "avatar")
        step = "klik avatar"
        await avatar.first.click(timeout=10_000)

        # div terdalam yang memuat label Balance, Total, dan Remaining = kartu balance
        step = "mencari kartu Balance"
        card = (
            page.locator("div")
            .filter(has=page.get_by_text(S.BALANCE_LABEL, exact=True))
            .filter(has=page.get_by_text("Total", exact=True))
            .filter(has=page.get_by_text("Remaining", exact=True))
            .last
        )
        await card.wait_for(state="visible", timeout=10_000)

        async def row_value(label: str) -> int | None:
            row = card.locator("div").filter(has=page.get_by_text(label, exact=True)).filter(
                has=page.locator(S.BALANCE_VALUE)
            ).last
            return _to_int(await row.locator(S.BALANCE_VALUE).first.inner_text(timeout=5_000))

        step = "membaca angka Total/Remaining"
        total = await row_value("Total")
        remaining = await row_value("Remaining")
    except PWTimeout as e:
        _check_login(page)
        shot = await _debug_shot(page, "credits")
        log.warning("credits: timeout saat %s (url: %s, screenshot: %s)", step, page.url, shot)
        raise PWTimeout(f"timeout saat {step}: {e.message[:150]}; screenshot: {shot}") from None
    finally:
        await page.close()

    return {
        "tier": None,
        "used": total - remaining if total is not None and remaining is not None else None,
        "limit": total,
        "remaining": remaining,
        "reset_unix": None,
        "source": "dom",
    }


# ---------------------------------------------------------------------------
# Voice library
# ---------------------------------------------------------------------------

_VOICE_FIELDS = (
    "voice_id", "name", "category", "gender", "age", "accent", "language", "locale",
    "use_case", "descriptive", "description", "preview_url", "cloned_by_count",
    "free_users_allowed", "is_added_by_user",
)


async def search_voices(
    ctx: BrowserContext,
    search: str | None = None,
    language: str | None = "id",
    sort: str = "trending",
    page_size: int = 30,
    page_no: int = 0,
) -> dict:
    """Cari voice di voice library (endpoint shared-voices)."""
    params = {
        k: v
        for k, v in {
            "required_languages": language,
            "search": search,
            "sort": sort,
            "page_size": page_size,
            "page": page_no,
        }.items()
        if v not in (None, "")
    }
    data, source = await _api_json(ctx, S.SHARED_VOICES_PATH, params), "network"
    if data is None:
        page = await ctx.new_page()
        try:
            await _open_app(page, S.TTS_URL, page.locator(_sel("TEXT_BLOCK")), "editor text-to-speech")
            data, source = await _search_voices_ui(page, search), "dom"
        finally:
            await page.close()

    return {
        "voices": [{k: v.get(k) for k in _VOICE_FIELDS} for v in data.get("voices", [])],
        "has_more": data.get("has_more", False),
        "total_count": data.get("total_count"),
        "source": source,
    }


async def _search_voices_ui(page: Page, search: str | None) -> dict:
    """Fallback: buka modal voice library lewat UI, ketik search, tangkap response-nya.
    Filter bahasa memakai yang sedang aktif di UI (default Indonesian)."""
    responses: list[Response] = []

    def on_response(r: Response) -> None:
        if S.SHARED_VOICES_PATH in r.url and r.ok:
            responses.append(r)

    page.on("response", on_response)
    try:
        block = page.locator(_sel("TEXT_BLOCK")).first
        await block.locator(_sel("VOICE_PICKER_BUTTON")).first.click()
        more = page.locator(S.DROPDOWN_MORE)
        modal_search = page.locator(S.MODAL_SEARCH)
        await more.or_(modal_search).first.wait_for(state="visible")
        if await more.is_visible():
            await more.click()
        await modal_search.wait_for(state="visible")

        if search:
            before = len(responses)
            await modal_search.fill(search)
            want = lambda: [r for r in responses[before:] if "search=" in r.url]  # noqa: E731
        else:
            want = lambda: responses  # noqa: E731

        for _ in range(int(settings.nav_timeout_ms / 250)):
            if want():
                return await want()[-1].json()
            await page.wait_for_timeout(250)
        raise PWTimeout("response shared-voices tidak tertangkap")
    finally:
        page.remove_listener("response", on_response)


# ---------------------------------------------------------------------------
# Text to speech
# ---------------------------------------------------------------------------

def _starts_with(name: str) -> re.Pattern:
    return re.compile(r"^\s*" + re.escape(name.strip()), re.I)


async def _select_voice(page: Page, block: Locator, voice: str) -> None:
    """Pilih voice berdasarkan nama (boleh awalannya saja, mis. "Zephlyn").
    Coba dropdown In use/Recents dulu; kalau tidak ada, cari di modal More voices."""
    picker = block.locator(_sel("VOICE_PICKER_BUTTON")).first
    if _starts_with(voice).search(await picker.inner_text()):
        return  # sudah terpilih

    await picker.click()
    more = page.locator(S.DROPDOWN_MORE)
    modal_search = page.locator(S.MODAL_SEARCH)
    await more.or_(modal_search).first.wait_for(state="visible")

    if await more.is_visible():
        option = page.locator(S.DROPDOWN_OPTION).filter(has_text=_starts_with(voice)).first
        if await option.count():
            await option.click()
            await _verify_voice(page, picker, voice)
            return
        await more.click()
        await modal_search.wait_for(state="visible")

    # modal besar
    await modal_search.fill(voice)
    use_btn = page.locator(S.MODAL_USE_BUTTON.format(name=voice.strip().replace('"', '\\"'))).first
    try:
        await use_btn.wait_for(state="attached", timeout=10_000)
    except PWTimeout:
        # mungkin tersaring filter bahasa; clear lalu cari lagi
        clear_lang = page.locator(S.MODAL_CLEAR_LANGUAGE)
        if not await clear_lang.count():
            raise VoiceNotFound(voice)
        await clear_lang.first.click()
        try:
            await use_btn.wait_for(state="attached", timeout=10_000)
        except PWTimeout:
            raise VoiceNotFound(voice) from None

    # tombol Use baru aktif saat baris di-hover
    row = page.locator(S.MODAL_ROW).filter(has=use_btn).first
    await row.scroll_into_view_if_needed()
    await row.hover()
    await use_btn.click()
    await _verify_voice(page, picker, voice)


async def _verify_voice(page: Page, picker: Locator, voice: str) -> None:
    if await page.locator(S.MODAL_SEARCH).is_visible():
        await page.keyboard.press("Escape")
    try:
        await expect(picker).to_have_text(_starts_with(voice), timeout=10_000)
    except AssertionError:
        raise VoiceNotFound(f"{voice} (voice terpilih: {(await picker.inner_text()).strip()!r})") from None


def _is_audio(data: bytes) -> bool:
    # MP3: tag ID3 atau frame sync 0xFFEx/0xFFFx
    return len(data) > 128 and (data[:3] == b"ID3" or (data[0] == 0xFF and data[1] & 0xE0 == 0xE0))


def _save_audio(data: bytes, out_base: Path) -> Path:
    out = out_base.with_suffix(".mp3")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    return out


async def _resolve_voice_id(ctx: BrowserContext, voice: str) -> str | None:
    """Cari voice_id dari nama (awalan nama boleh) lewat voice library, semua bahasa."""
    data = await _api_json(ctx, S.SHARED_VOICES_PATH, {"search": voice, "page_size": 30, "page": 0})
    pattern = _starts_with(voice)
    for v in (data or {}).get("voices", []):
        if v.get("voice_id") and pattern.search(v.get("name") or ""):
            return v["voice_id"]
    return None


async def _generate_via_api(
    ctx: BrowserContext, text: str, voice_id: str, model_id: str, voice_settings: dict
) -> bytes | None:
    body = {
        "inputs": [{"text": text, "voice_id": voice_id}],
        "model_id": model_id,
        "settings": voice_settings,
        "collect_preference": True,  # sama persis dengan yang dikirim web app
    }
    resp = await _api_request(
        ctx, "POST", S.DIALOGUE_STREAM_PATH, body=body, timeout_ms=settings.generate_timeout_ms
    )
    if resp is None:
        return None
    data = await resp.body()
    if not resp.ok or not _is_audio(data):
        log.warning("generate via api -> HTTP %s, %d bytes: %r", resp.status, len(data), data[:200])
        return None
    return data


async def generate_tts(
    ctx: BrowserContext,
    text: str,
    voice: str | None,
    voice_id: str | None,
    model_id: str,
    voice_settings: dict,
    out_base: Path,
    debug_png: Path,
) -> dict:
    """Generate voice over. Return {"path", "voice_id", "source"}.
    1) network: POST text-to-dialogue/stream langsung (pakai auth web app)
    2) fallback DOM: isi form + klik Generate, ambil audio dari response stream."""
    # 1. network
    vid = voice_id or (await _resolve_voice_id(ctx, voice) if voice else None)
    if vid:
        audio = await _generate_via_api(ctx, text, vid, model_id, voice_settings)
        if audio:
            return {"path": _save_audio(audio, out_base), "voice_id": vid, "source": "network"}
    elif voice:
        log.info("voice %r tidak ketemu di library, lanjut lewat UI", voice)

    # 2. fallback DOM
    if not voice:
        raise VoiceNotFound(f"{voice_id} (jalur UI butuh nama voice; kirim juga field 'voice')")
    page = await ctx.new_page()
    try:
        await _open_app(page, S.TTS_URL, page.locator(_sel("TEXT_BLOCK")), "editor text-to-speech")
        path = await _generate_via_ui(page, text, voice, out_base)
        return {"path": path, "voice_id": vid, "source": "dom"}
    except (NotLoggedIn, SelectorNotConfigured):
        raise
    except Exception:
        # screenshot untuk debugging selector
        try:
            debug_png.parent.mkdir(parents=True, exist_ok=True)
            await page.screenshot(path=str(debug_png), full_page=True)
        except Exception:
            pass
        raise
    finally:
        await page.close()


async def _generate_via_ui(page: Page, text: str, voice: str, out_base: Path) -> Path:
    """Isi teks, pilih voice, klik Generate. Audio diambil dari response stream;
    tombol Download hanya dipakai kalau response tidak tertangkap."""
    if S.TTS_URL not in page.url:
        await _goto(page, S.TTS_URL)
    block = page.locator(_sel("TEXT_BLOCK")).first
    editor = block.locator(_sel("TEXT_INPUT"))
    await editor.wait_for(state="visible")

    # 1. pilih voice (tombol voice ada di dalam block speaker)
    await _select_voice(page, block, voice)

    # 2. isi teks. Editornya ProseMirror, jadi kosongkan dulu lewat tombol
    #    "Clear text", lalu ketik lewat keyboard supaya state editor ikut update.
    clear = block.locator(S.CLEAR_TEXT_BUTTON)
    if await clear.count():
        await block.hover()  # tombol clear baru muncul saat hover
        await clear.first.click()
    await editor.click()
    if (await editor.inner_text()).strip():
        await page.keyboard.press("ControlOrMeta+A")
        await page.keyboard.press("Backspace")
    await page.keyboard.insert_text(text)

    typed = (await editor.inner_text()).strip()
    if text.strip()[:30] not in typed:
        raise RuntimeError(f"teks tidak masuk ke editor (isi sekarang: {typed[:80]!r})")

    # 3. generate + tangkap response stream (isinya MP3 utuh)
    gen = page.locator(_sel("GENERATE_BUTTON")).first
    await expect(gen).to_be_enabled()
    try:
        async with page.expect_response(
            lambda r: S.DIALOGUE_STREAM_PATH in r.url and r.request.method == "POST",
            timeout=60_000,
        ) as info:
            await gen.click()
        resp = await info.value
        audio = await resp.body()  # menunggu stream selesai
        if resp.ok and _is_audio(audio):
            return _save_audio(audio, out_base)
        log.warning("response stream bukan audio (HTTP %s), coba tombol Download", resp.status)
    except PWTimeout:
        log.warning("response stream tidak tertangkap, coba tombol Download")
    except Exception:
        log.warning("gagal membaca response stream, coba tombol Download", exc_info=True)

    # tunggu proses selesai: data-loading -> false
    await expect(gen).to_have_attribute(
        "data-loading", "false", timeout=settings.generate_timeout_ms
    )

    # 4. fallback: download dari audio player (hasil terbaru)
    player_dl = page.locator(S.AUDIO_PLAYER).locator(_sel("DOWNLOAD_BUTTON")).first
    try:
        await player_dl.wait_for(state="visible", timeout=30_000)
        dl = player_dl
    except PWTimeout:
        dl = page.locator(_sel("DOWNLOAD_BUTTON")).first
        await dl.wait_for(state="visible", timeout=10_000)
    async with page.expect_download(timeout=settings.generate_timeout_ms) as dl_info:
        await dl.click()
    download = await dl_info.value

    suffix = Path(download.suggested_filename).suffix or ".mp3"
    out = out_base.with_suffix(suffix)
    out.parent.mkdir(parents=True, exist_ok=True)
    await download.save_as(out)
    return out
