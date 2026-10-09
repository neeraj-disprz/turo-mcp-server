"""Pathway structure editing: chapters (anchors) and concepts (pitstops).

Destructive behaviour on these endpoints is not obvious from their names, so the
server withholds the dangerous options and gates deletions behind an explicit
confirm flag. `generate_pitstops` on anchor/update is never sent at all: it wipes
every concept in the chapter *and* its generated reels.
"""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import Context, MCPServer

from ..client import ArturoClient
from ..config import Settings
from ..errors import InvalidArgument

# Only the types this server can actually generate content for. Turo supports
# Reel 1.0/2.0, quizzes and Talking Head too, but there is no generation tool
# here for them — offering them would let the agent create a concept it then
# cannot fill. Change a type in the Turo UI if one of those is ever needed.
CONTENT_TYPES = (
    "Reel 3.0",
    "Simulation",
)

_CONFIRM_HINT = (
    "This permanently deletes generated content (reels, quizzes, simulations) that "
    "cannot be recovered. Re-run with confirm=true once the user has agreed."
)


async def _read_pathway(client, learning_pathway_id: str) -> dict[str, Any]:
    from ..client import unwrap

    body = await client.request(
        "POST",
        "/content-transformation/get-pathway/",
        json={"learning_pathway_id": learning_pathway_id},
    )
    return unwrap(body, "data") or {}


async def _move_to_end(client, *, learning_pathway_id: str, anchor_id: str) -> str | None:
    """Put a newly created chapter last.

    `display_order` defaults to 0 on the model and anchor/create does not accept
    it, so every new chapter lands at the top. The only fix available through the
    API is to create first and reorder after.

    Returns a warning string if the move failed; None on success.
    """
    try:
        pathway = await _read_pathway(client, learning_pathway_id)
        chapters = (pathway.get("learning_pathway") or {}).get("chapters") or []
        ordered = [c["anchor_id"] for c in chapters if c.get("anchor_id") != anchor_id]
        ordered.append(anchor_id)
        await client.request(
            "PUT",
            "/anchor/reorder/",
            json={"learning_pathway_id": learning_pathway_id, "anchor_ids": ordered},
        )
        return None
    except Exception as exc:  # noqa: BLE001 - the chapter exists either way
        return (
            f"Chapter was created but could not be moved to the end ({exc}). "
            "It is currently first; use reorder_pathway to position it."
        )


async def _move_concept_to_end(client, *, anchor_id: str, pitstop_id: str) -> str | None:
    """Put a newly created concept last within its chapter. See _move_to_end."""
    try:
        anchor = await client.request("GET", "/anchor/get/", params={"anchor_id": anchor_id})
        pathway = await _read_pathway(client, str(anchor["learning_pathway_id"]))
        chapters = (pathway.get("learning_pathway") or {}).get("chapters") or []
        chapter = next((c for c in chapters if c.get("anchor_id") == anchor_id), None)
        if chapter is None:
            return "Concept was created but its chapter could not be re-read to reorder it."

        ordered = [
            c["learning_pitstop_id"]
            for c in (chapter.get("concepts") or [])
            if c.get("learning_pitstop_id") != pitstop_id
        ]
        ordered.append(pitstop_id)
        await client.request(
            "PUT",
            "/pitstop/reorder/",
            json={"anchor_id": anchor_id, "pitstop_ids": ordered},
        )
        return None
    except Exception as exc:  # noqa: BLE001 - the concept exists either way
        return (
            f"Concept was created but could not be moved to the end ({exc}). "
            "It is currently first; use reorder_pathway to position it."
        )


