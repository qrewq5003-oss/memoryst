import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.config import config, validate_security
from app.db import init_schema
from app.routes.memory_api import router as memory_router
from app.routes.ui import router as ui_router
from app.services.backup_service import run_backup
from app.version import SERVICE_VERSION, get_version_info

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Validate security config, initialize database schema, and snapshot a
    backup on startup."""
    validate_security()
    init_schema()
    try:
        run_backup()
    except Exception:
        # A backup failure (e.g. disk full) must not block the server from
        # starting - the live db is unaffected either way.
        logger.exception("Startup database backup failed")
    yield


app = FastAPI(
    title="Memory Service",
    description="External memory service for SillyTavern",
    version=SERVICE_VERSION,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health_check() -> dict:
    """Health check endpoint."""
    return {"status": "ok"}


# Declared on the app (not the /memory router) so it is intentionally
# unauthenticated - like /health, it is a diagnostic handshake carrying no
# sensitive data, and the extension must be able to detect a version mismatch
# even when the API key is misconfigured (a stale extension is itself a likely
# cause of misconfiguration). Registering it here, before include_router below,
# also keeps it from being swallowed by the /memory/{id} catch-all route.
@app.get("/memory/version")
async def memory_version() -> dict:
    """Report backend version/compatibility info for the extension handshake."""
    return get_version_info()


app.include_router(memory_router)
app.include_router(ui_router)

# Mount static files
static_path = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(static_path)), name="static")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=config.APP_HOST,
        port=config.APP_PORT,
        reload=config.DEBUG,
    )
