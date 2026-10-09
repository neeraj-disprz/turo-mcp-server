"""Thin async HTTP client over the Arturo API.

One instance is built per tool call from the caller's own session cookie, so the
server itself stays stateless and holds no credential.
"""

from __future__ import annotations

from typing import Any, Mapping

import httpx

from .config import Settings
from .errors import RequestRejected, SessionExpired, UpstreamUnavailable

SESSION_COOKIE_NAME = "arturo_session"

# Arturo signals an unusable session with either of these on an authenticated
# endpoint. 403 is also used for "user has no tenant", which the message covers.
_AUTH_STATUSES = frozenset({401, 403})
_UPSTREAM_STATUSES = frozenset({502, 503, 504})


def unwrap(body: Any, key: str) -> Any:
    """Strip Arturo's `{"success": true, "<key>": {...}}` envelope.

    Several endpoints wrap their payload and several do not, so this only
    unwraps when the envelope is actually present and leaves anything else
    untouched. Handing the model the envelope would make it reach through
    `data.` on some tools and not others.
    """
    if isinstance(body, dict) and "success" in body and isinstance(body.get(key), (dict, list)):
        return body[key]
    return body


def headers_from(ctx: Any) -> Mapping[str, str] | None:
    """Request headers, or None when the transport has none.

    `Context.headers` reaches through to the transport's request object and
    raises outside an active request — true on stdio and in direct calls — so a
    missing context degrades to the configured fallback cookie rather than
    crashing the tool.
    """
    try:
        return ctx.headers
    except (ValueError, AttributeError):
        return None


# Cookie handed over at runtime by the `set_session` tool. Demo affordance for
# clients that cannot send connection headers: the cookie arrives in the chat
# instead. Deliberately not persisted — the conversation is the durable copy, so
# a restart just costs one silent `set_session` call, not a prompt to the user.
_RUNTIME_SESSION = ""


def set_runtime_session(cookie: str) -> None:
    global _RUNTIME_SESSION
    _RUNTIME_SESSION = cookie.strip()


def resolve_session_cookie(headers: Mapping[str, str] | None, settings: Settings) -> str:
    """Per-request cookie from the connection header, else runtime, else configured.

    Header lookup is case-insensitive because transports normalise differently.

    The header wins over `_RUNTIME_SESSION` on purpose. In multi-user mode every
    caller brings their own cookie, and letting one caller's `set_session` take
    precedence would silently run everyone else's requests as that account.
    """
    if headers:
        wanted = settings.session_header.lower()
        for name, value in headers.items():
            if name.lower() == wanted and value.strip():
                return value.strip()
    if _RUNTIME_SESSION:
        return _RUNTIME_SESSION
    if settings.session_cookie:
        return settings.session_cookie
    raise SessionExpired(settings.session_header)


def _message_from(body: Any, fallback: str) -> str:
    if not isinstance(body, dict):
        return fallback

    headline = ""
    for key in ("message", "error", "detail"):
        value = body.get(key)
        if isinstance(value, str) and value.strip():
            headline = value.strip()
            break

    # Arturo's generic wrappers ("Script generation API request failed") say
    # nothing on their own; the cause is in details/response_text. Dropping
    # those leaves the caller with an error it cannot act on.
    extras: list[str] = []
    for key in ("details", "response_text"):
        value = body.get(key)
        if isinstance(value, str) and value.strip() and value.strip() != headline:
            extras.append(f"{key}: {_truncate(value.strip())}")

    # Reel 3.0 validation returns the offending field paths.
    fields = body.get("fields")
    if isinstance(fields, list) and fields:
        extras.append(f"fields: {', '.join(str(f) for f in fields)}")

    if not headline:
        headline = fallback
    return " | ".join([headline, *extras])


def _truncate(text: str, limit: int = 600) -> str:
    return text if len(text) <= limit else text[:limit] + "…"


class ArturoClient:
    def __init__(self, settings: Settings, session_cookie: str) -> None:
        self._settings = settings
        self._cookie = session_cookie

    async def request(
        self,
        method: str,
        path: str,
        *,
        json: Any | None = None,
        params: Mapping[str, Any] | None = None,
        timeout: float | None = None,
    ) -> Any:
        url = f"{self._settings.base_url}{self._settings.api_prefix}{path}"
        headers = {
            "Accept": "application/json",
            # Deliberately no X-Tenant-ID: get_current_tenant() trusts that header
            # above the session, and the MCP has no reason to assert a tenant.
            "Cookie": f"{SESSION_COOKIE_NAME}={self._cookie}",
        }
        try:
            async with httpx.AsyncClient(
                timeout=timeout or self._settings.timeout_seconds
            ) as http:
                response = await http.request(
                    method, url, json=json, params=params, headers=headers
                )
        except httpx.TimeoutException as exc:
            raise UpstreamUnavailable(
                f"request to {path} timed out after "
                f"{timeout or self._settings.timeout_seconds:.0f}s. Arturo may still be "
                "working — check the matching status tool before retrying, because a "
                "retry starts the job again"
            ) from exc
        except httpx.RequestError as exc:
            raise UpstreamUnavailable(f"could not reach the Arturo API ({exc})") from exc

        return self._unwrap(response, path)

    def _unwrap(self, response: httpx.Response, path: str) -> Any:
        try:
            body: Any = response.json()
        except ValueError:
            body = None

        if response.status_code in _AUTH_STATUSES and _looks_like_auth_failure(body):
            raise SessionExpired(self._settings.session_header)

        if response.status_code in _UPSTREAM_STATUSES:
            raise UpstreamUnavailable(_message_from(body, f"HTTP {response.status_code} from {path}"))

        if response.status_code >= 400:
            raise RequestRejected(
                _message_from(body, f"Arturo rejected {path} with HTTP {response.status_code}")
            )

        return body if body is not None else {}


def _looks_like_auth_failure(body: Any) -> bool:
    """Distinguish 'your session is dead' from 'this user may not do that'.

    Arturo returns 403 for both an unauthenticated session and an authenticated
    user without a tenant; only the former is worth telling the caller to
    re-paste a cookie for, but both are unrecoverable without operator action, so
    we treat an explicit RBAC denial as a plain rejection and everything else as
    an expired session.
    """
    if isinstance(body, dict):
        message = " ".join(
            str(body.get(key, "")) for key in ("message", "error", "detail")
        ).lower()
        rbac_markers = ("permission", "not allowed", "forbidden for", "access denied", "only super")
        if any(marker in message for marker in rbac_markers):
            return False
    return True
