"""Editing a generated Single-Turn Simulation.

Three separate surfaces, because the generated activity is three kinds of asset:

- the activity JSON and its guidelines/rubric, saved together as one document
- per-scene images, regenerated from a new description
- per-scene audio, re-recorded from new text with a chosen voice

Re-reading with `get_simulation` before every edit is not optional: each save
replaces the whole document, so editing from a stale copy silently drops
whatever changed in between.
"""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import Context, MCPServer

from ..blob import resolve_blob_location
from ..client import ArturoClient
from ..config import Settings
from ..errors import InvalidArgument

# Same aliases the generation step accepts.
VOICES = ("adam", "alice", "charlie", "arnold", "rachel")


def register(server: MCPServer, settings: Settings, client_for) -> None:
    @server.tool(
        description=(
            "Save edits to a generated simulation: dialogue, scene text, character "
            "settings, guidelines and the scoring rubric. REPLACES the whole activity — "
            "call get_simulation first and send back the full, modified activity_json and "
            "guidelines, never a partial object or the guidelines will be lost. You can pass "
            "the activity exactly as get_simulation returned it. Changing "
            "narration text here does NOT re-record the audio; use replace_simulation_audio "
            "for that."
        )
    )
    async def save_simulation(
        ctx: Context,
        unique_id: str,
        activity_json: dict[str, Any],
        guidelines: dict[str, Any],
        lang: str = "",
        cult_context: str = "",
    ) -> dict[str, Any]:
        # get_simulation returns {"active_learning": {"activelearning": ...}};
        # the save endpoint wants the inner object. Unwrap rather than making
        # the caller re-shape what they just read.
        if "active_learning" in activity_json and "activelearning" not in activity_json:
            inner = activity_json.get("active_learning")
            if isinstance(inner, dict) and "activelearning" in inner:
                activity_json = inner

        if not activity_json:
            raise InvalidArgument(
                "activity_json is empty. Call get_simulation and pass back the full "
                "activity object with your edits applied."
            )
        if not guidelines:
            raise InvalidArgument(
                "guidelines is empty. The save replaces both documents, so sending empty "
                "guidelines would erase the scoring rubric. Pass the guidelines from "
                "get_simulation, edited or unchanged."
            )
        if "activelearning" not in activity_json:
            raise InvalidArgument(
                "activity_json must be the full object with its top-level 'activelearning' "
                "key, exactly as get_simulation returned it."
            )

        client: ArturoClient = client_for(ctx)
        blob_location, locale = await resolve_blob_location(
            client, unique_id, lang=lang, cult_context=cult_context
        )
        result = await client.request(
            "POST",
            "/active-learning/save-assessment-json/",
            json={
                "unique_id": blob_location,
                "activity_json": activity_json,
                "guidelines": guidelines,
            },
        )
        return {
            **(result or {}),
            "saved_to": blob_location,
            "locale": {
                "lang": locale.get("lang"),
                "culture_context": locale.get("culture_context"),
            },
        }

    @server.tool(
        description=(
            "Regenerate one scene image from a new description. image_name is the scene's "
            "image file name from get_simulation. Synchronous — the replacement is in "
            "place when this returns. The old image is overwritten and cannot be recovered."
        )
    )
    async def regenerate_simulation_image(
        ctx: Context, unique_id: str, image_name: str, description: str
    ) -> dict[str, Any]:
        if not description.strip():
            raise InvalidArgument(
                "description is empty. Describe the image you want in its place."
            )
        client: ArturoClient = client_for(ctx)
        unique_id, _ = await resolve_blob_location(client, unique_id)
        return await client.request(
            "POST",
            "/active-learning/regenerate-image/",
            timeout=settings.long_timeout_seconds,
            json={
                "unique_id": unique_id,
                "image_name": image_name,
                "rephrased_description": description.strip(),
            },
        )

    @server.tool(
        description=(
            "Re-record one scene's audio from new text. file_name is the scene's audio_name "
            "from get_simulation, and voice must be one of: adam, alice, charlie, arnold, "
            "rachel — keep it consistent with the speaker that scene already uses. This "
            "replaces only the audio; call save_simulation too if the written narration "
            "should change to match."
        )
    )
    async def replace_simulation_audio(
        ctx: Context,
        unique_id: str,
        file_name: str,
        text: str,
        voice: str,
        lang: str = "en-us",
    ) -> dict[str, Any]:
        if not text.strip():
            raise InvalidArgument("text is empty. Pass the line this scene should speak.")
        if voice not in VOICES:
            raise InvalidArgument(f"voice must be one of: {', '.join(VOICES)}")

        client: ArturoClient = client_for(ctx)
        unique_id, _ = await resolve_blob_location(client, unique_id)
        return await client.request(
            "POST",
            "/active-learning/edit-activityscene-audio/",
            timeout=settings.long_timeout_seconds,
            json={
                "unique_id": unique_id,
                "file_name": file_name,
                "rephrased_script": text.strip(),
                "voice_model": voice,
                "lang": lang,
            },
        )

    @server.tool(
        description=(
            "Get the playable URL for one scene's audio, to check a re-recording. "
            "audio_name is the scene's audio file name from get_simulation."
        )
    )
    async def get_simulation_audio(
        ctx: Context, unique_id: str, audio_name: str
    ) -> dict[str, Any]:
        client: ArturoClient = client_for(ctx)
        unique_id, _ = await resolve_blob_location(client, unique_id)
        return await client.request(
            "POST",
            "/active-learning/get-scene-audio/",
            json={"unique_id": unique_id, "audio_name": audio_name},
        )
