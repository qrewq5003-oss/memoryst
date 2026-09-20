from urllib.parse import urlsplit

from fastapi import Header, HTTPException, Request, status

from app.config import config

# Methods that cannot change state, so they need no CSRF guard.
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    """Validate X-API-Key for memory API requests when auth is enabled."""
    if not config.API_KEY:
        return

    if x_api_key != config.API_KEY:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
        )


def _origin_host(value: str) -> str:
    """The `host[:port]` of an Origin/Referer header, or "" if there isn't one."""
    return urlsplit(value).netloc


def require_same_origin(request: Request) -> None:
    """Reject cross-site state-changing requests to the web UI.

    The UI is form-based, and a form POST is a CORS *simple* request: the browser
    sends it and only withholds the response. So tightening CORS does not stop
    `https://somewhere-else/` from submitting a hidden form at
    `http://localhost:8001/ui/delete-chat` - the delete happens, the attacker just
    doesn't get to read the confirmation. That is the whole attack.

    The JSON `/memory` API is not exposed this way: `application/json` is not a
    simple content type and DELETE is not a simple method, so both are preflighted
    and the CORS allowlist already decides them. Which is just as well, because the
    API is legitimately called cross-origin - SillyTavern runs on another port, and
    a same-origin rule there would break the extension. Every legitimate caller of
    `/ui` is the UI page itself, so here the rule costs nothing.

    Safe methods are left alone: they change nothing, and a browser sends no Origin
    on a top-level navigation, so enforcing it would break simply opening the UI.

    A request carrying neither header is allowed through. Browsers always send
    Origin on a cross-origin POST, so "neither header" means curl, a script, or the
    test client - not the case being defended against. This is a CSRF guard, not
    authentication; it does not try to be one.
    """
    if request.method in SAFE_METHODS:
        return

    expected = request.headers.get("host", "")
    # Origin first: it is the header browsers guarantee on cross-origin writes.
    # Referer is the fallback for the browsers that omit Origin on a same-origin
    # form post (Safari has historically done this), where a present-and-matching
    # Referer is the only evidence available.
    for header in ("origin", "referer"):
        raw = request.headers.get(header)
        if not raw:
            continue
        if _origin_host(raw) != expected:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Cross-origin request rejected",
            )
        return
