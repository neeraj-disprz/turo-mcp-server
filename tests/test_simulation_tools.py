"""Single-Turn Simulation: standalone vs pathway-attached, and voice assignment."""

from __future__ import annotations

import httpx
import json
import pytest
import respx

from turo_mcp.tools.simulation import _pick_voices

from .conftest import API, body_of, call, call_expecting_error

SCRIPT = {
    "scene": "A manager and an employee discuss a missed deadline.",
    "dialogue": {"a_1": "You're late.", "b_1": "I know.", "a_2": "Why?", "b_2": "Blocked."},
    "hint_question": "How would you respond?",
    "gender_a": "male",
    "gender_b": "female",
    "name_a": "Raj",
    "name_b": "Priya",
}

STATUS = f"{API}/active-learning/get-assessment-status/"
SCRIPT_STEP = f"{API}/active-learning/generate-script/"


def already_registered():
    """Arturo answers the status probe, so no registration is needed."""
    return respx.post(STATUS).mock(
        return_value=httpx.Response(200, json={"status": "completed"})
    )


def not_registered():
    """Arturo has no activity row for this id yet."""
    return respx.post(STATUS).mock(
        return_value=httpx.Response(404, json={"error": "Activity not found"})
    )


class TestVoiceAssignment:
    def test_voices_follow_character_gender(self):
        assert _pick_voices("male", "female")[:2] == ("adam", "alice")

    @pytest.mark.parametrize("gender", ["male", "female"])
    def test_same_gender_characters_still_get_distinct_voices(self, gender):
        voice_a, voice_b, _ = _pick_voices(gender, gender)
        assert voice_a != voice_b, "two characters sharing a voice is unlistenable"

    @pytest.mark.parametrize(
        ("gender_a", "gender_b"), [("male", "female"), ("male", "male"), ("female", "female")]
    )
    def test_narrator_is_never_one_of_the_character_voices(self, gender_a, gender_b):
        voice_a, voice_b, narrator = _pick_voices(gender_a, gender_b)
        assert narrator not in {voice_a, voice_b}


class TestScriptGeneration:
    @respx.mock
    async def test_standalone_simulation_needs_no_pathway(self, server):
        route = respx.post(f"{API}/active-learning/generate-script/").mock(
            return_value=httpx.Response(200, json={"unique_id": "u1", "data": SCRIPT})
        )
        result = await call(
            server,
            "generate_simulation_script",
            module_name="Giving feedback",
            module_info="Performance review setting",
        )

        body = json.loads(route.calls[0].request.read())
        assert body["module_name"] == "Giving feedback"
        assert "learning_pitstop_id" not in body
        assert result["unique_id"] == "u1"

    @respx.mock
    async def test_concept_backed_simulation_sends_only_the_concept_id(self, server):
        route = respx.post(f"{API}/active-learning/generate-script/").mock(
            return_value=httpx.Response(200, json={"unique_id": "c1"})
        )
        await call(server, "generate_simulation_script", learning_pitstop_id="c1")

        body = json.loads(route.calls[0].request.read())
        assert body == {"learning_pitstop_id": "c1"}

    async def test_neither_input_explains_both_options(self, server):
        message = await call_expecting_error(server, "generate_simulation_script")
        assert "learning_pitstop_id" in message and "module_name" in message

    async def test_a_partial_standalone_request_is_rejected(self, server):
        message = await call_expecting_error(
            server, "generate_simulation_script", module_name="Only the name"
        )
        assert "module_info" in message


class TestGeneration:
    @respx.mock
    async def test_generation_assigns_voices_and_returns_the_poll_handle(self, server):
        already_registered()
        route = respx.post(f"{API}/active-learning/assessment-generation/").mock(
            return_value=httpx.Response(202, json={"blob_location": "u1-en-us"})
        )
        result = await call(
            server,
            "generate_simulation",
            unique_id="u1",
            script=SCRIPT,
            module_name="Giving feedback",
            module_info="Performance review",
        )

        body = json.loads(route.calls[0].request.read())
        assert body["blob_id"] == "u1"
        assert (body["vol1"], body["vol2"]) == ("adam", "alice")
        assert body["vol3"] not in {"adam", "alice"}
        assert result["blob_location"] == "u1-en-us"
        assert "Poll get_simulation" in result["next"]

    @respx.mock
    async def test_explicit_voices_override_the_automatic_choice(self, server):
        already_registered()
        route = respx.post(f"{API}/active-learning/assessment-generation/").mock(
            return_value=httpx.Response(202, json={})
        )
        await call(
            server,
            "generate_simulation",
            unique_id="u1",
            script=SCRIPT,
            module_name="m",
            module_info="i",
            voice_a="rachel",
        )
        assert json.loads(route.calls[0].request.read())["vol1"] == "rachel"

    async def test_an_unknown_voice_lists_the_valid_ones(self, server):
        message = await call_expecting_error(
            server,
            "generate_simulation",
            unique_id="u1",
            script=SCRIPT,
            module_name="m",
            module_info="i",
            voice_a="morgan",
        )
        assert "adam" in message

    async def test_a_script_missing_its_dialogue_is_caught_before_the_call(self, server):
        message = await call_expecting_error(
            server,
            "generate_simulation",
            unique_id="u1",
            script={"scene": "only a scene"},
            module_name="m",
            module_info="i",
        )
        assert "dialogue" in message
        assert "generate_simulation_script" in message


