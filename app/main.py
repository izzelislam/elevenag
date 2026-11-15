import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI

from . import jobs
from .auth import require_api_key
from .browser import browser_manager
from .config import settings
from .db import init_db
from .routers import account, sessions, tts

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not settings.api_key_set:
        raise RuntimeError("API_KEYS belum diisi di .env")
    settings.download_dir.mkdir(parents=True, exist_ok=True)
    init_db()
    jobs.fail_stale_jobs()
    yield
    await browser_manager.stop()


app = FastAPI(title="ElevenLabs Automation API", version="0.1.0", lifespan=lifespan)

protected = [Depends(require_api_key)]
app.include_router(sessions.router, dependencies=protected)
app.include_router(account.router, dependencies=protected)
app.include_router(tts.router, dependencies=protected)


@app.get("/health", tags=["meta"])
def health():
    return {"ok": True}
