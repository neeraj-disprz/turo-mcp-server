"""Pathway editing: correct payload mapping and the destructive-action guardrails."""

from __future__ import annotations

import httpx
import respx

from .conftest import API, body_of, call, call_expecting_error


class TestReads:
    @respx.mock
    async def test_list_pathways_scopes_the_search_to_doc_to_learning(self, server):
        route = respx.post(f"{API}/workspace/projects/search/").mock(
            return_value=httpx.Response(200, json={"results": [], "total_count": 0})
        )
        await call(server, "list_pathways", search="onboarding")

        body = body_of(route)
        assert body["agent_type"] == ["doc_to_learning"]
        assert body["search"] == "onboarding"

    @respx.mock
    async def test_list_pathways_clamps_limit_to_the_api_maximum(self, server):
        route = respx.post(f"{API}/workspace/projects/search/").mock(
            return_value=httpx.Response(200, json={})
        )
        await call(server, "list_pathways", limit=5000)
        assert body_of(route)["limit"] == 100

    async def test_list_pathways_rejects_an_unsupported_sort(self, server):
        message = await call_expecting_error(server, "list_pathways", sort_by="oldest")
        assert "recent" in message

    async def test_list_pathways_rejects_an_unsupported_scope(self, server):
        message = await call_expecting_error(server, "list_pathways", created_by_scope="team")
        assert "others" in message

    @respx.mock
    async def test_get_pathway_returns_the_chapter_tree(self, server):
        respx.post(f"{API}/content-transformation/get-pathway/").mock(
            return_value=httpx.Response(
                200,
                json={
                    "success": True,
                    "data": {"learning_pathway": {"chapters": [{"anchor_id": "a1"}]}},
                },
            )
        )
        result = await call(server, "get_pathway", learning_pathway_id="p1")
        assert result["learning_pathway"]["chapters"][0]["anchor_id"] == "a1"


class TestChapterEditing:
    @respx.mock
    async def test_create_chapter_maps_title_to_the_anchor_field(self, server):
        route = respx.post(f"{API}/anchor/create/").mock(
            return_value=httpx.Response(201, json={"anchor_id": "a1"})
        )
        await call(
            server,
            "create_chapter",
            learning_pathway_id="p1",
            title="Basics",
            description="d",
            position="start",
        )
        assert body_of(route)["anchor"] == "Basics"

    @respx.mock
    async def test_update_chapter_sends_only_the_fields_given(self, server):
        route = respx.put(f"{API}/anchor/update/").mock(
            return_value=httpx.Response(200, json={"message": "ok"})
        )
        await call(server, "update_chapter", anchor_id="a1", title="Renamed")

        body = body_of(route)
        assert body["anchor"] == "Renamed"
        assert "description" not in body
        assert "level" not in body

    @respx.mock
    async def test_update_chapter_never_sends_the_regenerate_flag(self, server):
        # generate_pitstops deletes every concept in the chapter and its reels.
        route = respx.put(f"{API}/anchor/update/").mock(return_value=httpx.Response(200, json={}))
        await call(server, "update_chapter", anchor_id="a1", title="x", description="y", level="z")
        assert "generate_pitstops" not in body_of(route)

    async def test_update_chapter_with_nothing_to_change_is_rejected(self, server):
        message = await call_expecting_error(server, "update_chapter", anchor_id="a1")
        assert "at least one" in message

    @respx.mock
    async def test_delete_chapters_requires_confirmation(self, server):
        route = respx.post(f"{API}/anchor/delete/").mock(return_value=httpx.Response(200, json={}))
        message = await call_expecting_error(server, "delete_chapters", anchor_ids=["a1", "a2"])

        assert "confirm=true" in message
        assert "2 chapter(s)" in message
        assert not route.called, "nothing may reach Arturo without confirmation"

    @respx.mock
    async def test_delete_chapters_proceeds_once_confirmed(self, server):
        route = respx.post(f"{API}/anchor/delete/").mock(
            return_value=httpx.Response(200, json={"message": "deleted"})
        )
        await call(server, "delete_chapters", anchor_ids=["a1"], confirm=True)
        assert body_of(route)["anchor_ids"] == ["a1"]

    async def test_an_empty_delete_is_rejected(self, server):
        message = await call_expecting_error(
            server, "delete_chapters", anchor_ids=[], confirm=True
        )
        assert "must not be empty" in message


