import unittest

from fastapi import HTTPException

from app.auth import require_api_key
from app.config import config, validate_security
from app.main import app
from app.routes.memory_api import router as memory_router


class MemoryApiKeyAuthTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original_api_key = config.API_KEY
        self.addCleanup(self._restore_config)

    def _restore_config(self) -> None:
        config.API_KEY = self.original_api_key

    def test_require_api_key_allows_requests_when_auth_is_disabled(self) -> None:
        config.API_KEY = ""
        self.assertIsNone(require_api_key())

    def test_require_api_key_rejects_missing_header_when_auth_is_enabled(self) -> None:
        config.API_KEY = "secret-key"

        with self.assertRaises(HTTPException) as exc:
            require_api_key()

        self.assertEqual(exc.exception.status_code, 401)
        self.assertEqual(exc.exception.detail, "Invalid API key")

    def test_require_api_key_rejects_wrong_header_when_auth_is_enabled(self) -> None:
        config.API_KEY = "secret-key"

        with self.assertRaises(HTTPException) as exc:
            require_api_key("wrong-key")

        self.assertEqual(exc.exception.status_code, 401)
        self.assertEqual(exc.exception.detail, "Invalid API key")

    def test_require_api_key_accepts_correct_header_when_auth_is_enabled(self) -> None:
        config.API_KEY = "secret-key"
        self.assertIsNone(require_api_key("secret-key"))

    def test_memory_router_uses_shared_api_key_dependency(self) -> None:
        self.assertTrue(
            any(dep.dependency is require_api_key for dep in memory_router.dependencies)
        )

    def test_health_route_has_no_api_key_dependency(self) -> None:
        health_route = next(route for route in app.routes if getattr(route, "path", None) == "/health")
        self.assertEqual(
            [
                dep.call
                for dep in health_route.dependant.dependencies
                if dep.call is require_api_key
            ],
            [],
        )


class ValidateSecurityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original_api_key = config.API_KEY
        self.original_app_host = config.APP_HOST
        self.addCleanup(self._restore_config)

    def _restore_config(self) -> None:
        config.API_KEY = self.original_api_key
        config.APP_HOST = self.original_app_host

    def test_refuses_public_bind_with_no_api_key(self) -> None:
        config.APP_HOST = "0.0.0.0"
        config.API_KEY = ""

        with self.assertRaises(RuntimeError):
            validate_security()

    def test_allows_public_bind_with_api_key_set(self) -> None:
        config.APP_HOST = "0.0.0.0"
        config.API_KEY = "secret-key"

        self.assertIsNone(validate_security())

    def test_allows_loopback_bind_with_no_api_key(self) -> None:
        config.APP_HOST = "127.0.0.1"
        config.API_KEY = ""

        self.assertIsNone(validate_security())


if __name__ == "__main__":
    unittest.main()
