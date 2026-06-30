import pytest
from unittest.mock import patch


@pytest.fixture(autouse=True)
def disable_vector_store():
    """Disable vector store in all tests to avoid API calls."""
    with patch("app.services.vector_store.is_vector_store_enabled", return_value=False):
        yield


@pytest.fixture(autouse=True)
def reset_chat_buffer():
    """Clear hot-buffer module state so tests don't leak buffered messages or
    cached sequence numbers across DB paths."""
    from app.services.chat_buffer_service import reset_all_buffers

    reset_all_buffers()
    yield
    reset_all_buffers()
