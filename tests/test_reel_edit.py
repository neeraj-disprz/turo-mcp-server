"""Editing a generated Reel 3.0: saving the document and re-recording narration."""

from __future__ import annotations

import httpx
import respx

from .conftest import API, body_of, call, call_expecting_error

REEL_DOCUMENT = {
    "reel": {
        "settings": {"aspectRatio": "16:9"},
        "scenes": [{"id": "s1", "narration": "Line one."}],
    }
}


class TestSaveReel:
    @respx.mock
    async def test_the_document_is_put_to_the_reel_endpoint(self, server):
        route = respx.put(f"{API}/reels/c1/reel").mock(
            return_value=httpx.Response(200, json={"status": "saved"})
        )

        result = await call(server, "save_reel", learning_pitstop_id="c1", reel=REEL_DOCUMENT)

        assert body_of(route) == REEL_DOCUMENT, "the document must be forwarded untouched"
        assert result["saved"] is True
        assert result["reel_id"] == "c1"

    @respx.mock
    async def test_a_bare_settings_scenes_object_is_accepted(self, server):
        # Author normalises both shapes, so a caller who unwrapped the envelope
        # themselves should not be forced to re-wrap it.
        route = respx.put(f"{API}/reels/c1/reel").mock(return_value=httpx.Response(200, json={}))
        bare = REEL_DOCUMENT["reel"]

        await call(server, "save_reel", learning_pitstop_id="c1", reel=bare)

        assert body_of(route) == bare

    async def test_the_full_status_response_is_refused(self, server):
        # The status payload wraps the reel alongside progress fields. PUTting it
        # whole would overwrite the reel with those fields.
        message = await call_expecting_error(
            server,
            "save_reel",
            learning_pitstop_id="c1",
            reel={"status": "completed", "progress": 100},
        )
        assert "settings" in message and "scenes" in message

    async def test_an_empty_reel_is_refused(self, server):
        message = await call_expecting_error(
            server, "save_reel", learning_pitstop_id="c1", reel={}
        )
        assert "empty" in message
        assert "get_reel3_status" in message

    @respx.mock
    async def test_the_caller_is_told_audio_is_untouched(self, server):
        respx.put(f"{API}/reels/c1/reel").mock(return_value=httpx.Response(200, json={}))
        result = await call(server, "save_reel", learning_pitstop_id="c1", reel=REEL_DOCUMENT)
        assert "regenerate_scene_narration" in result["note"]


class TestRegenerateSceneNarration:
    @respx.mock
    async def test_it_posts_the_scene_audio_payload_with_defaults(self, server):
        # Shape captured from a live call: audio metadata alongside the saved
        # reel, which is nested one level deeper than the caller wants it.
        route = respx.post(f"{API}/reels/c1/scenes/s1/audio/mp3").mock(
            return_value=httpx.Response(
                200,
                json={
                    "scene_id": "s1",
                    "audio": {"url": "https://blob/s1.mp3", "duration": 8.4},
                    "duration": 8.4,
                    "reel_total_duration": 96.2,
                    "reel": {"reel": {"settings": {}, "scenes": [{"scene_id": "s1"}]}},
                },
            )
        )

        result = await call(
            server,
            "regenerate_scene_narration",
            learning_pitstop_id="c1",
            scene_id="s1",
            script="The corrected line.",
            voice_id="OBniqvdbk1nBPJfwOJRw",
        )

        sent = body_of(route)
        assert sent["script"] == "The corrected line."
        assert sent["voiceId"] == "OBniqvdbk1nBPJfwOJRw"
        # Defaults keep a re-recorded scene matching the ones around it.
        assert sent["modelId"] == "eleven_v3"
        assert sent["cultureContext"] == "ind"
        assert "language" not in sent, "optional fields must be omitted, not sent empty"
        # The endpoint is synchronous and returns the saved reel; telling the
        # model to poll would send it to the reel-level status endpoint, which
        # 404s when no whole-reel job exists.
        assert result["recorded"] is True
        assert "do not poll" in result["note"]
        assert "queued" not in result
        # Arturo saves the reel itself, so the fresh document must reach the
        # caller — otherwise the next save_reel would revert the new timings.
        # It must arrive in get_reel3_status shape, ready for save_reel.
        assert result["reel"] == {"reel": {"settings": {}, "scenes": [{"scene_id": "s1"}]}}
        assert result["audio"] == {"url": "https://blob/s1.mp3", "duration": 8.4}
        assert result["reel_total_duration"] == 96.2

    @respx.mock
    async def test_explicit_values_override_the_defaults(self, server):
        route = respx.post(f"{API}/reels/c1/scenes/s1/audio/mp3").mock(
            return_value=httpx.Response(202, json={})
        )

        await call(
            server,
            "regenerate_scene_narration",
            learning_pitstop_id="c1",
            scene_id="s1",
            script="Line.",
            voice_id="v1",
            model_id="eleven_turbo_v2",
            language="en",
            culture_context="usa",
        )

        sent = body_of(route)
        assert sent["modelId"] == "eleven_turbo_v2"
        assert sent["language"] == "en"
        assert sent["cultureContext"] == "usa"

    async def test_a_missing_voice_points_at_list_reel_voices(self, server):
        message = await call_expecting_error(
            server,
            "regenerate_scene_narration",
            learning_pitstop_id="c1",
            scene_id="s1",
            script="Line.",
            voice_id="  ",
        )
        assert "list_reel_voices" in message

    async def test_an_empty_script_is_refused(self, server):
        message = await call_expecting_error(
            server,
            "regenerate_scene_narration",
            learning_pitstop_id="c1",
            scene_id="s1",
            script="   ",
            voice_id="v1",
        )
        assert "script is empty" in message

    async def test_an_empty_scene_id_is_refused(self, server):
        message = await call_expecting_error(
            server,
            "regenerate_scene_narration",
            learning_pitstop_id="c1",
            scene_id=" ",
            script="Line.",
            voice_id="v1",
        )
        assert "scene_id is required" in message


class TestReelAudioStatus:
    @respx.mock
    async def test_it_reads_the_audio_status_endpoint(self, server):
        respx.get(f"{API}/reels/c1/audio/status").mock(
            return_value=httpx.Response(200, json={"status": "completed"})
        )
        result = await call(server, "get_reel_audio_status", learning_pitstop_id="c1")
        assert result["status"] == "completed"

    @respx.mock
    async def test_an_unreachable_author_service_says_so(self, server):
        respx.get(f"{API}/reels/c1/audio/status").mock(return_value=httpx.Response(504))
        message = await call_expecting_error(
            server, "get_reel_audio_status", learning_pitstop_id="c1"
        )
        assert "transient" in message
