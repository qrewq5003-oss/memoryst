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
    # Retention is generational, not a flat file count. BACKUP_KEEP_DAYS is how many
    # *days* keep a backup; BACKUP_KEEP_RECENT is how many of the newest backups stay
    # regardless of day. A flat count alone was unsafe: backups are taken on every
    # server start as well as from cron, so an evening of 14 restarts would fill all
    # 14 slots with same-day copies and evict the entire daily history. Keeping the
    # newest backup per day defends against that; keeping the few newest overall
    # defends the other direction, so a pre-migration snapshot isn't immediately
    # replaced by a post-migration one taken the same day.
    BACKUP_KEEP_DAYS: int = int(os.getenv("BACKUP_KEEP_DAYS", os.getenv("BACKUP_KEEP", "14")))
    BACKUP_KEEP_RECENT: int = int(os.getenv("BACKUP_KEEP_RECENT", "3"))
    API_KEY: str = os.getenv("API_KEY", "")
    DEBUG: bool = os.getenv("DEBUG", "false").lower() == "true"

    LLM_API_BASE: str = os.getenv("LLM_API_BASE", "")
    # May hold a comma-separated failover pool: when one key hits its provider
    # quota (a 429), the client rotates to the next. A single key (no comma)
    # behaves exactly as before.
    LLM_API_KEY: str = os.getenv("LLM_API_KEY", "")
    # A non-reasoning model on purpose. Reasoning models (the previous default,
    # zai-org/glm-4.7, among them) spend their token budget on hidden reasoning and return
    # an empty completion, so every structured-output caller - scene extraction, and now
    # trackers - saw a JSONDecodeError instead of a payload. Verified live against the
    # NanoGPT catalog: reasoning_tokens=0, clean JSON. See CLAUDE.md's
    # scene-extraction-llm-failing investigation.
    LLM_MODEL: str = os.getenv("LLM_MODEL", "deepseek/deepseek-v4-pro")
    LLM_TIMEOUT: int = int(os.getenv("LLM_TIMEOUT", "30"))

    # Trackers rewrite a whole document per call, so a reasoning model measurably runs
    # 15-35s on one - right at the 30s default, which produced ReadTimeouts that looked
    # like the model rejecting the schema. Sized with headroom, and retried once, since
    # the observed failures were transient rather than deterministic.
    TRACKER_LLM_TIMEOUT: int = int(os.getenv("TRACKER_LLM_TIMEOUT", "120"))
    TRACKER_LLM_MAX_TOKENS: int = int(os.getenv("TRACKER_LLM_MAX_TOKENS", "10000"))
    TRACKER_LLM_RETRIES: int = int(os.getenv("TRACKER_LLM_RETRIES", "1"))

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
