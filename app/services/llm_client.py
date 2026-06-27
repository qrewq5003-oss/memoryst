import httpx

from app.config import config


def is_llm_enabled() -> bool:
    """Check if LLM integration is configured."""
    return bool(config.LLM_API_BASE)


def chat_completion(
    messages: list[dict[str, str]],
    *,
    max_tokens: int = 500,
    temperature: float = 0.3,
) -> str:
    """
    Call an OpenAI-compatible chat completion API.

    Returns the assistant's response text.
    Raises httpx.HTTPError on network/API errors.
    """
    url = f"{config.LLM_API_BASE.rstrip('/')}/v1/chat/completions"
    headers = {
        "Content-Type": "application/json",
    }
    if config.LLM_API_KEY:
        headers["Authorization"] = f"Bearer {config.LLM_API_KEY}"

    payload = {
        "model": config.LLM_MODEL,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }

    response = httpx.post(
        url,
        json=payload,
        headers=headers,
        timeout=config.LLM_TIMEOUT,
    )
    response.raise_for_status()

    data = response.json()
    return data["choices"][0]["message"]["content"]
