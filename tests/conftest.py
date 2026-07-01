import pytest
from unittest.mock import patch


@pytest.fixture(autouse=True)
def disable_vector_store():
    """Disable vector store in all tests to avoid API calls."""
    with patch("app.services.vector_store.is_vector_store_enabled", return_value=False):
        yield


@pytest.fixture(autouse=True)
def disable_llm_by_default():
    """Make every LLM provider look unconfigured by default, regardless of
    whatever real credentials happen to be in .env.

    llm_client.is_llm_enabled() is imported by value into several service
    modules (summary_service, llm_extractor, scene_extractor) - patching the
    function itself would need a separate patch target per importer. Config
    is a single shared singleton read live on every call, so mutating its
    attributes here is the one place that reliably reaches all of them.

    Without this, a test that never explicitly mocks is_llm_enabled()/
    chat_completion() silently assumes LLM is disabled via .env being blank.
    That assumption broke for real the moment real NanoGPT credentials were
    configured: several tests that expected the deterministic rule-based
    fallback text instead got genuine (non-deterministic) LLM output and
    failed - not because the code was wrong, but because those tests were
    never hermetic. Tests that specifically want to exercise the LLM path
    still patch is_llm_enabled/chat_completion themselves; that patch simply
    overrides what this fixture set.
    """
    from app.config import config

    original = {
        "LLM_API_BASE": config.LLM_API_BASE,
        "LLM_API_KEY": config.LLM_API_KEY,
        "OPENAI_API_BASE": config.OPENAI_API_BASE,
        "OPENAI_API_KEY": config.OPENAI_API_KEY,
        "ANTHROPIC_API_KEY": config.ANTHROPIC_API_KEY,
    }
    config.LLM_API_BASE = ""
    config.LLM_API_KEY = ""
    config.OPENAI_API_BASE = ""
    config.OPENAI_API_KEY = ""
    config.ANTHROPIC_API_KEY = ""
    try:
        yield
    finally:
        for key, value in original.items():
            setattr(config, key, value)


@pytest.fixture(autouse=True)
def reset_chat_buffer():
    """Clear hot-buffer module state so tests don't leak buffered messages or
    cached sequence numbers across DB paths."""
    from app.services.chat_buffer_service import reset_all_buffers

    reset_all_buffers()
    yield
    reset_all_buffers()
