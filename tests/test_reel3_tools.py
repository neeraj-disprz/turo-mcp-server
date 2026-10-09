"""Reel 3.0 tools: the prerequisite chain and the poll-don't-block contract."""

from __future__ import annotations

import httpx
import json
import respx

from .conftest import API, call, call_expecting_error


class TestScriptGeneration:
    @respx.mock
    async def test_script_generation_requests_version_3(self, server):
        route = respx.post(f"{API}/kb/v2/script/").mock(
            return_value=httpx.Response(200, json={"message": "started"})
        )
        await call(server, "generate_reel_script", learning_pitstop_id="c1", audience="ind")

        body = json.loads(route.calls[0].request.read())
        assert body["version"] == 3
        assert body["audience"] == "ind"
        assert body["fidelity_mode"] == "enhanced_engagement"

    @respx.mock
    async def test_audience_defaults_to_configured_culture_context(self, server):
        route = respx.post(f"{API}/kb/v2/script/").mock(return_value=httpx.Response(200, json={}))
        await call(server, "generate_reel_script", learning_pitstop_id="c1")
        assert json.loads(route.calls[0].request.read())["audience"] == "ind"

    @respx.mock
    async def test_missing_knowledge_base_surfaces_the_api_reason(self, server):
        respx.post(f"{API}/kb/v2/script/").mock(
            return_value=httpx.Response(400, json={"error": "Knowledge base content not available"})
        )
        message = await call_expecting_error(
            server, "generate_reel_script", learning_pitstop_id="c1"
        )
        assert "Knowledge base content not available" in message


class TestScriptRetrieval:
    @respx.mock
    async def test_in_progress_returns_a_polling_hint_and_no_script(self, server):
        respx.post(f"{API}/kb/script/status/").mock(
            return_value=httpx.Response(200, json={"status": "in_progress"})
        )
        content = respx.post(f"{API}/kb/script/content/")

        result = await call(server, "get_reel_script", learning_pitstop_id="c1")

        assert result["status"] == "in_progress"
        assert result["script"] is None
        assert "10-15 seconds" in result["hint"]
        assert not content.called, "content must not be fetched before completion"

    @respx.mock
    async def test_not_generated_points_at_the_step_that_was_skipped(self, server):
        respx.post(f"{API}/kb/script/status/").mock(
            return_value=httpx.Response(200, json={"status": "not_generated"})
        )
        result = await call(server, "get_reel_script", learning_pitstop_id="c1")
        assert "generate_reel_script" in result["hint"]

    @respx.mock
    async def test_completed_fetches_the_script_body(self, server):
        respx.post(f"{API}/kb/script/status/").mock(
            return_value=httpx.Response(200, json={"status": "completed"})
        )
        respx.post(f"{API}/kb/script/content/").mock(
            return_value=httpx.Response(200, json={"overall_scenes": ["Scene one"]})
        )
        result = await call(server, "get_reel_script", learning_pitstop_id="c1")
        assert result["status"] == "completed"
        assert result["script"]["overall_scenes"] == ["Scene one"]


class TestGeneration:
    @respx.mock
    async def test_generation_posts_a_complete_payload_to_the_concept_id(self, server):
        route = respx.post(f"{API}/reels/c1/generate").mock(
            return_value=httpx.Response(202, json={"status": "queued"})
        )
        result = await call(
            server,
            "generate_reel3",
            learning_pitstop_id="c1",
            reel_title="Objections",
            script="Scene one.",
            voice_id="GzE4TcXfh9rYCU9gVgPp",
        )

        body = json.loads(route.calls[0].request.read())
        assert body["reelTitle"] == "Objections"
        assert body["voiceId"] == "GzE4TcXfh9rYCU9gVgPp"
        assert body["reelSettings"]["brand_colors"]["primary"] == "#E82028"
        assert result["queued"] is True
        assert "Poll get_reel3_status" in result["next"]

    async def test_an_empty_script_names_the_tool_that_produces_one(self, server):
        message = await call_expecting_error(
            server,
            "generate_reel3",
            learning_pitstop_id="c1",
            reel_title="t",
            script="   ",
            voice_id="v",
        )
        assert "get_reel_script" in message

    async def test_a_missing_voice_names_the_tool_that_lists_them(self, server):
        message = await call_expecting_error(
            server,
            "generate_reel3",
            learning_pitstop_id="c1",
            reel_title="t",
            script="s",
            voice_id="",
        )
        assert "list_reel_voices" in message

    @respx.mock
    async def test_author_validation_errors_name_the_offending_fields(self, server):
        respx.post(f"{API}/reels/c1/generate").mock(
            return_value=httpx.Response(
                400, json={"error": "invalid_request", "fields": ["reelSettings.art_style"]}
            )
        )
        message = await call_expecting_error(
            server,
            "generate_reel3",
            learning_pitstop_id="c1",
            reel_title="t",
            script="s",
            voice_id="v",
        )
        assert "invalid_request" in message


