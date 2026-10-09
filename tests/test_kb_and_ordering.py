"""Knowledge-base writes, and the create-then-reorder workaround.

Two Turo behaviours drive this file:

1. `display_order` defaults to 0 and neither create endpoint accepts it, so a new
   chapter or concept always lands first. The only fix through the API is to
   create and then reorder.
2. Both reorder endpoints are PUT. A POST returns 405.
"""

from __future__ import annotations

import httpx
import respx

from .conftest import API, body_of, call, call_expecting_error

PATHWAY = {
    "success": True,
    "data": {
        "learning_pathway": {
            "chapters": [
                {
                    "anchor_id": "existing-1",
                    "concepts": [{"learning_pitstop_id": "c-old"}],
                },
                {"anchor_id": "existing-2", "concepts": []},
            ]
        }
    },
}


class TestKnowledgeBaseWrite:
    @respx.mock
    async def test_writing_a_kb_targets_the_text_update_endpoint(self, server):
        route = respx.post(f"{API}/kb/text/update/").mock(
            return_value=httpx.Response(200, json={"success": True, "kb_url": "https://blob/kb.docx"})
        )
        result = await call(
            server,
            "set_concept_kb",
            learning_pitstop_id="c1",
            kb_content="Fraud protection starts with verifying the caller.",
        )

        body = body_of(route)
        assert body["learning_pitstop_id"] == "c1"
        assert body["text_content"].startswith("Fraud protection")
        assert result["kb_url"] == "https://blob/kb.docx"
        assert "generate_reel_script" in result["next"]

    @respx.mock
    async def test_empty_kb_content_never_reaches_arturo(self, server):
        route = respx.post(f"{API}/kb/text/update/").mock(return_value=httpx.Response(200, json={}))
        message = await call_expecting_error(
            server, "set_concept_kb", learning_pitstop_id="c1", kb_content="   "
        )
        assert "empty" in message
        assert not route.called

    @respx.mock
    async def test_reading_a_kb_unwraps_the_envelope(self, server):
        respx.post(f"{API}/content-transformation/get-content-kb/").mock(
            return_value=httpx.Response(200, json={"success": True, "data": {"kb_content": "text"}})
        )
        assert (await call(server, "get_concept_kb", learning_pitstop_id="c1"))["kb_content"] == "text"


class TestChapterPlacement:
    @respx.mock
    async def test_new_chapter_is_moved_to_the_end(self, server):
        respx.post(f"{API}/anchor/create/").mock(
            return_value=httpx.Response(201, json={"anchor_id": "new-1"})
        )
        respx.post(f"{API}/content-transformation/get-pathway/").mock(
            return_value=httpx.Response(200, json=PATHWAY)
        )
        reorder = respx.put(f"{API}/anchor/reorder/").mock(
            return_value=httpx.Response(200, json={"success": True})
        )

        result = await call(
            server, "create_chapter", learning_pathway_id="p1", title="New", description="d"
        )

        assert body_of(reorder)["anchor_ids"] == ["existing-1", "existing-2", "new-1"]
        assert result["position"] == "end"

    @respx.mock
    async def test_position_start_skips_the_reorder(self, server):
        respx.post(f"{API}/anchor/create/").mock(
            return_value=httpx.Response(201, json={"anchor_id": "new-1"})
        )
        reorder = respx.put(f"{API}/anchor/reorder/")

        result = await call(
            server,
            "create_chapter",
            learning_pathway_id="p1",
            title="New",
            description="d",
            position="start",
        )

        assert not reorder.called
        assert result["position"] == "start"

    @respx.mock
    async def test_a_failed_reorder_still_reports_the_created_chapter(self, server):
        respx.post(f"{API}/anchor/create/").mock(
            return_value=httpx.Response(201, json={"anchor_id": "new-1"})
        )
        respx.post(f"{API}/content-transformation/get-pathway/").mock(
            return_value=httpx.Response(500, json={"message": "boom"})
        )

        result = await call(
            server, "create_chapter", learning_pathway_id="p1", title="New", description="d"
        )

        # The chapter exists; losing the reorder must not read as total failure.
        assert result["anchor_id"] == "new-1"
        assert result["position"] == "start"
        assert "reorder_pathway" in result["warning"]

    async def test_an_unknown_position_is_rejected(self, server):
        message = await call_expecting_error(
            server,
            "create_chapter",
            learning_pathway_id="p1",
            title="t",
            description="d",
            position="middle",
        )
        assert "'end' or 'start'" in message


class TestConceptPlacement:
    @respx.mock
    async def test_new_concept_is_moved_to_the_end_of_its_chapter(self, server):
        respx.post(f"{API}/pitstop/create/").mock(
            return_value=httpx.Response(201, json={"pitstop_id": "c-new"})
        )
        # anchor_id alone does not identify the pathway, so resolve it first.
        respx.get(f"{API}/anchor/get/").mock(
            return_value=httpx.Response(200, json={"learning_pathway_id": "p1"})
        )
        respx.post(f"{API}/content-transformation/get-pathway/").mock(
            return_value=httpx.Response(200, json=PATHWAY)
        )
        reorder = respx.put(f"{API}/pitstop/reorder/").mock(
            return_value=httpx.Response(200, json={"success": True})
        )

        result = await call(
            server,
            "create_concept",
            anchor_id="existing-1",
            title="New",
            description="d",
            content_type="Simulation",
        )

        assert body_of(reorder)["pitstop_ids"] == ["c-old", "c-new"]
        assert result["position"] == "end"

    @respx.mock
    async def test_creating_a_concept_warns_that_it_has_no_knowledge_base(self, server):
        respx.post(f"{API}/pitstop/create/").mock(
            return_value=httpx.Response(201, json={"pitstop_id": "c-new"})
        )
        result = await call(
            server,
            "create_concept",
            anchor_id="a1",
            title="New",
            description="d",
            content_type="Simulation",
            position="start",
        )
        assert "set_concept_kb" in result["next"]
