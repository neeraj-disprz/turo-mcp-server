"""Single-Turn Simulation (STS) generation.

Two steps: produce a script (scene, two characters, a four-turn dialogue, a hint
question), then render it into the playable activity with audio, images,
guidelines and a scoring rubric.

Unlike Reel 3.0 this works standalone — no pathway or concept required.

Arturo only ever creates the ActiveLearningActivity row inside the script step.
The render endpoint looks that row up and guards every database write behind
`if activity:`, so when it is missing the render still runs and still writes the
blob, but nothing — not the status, not the language mapping, not completion —
is ever recorded. The activity then reads as "Activity not found" forever. So a
caller-supplied script has to register the concept first; see `_ensure_registered`.
"""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import Context, MCPServer

from ..blob import resolve_blob_location
from ..client import ArturoClient
from ..config import Settings
from ..kb import push_concept_kb
from ..errors import InvalidArgument, MissingPrerequisite, RequestRejected

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


async def _ensure_registered(
    client: ArturoClient, unique_id: str, settings: Settings
) -> bool:
    """Make sure Arturo has an activity row for `unique_id`. Returns True if it
    had to create one.

    The render endpoint silently no-ops its bookkeeping when the row is absent,
    so without this a caller-supplied script produces an activity that generates
    fine and can never be read back. The script endpoint is the only thing that
    creates the row, and it creates it only after its own generation call
    succeeds — there is no cheaper way in, so registering costs one wasted script
    generation. It is skipped whenever the row already exists, which makes
    re-runs and anything that went through generate_simulation_script free.
    """
    try:
        await client.request(
            "POST",
            "/active-learning/get-assessment-status/",
            json={"blob_location": unique_id},
        )
    except RequestRejected as exc:
        # Only a genuine "not found" means unregistered; anything else is a real
        # error and should surface rather than trigger a pointless generation.
        if "not found" not in str(exc).lower():
            raise
    else:
        return False

    try:
        await client.request(
            "POST",
            "/active-learning/generate-script/",
            json={"learning_pitstop_id": unique_id},
            timeout=settings.long_timeout_seconds,
        )
    except RequestRejected as exc:
        # Registration goes through the script endpoint, which forwards the
        # concept's KB to the content service — and that service rejects an empty
        # one with a 422 naming the field. So an unregistered concept with no KB
        # cannot be rendered at all, and the raw upstream error says nothing the
        # model can act on.
        if "knowledge_base" in str(exc):
            raise MissingPrerequisite(
                f"Concept {unique_id} has no knowledge base, and Arturo cannot register "
                "an activity without one. Pass kb_content to this call — it stores the KB "
                "and registers in one shot. (The render itself does not use the KB; "
                "registration does.)"
            ) from exc
        raise MissingPrerequisite(
            f"Arturo has no activity registered for {unique_id} and registering one "
            f"failed: {exc}. For a concept in a pathway, unique_id must be its "
            "learning_pitstop_id. For a standalone simulation, call "
            "generate_simulation_script first and use the unique_id it returns."
        ) from exc
    return True


def register(server: MCPServer, settings: Settings, client_for) -> None:
    @server.tool(
        description=(
            "Generate a single-turn simulation script: scene, two characters, a four-turn "
            "dialogue and a hint question. Pass learning_pitstop_id to build it from an "
            "existing concept and its knowledge base, or module_name + module_info to "
            "create a standalone simulation with no pathway. Review the scene and dialogue "
            "with the user before calling generate_simulation. REQUIRED for a standalone "
            "simulation, where it mints the unique_id. Optional when you already have your "
            "own script for a pathway concept — generate_simulation takes a script directly "
            "and unique_id is just the learning_pitstop_id."
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
            "first if you already have the script; this registers the activity with Arturo "
            "itself when needed, which is reported as activity_registered. Pass your own "
            "script object with the "
            "same shape (scene, dialogue, and gender_a/gender_b so voices can be picked). "
            "Pass kb_content with the source material the script came from. It is "
            "optional ONLY for a concept that already has a knowledge base: the render "
            "ignores the KB, but registering the activity does not, so a concept with no "
            "KB at all cannot be rendered. It also means a concept left without one "
            "cannot have a script regenerated later, and the UI will offer to generate "
            "one, which overwrites authored content. Voices are chosen automatically from the characters' "
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

        # Register before rendering. Arturo's render endpoint writes no status,
        # no language mapping and no completion when the activity row is missing
        # — it just starts the job and returns 202 — so an unregistered concept
        # yields content that can never be polled or opened. Done after the KB
        # push so registration picks up the KB we just stored.
        registered = await _ensure_registered(client, unique_id, settings)

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
            # True only when THIS call wrote a KB; a concept can already have a
            # good one from set_concept_kb, in which case False is expected.
            "kb_saved": kb_saved,
            "activity_registered": registered,
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
                "hint": {
                    "in_progress": "Still generating — poll again in 10-15 seconds.",
                    "failed": (
                        "Generation failed. Re-run generate_simulation with the same script."
                    ),
                    # Registered but never rendered, or rendered while unregistered
                    # so the result was never recorded. Either way the fix is to
                    # render again; generate_simulation registers as it goes.
                    "not_generated": (
                        "Registered but not rendered. Call generate_simulation with the "
                        "script to produce the activity."
                    ),
                }.get(status, "Unexpected status; verify the blob_location."),
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
