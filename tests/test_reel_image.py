"""Reel 3.0 scene images: token discovery, regeneration and repair."""

from __future__ import annotations

import base64
from urllib.parse import parse_qs

import httpx
import pytest
import respx

from turo_mcp.tools.reel_image import token_for

from .conftest import API, call, call_expecting_error

PNG = base64.b64encode(b"\x89PNG\r\n\x1a\nfake").decode()

REEL_STATUS = {
    "reel": {
        "settings": {},
        "scenes": [
            {
                "scene_id": "SC001",
                "image": [{"url": "https://blob/x/ASSET_SC001_A2-regenerate-1-abc.png"}],
            },
            {
                "scene_id": "SC002",
                "image": [
                    {"url": "https://blob/x/ASSET_SC002_A1-gen.png"},
                    {"url": "https://blob/x/ASSET_SC002_A3-gen.png"},
                ],
            },
        ],
    }
}

TRANSFORMED = {
    "status": "generated",
    "token": "{{ASSET:SC001:A2}}",
    "image_base64": PNG,
    "mime_type": "image/png",
    "width": 1536,
    "height": 1024,
    "prompt": "refined prompt",
    "negative_prompt": "text, watermark",
    "provider": "gpt-image-1.5",
}

ACCEPTED = {"status": "accepted", "image_url": "https://blob/x/new.png"}


def form(route, index: int = 0) -> dict[str, str]:
    """The form fields of a captured request, decoded.

    `transform` is sent form-encoded, so raw substring matching trips over `+`
    for spaces and percent-escapes in URLs.
    """
    body = route.calls[index].request.content.decode("utf-8", "replace")
    return {key: values[0] for key, values in parse_qs(body).items()}


class TestTokenDerivation:
    @pytest.mark.parametrize(
        "url,expected",
        [
            ("https://b/ASSET_SC001_A2-regenerate-1-x.png", "{{ASSET:SC001:A2}}"),
            ("https://b/regenerated/ASSET_SC010_A11_edited_123.png", "{{ASSET:SC010:A11}}"),
            ("https://b/asset_sc004_a3.png", "{{ASSET:SC004:A3}}"),
            ("https://b/no-token-here.png", ""),
            ("", ""),
        ],
    )
    def test_the_token_is_read_out_of_the_image_url(self, url, expected):
        # Tokens appear nowhere in the reel document; the URL is the only source.
        assert token_for(url) == expected


class TestListReelImages:
    @respx.mock
    async def test_every_image_is_listed_with_its_token(self, server):
        respx.get(f"{API}/reels/c1/status").mock(return_value=httpx.Response(200, json=REEL_STATUS))

        result = await call(server, "list_reel_images", learning_pitstop_id="c1")

        assert result["count"] == 3
        assert [i["token"] for i in result["images"]] == [
            "{{ASSET:SC001:A2}}",
            "{{ASSET:SC002:A1}}",
            "{{ASSET:SC002:A3}}",
        ]
        assert result["images"][0]["scene_id"] == "SC001"

    @respx.mock
    async def test_a_reel_with_no_images_lists_nothing_rather_than_failing(self, server):
        respx.get(f"{API}/reels/c1/status").mock(
            return_value=httpx.Response(200, json={"reel": {"scenes": [{"scene_id": "SC001"}]}})
        )
        result = await call(server, "list_reel_images", learning_pitstop_id="c1")
        assert result["count"] == 0


