from fastapi import Header, HTTPException, status

from app.config import config


def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    """Validate X-API-Key for memory API requests when auth is enabled."""
    if not config.API_KEY:
        return

    if x_api_key != config.API_KEY:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
        )
