"""Editing a generated Reel 3.0.

Two surfaces, matching how Author splits the asset:

- the reel document (settings + scenes), replaced wholesale on every save
- per-scene narration, re-recorded asynchronously from new text and a voice

Editing text in the document does NOT re-record audio. The scenes keep whatever
narration was already rendered, so a save that changes wording leaves the video
saying the old words until `regenerate_scene_narration` runs for that scene.

The two write paths differ in an easy-to-miss way. `save_reel` is a plain PUT of
the document. `regenerate_scene_narration` is synchronous and *also* writes: it
re-times the scene and saves reel.json server-side, returning the updated reel.
So a document read before that call is stale, and saving it afterwards would
revert the new timings.

Re-reading with `get_reel3_status` before every save is not optional: the save
replaces the whole document, so editing a stale copy silently drops anything
that changed in between.
"""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import Context, MCPServer

from ..client import ArturoClient
from ..config import Settings
from ..errors import InvalidArgument, MissingPrerequisite


def _reel_document(reel: dict[str, Any]) -> dict[str, Any]:
    """The payload to PUT, given whatever shape the caller passed.

    Author accepts either `{"reel": {...}}` or a bare `{settings, scenes}` and
    normalises internally, so both are forwarded untouched. What is rejected is
    an object that is neither — usually the full status response, which wraps the
    reel alongside progress fields and would overwrite the reel with junk.
    """
    inner = reel.get("reel") if isinstance(reel.get("reel"), dict) else reel
    if not isinstance(inner, dict) or not inner:
        raise InvalidArgument(
            "reel is empty. Call get_reel3_status and pass back the full reel "
            "document with your edits applied."
        )
    if not any(key in inner for key in ("settings", "scenes")):
        raise InvalidArgument(
            "reel must be the reel document — the object holding 'settings' and "
            "'scenes', exactly as get_reel3_status returned it (either the whole "
            "{'reel': ...} envelope or the inner object). Got keys: "
            f"{', '.join(sorted(inner)[:8]) or 'none'}"
        )
    return reel


def register(server: MCPServer, settings: Settings, client_for) -> None:
    @server.tool(
        description=(
            "Save edits to a generated Reel 3.0: spoken text (`narration_text`), on-screen "
            "text, layout and reel settings. REPLACES the whole reel — call get_reel3_status "
            "first and send back the full, modified reel document, never a partial object. "
            "You can pass it exactly as get_reel3_status returned it. Editing "
            "`narration_text` here does NOT re-record the audio: the scene keeps its "
            "existing voiceover until you call regenerate_scene_narration for that scene."
        )
    )
    async def save_reel(
        ctx: Context, learning_pitstop_id: str, reel: dict[str, Any]
    ) -> dict[str, Any]:
        payload = _reel_document(reel)
        client: ArturoClient = client_for(ctx)
        result = await client.request(
            "PUT",
            f"/reels/{learning_pitstop_id}/reel",
            json=payload,
            timeout=settings.long_timeout_seconds,
        )
        return {
            "saved": True,
            "reel_id": learning_pitstop_id,
            "response": result,
            "note": (
                "Narration audio is unchanged. Call regenerate_scene_narration for any "
                "scene whose spoken text you edited."
            ),
        }

    @server.tool(
        description=(
            "Re-record one scene's narration from new text. scene_id is the scene's "
            "`scene_id` field from get_reel3_status (scenes have no `id`). SYNCHRONOUS — the "
            "new audio is in place when this returns, so do NOT poll afterwards. It can take "
            "a minute. Overwrites that scene's existing audio, which cannot be recovered, "
            "and costs generation credits. Arturo re-times the scene and saves the reel "
            "itself, so this also updates `narration_text` for you — any reel document you "
            "were holding is now stale; re-read with get_reel3_status before save_reel."
        )
    )
    async def regenerate_scene_narration(
        ctx: Context,
        learning_pitstop_id: str,
        scene_id: str,
        script: str,
        voice_id: str,
        model_id: str = "",
        language: str = "",
        culture_context: str = "",
    ) -> dict[str, Any]:
        if not script.strip():
            raise InvalidArgument(
                "script is empty. Pass the narration text this scene should say."
            )
        if not scene_id.strip():
            raise InvalidArgument("scene_id is required — take it from get_reel3_status.")
        if not voice_id.strip():
            raise MissingPrerequisite(
                "voice_id is required. Call list_reel_voices and pass an id from that "
                "list, or reuse the voice already on the reel so scenes stay consistent."
            )

        payload: dict[str, Any] = {
            "script": script,
            "voiceId": voice_id,
            # Mirror the generation defaults so a re-recorded scene matches the
            # voice model and accent of the ones around it.
            "modelId": model_id or settings.model_id,
            "cultureContext": culture_context or settings.culture_context,
        }
        if language.strip():
            payload["language"] = language.strip()

        client: ArturoClient = client_for(ctx)
        result = await client.request(
            "POST",
            f"/reels/{learning_pitstop_id}/scenes/{scene_id}/audio/mp3",
            json=payload,
            timeout=settings.long_timeout_seconds,
        )
        # Author answers with audio metadata and the saved reel side by side, the
        # reel nested one level further in. Lifting it out means `reel` here is
        # the same envelope get_reel3_status returns, so it can go straight back
        # into save_reel instead of the caller unwrapping two layers.
        body = result if isinstance(result, dict) else {}
        return {
            "recorded": True,
            "reel_id": learning_pitstop_id,
            "scene_id": scene_id,
            "audio": body.get("audio"),
            "scene_duration": body.get("duration"),
            "reel_total_duration": body.get("reel_total_duration"),
            "reel": body.get("reel"),
            "note": (
                "Audio is already in place — do not poll. `reel` is the current saved "
                "state in the same shape get_reel3_status returns; use it rather than any "
                "copy read before this call."
            ),
        }

    @server.tool(
        description=(
            "Status of a WHOLE-REEL narration job — the kind started from the Turo UI, not "
            "by regenerate_scene_narration (that one is synchronous and needs no polling). "
            "Reports 'audio generation was not found' when no reel-level job has been "
            "started, which is normal and not an error in itself. A 502/504 here means the "
            "Author service was unreachable, not that generation failed — retry before "
            "concluding anything."
        )
    )
    async def get_reel_audio_status(
        ctx: Context, learning_pitstop_id: str
    ) -> dict[str, Any]:
        client: ArturoClient = client_for(ctx)
        return await client.request("GET", f"/reels/{learning_pitstop_id}/audio/status")
