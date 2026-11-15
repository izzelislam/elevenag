import asyncio
import logging
import time
from collections import defaultdict
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import AsyncIterator

from playwright.async_api import Browser, BrowserContext, Playwright, async_playwright

from .config import settings
from .elevenlabs.selectors import BASE_URL
from .services import save_cookies, save_storage
from .storage_js import DUMP_FN, SEED_FN, has_firebase_auth, session_seed_script

log = logging.getLogger(__name__)

SEED_URL = f"{BASE_URL}/__elevenag_seed"


@dataclass
class _Entry:
    ctx: BrowserContext
    browser: Browser
    has_storage: bool
    last_used: float = field(default_factory=time.monotonic)
    closed: bool = False


class BrowserManager:
    """Satu Chromium dipakai bersama. Tiap akun (session) punya BrowserContext sendiri
    yang DIPAKAI ULANG antar request, supaya cache JS aplikasi ElevenLabs (yang berat,
    ~45 detik saat cache kosong) dan state login tetap hangat. Context yang menganggur
    lebih dari CONTEXT_IDLE_MINUTES ditutup otomatis."""

    REAP_INTERVAL_S = 60

    def __init__(self) -> None:
        self._pw: Playwright | None = None
        self._browser: Browser | None = None
        self._launch_lock = asyncio.Lock()
        self._sem = asyncio.Semaphore(settings.max_concurrency)
        # satu akun hanya dipakai satu aksi pada satu waktu
        self._session_locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._contexts: dict[int, _Entry] = {}
        self._reaper: asyncio.Task | None = None

    async def _ensure_browser(self) -> Browser:
        async with self._launch_lock:
            if self._browser and self._browser.is_connected():
                return self._browser
            if not self._pw:
                self._pw = await async_playwright().start()
            log.info("launching chromium (headless=%s)", settings.headless)
            self._browser = await self._pw.chromium.launch(
                headless=settings.headless,
                args=["--disable-blink-features=AutomationControlled", "--disable-dev-shm-usage"],
            )
            self._contexts.clear()  # context lama ikut mati bersama browser lama
            if not self._reaper or self._reaper.done():
                self._reaper = asyncio.create_task(self._reap_idle())
            return self._browser

    async def stop(self) -> None:
        if self._reaper:
            self._reaper.cancel()
        for sid in list(self._contexts):
            await self._close_entry(sid)
        if self._browser:
            await self._browser.close()
        if self._pw:
            await self._pw.stop()
        self._browser = self._pw = None

    async def invalidate(self, session_id: int, forget: bool = False) -> None:
        """Tutup context akun ini (mis. setelah data login diganti).
        forget=True untuk session yang dihapus: lock-nya ikut dibuang."""
        async with self._session_locks[session_id]:
            await self._close_entry(session_id)
        if forget:
            self._session_locks.pop(session_id, None)

    async def _close_entry(self, session_id: int) -> None:
        entry = self._contexts.pop(session_id, None)
        if entry and not entry.closed:
            entry.closed = True
            try:
                await entry.ctx.close()
            except Exception:
                pass

    async def _reap_idle(self) -> None:
        """Tutup context yang menganggur; kalau tidak ada context sama sekali selama
        CONTEXT_IDLE_MINUTES, matikan juga proses Chromium (hidup lagi saat dibutuhkan)."""
        idle_s = settings.context_idle_minutes * 60
        empty_since: float | None = None
        while True:
            await asyncio.sleep(self.REAP_INTERVAL_S)
            now = time.monotonic()
            for sid, entry in list(self._contexts.items()):
                lock = self._session_locks[sid]
                if now - entry.last_used > idle_s and not lock.locked():
                    async with lock:
                        log.info("menutup context idle untuk session %s", sid)
                        await self._close_entry(sid)

            busy = any(lock.locked() for lock in self._session_locks.values())
            if self._contexts or busy:
                empty_since = None
                continue
            empty_since = empty_since or now
            if now - empty_since >= idle_s:
                await self._shutdown_browser()
                return  # reaper dibuat lagi saat browser diluncurkan ulang

    async def _shutdown_browser(self) -> None:
        async with self._launch_lock:
            if self._contexts:
                return  # ada yang baru dipakai saat kita menunggu lock
            log.info("tidak ada context aktif, mematikan Chromium untuk hemat RAM")
            try:
                if self._browser:
                    await self._browser.close()
                if self._pw:
                    await self._pw.stop()
            except Exception:
                log.exception("gagal mematikan Chromium")
            self._browser = self._pw = None

    async def _new_context(
        self, browser: Browser, cookies: list[dict], user_agent: str | None, storage: dict | None
    ) -> BrowserContext:
        if user_agent and not user_agent.startswith("Mozilla/"):
            log.warning("user_agent tidak valid (%r), pakai bawaan Chromium", user_agent[:40])
            user_agent = None
        ctx = await browser.new_context(
            user_agent=user_agent or None,
            accept_downloads=True,
            viewport={"width": 1366, "height": 900},
            locale="en-US",
        )
        ctx.set_default_timeout(settings.nav_timeout_ms)
        if cookies:
            await ctx.add_cookies(cookies)
        if storage:
            if storage.get("sessionStorage"):
                await ctx.add_init_script(script=session_seed_script(storage["sessionStorage"], BASE_URL))
            await self._run_on_origin(ctx, SEED_FN, storage)
        return ctx

    @asynccontextmanager
    async def session_context(
        self,
        session_id: int,
        cookies: list[dict],
        user_agent: str | None,
        storage: dict | None = None,
    ) -> AsyncIterator[BrowserContext]:
        async with self._session_locks[session_id], self._sem:
            browser = await self._ensure_browser()
            entry = self._contexts.get(session_id)
            if entry and (entry.closed or entry.browser is not browser):
                await self._close_entry(session_id)
                entry = None
            if not entry:
                log.info("membuat context baru untuk session %s", session_id)
                ctx = await self._new_context(browser, cookies, user_agent, storage)
                entry = _Entry(ctx=ctx, browser=browser, has_storage=bool(storage))
                ctx.on("close", lambda _: setattr(entry, "closed", True))
                self._contexts[session_id] = entry

            try:
                yield entry.ctx
            finally:
                entry.last_used = time.monotonic()
                if not entry.closed:
                    # simpan cookie & storage terbaru (token yang di-refresh situsnya)
                    try:
                        save_cookies(session_id, await entry.ctx.cookies())
                        if entry.has_storage:
                            dumped = await self._run_on_origin(entry.ctx, DUMP_FN)
                            if has_firebase_auth(dumped):
                                save_storage(session_id, dumped)
                    except Exception:
                        log.exception("gagal menyimpan session terbaru untuk session %s", session_id)

    @staticmethod
    async def _run_on_origin(ctx: BrowserContext, fn: str, arg=None):
        """Jalankan JS di origin elevenlabs.io tanpa memuat aplikasinya
        (halaman kosong palsu), supaya storage bisa ditulis sebelum app jalan."""
        page = await ctx.new_page()
        try:
            await page.route(
                SEED_URL,
                lambda route: route.fulfill(status=200, content_type="text/html", body="<html></html>"),
            )
            await page.goto(SEED_URL)
            return await page.evaluate(fn, arg)
        finally:
            await page.close()


browser_manager = BrowserManager()
