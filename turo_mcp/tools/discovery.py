"""Session preflight and pathway reads."""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import Context, MCPServer

from ..client import ArturoClient, set_runtime_session, unwrap
from ..config import Settings
from ..errors import InvalidArgument


def _add_pathway_urls(body: Any, settings: Settings) -> None:
    """Attach a clickable UI link to each search result, in place.

    Deliberately forgiving about the shape: the link is a convenience, so a
    search response that comes back looking different must still return its
    results rather than fail the whole tool.
    """
    if not isinstance(body, dict):
        return
    for item in body.get("results") or []:
        if not isinstance(item, dict):
            continue
        pathway_id = item.get("pathway_id") or item.get("id")
        if pathway_id:
            item["url"] = settings.pathway_url(str(pathway_id))


def register(server: MCPServer, settings: Settings, client_for) -> None:
    @server.tool(
        description=(
            "Hand the server an arturo_session cookie to use for subsequent calls. "
            "Call this whenever a tool reports the session expired: first reuse a "
            "cookie already supplied in this conversation (the server forgets it on "
            "restart, so re-sending a still-valid one is the normal recovery and "
            "needs no prompt), and only ask the user for a fresh one if that fails. "
            "The cookie is verified before it is stored. Has no effect where the "
            "caller sends the session as a connection header — that always wins."
        )
    )
    async def set_session(ctx: Context, cookie: str) -> dict[str, Any]:
        value = cookie.strip()
        if not value:
            raise InvalidArgument("cookie must not be empty")
        # Verify before storing, so a dud cookie fails here rather than being
        # cached and failing every later tool with the same recovery message.
        body = await ArturoClient(settings, value).request("GET", "/auth/user-details/")
        set_runtime_session(value)
        return {"stored": True, "user": unwrap(body, "user")}

    @server.tool(
        description=(
            "Confirm the Arturo session is live and show who it belongs to. Call this "
            "first in a session: it fails fast with a clear message if the cookie has "
            "expired, instead of letting 401s surface halfway through a generation chain."
        )
    )
    async def whoami(ctx: Context) -> dict[str, Any]:
        client: ArturoClient = client_for(ctx)
        body = await client.request("GET", "/auth/user-details/")
        return unwrap(body, "user")

    @server.tool(
        description=(
            "List learning pathways (document-to-learning projects) in the tenant "
            "workspace. Use this to find a pathway's learning_pathway_id before "
            "reading or editing it. Each result carries a `url` that opens the "
            "pathway in Turo — include it when you name a pathway to the user."
        )
    )
    async def list_pathways(
        ctx: Context,
        search: str = "",
        created_by_scope: str = "all",
        sort_by: str = "recent",
        offset: int = 0,
        limit: int = 20,
    ) -> dict[str, Any]:
        if created_by_scope not in {"all", "me", "others"}:
            raise InvalidArgument("created_by_scope must be one of: all, me, others")
        if sort_by not in {"recent", "title", "created_at"}:
            raise InvalidArgument("sort_by must be one of: recent, title, created_at")

        client: ArturoClient = client_for(ctx)
        body = await client.request(
            "POST",
            "/workspace/projects/search/",
            json={
                "search": search,
                "agent_type": ["doc_to_learning"],
                "created_by_scope": created_by_scope,
                "sort_by": sort_by,
                "offset": max(offset, 0),
                "limit": min(max(limit, 1), 100),
            },
        )
        _add_pathway_urls(body, settings)
        return body

    @server.tool(
        description=(
            "Read a full pathway: its chapters and, under each, the concepts with their "
            "content type, duration and generation state (is_kb_generated, "
            "is_script_generated, is_content_generated). This is the primary read — call "
            "it before any edit or generation so you are working from current ids and state. "
            "Returns a `url` that opens this pathway in Turo — give it to the user when you "
            "report on or finish work against the pathway."
        )
    )
    async def get_pathway(ctx: Context, learning_pathway_id: str) -> dict[str, Any]:
        client: ArturoClient = client_for(ctx)
        body = await client.request(
            "POST",
            "/content-transformation/get-pathway/",
            json={"learning_pathway_id": learning_pathway_id},
        )
        pathway = unwrap(body, "data")
        if isinstance(pathway, dict):
            pathway["url"] = settings.pathway_url(learning_pathway_id)
        return pathway
