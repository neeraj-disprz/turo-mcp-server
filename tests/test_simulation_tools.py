"""Single-Turn Simulation: standalone vs pathway-attached, and voice assignment."""

from __future__ import annotations

import httpx
import json
import pytest
import respx

from turo_mcp.tools.simulation import _pick_voices

from .conftest import API, call, call_expecting_error

SCRIPT = {
    "scene": "A manager and an employee discuss a missed deadline.",
    "dialogue": {"a_1": "You're late.", "b_1": "I know.", "a_2": "Why?", "b_2": "Blocked."},
    "hint_question": "How would you respond?",
    "gender_a": "male",
    "gender_b": "female",
    "name_a": "Raj",
    "name_b": "Priya",
}


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
