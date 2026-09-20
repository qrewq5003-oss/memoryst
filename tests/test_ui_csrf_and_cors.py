"""The browser-side half of the auth story.

API_KEY guards who may call /memory from outside the machine. Neither it nor the
loopback bind does anything about the other browser tab: a page on any site can
submit a form at http://localhost:8001/ui/delete-chat, and before these guards the
delete simply happened. These tests pin both halves of the fix - the CORS allowlist
that stops other origins reading responses, and the same-origin rule that stops them
issuing writes.
"""

import re
import unittest

from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.auth import require_same_origin
from app.config import config
from app.main import app


class _FakeRequest:
    """Enough of a Request for the dependency: a method and headers."""

    def __init__(self, method: str, headers: dict[str, str]) -> None:
        self.method = method
        self.headers = {k.lower(): v for k, v in headers.items()}


class SameOriginGuardTests(unittest.TestCase):
    def _call(self, method: str, **headers: str):
        return require_same_origin(_FakeRequest(method, headers))

    def test_cross_origin_post_is_rejected(self) -> None:
        with self.assertRaises(HTTPException) as exc:
            self._call("POST", host="localhost:8001", origin="https://evil.example")

        self.assertEqual(exc.exception.status_code, 403)
        self.assertEqual(exc.exception.detail, "Cross-origin request rejected")

    def test_same_origin_post_is_allowed(self) -> None:
        self.assertIsNone(
            self._call("POST", host="localhost:8001", origin="http://localhost:8001")
        )

    def test_a_lookalike_host_does_not_pass(self) -> None:
        # localhost:8001.evil.example must not satisfy a check for localhost:8001.
        with self.assertRaises(HTTPException):
            self._call(
                "POST",
                host="localhost:8001",
                origin="http://localhost:8001.evil.example",
            )

    def test_referer_is_used_when_origin_is_absent(self) -> None:
        # Some browsers omit Origin on a same-origin form post; Referer is then the
        # only evidence there is, and a cross-site one still has to lose.
        self.assertIsNone(
            self._call("POST", host="localhost:8001", referer="http://localhost:8001/ui")
        )
        with self.assertRaises(HTTPException):
            self._call("POST", host="localhost:8001", referer="https://evil.example/x")

    def test_origin_wins_over_referer(self) -> None:
        # A matching Referer must not rescue a request whose Origin is cross-site.
        with self.assertRaises(HTTPException):
            self._call(
                "POST",
                host="localhost:8001",
                origin="https://evil.example",
                referer="http://localhost:8001/ui",
            )

    def test_a_request_with_neither_header_is_allowed(self) -> None:
        # curl, scripts, the test client. Browsers always send Origin on a
        # cross-origin write, so this is not the case being defended against - and
        # rejecting it would break every non-browser caller for no security gain.
        self.assertIsNone(self._call("POST", host="localhost:8001"))

    def test_safe_methods_are_never_blocked(self) -> None:
        # A top-level navigation carries no Origin, so enforcing it on GET would make
        # the UI impossible to open.
        for method in ("GET", "HEAD", "OPTIONS"):
            self.assertIsNone(
                self._call(method, host="localhost:8001", origin="https://evil.example")
            )


class UiRouterIsGuardedTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)

    def test_cross_origin_form_post_to_the_ui_is_refused(self) -> None:
        response = self.client.post(
            "/ui/delete-chat",
            data={"chat_id": "whatever", "character_id": "whatever"},
            headers={"Origin": "https://evil.example"},
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 403)

    def test_the_guard_covers_the_whole_router_not_one_route(self) -> None:
        # The point of putting the dependency on the router is that a route added
        # later is covered without anyone remembering to.
        for path in ("/ui/store", "/ui/retrieve", "/ui/create"):
            with self.subTest(path=path):
                response = self.client.post(
                    path,
                    data={"anything": "x"},
                    headers={"Origin": "https://evil.example"},
                    follow_redirects=False,
                )
                self.assertEqual(response.status_code, 403)

    def test_opening_the_ui_still_works(self) -> None:
        self.assertEqual(self.client.get("/ui").status_code, 200)

    def test_same_origin_post_is_not_blocked_by_the_guard(self) -> None:
        # Reaches the handler: whatever it answers, it is not the 403 above.
        response = self.client.post(
            "/ui/delete-chat",
            data={"chat_id": "no-such-chat", "character_id": "no-such-character"},
            headers={"Origin": "http://testserver"},
            follow_redirects=False,
        )
        self.assertNotEqual(response.status_code, 403)


class CorsAllowlistTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)

    def _preflight(self, origin: str, path: str = "/memory/store"):
        return self.client.options(
            path,
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
            },
        )

    def test_a_foreign_site_is_not_granted_access(self) -> None:
        response = self._preflight("https://evil.example")
        self.assertNotIn("access-control-allow-origin", response.headers)

    def test_sillytavern_on_another_localhost_port_still_works(self) -> None:
        # The one cross-origin caller that has to keep working.
        response = self._preflight("http://localhost:8000")
        self.assertEqual(
            response.headers.get("access-control-allow-origin"), "http://localhost:8000"
        )

    def test_the_default_regex_is_anchored_to_loopback(self) -> None:
        pattern = re.compile(config.CORS_ALLOW_ORIGIN_REGEX)
        for origin in ("http://localhost:8000", "http://127.0.0.1:8001", "http://[::1]:8001"):
            with self.subTest(origin=origin):
                self.assertTrue(pattern.match(origin))
        for origin in (
            "https://evil.example",
            "http://localhost.evil.example",
            "http://127.0.0.1.evil.example",
            "http://notlocalhost",
        ):
            with self.subTest(origin=origin):
                self.assertIsNone(pattern.match(origin))

    def test_an_explicit_list_replaces_the_regex_rather_than_widening_it(self) -> None:
        # Both handed to Starlette at once would OR together, quietly keeping the
        # broad default alive beside the narrow list someone chose on purpose.
        from app import main

        source = main.__file__
        with open(source, encoding="utf-8") as handle:
            text = handle.read()
        self.assertIn("if config.CORS_ALLOW_ORIGINS", text)
        self.assertNotIn('allow_origins=["*"]', text)


if __name__ == "__main__":
    unittest.main()