class TestActivityRegistration:
    """Arturo creates the activity row only inside the script step, and its render
    endpoint guards every database write behind `if activity:`. Rendering an
    unregistered concept therefore succeeds, writes the blob, and leaves an
    activity that answers "Activity not found" to every later read. Observed
    live: a hand-written script rendered fine and was permanently unreachable,
    while the same concept via the script step worked.
    """

    @respx.mock
    async def test_an_unregistered_concept_is_registered_before_rendering(self, server):
        not_registered()
        step = respx.post(SCRIPT_STEP).mock(
            return_value=httpx.Response(200, json={"unique_id": "c1"})
        )
        gen = respx.post(f"{API}/active-learning/assessment-generation/").mock(
            return_value=httpx.Response(202, json={"blob_location": "c1"})
        )

        result = await call(
            server,
            "generate_simulation",
            unique_id="c1",
            script=SCRIPT,
            module_name="M",
            module_info="I",
        )

        assert json.loads(step.calls[0].request.read()) == {"learning_pitstop_id": "c1"}
        assert gen.called, "the render must still happen after registering"
        assert result["activity_registered"] is True

    @respx.mock
    async def test_an_already_registered_concept_skips_the_wasted_generation(self, server):
        already_registered()
        step = respx.post(SCRIPT_STEP)
        respx.post(f"{API}/active-learning/assessment-generation/").mock(
            return_value=httpx.Response(202, json={})
        )

        result = await call(
            server,
            "generate_simulation",
            unique_id="c1",
            script=SCRIPT,
            module_name="M",
            module_info="I",
        )

        assert not step.called, "re-running must not burn a script generation"
        assert result["activity_registered"] is False

    @respx.mock
    async def test_registration_happens_after_the_kb_push(self, server):
        # Registration copies the concept's kb_url onto the activity row, so the
        # KB has to be stored first or the row points at the old one.
        calls: list[str] = []
        respx.post(f"{API}/kb/text/update/").mock(
            side_effect=lambda request: calls.append("kb") or httpx.Response(200, json={})
        )
        not_registered()
        respx.post(SCRIPT_STEP).mock(
            side_effect=lambda request: calls.append("register") or httpx.Response(200, json={})
        )
        respx.post(f"{API}/active-learning/assessment-generation/").mock(
            side_effect=lambda request: calls.append("render") or httpx.Response(202, json={})
        )

        await call(
            server,
            "generate_simulation",
            unique_id="c1",
            script=SCRIPT,
            module_name="M",
            module_info="I",
            kb_content="The source material.",
        )
        assert calls == ["kb", "register", "render"]

    @respx.mock
    async def test_a_failed_registration_stops_the_render_and_explains_itself(self, server):
        not_registered()
        respx.post(SCRIPT_STEP).mock(
            return_value=httpx.Response(404, json={"error": "Learning pitstop not found"})
        )
        gen = respx.post(f"{API}/active-learning/assessment-generation/").mock(
            return_value=httpx.Response(202, json={})
        )

        message = await call_expecting_error(
            server,
            "generate_simulation",
            unique_id="nope",
            script=SCRIPT,
            module_name="M",
            module_info="I",
        )

        assert "learning_pitstop_id" in message
        assert not gen.called, (
            "rendering unregistered produces content that can never be read back"
        )

    @respx.mock
    async def test_an_unexpected_probe_failure_is_not_mistaken_for_unregistered(self, server):
        respx.post(STATUS).mock(return_value=httpx.Response(500, json={"error": "boom"}))
        step = respx.post(SCRIPT_STEP)

        await call_expecting_error(
            server,
            "generate_simulation",
            unique_id="c1",
            script=SCRIPT,
            module_name="M",
            module_info="I",
        )
        assert not step.called


