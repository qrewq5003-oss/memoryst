import pytest
from unittest.mock import patch


@pytest.fixture(autouse=True)
def disable_vector_store():
    """Disable vector store in all tests to avoid API calls."""
    with patch("app.services.vector_store.is_vector_store_enabled", return_value=False):
        yield
