"""Reel 3.0 scene images: regenerating them and repairing them.

Author splits an image change into transform (make a candidate) and accept
(commit it), and hands the candidate back as base64 in the response body. A
1536x1024 PNG is about a megabyte of base64, so returning it to the model would
bury the conversation in pixels. Both tools therefore do transform *and* accept
in one call and return only metadata plus the committed URL — the bytes never
leave this module.

The cost is that there is no preview step: a regenerate replaces the image. That
matches how the API behaves anyway, since regeneration is non-deterministic and
a discarded candidate cannot be recreated.

Images are addressed by an asset token like `{{ASSET:SC001:A2}}`. Those tokens
appear nowhere in the reel document — the only trace is an `ASSET_SC001_A2`
fragment inside each image URL — so `list_reel_images` derives them, and nothing
else here can be used without it.
"""

from __future__ import annotations

import base64
import re
from typing import Any

import httpx
from mcp.server.mcpserver import Context, MCPServer

from ..client import ArturoClient
from ..config import Settings
from ..errors import InvalidArgument, MissingPrerequisite, UpstreamUnavailable

# Matches the asset fragment Author embeds in generated image file names.
_ASSET_IN_URL = re.compile(r"ASSET_(SC\d+)_(A\d+)", re.I)


def token_for(url: str) -> str:
    """`…/ASSET_SC001_A2-regenerate-1-….png` -> `{{ASSET:SC001:A2}}`.

    Author accepts only the braced form; the raw fragment is rejected.
    """
    found = _ASSET_IN_URL.search(url or "")
    if not found:
        return ""
    return f"{{{{ASSET:{found.group(1).upper()}:{found.group(2).upper()}}}}}"


async def image_index(client: ArturoClient, reel_id: str) -> dict[str, dict[str, Any]]:
    """token -> {scene_id, url}, read from the current reel document.

    Also supplies `ai_image_url` for transform: Author rejects the call without
    it even though Arturo's own layer treats it as optional, and the model has no
    way to know that from the error.
    """
    document = await client.request("GET", f"/reels/{reel_id}/status")
    reel = document.get("reel") if isinstance(document.get("reel"), dict) else document
    index: dict[str, dict[str, Any]] = {}
    for scene in (reel or {}).get("scenes") or []:
        if not isinstance(scene, dict):
            continue
        images = scene.get("image")
        if not isinstance(images, list):
            continue
        for image in images:
            if not isinstance(image, dict):
                continue
            token = token_for(image.get("url", ""))
            if token and token not in index:
                index[token] = {
                    "scene_id": scene.get("scene_id"),
                    "url": image.get("url"),
                }
    return index


async def _download(url: str) -> bytes:
    try:
        async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as http:
            response = await http.get(url)
            response.raise_for_status()
            return response.content
    except httpx.HTTPError as exc:
        raise UpstreamUnavailable(f"could not download the image from blob storage ({exc})") from exc


