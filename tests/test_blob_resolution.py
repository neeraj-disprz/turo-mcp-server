"""Locale-suffixed blob locations.

A simulation generated for any locale other than en-us/en-us lives at
`<concept-id>-<lang>-<cult>`. Reads 404 against the bare concept id, and — the
dangerous part — writes to it return HTTP 200 and change nothing. Confirmed
against a live activity: saving to the concept id did not stick, saving to
`<id>-en-us-ind` did.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from turo_mcp.blob import base_id, resolve_blob_location
from turo_mcp.client import ArturoClient
from turo_mcp.errors import RequestRejected

from .conftest import API, body_of, call

CONCEPT = "f1b29adf-5539-40b5-91a6-e9efec110c6e"
SUFFIXED = f"{CONCEPT}-en-us-ind"

LANGUAGES = {
    "status": "completed",
    "learning_pitstop_id": CONCEPT,
    "languages": [
        {
            "lang": "en-us",
            "culture_context": "ind",
            "blob_location": SUFFIXED,
            "status": "completed",
        }
    ],
}


class TestBaseId:
    def test_a_locale_suffix_is_stripped(self):
        assert base_id(SUFFIXED) == CONCEPT

    def test_a_bare_uuid_is_unchanged(self):
        assert base_id(CONCEPT) == CONCEPT

    def test_a_non_uuid_is_passed_through(self):
        assert base_id("some-other-id") == "some-other-id"


class TestResolution:
    @respx.mock
    async def test_a_concept_id_resolves_to_the_suffixed_location(self, settings):
        respx.post(f"{API}/active-learning/get-activity-languages/").mock(
            return_value=httpx.Response(200, json=LANGUAGES)
        )
        blob, locale = await resolve_blob_location(ArturoClient(settings, "c"), CONCEPT)
        assert blob == SUFFIXED
        assert locale["culture_context"] == "ind"

    @respx.mock
    async def test_an_already_suffixed_id_is_looked_up_by_its_base(self, settings):
        route = respx.post(f"{API}/active-learning/get-activity-languages/").mock(
            return_value=httpx.Response(200, json=LANGUAGES)
        )
        blob, _ = await resolve_blob_location(ArturoClient(settings, "c"), SUFFIXED)
        assert body_of(route)["learning_pitstop_id"] == CONCEPT
        assert blob == SUFFIXED

    @respx.mock
    async def test_a_requested_locale_is_honoured(self, settings):
        respx.post(f"{API}/active-learning/get-activity-languages/").mock(
            return_value=httpx.Response(
                200,
                json={
                    "languages": [
                        {"lang": "en-us", "culture_context": "ind", "blob_location": SUFFIXED},
                        {
                            "lang": "ar",
                            "culture_context": "ar",
                            "blob_location": f"{CONCEPT}-ar-ar",
                        },
                    ]
                },
            )
        )
        blob, _ = await resolve_blob_location(
            ArturoClient(settings, "c"), CONCEPT, lang="ar", cult_context="ar"
        )
        assert blob == f"{CONCEPT}-ar-ar"

    @respx.mock
    async def test_an_absent_locale_lists_what_exists(self, settings):
        respx.post(f"{API}/active-learning/get-activity-languages/").mock(
            return_value=httpx.Response(200, json=LANGUAGES)
        )
        with pytest.raises(RequestRejected) as exc:
            await resolve_blob_location(
                ArturoClient(settings, "c"), CONCEPT, lang="fr", cult_context="fr"
            )
        assert "en-us/ind" in str(exc.value)

    @respx.mock
    async def test_an_activity_with_no_language_records_falls_back(self, settings):
        # Standalone simulations have no pitstop, so the lookup 404s.
        respx.post(f"{API}/active-learning/get-activity-languages/").mock(
            return_value=httpx.Response(404, json={"error": "not found"})
        )
        blob, locale = await resolve_blob_location(ArturoClient(settings, "c"), "standalone-id")
        assert blob == "standalone-id"
        assert locale == {}


class TestToolsUseTheResolvedLocation:
    @respx.mock
    async def test_save_writes_to_the_suffixed_location(self, server):
        respx.post(f"{API}/active-learning/get-activity-languages/").mock(
            return_value=httpx.Response(200, json=LANGUAGES)
        )
        route = respx.post(f"{API}/active-learning/save-assessment-json/").mock(
            return_value=httpx.Response(200, json={"act_response": "ok"})
        )

        result = await call(
            server,
            "save_simulation",
            unique_id=CONCEPT,  # the id an agent naturally holds
            activity_json={"activelearning": {"scenes": []}},
            guidelines={"total_scores": 10},
        )

        assert body_of(route)["unique_id"] == SUFFIXED, "a save to the concept id silently no-ops"
        assert result["saved_to"] == SUFFIXED

    @respx.mock
    async def test_the_wrapper_from_get_simulation_is_accepted(self, server):
        respx.post(f"{API}/active-learning/get-activity-languages/").mock(
            return_value=httpx.Response(200, json=LANGUAGES)
        )
        route = respx.post(f"{API}/active-learning/save-assessment-json/").mock(
            return_value=httpx.Response(200, json={})
        )

        await call(
            server,
            "save_simulation",
            unique_id=CONCEPT,
            # Exactly the shape get_simulation returns.
            activity_json={"active_learning": {"activelearning": {"scenes": [1]}}},
            guidelines={"total_scores": 10},
        )

        sent = body_of(route)["activity_json"]
        assert "activelearning" in sent and "active_learning" not in sent

    @respx.mock
    async def test_reads_resolve_and_report_the_location(self, server):
        respx.post(f"{API}/active-learning/get-activity-languages/").mock(
            return_value=httpx.Response(200, json=LANGUAGES)
        )
        status = respx.post(f"{API}/active-learning/get-assessment-status/").mock(
            return_value=httpx.Response(200, json={"status": "completed"})
        )
        respx.post(f"{API}/active-learning/get-active-learning-json/").mock(
            return_value=httpx.Response(200, json={"guidelines": {}})
        )

        result = await call(server, "get_simulation", blob_location=CONCEPT)

        assert body_of(status)["blob_location"] == SUFFIXED
        assert result["blob_location"] == SUFFIXED
        assert result["locale"]["culture_context"] == "ind"

    @respx.mock
    async def test_audio_replacement_also_resolves(self, server):
        respx.post(f"{API}/active-learning/get-activity-languages/").mock(
            return_value=httpx.Response(200, json=LANGUAGES)
        )
        route = respx.post(f"{API}/active-learning/edit-activityscene-audio/").mock(
            return_value=httpx.Response(200, json={})
        )
        await call(
            server,
            "replace_simulation_audio",
            unique_id=CONCEPT,
            file_name="a_1.mp3",
            text="New line.",
            voice="adam",
        )
        assert body_of(route)["unique_id"] == SUFFIXED
