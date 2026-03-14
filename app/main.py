from fastapi import FastAPI

from app.config import config
from app.db import init_schema

app = FastAPI(
    title="Memory Service",
    description="External memory service for SillyTavern",
    version="0.1.0",
)


@app.on_event("startup")
async def startup_event() -> None:
    """Initialize database schema on startup."""
    init_schema()


@app.get("/health")
async def health_check() -> dict:
    """Health check endpoint."""
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=config.APP_HOST,
        port=config.APP_PORT,
        reload=config.DEBUG,
    )