def register(server: MCPServer, settings: Settings, client_for) -> None:
    @server.tool(
        description=(
            "Add a chapter to a pathway. A chapter groups related concepts; it holds no "
            "content itself. New chapters are placed last by default — pass position='start' "
            "to leave it first instead."
        )
    )
    async def create_chapter(
        ctx: Context,
        learning_pathway_id: str,
        title: str,
        description: str,
        level: str = "Beginner",
        position: str = "end",
    ) -> dict[str, Any]:
        if position not in {"end", "start"}:
            raise InvalidArgument("position must be 'end' or 'start'")

        client: ArturoClient = client_for(ctx)
        created = await client.request(
            "POST",
            "/anchor/create/",
            json={
                "learning_pathway_id": learning_pathway_id,
                "anchor": title,
                "description": description,
                "level": level,
            },
        )

        anchor_id = str((created or {}).get("anchor_id") or "")
        if position == "end" and anchor_id:
            warning = await _move_to_end(
                client, learning_pathway_id=learning_pathway_id, anchor_id=anchor_id
            )
            if warning:
                return {**created, "position": "start", "warning": warning}
            return {**created, "position": "end"}
        return {**created, "position": "start"}

    @server.tool(
        description=(
            "Rename a chapter or change its description/level. Only the fields you pass "
            "are changed. This never regenerates or deletes the chapter's concepts."
        )
    )
    async def update_chapter(
        ctx: Context,
        anchor_id: str,
        title: str | None = None,
        description: str | None = None,
        level: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"anchor_id": anchor_id}
        if title is not None:
            payload["anchor"] = title
        if description is not None:
            payload["description"] = description
        if level is not None:
            payload["level"] = level
        if len(payload) == 1:
            raise InvalidArgument("Pass at least one of: title, description, level")

        client: ArturoClient = client_for(ctx)
        return await client.request("PUT", "/anchor/update/", json=payload)

    @server.tool(
        description=(
            "Delete chapters. DESTRUCTIVE: this cascades to every concept inside them and "
            "to any reels, quizzes or simulations already generated for those concepts. "
            "Requires confirm=true."
        )
    )
    async def delete_chapters(
        ctx: Context, anchor_ids: list[str], confirm: bool = False
    ) -> dict[str, Any]:
        if not anchor_ids:
            raise InvalidArgument("anchor_ids must not be empty")
        if not confirm:
            raise InvalidArgument(
                f"Refusing to delete {len(anchor_ids)} chapter(s) without confirmation. {_CONFIRM_HINT}"
            )
        client: ArturoClient = client_for(ctx)
        return await client.request("POST", "/anchor/delete/", json={"anchor_ids": anchor_ids})

    @server.tool(
        description=(
            "Add a concept (a single learning activity) to a chapter. content_type decides "
            "what can later be generated for it: 'Reel 3.0' is the advanced explainer video "
            "(also called Advanced Explainer 2.0) and 'Simulation' is a single-turn roleplay. "
            "These are the only two this server can generate. New concepts are placed last "
            "in the chapter by default. A hand-created concept starts with an EMPTY knowledge "
            "base (KBs are normally derived from an uploaded document), and script generation "
            "needs one — call set_concept_kb before generate_reel_script or "
            "generate_simulation_script."
        )
    )
    async def create_concept(
        ctx: Context,
        anchor_id: str,
        title: str,
        description: str,
        content_type: str = "Reel 3.0",
        duration_minutes: int = 5,
        position: str = "end",
    ) -> dict[str, Any]:
        if content_type not in CONTENT_TYPES:
            raise InvalidArgument(f"content_type must be one of: {', '.join(CONTENT_TYPES)}")
        if position not in {"end", "start"}:
            raise InvalidArgument("position must be 'end' or 'start'")

        client: ArturoClient = client_for(ctx)
        created = await client.request(
            "POST",
            "/pitstop/create/",
            json={
                "anchor_id": anchor_id,
                "pitstop": title,
                "class_activity": description,
                "description": description,
                "type": content_type,
                "duration": duration_minutes,
            },
        )

        pitstop_id = str((created or {}).get("pitstop_id") or "")
        result = {
            **created,
            "next": "This concept has no knowledge base yet. Call set_concept_kb before generating a script.",
        }
        if position == "end" and pitstop_id:
            warning = await _move_concept_to_end(
                client, anchor_id=anchor_id, pitstop_id=pitstop_id
            )
            if warning:
                return {**result, "position": "start", "warning": warning}
            return {**result, "position": "end"}
        return {**result, "position": "start"}

    @server.tool(
        description=(
            "Update a concept's title, description, duration or content type. "
            "SIDE EFFECT: any edit clears the concept's 'reel generated' flag, so an "
            "already-generated reel stops being treated as current. Setting "
            "reset_contents=true additionally deletes the generated reel outright and "
            "requires confirm=true."
        )
    )
    async def update_concept(
        ctx: Context,
        learning_pitstop_id: str,
        title: str | None = None,
        description: str | None = None,
        content_type: str | None = None,
        duration_minutes: int | None = None,
        reset_contents: bool = False,
        confirm: bool = False,
    ) -> dict[str, Any]:
        if content_type is not None and content_type not in CONTENT_TYPES:
            raise InvalidArgument(f"content_type must be one of: {', '.join(CONTENT_TYPES)}")
        if reset_contents and not confirm:
            raise InvalidArgument(f"reset_contents=true deletes generated content. {_CONFIRM_HINT}")

        payload: dict[str, Any] = {"learning_pitstop_id": learning_pitstop_id}
        if title is not None:
            payload["pitstop"] = title
        if description is not None:
            payload["class_activity"] = description
            payload["description"] = description
        if content_type is not None:
            payload["type"] = content_type
        if duration_minutes is not None:
            payload["duration"] = duration_minutes
        if reset_contents:
            payload["reset_contents"] = True
        if len(payload) == 1:
            raise InvalidArgument(
                "Pass at least one of: title, description, content_type, duration_minutes"
            )

        client: ArturoClient = client_for(ctx)
        return await client.request("PUT", "/pitstop/update/", json=payload)

    @server.tool(
        description=(
            "Delete concepts. DESTRUCTIVE: removes any reel, quiz or simulation already "
            "generated for them. Requires confirm=true."
        )
    )
    async def delete_concepts(
        ctx: Context, learning_pitstop_ids: list[str], confirm: bool = False
    ) -> dict[str, Any]:
        if not learning_pitstop_ids:
            raise InvalidArgument("learning_pitstop_ids must not be empty")
        if not confirm:
            raise InvalidArgument(
                f"Refusing to delete {len(learning_pitstop_ids)} concept(s) without confirmation. {_CONFIRM_HINT}"
            )
        client: ArturoClient = client_for(ctx)
        return await client.request(
            "POST", "/pitstop/delete/", json={"pitstop_ids": learning_pitstop_ids}
        )

    @server.tool(
        description=(
            "Reorder a pathway. Pass learning_pathway_id + anchor_ids to reorder chapters, "
            "or anchor_id + learning_pitstop_ids to reorder concepts within one chapter. "
            "Each id list must be the complete desired order, not just the moved items."
        )
    )
    async def reorder_pathway(
        ctx: Context,
        learning_pathway_id: str | None = None,
        anchor_ids: list[str] | None = None,
        anchor_id: str | None = None,
        learning_pitstop_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        client: ArturoClient = client_for(ctx)

        # Both reorder endpoints are PUT; POST returns 405.
        if learning_pathway_id and anchor_ids:
            return await client.request(
                "PUT",
                "/anchor/reorder/",
                json={"learning_pathway_id": learning_pathway_id, "anchor_ids": anchor_ids},
            )
        if anchor_id and learning_pitstop_ids:
            return await client.request(
                "PUT",
                "/pitstop/reorder/",
                json={"anchor_id": anchor_id, "pitstop_ids": learning_pitstop_ids},
            )
        raise InvalidArgument(
            "Pass either (learning_pathway_id and anchor_ids) to reorder chapters, "
            "or (anchor_id and learning_pitstop_ids) to reorder concepts."
        )
