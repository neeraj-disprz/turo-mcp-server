"""Session handling and HTTP error translation."""

from __future__ import annotations

import httpx
import pytest
import respx

from turo_mcp.client import (
    ArturoClient,
    headers_from,
    resolve_session_cookie,
    set_runtime_session,
)
from turo_mcp.errors import RequestRejected, SessionExpired, UpstreamUnavailable

from .conftest import API, call, call_expecting_error


class TestSetSessionTool:
    """The in-chat recovery path for clients that cannot send headers."""

    @respx.mock
    async def test_a_verified_cookie_is_stored_and_used_by_later_calls(self, server, settings):
        route = respx.get(f"{API}/auth/user-details/").mock(
            return_value=httpx.Response(200, json={"success": True, "user": {"email": "a@b.c"}})
        )

        result = await call(server, "set_session", cookie="  fresh-cookie  ")

        assert result["stored"] is True
        assert resolve_session_cookie(None, settings) == "fresh-cookie", "whitespace must be trimmed"
        assert route.calls[0].request.headers["cookie"] == "arturo_session=fresh-cookie"

    @respx.mock
    async def test_a_rejected_cookie_is_not_stored(self, server, settings):
        # Storing first and verifying after would cache a dud and make every
        # later tool fail with the same message, hiding the real cause.
        respx.get(f"{API}/auth/user-details/").mock(
            return_value=httpx.Response(401, json={"detail": "Authentication credentials were not provided."})
        )

        message = await call_expecting_error(server, "set_session", cookie="stale-cookie")

        assert "expired" in message
        assert resolve_session_cookie(None, settings) == "fallback-cookie", "the dud must not stick"

    async def test_an_empty_cookie_is_refused_without_a_network_call(self, server):
        message = await call_expecting_error(server, "set_session", cookie="   ")
        assert "must not be empty" in message


class TestSessionResolution:
    def test_prefers_connection_header_over_configured_fallback(self, settings):
        cookie = resolve_session_cookie({"X-Arturo-Session": "per-request"}, settings)
        assert cookie == "per-request"

    def test_header_lookup_is_case_insensitive(self, settings):
        # Transports normalise header casing differently; both must work.
        assert resolve_session_cookie({"x-arturo-session": "abc"}, settings) == "abc"

    def test_blank_header_falls_through_to_fallback(self, settings):
        assert resolve_session_cookie({"X-Arturo-Session": "   "}, settings) == "fallback-cookie"

    def test_no_header_uses_configured_fallback(self, settings):
        assert resolve_session_cookie(None, settings) == "fallback-cookie"

    def test_no_header_and_no_fallback_tells_caller_how_to_fix_it(self, settings):
        stateless = type(settings)(base_url=settings.base_url, session_cookie="")
        with pytest.raises(SessionExpired) as exc:
            resolve_session_cookie(None, stateless)
        assert "X-Arturo-Session" in str(exc.value)

    def test_runtime_session_is_used_when_no_header_is_sent(self, settings):
        set_runtime_session("handed-over-in-chat")
        assert resolve_session_cookie(None, settings) == "handed-over-in-chat"

    def test_connection_header_still_wins_over_the_runtime_session(self, settings):
        # The safety property for multi-user mode: one caller's `set_session`
        # must never silently run another caller's requests as that account.
        set_runtime_session("handed-over-in-chat")
        assert resolve_session_cookie({"X-Arturo-Session": "mine"}, settings) == "mine"

    def test_headers_from_degrades_when_transport_has_no_request(self):
        class NoContext:
            @property
            def headers(self):
                raise ValueError("Context is not available outside of a request")

        assert headers_from(NoContext()) is None


class TestRequestShape:
    @respx.mock
    async def test_sends_session_cookie_and_never_asserts_a_tenant(self, settings):
        route = respx.get(f"{API}/auth/user-details/").mock(
            return_value=httpx.Response(200, json={"ok": True})
        )
        await ArturoClient(settings, "live-cookie").request("GET", "/auth/user-details/")

        headers = route.calls[0].request.headers
        assert headers["cookie"] == "arturo_session=live-cookie"
        # get_current_tenant() trusts X-Tenant-ID above the session — never send it.
        assert "x-tenant-id" not in headers

    @respx.mock
    async def test_empty_body_becomes_empty_dict(self, settings):
        respx.post(f"{API}/anchor/delete/").mock(return_value=httpx.Response(204))
        assert await ArturoClient(settings, "c").request("POST", "/anchor/delete/") == {}


class TestErrorTranslation:
    @respx.mock
    async def test_401_is_reported_as_an_expired_session(self, settings):
        respx.get(f"{API}/auth/user-details/").mock(
            return_value=httpx.Response(401, json={"message": "Not authenticated"})
        )
        with pytest.raises(SessionExpired) as exc:
            await ArturoClient(settings, "c").request("GET", "/auth/user-details/")
        assert "24 hours" in str(exc.value)

    @respx.mock
    async def test_rbac_denial_is_not_mistaken_for_an_expired_session(self, settings):
        # A permission failure must not send the operator chasing a new cookie.
        respx.post(f"{API}/anchor/delete/").mock(
            return_value=httpx.Response(403, json={"error": "You do not have permission"})
        )
        with pytest.raises(RequestRejected):
            await ArturoClient(settings, "c").request("POST", "/anchor/delete/")

    @respx.mock
    async def test_reel3_validation_fields_reach_the_caller(self, settings):
        respx.post(f"{API}/reels/abc/generate").mock(
            return_value=httpx.Response(
                400,
                json={
                    "error": "invalid_request",
                    "fields": ["reelSettings.brand_colors.primary", "artStyle"],
                },
            )
        )
        with pytest.raises(RequestRejected) as exc:
            await ArturoClient(settings, "c").request("POST", "/reels/abc/generate")
        assert "invalid_request" in str(exc.value)

    @pytest.mark.parametrize("status", [502, 503, 504])
    @respx.mock
    async def test_gateway_failures_are_flagged_as_retryable(self, settings, status):
        respx.get(f"{API}/reels/abc/status").mock(return_value=httpx.Response(status))
        with pytest.raises(UpstreamUnavailable) as exc:
            await ArturoClient(settings, "c").request("GET", "/reels/abc/status")
        assert "retry" in str(exc.value).lower()

    @respx.mock
    async def test_timeout_is_not_reported_as_a_content_failure(self, settings):
        respx.get(f"{API}/reels/abc/status").mock(side_effect=httpx.ReadTimeout("slow"))
        with pytest.raises(UpstreamUnavailable) as exc:
            await ArturoClient(settings, "c").request("GET", "/reels/abc/status")
        assert "not a content failure" in str(exc.value)
