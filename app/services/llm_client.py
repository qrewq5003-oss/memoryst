import httpx

from app.config import config


def is_llm_enabled() -> bool:
    """Check if LLM integration is configured."""
    return bool(config.LLM_API_BASE)


def list_models() -> list[str]:
    """List available models from the LLM API."""
    if not is_llm_enabled():
        return []
    url = f"{config.LLM_API_BASE.rstrip('/')}/v1/models"
    headers = {}
    if config.LLM_API_KEY:
        headers["Authorization"] = f"Bearer {config.LLM_API_KEY}"
    try:
        resp = httpx.get(url, headers=headers, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        return [m["id"] for m in data.get("data", [])]
    except Exception:
        return [config.LLM_MODEL]


def chat_completion(
    messages: list[dict[str, str]],
    *,
    model: str | None = None,
    max_tokens: int = 500,
    temperature: float = 0.3,
) -> str:
    """
    Call an OpenAI-compatible chat completion API.
    Optional model override — defaults to config.LLM_MODEL.
    """
    url = f"{config.LLM_API_BASE.rstrip('/')}/v1/chat/completions"
    headers = {"Content-Type": "application/json"}
    if config.LLM_API_KEY:
        headers["Authorization"] = f"Bearer {config.LLM_API_KEY}"

    payload = {
        "model": model or config.LLM_MODEL,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }

    response = httpx.post(url, json=payload, headers=headers, timeout=config.LLM_TIMEOUT)
    response.raise_for_status()
    return response.json()["choices"][0]["message"]["content"]
