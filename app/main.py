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
    """Validate security config, snapshot a backup, then initialize the schema.

    The backup deliberately runs *before* init_schema(). init_schema is the only
    place that rewrites the memories table - _run_summary_migration and
    _run_tracker_migration rename, copy and DROP it - so a snapshot taken after it
    captures the already-migrated state. If a migration ever loses rows, backing up
    afterwards would overwrite the last good copy with the damaged one, at exactly
    the moment it is needed. The savepoints inside those migrations protect against
    a failed transaction, not against a migration that succeeds and is wrong.

    On a first run there is no database yet, create_backup() returns None, and
    init_schema() creates it - the order costs nothing there.
    """
    validate_security()
    try:
        run_backup()
    except Exception:
        # A backup failure (e.g. disk full) must not block the server from
        # starting - the live db is unaffected either way.
        logger.exception("Startup database backup failed")
    init_schema()
    yield


app = FastAPI(
    title="Memory Service",
    description="External memory service for SillyTavern",
    version=SERVICE_VERSION,
    lifespan=lifespan,
)

# An explicit CORS_ALLOW_ORIGINS list wins outright; otherwise the loopback regex
# applies. Passing both to Starlette would OR them together, which would silently keep
# a wide default alive next to the narrow list someone deliberately configured.
_cors_kwargs: dict = (
    {"allow_origins": config.CORS_ALLOW_ORIGINS}
    if config.CORS_ALLOW_ORIGINS
    else {"allow_origin_regex": config.CORS_ALLOW_ORIGIN_REGEX}
)

app.add_middleware(
    CORSMiddleware,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
    **_cors_kwargs,
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
async def memory_version(build: str | None = None) -> dict:
    """Report backend version/compatibility info for the extension handshake.

    `build` is the extension's MEMORY_EXTENSION_BUILD. It is reported here so that
    "which build is actually loaded in the browser" can be answered right after a
    reload. It used to be observable only inside an audit record, and a turn writes
    those - a page load does not. So the one question the build stamp exists to answer
    needed either a turn or a browser console, and this project already documents the
    console as unavailable on the phone it runs on (see `services/audit_sink`), which
    is why the audit is mirrored here in the first place.

    A query parameter rather than a header on purpose: it lands in uvicorn's access log
    with no code at all, so `grep "/memory/version" data/server.log` is the whole
    interface, the same way `tail data/audit.jsonl` is for the audit. Logged explicitly
    as well, so the answer survives a change of access-log format.

    Optional, and never validated beyond truncation: an extension older than this sends
    no build, and a handshake must not fail over a diagnostic field. The value is
    attacker-controlled in principle - the endpoint is deliberately unauthenticated -
    so it is length-capped and %r-quoted rather than interpolated raw into the log.
    """
    if build:
        logger.info("extension handshake: build %r", build[:64])
    else:
        logger.info("extension handshake: build not reported")
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