class TestConceptEditing:
    @respx.mock
    async def test_create_concept_maps_fields_to_pitstop_names(self, server):
        route = respx.post(f"{API}/pitstop/create/").mock(
            return_value=httpx.Response(201, json={"pitstop_id": "c1"})
        )
        await call(
            server,
            "create_concept",
            anchor_id="a1",
            title="Objections",
            description="Handling pushback",
            content_type="Reel 3.0",
            duration_minutes=7,
            position="start",
        )

        body = body_of(route)
        assert body["pitstop"] == "Objections"
        assert body["type"] == "Reel 3.0"
        assert body["duration"] == 7

    async def test_create_concept_rejects_an_unknown_content_type(self, server):
        message = await call_expecting_error(
            server,
            "create_concept",
            anchor_id="a1",
            title="t",
            description="d",
            content_type="Reel 4.0",
        )
        assert "Reel 3.0" in message

    @respx.mock
    async def test_reset_contents_requires_confirmation(self, server):
        route = respx.put(f"{API}/pitstop/update/").mock(return_value=httpx.Response(200, json={}))
        message = await call_expecting_error(
            server, "update_concept", learning_pitstop_id="c1", title="x", reset_contents=True
        )
        assert "confirm=true" in message
        assert not route.called

    @respx.mock
    async def test_reset_contents_is_forwarded_once_confirmed(self, server):
        route = respx.put(f"{API}/pitstop/update/").mock(return_value=httpx.Response(200, json={}))
        await call(
            server,
            "update_concept",
            learning_pitstop_id="c1",
            title="x",
            reset_contents=True,
            confirm=True,
        )
        assert body_of(route)["reset_contents"] is True

    @respx.mock
    async def test_ordinary_update_does_not_send_reset_contents(self, server):
        route = respx.put(f"{API}/pitstop/update/").mock(return_value=httpx.Response(200, json={}))
        await call(server, "update_concept", learning_pitstop_id="c1", title="Renamed")
        assert "reset_contents" not in body_of(route)

    async def test_update_concept_with_nothing_to_change_is_rejected(self, server):
        message = await call_expecting_error(
            server, "update_concept", learning_pitstop_id="c1"
        )
        assert "at least one" in message

    @respx.mock
    async def test_delete_concepts_requires_confirmation(self, server):
        route = respx.post(f"{API}/pitstop/delete/").mock(return_value=httpx.Response(200, json={}))
        message = await call_expecting_error(
            server, "delete_concepts", learning_pitstop_ids=["c1"]
        )
        assert "confirm=true" in message
        assert not route.called


class TestReordering:
    @respx.mock
    async def test_chapter_order_hits_the_anchor_endpoint(self, server):
        # Both reorder endpoints are PUT-only; POST returns 405.
        route = respx.put(f"{API}/anchor/reorder/").mock(return_value=httpx.Response(200, json={}))
        await call(server, "reorder_pathway", learning_pathway_id="p1", anchor_ids=["a2", "a1"])
        assert body_of(route)["anchor_ids"] == ["a2", "a1"]

    @respx.mock
    async def test_concept_order_hits_the_pitstop_endpoint(self, server):
        route = respx.put(f"{API}/pitstop/reorder/").mock(
            return_value=httpx.Response(200, json={})
        )
        await call(server, "reorder_pathway", anchor_id="a1", learning_pitstop_ids=["c2", "c1"])
        assert body_of(route)["pitstop_ids"] == ["c2", "c1"]

    async def test_an_incomplete_pairing_explains_both_valid_shapes(self, server):
        message = await call_expecting_error(server, "reorder_pathway", anchor_ids=["a1"])
        assert "learning_pathway_id" in message and "anchor_id" in message