class TestStatus:
    @respx.mock
    async def test_status_is_passed_through(self, server):
        respx.get(f"{API}/reels/c1/status").mock(
            return_value=httpx.Response(200, json={"status": "in_progress", "progress": 40})
        )
        result = await call(server, "get_reel3_status", learning_pitstop_id="c1")
        assert result["progress"] == 40

    @respx.mock
    async def test_author_outage_is_distinguished_from_a_failed_generation(self, server):
        respx.get(f"{API}/reels/c1/status").mock(return_value=httpx.Response(504))
        message = await call_expecting_error(server, "get_reel3_status", learning_pitstop_id="c1")
        assert "not a content failure" in message


class TestVoices:
    @respx.mock
    async def test_search_is_forwarded_as_a_query_parameter(self, server):
        route = respx.get(f"{API}/reels/voices").mock(
            return_value=httpx.Response(200, json={"voices": []})
        )
        await call(server, "list_reel_voices", search="indian")
        assert route.calls[0].request.url.params["search"] == "indian"

    @respx.mock
    async def test_omitting_search_sends_no_parameters(self, server):
        route = respx.get(f"{API}/reels/voices").mock(
            return_value=httpx.Response(200, json={"voices": [{"id": "v1", "name": "Ranbir"}]})
        )
        result = await call(server, "list_reel_voices")
        assert not route.calls[0].request.url.params
        assert result["voices"][0]["id"] == "v1"


class TestScriptStyleDefaults:
    """Empty script_style/script_type made the downstream service fail.

    The web app always sends concrete values; blank strings reached the script
    service and came back as a bare "Script generation API request failed".
    """

    @respx.mock
    async def test_defaults_match_what_the_web_app_sends(self, server):
        route = respx.post(f"{API}/kb/v2/script/").mock(return_value=httpx.Response(200, json={}))
        await call(server, "generate_reel_script", learning_pitstop_id="c1")

        body = json.loads(route.calls[0].request.read())
        assert body["script_style"] == "teaching_a_friend"
        assert body["script_type"] == "reel"
        assert body["version"] == 3

    @respx.mock
    async def test_blank_values_are_replaced_not_forwarded(self, server):
        route = respx.post(f"{API}/kb/v2/script/").mock(return_value=httpx.Response(200, json={}))
        await call(
            server,
            "generate_reel_script",
            learning_pitstop_id="c1",
            script_style="   ",
            script_type="",
        )

        body = json.loads(route.calls[0].request.read())
        assert body["script_style"] == "teaching_a_friend"
        assert body["script_type"] == "reel"

    @respx.mock
    async def test_a_chosen_style_is_forwarded(self, server):
        route = respx.post(f"{API}/kb/v2/script/").mock(return_value=httpx.Response(200, json={}))
        await call(
            server, "generate_reel_script", learning_pitstop_id="c1", script_style="ted_talk"
        )
        assert json.loads(route.calls[0].request.read())["script_style"] == "ted_talk"

    async def test_an_invented_style_lists_the_valid_ones(self, server):
        message = await call_expecting_error(
            server, "generate_reel_script", learning_pitstop_id="c1", script_style="pirate"
        )
        assert "teaching_a_friend" in message


class TestDownstreamErrorDetail:
    @respx.mock
    async def test_the_real_cause_is_not_swallowed_by_the_wrapper(self, server):
        # Arturo wraps downstream failures in a generic "error"; the cause is in
        # details/response_text, which the caller needs in order to act.
        respx.post(f"{API}/kb/v2/script/").mock(
            return_value=httpx.Response(
                500,
                json={
                    "error": "Script generation API request failed",
                    "details": "422 Client Error for url: .../script_agentic_flow_v2",
                    "response_text": '{"detail":[{"loc":["body","script_style"]}]}',
                },
            )
        )
        message = await call_expecting_error(
            server, "generate_reel_script", learning_pitstop_id="c1"
        )

        assert "Script generation API request failed" in message
        assert "422" in message
        assert "script_style" in message


class TestScriptHandoff:
    @respx.mock
    async def test_completed_script_is_offered_as_flat_text(self, server):
        respx.post(f"{API}/kb/script/status/").mock(
            return_value=httpx.Response(200, json={"status": "completed"})
        )
        respx.post(f"{API}/kb/script/content/").mock(
            return_value=httpx.Response(
                200,
                json={"overall_scenes": [{"narration": ["[warm] Line one."]}, {"narration": "Line two."}]},
            )
        )
        result = await call(server, "get_reel_script", learning_pitstop_id="c1")

        assert result["script_text"] == "[warm] Line one.\n\nLine two."
        assert "script_text" in result["next"]

    @respx.mock
    async def test_generation_flattens_a_json_script_rather_than_failing(self, server):
        # Author parses "[" as an audio tag, so raw script JSON is rejected.
        route = respx.post(f"{API}/reels/c1/generate").mock(
            return_value=httpx.Response(202, json={"status": "queued"})
        )
        await call(
            server,
            "generate_reel3",
            learning_pitstop_id="c1",
            reel_title="t",
            script=json.dumps({"overall_scenes": [{"narration": "[warm] Hello."}]}),
            voice_id="v1",
        )

        sent = json.loads(route.calls[0].request.read())["script"]
        assert sent == "[warm] Hello."
        assert not sent.startswith("{")
