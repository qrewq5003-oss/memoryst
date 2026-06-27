import os


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
    APP_PORT: int = _parse_port(os.getenv("APP_PORT", "8000"))
    DATABASE_PATH: str = os.getenv("DATABASE_PATH", "data/memory.db")
    API_KEY: str = os.getenv("API_KEY", "")
    DEBUG: bool = os.getenv("DEBUG", "false").lower() == "true"

    LLM_API_BASE: str = os.getenv("LLM_API_BASE", "")
    LLM_API_KEY: str = os.getenv("LLM_API_KEY", "")
    LLM_MODEL: str = os.getenv("LLM_MODEL", "zai-org/glm-4.7")
    LLM_TIMEOUT: int = int(os.getenv("LLM_TIMEOUT", "30"))


config = Config()
