import os
from pathlib import Path

from dotenv import load_dotenv

# Load .env from project root
_env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(_env_path)


def _parse_port(value: str) -> int:
    try:
        port = int(value)
    except ValueError:
        raise ValueError(f"APP_PORT must be a valid integer, got: {value!r}")
    if not (1 <= port <= 65535):
        raise ValueError(f"APP_PORT must be between 1 and 65535, got: {port}")
    return port


class Config:
    APP_HOST: str = os.getenv("APP_HOST", "0.0.0.0")
    APP_PORT: int = _parse_port(os.getenv("APP_PORT", "8001"))
    DATABASE_PATH: str = os.getenv("DATABASE_PATH", "data/memory.db")
    BACKUP_DIR: str = os.getenv("BACKUP_DIR", "data/backups")
    BACKUP_KEEP: int = int(os.getenv("BACKUP_KEEP", "14"))
    API_KEY: str = os.getenv("API_KEY", "")
    DEBUG: bool = os.getenv("DEBUG", "false").lower() == "true"

    LLM_API_BASE: str = os.getenv("LLM_API_BASE", "")
    LLM_API_KEY: str = os.getenv("LLM_API_KEY", "")
    LLM_MODEL: str = os.getenv("LLM_MODEL", "zai-org/glm-4.7")
    LLM_TIMEOUT: int = int(os.getenv("LLM_TIMEOUT", "30"))

    ACTIVE_LLM_PROVIDER: str = os.getenv("ACTIVE_LLM_PROVIDER", "nanogpt")

    OPENAI_API_BASE: str = os.getenv("OPENAI_API_BASE", "https://api.openai.com")
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    OPENAI_MODEL: str = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

    ANTHROPIC_API_BASE: str = os.getenv("ANTHROPIC_API_BASE", "https://api.anthropic.com")
    ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")
    ANTHROPIC_MODEL: str = os.getenv("ANTHROPIC_MODEL", "claude-opus-4-8")

    GOOGLE_API_KEYS: list[str] = [
        k.strip() for k in os.getenv("GOOGLE_API_KEYS", "").split(",") if k.strip()
    ]
    GOOGLE_EMBEDDING_MODEL: str = os.getenv("GOOGLE_EMBEDDING_MODEL", "gemini-embedding-2-preview")
    CHROMADB_PATH: str = os.getenv("CHROMADB_PATH", "data/chromadb")


config = Config()

LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


def validate_security(cfg: Config = config) -> None:
    """Fail fast rather than silently serving with auth disabled on a public bind.

    APP_HOST defaults to 0.0.0.0, so an empty API_KEY (also the default) would
    otherwise leave every memory read/write endpoint open to anyone who can
    reach the host.
    """
    if cfg.APP_HOST not in LOOPBACK_HOSTS and not cfg.API_KEY:
        raise RuntimeError(
            f"Refusing to start: APP_HOST={cfg.APP_HOST!r} is not loopback and "
            "API_KEY is empty. Set API_KEY in .env, or set APP_HOST=127.0.0.1 "
            "for local-only use."
        )
