"""Editing a generated simulation.

The save endpoint replaces both the activity and its guidelines in one call, so
the guardrails here are about not destroying what the caller didn't mean to
touch.
"""

from __future__ import annotations

import httpx
import respx

from .conftest import API, body_of, call, call_expecting_error

ACTIVITY = {
    "activelearning": {
        "settings": {"char1": ["Raj"], "char2": ["Priya"]},
        "scenes": [{"narration": "You're late.", "audio_name": "a_1.mp3", "image": "s1.png"}],
    }
}
GUIDELINES = {"rubric": [{"criterion": "Empathy", "weight": 0.4}]}


class TestSave:
    @respx.mock
    async def test_a_full_save_sends_both_documents(self, server):
        route = respx.post(f"{API}/active-learning/save-assessment-json/").mock(
            return_value=httpx.Response(200, json={"act_response": "ok"})
        )
        await call(
            server,
            "save_simulation",
            unique_id="u1",
            activity_json=ACTIVITY,
            guidelines=GUIDELINES,
        )

        body = body_of(route)
        assert body["unique_id"] == "u1"
        assert body["activity_json"]["activelearning"]["settings"]["char1"] == ["Raj"]
        assert body["guidelines"] == GUIDELINES

    @respx.mock
    async def test_empty_guidelines_are_refused_before_they_erase_the_rubric(self, server):
        route = respx.post(f"{API}/active-learning/save-assessment-json/")
        message = await call_expecting_error(
            server, "save_simulation", unique_id="u1", activity_json=ACTIVITY, guidelines={}
        )
        assert "erase the scoring rubric" in message
        assert not route.called

    @respx.mock
    async def test_an_empty_activity_is_refused(self, server):
        route = respx.post(f"{API}/active-learning/save-assessment-json/")
        message = await call_expecting_error(
            server, "save_simulation", unique_id="u1", activity_json={}, guidelines=GUIDELINES
        )
        assert "get_simulation" in message
        assert not route.called

    @respx.mock
    async def test_a_partial_activity_object_is_refused(self, server):
        # Sending just the scenes array would drop settings and characters.
        route = respx.post(f"{API}/active-learning/save-assessment-json/")
        message = await call_expecting_error(
            server,
            "save_simulation",
            unique_id="u1",
            activity_json={"scenes": []},
            guidelines=GUIDELINES,
        )
        assert "activelearning" in message
        assert not route.called


class TestImageEdit:
    @respx.mock
    async def test_regeneration_sends_the_description_under_the_api_field_name(self, server):
        route = respx.post(f"{API}/active-learning/regenerate-image/").mock(
            return_value=httpx.Response(200, json={"status": "ok"})
        )
        await call(
            server,
            "regenerate_simulation_image",
            unique_id="u1",
            image_name="s1.png",
            description="A manager and employee at a desk, tense.",
        )

        body = body_of(route)
        assert body["image_name"] == "s1.png"
        assert body["rephrased_description"].startswith("A manager")

    async def test_an_empty_description_is_refused(self, server):
        message = await call_expecting_error(
            server,
            "regenerate_simulation_image",
            unique_id="u1",
            image_name="s1.png",
            description="  ",
        )
        assert "empty" in message


class TestAudioEdit:
    @respx.mock
    async def test_audio_replacement_maps_to_the_api_field_names(self, server):
        route = respx.post(f"{API}/active-learning/edit-activityscene-audio/").mock(
            return_value=httpx.Response(200, json={"status": "ok"})
        )
        await call(
            server,
            "replace_simulation_audio",
            unique_id="u1",
            file_name="a_1.mp3",
            text="Let's talk about the deadline.",
            voice="adam",
        )

        body = body_of(route)
        assert body["file_name"] == "a_1.mp3"
        assert body["rephrased_script"] == "Let's talk about the deadline."
        assert body["voice_model"] == "adam"
        assert body["lang"] == "en-us"

    async def test_an_unknown_voice_lists_the_valid_ones(self, server):
        message = await call_expecting_error(
            server,
            "replace_simulation_audio",
            unique_id="u1",
            file_name="a_1.mp3",
            text="hi",
            voice="morgan",
        )
        assert "adam" in message

    async def test_empty_text_is_refused(self, server):
        message = await call_expecting_error(
            server,
            "replace_simulation_audio",
            unique_id="u1",
            file_name="a_1.mp3",
            text="   ",
            voice="adam",
        )
        assert "empty" in message

    @respx.mock
    async def test_scene_audio_url_can_be_read_back(self, server):
        respx.post(f"{API}/active-learning/get-scene-audio/").mock(
            return_value=httpx.Response(200, json={"audio_url": "https://blob/a_1.mp3?warri=x"})
        )
        result = await call(
            server, "get_simulation_audio", unique_id="u1", audio_name="a_1.mp3"
        )
        assert result["audio_url"].startswith("https://blob/")