class TestStatus:
    @respx.mock
    async def test_in_progress_returns_a_hint_and_skips_the_content_fetch(self, server):
        respx.post(f"{API}/active-learning/get-assessment-status/").mock(
            return_value=httpx.Response(200, json={"status": "in_progress"})
        )
        content = respx.post(f"{API}/active-learning/get-active-learning-json/")

        result = await call(server, "get_simulation", blob_location="u1")

        assert result["activity"] is None
        assert "10-15 seconds" in result["hint"]
        assert not content.called

    @respx.mock
    async def test_completed_returns_the_activity(self, server):
        respx.post(f"{API}/active-learning/get-assessment-status/").mock(
            return_value=httpx.Response(200, json={"status": "completed"})
        )
        respx.post(f"{API}/active-learning/get-active-learning-json/").mock(
            return_value=httpx.Response(200, json={"guidelines": ["Be specific"]})
        )
        result = await call(server, "get_simulation", blob_location="u1")
        assert result["activity"]["guidelines"] == ["Be specific"]

    @respx.mock
    async def test_pathway_id_is_only_sent_when_supplied(self, server):
        respx.post(f"{API}/active-learning/get-assessment-status/").mock(
            return_value=httpx.Response(200, json={"status": "completed"})
        )
        route = respx.post(f"{API}/active-learning/get-active-learning-json/").mock(
            return_value=httpx.Response(200, json={})
        )

        await call(server, "get_simulation", blob_location="u1")
        assert "pathway_id" not in json.loads(route.calls[0].request.read())

        await call(server, "get_simulation", blob_location="u1", pathway_id="p1")
        assert json.loads(route.calls[1].request.read())["pathway_id"] == "p1"

    @respx.mock
    async def test_failure_tells_the_caller_how_to_recover(self, server):
        respx.post(f"{API}/active-learning/get-assessment-status/").mock(
            return_value=httpx.Response(200, json={"status": "failed"})
        )
        result = await call(server, "get_simulation", blob_location="u1")
        assert result["status"] == "failed"
        assert "generate_simulation" in result["hint"]


class TestSimulationKbPush:
    """A simulation rendered without a KB leaves the concept empty, and the UI
    then offers to generate one — which overwrites authored content."""

    @respx.mock
    async def test_the_kb_is_pushed_before_the_render_is_queued(self, server):
        already_registered()
        kb = respx.post(f"{API}/kb/text/update/").mock(
            return_value=httpx.Response(200, json={"success": True})
        )
        gen = respx.post(f"{API}/active-learning/assessment-generation/").mock(
            return_value=httpx.Response(200, json={"blob_location": "b1"})
        )

        result = await call(
            server,
            "generate_simulation",
            unique_id="b1",
            script={"scene": "A store.", "dialogue": [{"a": "Hi"}], "gender_a": "male", "gender_b": "female"},
            module_name="M",
            module_info="I",
            kb_content="The real Rivoli scenario.",
        )

        assert body_of(kb) == {
            "learning_pitstop_id": "b1",
            "text_content": "The real Rivoli scenario.",
        }
        assert result["kb_saved"] is True
        assert gen.called

    @respx.mock
    async def test_a_failed_kb_push_stops_the_render(self, server):
        respx.post(f"{API}/kb/text/update/").mock(
            return_value=httpx.Response(400, json={"message": "bad kb"})
        )
        gen = respx.post(f"{API}/active-learning/assessment-generation/").mock(
            return_value=httpx.Response(200, json={})
        )

        await call_expecting_error(
            server,
            "generate_simulation",
            unique_id="b1",
            script={"scene": "S", "dialogue": [{"a": "Hi"}], "gender_a": "male", "gender_b": "female"},
            module_name="M",
            module_info="I",
            kb_content="bad",
        )
        assert not gen.called, "rendering must not start when the KB push failed"

    @respx.mock
    async def test_omitting_the_kb_leaves_the_stored_one_untouched(self, server):
        already_registered()
        kb = respx.post(f"{API}/kb/text/update/").mock(return_value=httpx.Response(200, json={}))
        respx.post(f"{API}/active-learning/assessment-generation/").mock(
            return_value=httpx.Response(200, json={"blob_location": "b1"})
        )

        result = await call(
            server,
            "generate_simulation",
            unique_id="b1",
            script={"scene": "S", "dialogue": [{"a": "Hi"}], "gender_a": "male", "gender_b": "female"},
            module_name="M",
            module_info="I",
        )

        assert not kb.called
        assert result["kb_saved"] is False
