"""End-to-end over a real socket.

This is the test for the central design claim: the Arturo session cookie travels
on the MCP *connection header*, reaches the tool, and is forwarded to Arturo —
without ever appearing as a tool argument. Everything else is mocked at the HTTP
boundary; here both hops are real.
"""

from __future__ import annotations

import asyncio
import socket
from contextlib import asynccontextmanager

import httpx2
import pytest
import uvicorn
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from turo_mcp.config import Settings
from turo_mcp.server import build_server


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@asynccontextmanager
async def _serve(app, port: int):
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    try:
        for _ in range(100):
            if server.started:
                break
            await asyncio.sleep(0.05)
        else:  # pragma: no cover - only on a pathologically slow machine
            raise RuntimeError("server did not start")
        yield
    finally:
        server.should_exit = True
        await task


class FakeArturo:
    """Stands in for the Arturo API and records what it was sent."""

    def __init__(self) -> None:
        self.received: list[Request] = []

    def app(self) -> Starlette:
        async def user_details(request: Request) -> JSONResponse:
            self.received.append(request)
            return JSONResponse(
                {"success": True, "user": {"email": "author@disprz.com", "role": "admin"}}
            )

        async def unauthorised(request: Request) -> JSONResponse:
            self.received.append(request)
            return JSONResponse({"message": "Not authenticated"}, status_code=401)

        return Starlette(
            routes=[
                Route("/ui/api/auth/user-details/", user_details),
                Route("/ui/api/reels/voices", unauthorised),
            ]
        )


@asynccontextmanager
async def _connected(mcp_port: int, headers: dict[str, str] | None):
    url = f"http://127.0.0.1:{mcp_port}/mcp"
    async with httpx2.AsyncClient(headers=headers or {}) as http_client:
        async with streamable_http_client(url, http_client=http_client) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield session


@pytest.fixture
async def live(request):
    """A running fake Arturo plus a running MCP server wired to it."""
    arturo = FakeArturo()
    arturo_port, mcp_port = _free_port(), _free_port()

    settings = Settings(
        base_url=f"http://127.0.0.1:{arturo_port}",
        session_header="X-Arturo-Session",
        session_cookie="",  # stateless: no fallback, the header is the only credential
        timeout_seconds=10.0,
    )
    mcp_app = build_server(settings).streamable_http_app()

    async with _serve(arturo.app(), arturo_port):
        async with _serve(mcp_app, mcp_port):
            yield arturo, mcp_port


async def test_connection_header_becomes_the_arturo_cookie(live):
    arturo, mcp_port = live

    async with _connected(mcp_port, {"X-Arturo-Session": "live-session-value"}) as session:
        result = await session.call_tool("whoami", {})

    assert result.structured_content["email"] == "author@disprz.com"
    assert arturo.received[0].headers["cookie"] == "arturo_session=live-session-value"


async def test_tenant_is_never_asserted_by_the_mcp_server(live):
    arturo, mcp_port = live

    async with _connected(mcp_port, {"X-Arturo-Session": "v"}) as session:
        await session.call_tool("whoami", {})

    # get_current_tenant() trusts X-Tenant-ID above the session; sending it would
    # be a tenant-spoofing surface.
    assert "x-tenant-id" not in arturo.received[0].headers


async def test_a_connection_without_the_header_is_refused(live):
    _, mcp_port = live

    async with _connected(mcp_port, None) as session:
        result = await session.call_tool("whoami", {})

    assert result.is_error
    assert "X-Arturo-Session" in result.content[0].text


async def test_an_expired_cookie_produces_one_actionable_message(live):
    _, mcp_port = live

    async with _connected(mcp_port, {"X-Arturo-Session": "stale"}) as session:
        result = await session.call_tool("list_reel_voices", {})

    assert result.is_error
    message = result.content[0].text
    assert "expired" in message and "24 hours" in message


async def test_the_full_tool_surface_is_advertised_over_http(live):
    _, mcp_port = live

    async with _connected(mcp_port, {"X-Arturo-Session": "v"}) as session:
        listed = await session.list_tools()

    names = {tool.name for tool in listed.tools}
    assert len(names) == 25
    assert {"whoami", "generate_reel3", "generate_simulation"} <= names


async def test_two_callers_use_their_own_sessions_on_one_server(live):
    """The server is stateless: concurrent callers must not share a credential."""
    arturo, mcp_port = live

    async with _connected(mcp_port, {"X-Arturo-Session": "alice-cookie"}) as alice:
        await alice.call_tool("whoami", {})
    async with _connected(mcp_port, {"X-Arturo-Session": "bob-cookie"}) as bob:
        await bob.call_tool("whoami", {})

    cookies = [r.headers["cookie"] for r in arturo.received]
    assert cookies == ["arturo_session=alice-cookie", "arturo_session=bob-cookie"]