def register(server: MCPServer, settings: Settings, client_for) -> None:
    async def _transform_and_accept(
        client: ArturoClient,
        reel_id: str,
        token: str,
        mode: str,
        prompt: str,
        reference_image_url: str = "",
    ) -> dict[str, Any]:
        index = await image_index(client, reel_id)
        if token not in index:
            raise InvalidArgument(
                f"No image {token} on this reel. Call list_reel_images for the valid "
                f"tokens — there are {len(index)}: {', '.join(sorted(index)[:8])}"
                + ("…" if len(index) > 8 else "")
            )
        current_url = index[token]["url"]

        if not prompt.strip():
            stored = await client.request(
                "GET", f"/reels/{reel_id}/assets/image/prompt", params={"token": token}
            )
            prompt = (stored or {}).get("prompt") or ""
            if not prompt.strip():
                raise MissingPrerequisite(
                    f"No prompt given and none stored for {token}. Pass a prompt describing "
                    "the image you want."
                )

        fields = {
            "token": token,
            "mode": mode,
            "prompt": prompt,
            # Author requires this even though Arturo's layer marks it optional;
            # without it the call fails with a bare "ai_image_url is required".
            "ai_image_url": reference_image_url.strip() or current_url,
        }
        candidate = await client.request(
            "POST",
            f"/reels/{reel_id}/assets/image/transform",
            data=fields,
            timeout=settings.long_timeout_seconds,
        )
        if not isinstance(candidate, dict) or not candidate.get("image_base64"):
            raise UpstreamUnavailable(
                "Author returned no image for the transform. This is a generation failure, "
                "not a rejected request — retry before changing the prompt."
            )

        # Commit. `accept` is multipart and wants the real bytes, so the base64
        # is decoded here and never surfaced.
        image_bytes = base64.b64decode(candidate["image_base64"])
        mime = candidate.get("mime_type") or "image/png"
        suffix = "png" if "png" in mime else "jpg"
        accepted = await client.request(
            "POST",
            f"/reels/{reel_id}/assets/image/accept",
            data={
                "token": token,
                "prompt": candidate.get("prompt") or prompt,
                "negative_prompt": candidate.get("negative_prompt") or "",
                # Required here too, for the same undocumented reason as transform.
                # Omitting it fails *after* the image has been generated, so the
                # credits are already spent by the time the error appears.
                "ai_image_url": fields["ai_image_url"],
            },
            files={"image": (f"{token.strip('{}').replace(':', '_')}.{suffix}", image_bytes, mime)},
            timeout=settings.long_timeout_seconds,
        )
        return {
            "committed": True,
            "reel_id": reel_id,
            "token": token,
            "scene_id": index[token]["scene_id"],
            "mode": mode,
            "size": [candidate.get("width"), candidate.get("height")],
            "provider": candidate.get("provider"),
            "prompt_used": candidate.get("prompt") or prompt,
            "previous_url": current_url,
            "new_url": (accepted or {}).get("image_url") if isinstance(accepted, dict) else None,
            "note": (
                "The image is replaced and the old one is gone. The reel document you may "
                "be holding is stale — re-read with get_reel3_status before save_reel."
            ),
        }

    @server.tool(
        description=(
            "List every scene image on a reel with the asset token needed to change it. "
            "Call this FIRST: tokens like {{ASSET:SC001:A2}} appear nowhere in the reel "
            "document, so regenerate_scene_image and repair_scene_image cannot be used "
            "without it. Returns one entry per image with its scene_id and current URL."
        )
    )
    async def list_reel_images(
        ctx: Context, learning_pitstop_id: str
    ) -> dict[str, Any]:
        client: ArturoClient = client_for(ctx)
        index = await image_index(client, learning_pitstop_id)
        return {
            "reel_id": learning_pitstop_id,
            "count": len(index),
            "images": [
                {"token": token, "scene_id": meta["scene_id"], "url": meta["url"]}
                for token, meta in sorted(index.items())
            ],
        }

    @server.tool(
        description=(
            "Read the stored generation prompt for one scene image. Useful before "
            "repair_scene_image, so the edit builds on the existing prompt instead of "
            "replacing the art direction wholesale. token comes from list_reel_images."
        )
    )
    async def get_scene_image_prompt(
        ctx: Context, learning_pitstop_id: str, token: str
    ) -> dict[str, Any]:
        if not token.strip():
            raise InvalidArgument("token is required — call list_reel_images for valid ones.")
        client: ArturoClient = client_for(ctx)
        return await client.request(
            "GET",
            f"/reels/{learning_pitstop_id}/assets/image/prompt",
            params={"token": token.strip()},
        )

    @server.tool(
        description=(
            "Generate a brand-new image for one scene from a prompt and COMMIT it. "
            "token comes from list_reel_images. Leave prompt empty to re-roll the existing "
            "art direction unchanged. REPLACES the current image irreversibly and costs "
            "generation credits; there is no preview, because generation is random and a "
            "discarded result cannot be recreated. Use repair_scene_image instead when the "
            "picture is nearly right and only needs a fix."
        )
    )
    async def regenerate_scene_image(
        ctx: Context, learning_pitstop_id: str, token: str, prompt: str = ""
    ) -> dict[str, Any]:
        if not token.strip():
            raise InvalidArgument("token is required — call list_reel_images for valid ones.")
        client: ArturoClient = client_for(ctx)
        return await _transform_and_accept(
            client, learning_pitstop_id, token.strip(), "regenerate", prompt
        )

    @server.tool(
        description=(
            "Repair an existing scene image: fix what is wrong while keeping the rest. The "
            "prompt should describe the correction ('remove the text on the wall', 'fix the "
            "hand'), not the whole picture. token comes from list_reel_images. Optionally "
            "pass reference_image_url to steer from a different image. REPLACES the current "
            "image irreversibly and costs generation credits."
        )
    )
    async def repair_scene_image(
        ctx: Context,
        learning_pitstop_id: str,
        token: str,
        prompt: str,
        reference_image_url: str = "",
    ) -> dict[str, Any]:
        if not token.strip():
            raise InvalidArgument("token is required — call list_reel_images for valid ones.")
        if not prompt.strip():
            raise InvalidArgument(
                "prompt is required for a repair — describe the correction to make."
            )
        client: ArturoClient = client_for(ctx)
        return await _transform_and_accept(
            client, learning_pitstop_id, token.strip(), "edit", prompt, reference_image_url
        )
