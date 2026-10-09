"""Regression tests against response shapes captured from the live API.

Every fixture here is a trimmed copy of a real response from
https://turo.disprz.ai (verified 2026-10-07). Arturo wraps some payloads in a
`{"success": true, "data": …}` envelope and returns others flat; getting that
wrong is invisible in a hand-written mock, so these lock in the real thing.
"""

from __future__ import annotations

import httpx
import respx

from .conftest import API, call

# GET /ui/api/auth/user-details/
USER_DETAILS = {
    "success": True,
    "user": {
        "name": "skilturo_super_admin",
        "email": "skillturosuperadmin@gmail.com",
        "role": "super_admin",
        "user_id": "e50cdbea-5c76-4b0c-b63e-d18708616da2",
    },
}

# POST /ui/api/workspace/projects/search/  — flat, no envelope
PROJECT_SEARCH = {
    "results": [
        {
            "id": "bd68bbff-a394-4c32-89da-03cff7e1d8d2",
            "type": "project",
            "agent_type": "doc_to_learning",
            "title": "Customer Focus & Luxury Service Excellence",
            "status": "In - progress",
            "is_published": False,
            "pathway_id": "bd68bbff-a394-4c32-89da-03cff7e1d8d2",
            "pathway_type": "content_based",
            "total_activities": 5,
            "total_chapters": 3,
        }
    ],
    "pagination": {},
    "result_counts": {},
    "match_counts": {},
    "filters": {},
}

# POST /ui/api/content-transformation/get-pathway/ — enveloped
GET_PATHWAY = {
    "success": True,
    "learning_pathway_id": "bd68bbff-a394-4c32-89da-03cff7e1d8d2",
    "data": {
        "conversation_id": "dee9e0b0-7c6b-4d5d-a3bb-036e18e3333d",
        "document_id": "75c66967-7aa6-4e22-b0b4-2d2f098787db",
        "pathway_details": {
            "pathway_title": "Customer Focus & Luxury Service Excellence",
            "learning_pathway_id": "bd68bbff-a394-4c32-89da-03cff7e1d8d2",
        },
        "learning_pathway": {
            "chapters": [
                {
                    "anchor_id": "8f3827ab-8c3a-4ac2-9e9b-1fb356e41796",
                    "chapter_number": 1,
                    "chapter_title": "Luxury Service Rituals & Ceremony of Sale",
                    "anchor_order": 1,
                    "export_status": "Not Started",
                    "total_activities": 3,
                    "concepts": [
                        {
                            "learning_pitstop_id": "b763ada6-41fd-45a9-b9da-933d1c9acde5",
                            "concept_number": 1,
                            "concept_title": "The First 30 Seconds",
                            "content_type": "Reel 2.0",
                            "lang": "en-us",
                            "cult_context": "en-us",
                            "is_kb_generated": True,
                            "is_script_generated": False,
                            "script_generation_status": "not_generated",
                        }
                    ],
                }
            ]
        },
    },
}

# POST /ui/api/content-transformation/get-content-kb/ — enveloped
GET_CONTENT_KB = {
    "success": True,
    "data": {"kb_content": "Mastering client dynamics and observation is essential…"},
}

# POST /ui/api/kb/script/status/ — flat, no envelope
SCRIPT_STATUS = {
    "learning_pitstop_id": "b763ada6-41fd-45a9-b9da-933d1c9acde5",
    "status": "not_generated",
}

# GET /ui/api/reels/voices — flat, no envelope
VOICES = {
    "voices": [
        {
            "id": "OBniqvdbk1nBPJfwOJRw",
            "name": "Ranbir M - Friendly Customer Support",
            "gender": "male",
            "accent": "indian",
            "previewUrl": "https://storage.googleapis.com/eleven-public-prod/preview.mp3",
            "verifiedLanguages": [],
        }
    ]
}


@respx.mock
async def test_whoami_returns_the_user_not_the_envelope(server):
    respx.get(f"{API}/auth/user-details/").mock(return_value=httpx.Response(200, json=USER_DETAILS))
    result = await call(server, "whoami")

    assert result["email"] == "skillturosuperadmin@gmail.com"
    assert result["role"] == "super_admin"
    assert "success" not in result, "the envelope must not reach the model"


@respx.mock
async def test_get_pathway_unwraps_to_the_chapter_tree(server):
    respx.post(f"{API}/content-transformation/get-pathway/").mock(
        return_value=httpx.Response(200, json=GET_PATHWAY)
    )
    result = await call(server, "get_pathway", learning_pathway_id="p1")

    # Reading chapters off the envelope silently yields zero chapters — the
    # exact failure the live run surfaced.
    chapters = result["learning_pathway"]["chapters"]
    assert len(chapters) == 1
    assert chapters[0]["concepts"][0]["concept_title"] == "The First 30 Seconds"
    assert result["pathway_details"]["pathway_title"].startswith("Customer Focus")
    # The link the user clicks to open what the model just described.
    assert result["url"] == "https://arturo.test/ui/workspace/documentToLearning/?pathwayId=p1"


@respx.mock
async def test_get_concept_kb_unwraps_to_the_content(server):
    respx.post(f"{API}/content-transformation/get-content-kb/").mock(
        return_value=httpx.Response(200, json=GET_CONTENT_KB)
    )
    result = await call(server, "get_concept_kb", learning_pitstop_id="c1")
    assert result["kb_content"].startswith("Mastering client dynamics")


@respx.mock
async def test_list_pathways_passes_the_flat_search_response_through(server):
    respx.post(f"{API}/workspace/projects/search/").mock(
        return_value=httpx.Response(200, json=PROJECT_SEARCH)
    )
    result = await call(server, "list_pathways")

    assert result["results"][0]["pathway_id"] == "bd68bbff-a394-4c32-89da-03cff7e1d8d2"
    assert result["results"][0]["total_chapters"] == 3
    assert result["results"][0]["url"] == (
        "https://arturo.test/ui/workspace/documentToLearning/"
        "?pathwayId=bd68bbff-a394-4c32-89da-03cff7e1d8d2"
    )


@respx.mock
async def test_a_search_response_without_results_still_returns(server):
    # The link is a convenience; an unexpected shape must not fail the tool.
    respx.post(f"{API}/workspace/projects/search/").mock(
        return_value=httpx.Response(200, json={"pagination": {}})
    )
    assert await call(server, "list_pathways") == {"pagination": {}}


@respx.mock
async def test_script_status_is_read_from_a_flat_response(server):
    respx.post(f"{API}/kb/script/status/").mock(
        return_value=httpx.Response(200, json=SCRIPT_STATUS)
    )
    result = await call(server, "get_reel_script", learning_pitstop_id="c1")

    assert result["status"] == "not_generated"
    assert "generate_reel_script" in result["hint"]


@respx.mock
async def test_voice_ids_are_elevenlabs_ids(server):
    respx.get(f"{API}/reels/voices").mock(return_value=httpx.Response(200, json=VOICES))
    result = await call(server, "list_reel_voices")

    voice = result["voices"][0]
    assert voice["id"] == "OBniqvdbk1nBPJfwOJRw"
    # Legacy aliases like "en-us-m-adam" are rejected by the Author service.
    assert not voice["id"].startswith("en-")
