from __future__ import annotations

import json
from typing import Any

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from turo_mcp import client as client_module
from turo_mcp.config import Settings
from turo_mcp.server import build_server

API = "https://arturo.test/ui/api"


@pytest.fixture(autouse=True)
def _clear_runtime_session():
    """`set_session` stores the cookie in module state, which would otherwise
    leak into every test that runs after it."""
    client_module.set_runtime_session("")
    yield
    client_module.set_runtime_session("")


@pytest.fixture
def settings() -> Settings:
    return Settings(
        base_url="https://arturo.test",
        session_header="X-Arturo-Session",
        session_cookie="fallback-cookie",
        timeout_seconds=5.0,
    )


@pytest.fixture
def server(settings: Settings):
    return build_server(settings)


async def call(server, name: str, **arguments: Any) -> Any:
    """Invoke a tool and return its structured result."""
    result = await server.call_tool(name, arguments)
    return result.structured_content


def body_of(route, index: int = 0) -> dict[str, Any]:
    """The JSON body of a captured request, parsed."""
    return json.loads(route.calls[index].request.read())


async def call_expecting_error(server, name: str, **arguments: Any) -> str:
    """Invoke a tool that should fail, returning the message the model would see."""
    try:
        await server.call_tool(name, arguments)
    except ToolError as exc:
        return str(exc)
    raise AssertionError(f"{name} was expected to fail but succeeded")
