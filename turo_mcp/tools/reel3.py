"""Reel 3.0 generation.

Strict prerequisite chain: concept -> knowledge base -> script -> reel. Each tool
checks the step before it and names the missing one, because the raw API returns
a bare 400 that the model cannot act on.

`reel_id` is the concept's `learning_pitstop_id`; there is no separate reel id.
"""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import Context, MCPServer

from ..client import ArturoClient, unwrap
from ..config import Settings
from ..errors import InvalidArgument, MissingPrerequisite
from ..reel3_defaults import (
    ASPECT_RATIO_TO_CANVAS,
    build_generate_payload,
    flatten_script,
    normalise_brand_colors,
)

# Values the web app offers; the downstream service rejects anything else.
SCRIPT_STYLES = (
    "teaching_a_friend",
    "funny_speech",
    "ted_talk",
    "university_lecture_by_professor",
)


def register(server: MCPServer, settings: Settings, client_for) -> None:
    @server.tool(
        description=(
            "Read a concept's knowledge base. Use it to confirm a KB exists before "
            "generating a script, and to ground the script review. An empty result means "
            "the concept has no KB — see set_concept_kb."
        )
    )
    async def get_concept_kb(ctx: Context, learning_pitstop_id: str) -> dict[str, Any]:
        client: ArturoClient = client_for(ctx)
        body = await client.request(
            "POST",
            "/content-transformation/get-content-kb/",
            json={"learning_pitstop_id": learning_pitstop_id},
        )
        return unwrap(body, "data")

    @server.tool(
        description=(
            "Write the knowledge base for a concept. Turo normally derives a KB from the "
            "uploaded source document, so a hand-created concept has none and script "
            "generation fails against it — this fills that gap. Pass the full KB text; it "
            "replaces whatever is there. After this, generate_reel_script and "
            "generate_simulation_script work normally. This does not touch the source "
            "document, and it will not reproduce the depth of a document-derived KB, so "
            "write enough substance for a script to be built from."
        )
    )
    async def set_concept_kb(
        ctx: Context, learning_pitstop_id: str, kb_content: str
    ) -> dict[str, Any]:
        if not kb_content.strip():
            raise InvalidArgument(
                "kb_content is empty. Pass the knowledge base text to store against "
                "this concept."
            )
        client: ArturoClient = client_for(ctx)
        result = await client.request(
            "POST",
            "/kb/text/update/",
            json={
                "learning_pitstop_id": learning_pitstop_id,
                "text_content": kb_content,
            },
        )
        return {
            **(result or {}),
            "next": "Knowledge base saved. generate_reel_script / generate_simulation_script can now run.",
        }

    @server.tool(
        description=(
            "Start Reel 3.0 script generation for a concept. Requires the concept to "
            "already have a knowledge base — call get_concept_kb, or set_concept_kb if it "
            "is empty. audience is the culture context (en-us, ind, ar, id) and is "
            "persisted onto the concept. script_style sets the tone: teaching_a_friend "
            "(default), funny_speech, ted_talk, university_lecture_by_professor. Poll "
            "get_reel_script afterwards; the script must be reviewed before generate_reel3."
        )
    )
    async def generate_reel_script(
        ctx: Context,
        learning_pitstop_id: str,
        audience: str = "",
        script_style: str = "teaching_a_friend",
        script_type: str = "reel",
    ) -> dict[str, Any]:
        # The downstream script service rejects empty style/type, so these must
        # carry the same defaults the web app sends rather than blank strings.
        style = script_style.strip() or "teaching_a_friend"
        if style not in SCRIPT_STYLES:
            raise InvalidArgument(f"script_style must be one of: {', '.join(SCRIPT_STYLES)}")

        client: ArturoClient = client_for(ctx)
        return await client.request(
            "POST",
            "/kb/v2/script/",
            timeout=settings.long_timeout_seconds,
            json={
                "learning_pitstop_id": learning_pitstop_id,
                "version": 3,
                "audience": audience or settings.culture_context,
                "script_style": style,
                "script_type": script_type.strip() or "reel",
                "fidelity_mode": "enhanced_engagement",
            },
        )

    @server.tool(
        description=(
            "Get Reel 3.0 script generation status and, once complete, the script itself. "
            "Returns status: not_generated | in_progress | completed | failed. Poll every "
            "10-15 seconds rather than blocking. Review the returned script with the user "
            "before calling generate_reel3."
        )
    )
    async def get_reel_script(ctx: Context, learning_pitstop_id: str) -> dict[str, Any]:
        client: ArturoClient = client_for(ctx)
        status_body = await client.request(
            "POST", "/kb/script/status/", json={"learning_pitstop_id": learning_pitstop_id}
        )
        status = (status_body or {}).get("status", "unknown")

        if status != "completed":
            return {
                "status": status,
                "script": None,
                "hint": {
                    "not_generated": "Call generate_reel_script first.",
                    "in_progress": "Still generating — poll again in 10-15 seconds.",
                    "failed": "Script generation failed. Check the concept has a usable knowledge base, then retry generate_reel_script.",
                }.get(status, "Unexpected status; retry or inspect the concept."),
            }

        script = await client.request(
            "POST", "/kb/script/content/", json={"learning_pitstop_id": learning_pitstop_id}
        )
        return {
            "status": "completed",
            "script_text": flatten_script(script),
            "script": script,
            "next": (
                "Pass script_text to generate_reel3 — not the script JSON. Author reads "
                "the script as narration, so JSON brackets are parsed as audio tags and "
                "rejected."
            ),
        }

    @server.tool(
        description=(
            "List the voices available for Reel 3.0, with preview URLs. The id returned "
            "here is the only acceptable voice_id for generate_reel3 — legacy aliases like "
            "'en-us-m-adam' are rejected by the Author service."
        )
    )
    async def list_reel_voices(ctx: Context, search: str = "") -> dict[str, Any]:
        client: ArturoClient = client_for(ctx)
        params = {"search": search} if search else None
        return await client.request("GET", "/reels/voices", params=params)

    @server.tool(
        description=(
            "Queue Reel 3.0 generation for a concept. Supply the reviewed script and a "
            "voice_id from list_reel_voices; art style, canvas, language and model are "
            "filled from server configuration. Set brand_primary / brand_secondary / "
            "brand_on_primary (hex, e.g. #E82028) to brand the reel for the tenant — "
            "Arturo does NOT read brand colours from the tenant record, so without them "
            "the reel uses the server's configured defaults, which may be the wrong "
            "company's colours. brand_on_primary is what sits ON the primary colour "
            "(usually #FFFFFF or #000000), not a third brand colour. Takes 5-15 minutes "
            "— poll get_reel3_status, do not block. Re-running regenerates from scratch "
            "and overwrites the previous reel, so check get_reel3_status before retrying."
        )
    )
    async def generate_reel3(
        ctx: Context,
        learning_pitstop_id: str,
        reel_title: str,
        script: str,
        voice_id: str,
        culture_context: str = "",
        art_style: str = "",
        aspect_ratio: str = "16:9",
        video_frequency: str = "medium",
        creative_guidance: str = "",
        enable_voice_response: bool = False,
        enable_hand_raise: bool = False,
        brand_primary: str = "",
        brand_secondary: str = "",
        brand_on_primary: str = "",
    ) -> dict[str, Any]:
        if not script.strip():
            raise MissingPrerequisite(
                "script is empty. Call get_reel_script and pass its script_text."
            )
        # Author parses the script as narration and reads "[" as an ElevenLabs
        # audio tag, so raw script JSON fails with "nested '['". Flatten it here
        # rather than making that the caller's problem.
        script = flatten_script(script) if script.lstrip()[:1] in "[{" else script
        if not voice_id.strip():
            raise MissingPrerequisite(
                "voice_id is required. Call list_reel_voices and pass an id from that list."
            )
        if aspect_ratio not in ASPECT_RATIO_TO_CANVAS:
            raise InvalidArgument(
                f"aspect_ratio must be one of: {', '.join(sorted(ASPECT_RATIO_TO_CANVAS))}"
            )

        payload = build_generate_payload(
            settings=settings,
            reel_title=reel_title,
            script=script,
            voice_id=voice_id,
            culture_context=culture_context or None,
            art_style=art_style or None,
            aspect_ratio=aspect_ratio,
            video_frequency=video_frequency or None,
            creative_guidance=creative_guidance or None,
            enable_voice_response=enable_voice_response,
            enable_hand_raise=enable_hand_raise,
            brand_colors=normalise_brand_colors(
                settings, brand_primary, brand_secondary, brand_on_primary
            ),
        )

        client: ArturoClient = client_for(ctx)
        result = await client.request(
            "POST", f"/reels/{learning_pitstop_id}/generate", json=payload
        )
        return {
            "queued": True,
            "reel_id": learning_pitstop_id,
            "response": result,
            "next": "Poll get_reel3_status every 10-15 seconds. Typical run: 5-15 minutes.",
        }

    @server.tool(
        description=(
            "Get Reel 3.0 generation status for a concept. While running this returns "
            "progress; when finished it returns the completed reel JSON. A 502/504 here "
            "means the Author service was unreachable, not that generation failed — retry "
            "before concluding anything."
        )
    )
    async def get_reel3_status(ctx: Context, learning_pitstop_id: str) -> dict[str, Any]:
        client: ArturoClient = client_for(ctx)
        return await client.request("GET", f"/reels/{learning_pitstop_id}/status")
