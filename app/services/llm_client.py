import httpx

from app.config import config

ANTHROPIC_API_VERSION = "2023-06-01"

PROVIDERS = ("nanogpt", "openai", "anthropic")

ACTIVE_PROVIDER_SETTING_KEY = "active_llm_provider"

# In-process cache of the active provider, lazily loaded from the app_settings
# table on first access (see get_active_provider). None means "not loaded yet -
# check SQLite, then fall back to config.ACTIVE_LLM_PROVIDER". Persisting to
# SQLite (rather than rewriting .env) means switching is safe and instant, and
# survives restarts because the row is loaded back on the first call after
# a fresh process starts.
_active_provider_override: str | None = None


def _load_persisted_provider() -> str | None:
    """Read the persisted active provider from SQLite.

    Swallows errors so a not-yet-migrated database (e.g. before init_schema()
    has run) degrades to the .env default instead of crashing every caller.
    """
    try:
        from app.repositories.app_settings_repo import get_setting

        return get_setting(ACTIVE_PROVIDER_SETTING_KEY)
    except Exception:
        return None


def get_active_provider() -> str:
    """Return the currently active LLM provider name.

    Resolution order: in-process cache -> SQLite app_settings -> .env default.
    """
    global _active_provider_override
    if _active_provider_override is not None:
        return _active_provider_override

    persisted = _load_persisted_provider()
    if persisted in PROVIDERS:
        _active_provider_override = persisted
        return persisted

    return config.ACTIVE_LLM_PROVIDER


def set_active_provider(provider: str) -> None:
    """Switch the active LLM provider, persisting the choice to SQLite so it
    survives a server restart."""
    if provider not in PROVIDERS:
        raise ValueError(f"Unknown provider: {provider!r}. Must be one of {PROVIDERS}.")

    from app.repositories.app_settings_repo import set_setting

    set_setting(ACTIVE_PROVIDER_SETTING_KEY, provider)

    global _active_provider_override
    _active_provider_override = provider


def _provider_settings(provider: str) -> dict:
    if provider == "nanogpt":
        return {
            "api_base": config.LLM_API_BASE,
            "api_key": config.LLM_API_KEY,
            "model": config.LLM_MODEL,
        }
    if provider == "openai":
        return {
            "api_base": config.OPENAI_API_BASE,
            "api_key": config.OPENAI_API_KEY,
            "model": config.OPENAI_MODEL,
        }
    if provider == "anthropic":
        return {
            "api_base": config.ANTHROPIC_API_BASE,
            "api_key": config.ANTHROPIC_API_KEY,
            "model": config.ANTHROPIC_MODEL,
        }
    raise ValueError(f"Unknown provider: {provider!r}. Must be one of {PROVIDERS}.")


def is_llm_enabled() -> bool:
    """Check if the active LLM provider is configured."""
    provider = get_active_provider()
    settings = _provider_settings(provider)
    if provider == "anthropic":
        return bool(settings["api_key"])
    return bool(settings["api_base"])


def _list_models_via_get(url: str, headers: dict, fallback_model: str) -> list[str]:
    try:
        resp = httpx.get(url, headers=headers, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        return [m["id"] for m in data.get("data", [])]
    except Exception:
        return [fallback_model]


def list_models() -> list[str]:
    """List available models from the active LLM provider's API."""
    if not is_llm_enabled():
        return []

    provider = get_active_provider()
    settings = _provider_settings(provider)

    if provider == "anthropic":
        url = f"{settings['api_base'].rstrip('/')}/v1/models"
        headers = {
            "x-api-key": settings["api_key"],
            "anthropic-version": ANTHROPIC_API_VERSION,
        }
    else:
        url = f"{settings['api_base'].rstrip('/')}/v1/models"
        headers = {}
        if settings["api_key"]:
            headers["Authorization"] = f"Bearer {settings['api_key']}"

    return _list_models_via_get(url, headers, settings["model"])


def chat_completion(
    messages: list[dict[str, str]],
    *,
    model: str | None = None,
    max_tokens: int = 500,
    temperature: float = 0.3,
    response_format: dict | None = None,
) -> str:
    """
    Call a chat completion API on the active LLM provider.
    Optional model override — defaults to the active provider's configured model.

    response_format, when given, is OpenAI-style
    ({"type": "json_schema", "json_schema": {"name":..., "schema": {...}}}) and is
    translated as needed for the active provider so callers don't need to know
    which provider is active.
    """
    provider = get_active_provider()
    settings = _provider_settings(provider)
    resolved_model = model or settings["model"]

    if provider == "anthropic":
        return _chat_completion_anthropic(
            messages,
            settings,
            model=resolved_model,
            max_tokens=max_tokens,
            response_format=response_format,
        )

    return _chat_completion_openai_compatible(
        messages,
        settings,
        model=resolved_model,
        max_tokens=max_tokens,
        temperature=temperature,
        response_format=response_format,
    )


def _chat_completion_openai_compatible(
    messages: list[dict[str, str]],
    settings: dict,
    *,
    model: str,
    max_tokens: int,
    temperature: float,
    response_format: dict | None,
) -> str:
    url = f"{settings['api_base'].rstrip('/')}/v1/chat/completions"
    headers = {"Content-Type": "application/json"}
    if settings["api_key"]:
        headers["Authorization"] = f"Bearer {settings['api_key']}"

    payload = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    if response_format is not None:
        payload["response_format"] = response_format

    response = httpx.post(url, json=payload, headers=headers, timeout=config.LLM_TIMEOUT)
    response.raise_for_status()
    return response.json()["choices"][0]["message"]["content"]


def _translate_response_format_anthropic(response_format: dict | None) -> dict | None:
    """Translate an OpenAI-style json_schema response_format into Anthropic's
    output_config.format shape (schema only — Anthropic doesn't take name/strict)."""
    if not response_format:
        return None
    schema = (response_format.get("json_schema") or {}).get("schema")
    if schema is None:
        return None
    return {"type": "json_schema", "schema": schema}


def _chat_completion_anthropic(
    messages: list[dict[str, str]],
    settings: dict,
    *,
    model: str,
    max_tokens: int,
    response_format: dict | None,
) -> str:
    system_text = "\n\n".join(m["content"] for m in messages if m.get("role") == "system")
    conversation = [
        {"role": m["role"], "content": m["content"]}
        for m in messages
        if m.get("role") in ("user", "assistant")
    ]

    url = f"{settings['api_base'].rstrip('/')}/v1/messages"
    headers = {
        "x-api-key": settings["api_key"],
        "anthropic-version": ANTHROPIC_API_VERSION,
        "Content-Type": "application/json",
    }

    payload = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": conversation,
    }
    if system_text:
        payload["system"] = system_text
    # Recent Claude models (Opus 4.7+, Sonnet 5, Fable 5) reject non-default
    # temperature/top_p/top_k with a 400, so it's intentionally not forwarded here.

    output_format = _translate_response_format_anthropic(response_format)
    if output_format is not None:
        payload["output_config"] = {"format": output_format}

    response = httpx.post(url, json=payload, headers=headers, timeout=config.LLM_TIMEOUT)
    response.raise_for_status()
    data = response.json()

    if data.get("stop_reason") == "refusal":
        raise RuntimeError("Anthropic declined the request (stop_reason=refusal)")

    for block in data.get("content", []):
        if block.get("type") == "text":
            return block.get("text", "")
    raise RuntimeError("Anthropic response contained no text content")