class TestRegenerateSceneImage:
    @respx.mock
    async def test_it_transforms_then_accepts_and_hides_the_bytes(self, server):
        respx.get(f"{API}/reels/c1/status").mock(return_value=httpx.Response(200, json=REEL_STATUS))
        transform = respx.post(f"{API}/reels/c1/assets/image/transform").mock(
            return_value=httpx.Response(200, json=TRANSFORMED)
        )
        accept = respx.post(f"{API}/reels/c1/assets/image/accept").mock(
            return_value=httpx.Response(200, json=ACCEPTED)
        )

        result = await call(
            server,
            "regenerate_scene_image",
            learning_pitstop_id="c1",
            token="{{ASSET:SC001:A2}}",
            prompt="a calm waiting room",
        )

        assert transform.called and accept.called
        sent = form(transform)
        assert sent["mode"] == "regenerate"
        assert sent["prompt"] == "a calm waiting room"
        # Author rejects BOTH calls without ai_image_url. Missing it on accept
        # fails only after the image is generated, so the credits are spent.
        assert sent["ai_image_url"] == "https://blob/x/ASSET_SC001_A2-regenerate-1-abc.png"
        assert b"ai_image_url" in accept.calls[0].request.content

        assert result["committed"] is True
        assert result["new_url"] == "https://blob/x/new.png"
        assert result["previous_url"] == "https://blob/x/ASSET_SC001_A2-regenerate-1-abc.png"
        # A megabyte of base64 must never reach the model.
        assert "image_base64" not in str(result)

    @respx.mock
    async def test_an_unknown_token_lists_the_real_ones(self, server):
        respx.get(f"{API}/reels/c1/status").mock(return_value=httpx.Response(200, json=REEL_STATUS))

        message = await call_expecting_error(
            server,
            "regenerate_scene_image",
            learning_pitstop_id="c1",
            token="{{ASSET:SC009:A9}}",
            prompt="x",
        )
        assert "list_reel_images" in message
        assert "{{ASSET:SC001:A2}}" in message

    @respx.mock
    async def test_an_empty_prompt_falls_back_to_the_stored_one(self, server):
        respx.get(f"{API}/reels/c1/status").mock(return_value=httpx.Response(200, json=REEL_STATUS))
        respx.get(f"{API}/reels/c1/assets/image/prompt").mock(
            return_value=httpx.Response(200, json={"prompt": "the stored art direction"})
        )
        transform = respx.post(f"{API}/reels/c1/assets/image/transform").mock(
            return_value=httpx.Response(200, json=TRANSFORMED)
        )
        respx.post(f"{API}/reels/c1/assets/image/accept").mock(
            return_value=httpx.Response(200, json=ACCEPTED)
        )

        await call(
            server, "regenerate_scene_image", learning_pitstop_id="c1", token="{{ASSET:SC001:A2}}"
        )

        assert form(transform)["prompt"] == "the stored art direction"

    @respx.mock
    async def test_a_generation_that_returns_no_image_says_it_is_transient(self, server):
        respx.get(f"{API}/reels/c1/status").mock(return_value=httpx.Response(200, json=REEL_STATUS))
        respx.post(f"{API}/reels/c1/assets/image/transform").mock(
            return_value=httpx.Response(200, json={"status": "failed"})
        )

        message = await call_expecting_error(
            server,
            "regenerate_scene_image",
            learning_pitstop_id="c1",
            token="{{ASSET:SC001:A2}}",
            prompt="x",
        )
        assert "retry" in message


class TestRepairSceneImage:
    @respx.mock
    async def test_repair_uses_edit_mode(self, server):
        respx.get(f"{API}/reels/c1/status").mock(return_value=httpx.Response(200, json=REEL_STATUS))
        transform = respx.post(f"{API}/reels/c1/assets/image/transform").mock(
            return_value=httpx.Response(200, json=TRANSFORMED)
        )
        respx.post(f"{API}/reels/c1/assets/image/accept").mock(
            return_value=httpx.Response(200, json=ACCEPTED)
        )

        result = await call(
            server,
            "repair_scene_image",
            learning_pitstop_id="c1",
            token="{{ASSET:SC001:A2}}",
            prompt="remove the text on the wall",
        )

        sent = form(transform)
        assert sent["mode"] == "edit"
        assert sent["prompt"] == "remove the text on the wall"
        assert result["mode"] == "edit"

    @respx.mock
    async def test_a_reference_url_overrides_the_current_image(self, server):
        respx.get(f"{API}/reels/c1/status").mock(return_value=httpx.Response(200, json=REEL_STATUS))
        transform = respx.post(f"{API}/reels/c1/assets/image/transform").mock(
            return_value=httpx.Response(200, json=TRANSFORMED)
        )
        respx.post(f"{API}/reels/c1/assets/image/accept").mock(
            return_value=httpx.Response(200, json=ACCEPTED)
        )

        await call(
            server,
            "repair_scene_image",
            learning_pitstop_id="c1",
            token="{{ASSET:SC001:A2}}",
            prompt="match this style",
            reference_image_url="https://example.com/ref.png",
        )

        assert form(transform)["ai_image_url"] == "https://example.com/ref.png"

    async def test_repair_demands_a_prompt(self, server):
        message = await call_expecting_error(
            server,
            "repair_scene_image",
            learning_pitstop_id="c1",
            token="{{ASSET:SC001:A2}}",
            prompt="   ",
        )
        assert "describe the correction" in message
