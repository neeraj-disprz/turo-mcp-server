"""Single-Turn Simulation (STS) generation.

Two steps: produce a script (scene, two characters, a four-turn dialogue, a hint
question), then render it into the playable activity with audio, images,
guidelines and a scoring rubric.

Unlike Reel 3.0 this works standalone — no pathway or concept required.
"""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import Context, MCPServer

from ..blob import resolve_blob_location
from ..client import ArturoClient
from ..config import Settings
from ..kb import push_concept_kb
from ..errors import InvalidArgument, MissingPrerequisite

# Narrator voice must differ from both character voices.
_VOICES = ("adam", "alice", "charlie", "arnold", "rachel")


def _pick_voices(gender_a: str, gender_b: str) -> tuple[str, str, str]:
    voice_a = "adam" if gender_a.strip().lower() == "male" else "alice"
    voice_b = "adam" if gender_b.strip().lower() == "male" else "alice"
    if voice_a == voice_b:
        # Two same-gender characters still need distinguishable voices.
        voice_b = "arnold" if voice_b == "adam" else "rachel"
    narrator = next(v for v in ("charlie", "arnold", "rachel") if v not in {voice_a, voice_b})
    return voice_a, voice_b, narrator


def register(server: MCPServer, settings: Settings, client_for) -> None:
    @server.tool(
        description=(
            "Generate a single-turn simulation script: scene, two characters, a four-turn "
            "dialogue and a hint question. Pass learning_pitstop_id to build it from an "
            "existing concept and its knowledge base, or module_name + module_info to "
            "create a standalone simulation with no pathway. Review the scene and dialogue "
            "with the user before calling generate_simulation. SKIP this when you already "
            "have the script and the concept is in a pathway — generate_simulation accepts "
            "a script directly and unique_id is just the learning_pitstop_id, so calling "
            "this only to replace its output wastes a generation and grounds the draft in "
            "whatever knowledge base happens to be stored. It is only REQUIRED for a "
            "standalone simulation, where it mints the unique_id."
        )
    )
    async def generate_simulation_script(
        ctx: Context,
        learning_pitstop_id: str = "",
        module_name: str = "",
        module_info: str = "",
    ) -> dict[str, Any]:
        if not learning_pitstop_id and not (module_name and module_info):
            raise InvalidArgument(
                "Pass either learning_pitstop_id, or both module_name and module_info "
                "for a standalone simulation."
            )

        payload: dict[str, Any] = {}
        if learning_pitstop_id:
            payload["learning_pitstop_id"] = learning_pitstop_id
        else:
            payload["module_name"] = module_name
            payload["module_info"] = module_info

        client: ArturoClient = client_for(ctx)
        return await client.request(
            "POST",
            "/active-learning/generate-script/",
            json=payload,
            timeout=settings.long_timeout_seconds,
        )

    @server.tool(
        description=(
            "Render a simulation script into the playable activity (audio, images, "
            "guidelines, scoring rubric). For a concept in a pathway, unique_id is simply "
            "its learning_pitstop_id — you do NOT have to call generate_simulation_script "
            "first if you already have the script. Pass your own script object with the "
            "same shape (scene, dialogue, and gender_a/gender_b so voices can be picked). "
            "Pass kb_content with the source material the script came from: the render "
            "ignores it, but a concept with no knowledge base cannot have a script "
            "regenerated later and the UI will offer to generate one, which overwrites "
            "authored content. Voices are chosen automatically from the characters' "
            "genders unless you override them. Returns blob_location — use that to poll "
            "get_simulation. Takes 2-5 minutes."
        )
    )
    async def generate_simulation(
        ctx: Context,
        unique_id: str,
        script: dict[str, Any],
        module_name: str,
        module_info: str,
        kb_content: str = "",
        lang: str = "en-us",
        cult_context: str = "en-us",
        voice_a: str = "",
        voice_b: str = "",
        voice_narrator: str = "",
    ) -> dict[str, Any]:
        for key in ("scene", "dialogue"):
            if not script.get(key):
                raise MissingPrerequisite(
                    f"script is missing {key!r}. Pass the full script object returned by "
                    "generate_simulation_script."
                )

        auto_a, auto_b, auto_narrator = _pick_voices(
            str(script.get("gender_a", "")), str(script.get("gender_b", ""))
        )
        chosen = {
            "vol1": voice_a or auto_a,
            "vol2": voice_b or auto_b,
            "vol3": voice_narrator or auto_narrator,
        }
        for field, value in chosen.items():
            if value not in _VOICES:
                raise InvalidArgument(f"{field} must be one of: {', '.join(_VOICES)}")

        client: ArturoClient = client_for(ctx)

        # Store the source material on the concept before rendering. The render
        # itself never reads the KB — it works from blob_id and the script you
        # pass — but a concept left without one cannot have a script regenerated
        # later, and the UI will offer to "generate" a KB that then overwrites
        # whatever the author wrote. Pushing first also fails cheaply.
        kb_saved = False
        if kb_content.strip():
            await push_concept_kb(client, unique_id, kb_content)
            kb_saved = True

        result = await client.request(
            "POST",
            "/active-learning/assessment-generation/",
            json={
                "blob_id": unique_id,
                "module_name": module_name,
                "module_info": module_info,
                "lang": lang,
                "cult_context": cult_context,
                "script": script,
                **chosen,
            },
        )
        return {
            "queued": True,
            "blob_location": (result or {}).get("blob_location", unique_id),
            "kb_saved": kb_saved,
            "voices": chosen,
            "response": result,
            "next": "Poll get_simulation every 10-15 seconds. Typical run: 2-5 minutes.",
        }

    @server.tool(
        description=(
            "Get simulation generation status and, once complete, the full activity: "
            "scenes, image descriptions, guidelines, scoring rubric and asset URLs. "
            "Returns status in_progress | completed | failed, plus the blob_location the "
            "content actually lives at — pass that to save_simulation and the edit tools, "
            "not the concept id. pathway_id is required only for simulations attached to "
            "a pathway concept."
        )
    )
    async def get_simulation(
        ctx: Context,
        blob_location: str,
        pathway_id: str = "",
        lang: str = "",
        cult_context: str = "",
    ) -> dict[str, Any]:
        client: ArturoClient = client_for(ctx)
        # A simulation generated for a non-default locale lives at
        # "<id>-<lang>-<cult>", not at the concept id. Reads 404 and writes
        # silently no-op against the wrong one, so resolve it up front.
        blob_location, locale = await resolve_blob_location(
            client, blob_location, lang=lang, cult_context=cult_context
        )
        status_body = await client.request(
            "POST",
            "/active-learning/get-assessment-status/",
            json={"blob_location": blob_location},
        )
        status = (status_body or {}).get("status", "unknown")

        if status != "completed":
            return {
                "status": status,
                "blob_location": blob_location,
                "activity": None,
                "hint": (
                    "Still generating — poll again in 10-15 seconds."
                    if status == "in_progress"
                    else "Generation failed. Re-run generate_simulation with the same script."
                    if status == "failed"
                    else "Unexpected status; verify the blob_location."
                ),
            }

        payload: dict[str, Any] = {"activity_id": blob_location}
        if pathway_id:
            payload["pathway_id"] = pathway_id
        activity = await client.request(
            "POST", "/active-learning/get-active-learning-json/", json=payload
        )
        return {
            "status": "completed",
            # Pass this back to save_simulation and the edit tools; it is the
            # only id those writes land on.
            "blob_location": blob_location,
            "locale": {
                "lang": locale.get("lang"),
                "culture_context": locale.get("culture_context"),
            },
            "activity": activity,
        }
