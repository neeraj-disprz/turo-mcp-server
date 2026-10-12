"""Reel 3.0 payload assembly.

`POST /api/reels/{id}/generate` requires eight non-empty strings plus a settings
block. Asking a model to produce all of that freehand fails validation often, so
the server derives everything it can and leaves the caller with title, script and
voice. Values mirror Arturo-Web's ContentGenerationModal.
"""

from __future__ import annotations

import json
import re
from typing import Any

from .config import Settings
from .errors import InvalidArgument

# UI culture context -> value the Author API expects.
CULTURE_CONTEXT_MAP = {
    "en-us": "global",
    "global": "global",
    "ind": "india",
    "india": "india",
    "id": "south-east-asia",
    "south-east-asia": "south-east-asia",
    "ar": "middle-east",
    "middle-east": "middle-east",
}

# UI culture context -> reel language code.
LANGUAGE_BY_CONTEXT = {
    "en-us": "en-us",
    "global": "en-us",
    "ind": "en-ind",
    "india": "en-ind",
    "ar": "en-ar",
    "middle-east": "en-ar",
    "id": "en-id",
    "south-east-asia": "en-id",
}

BRAND_VISUAL_BY_ART_STYLE = {
    "photoreal": "Use highly realistic photographic imagery with natural lighting and authentic environments.",
    "editorial-illustration": "Use premium, clean, editorial visuals with strong contrast.",
    "editorial-ink": "Use premium, clean, editorial ink visuals with strong contrast.",
    "soft-gouache": "Use warm, soft gouache-style illustrated visuals for engaging learning.",
    "flat-vector": "Use bold flat vector shapes and simple forms with clear hierarchy.",
    "isometric-3d": "Use dimensional isometric 3D scenes that clarify systems and processes.",
    "paper-craft": "Use layered paper-craft textures with a handmade feel.",
    "cinematic-render": "Use dramatic cinematic lighting and polished film-like renders.",
    "duotone-graphic": "Use high-contrast duotone graphic treatments with strong visual impact.",
}

ASPECT_RATIO_TO_CANVAS = {
    "1:1": "1080x1080",
    "3:4": "1080x1440",
    "4:5": "1080x1350",
    "5:4": "1350x1080",
    "9:16": "1080x1920",
    "16:9": "1920x1080",
    "21:9": "2560x1080",
}

DEFAULT_CREATIVE_GUIDANCE = (
    "Make the reel feel practical, cinematic, and visually memorable."
)

TEXT_RESPONSE = "Text Response"
VOICE_RESPONSE = "Voice Response"


def _scenes_of(script: Any) -> list[dict[str, Any]]:
    """The scene list, whichever shape the script service returned."""
    parsed = script
    if isinstance(parsed, str):
        try:
            parsed = json.loads(parsed)
        except ValueError:
            return []

    if isinstance(parsed, list):
        return [s for s in parsed if isinstance(s, dict)]
    if isinstance(parsed, dict):
        scenes = parsed.get("overall_scenes")
        if isinstance(scenes, dict):
            scenes = scenes.get("scenes")
        if isinstance(scenes, list):
            return [s for s in scenes if isinstance(s, dict)]
    return []


def flatten_script(script: Any) -> str:
    """Narration text from a generated script.

    Author treats the script as narration and parses `[...]` as ElevenLabs audio
    tags, so handing it the raw JSON fails with "nested '['". Mirrors the web
    app's getStructuredScriptPreviewText, including nested subScenes.
    """
    lines: list[str] = []

    def collect(scene: dict[str, Any]) -> None:
        narration = scene.get("narration")
        text = " ".join(str(n) for n in narration) if isinstance(narration, list) else str(narration or "")
        if text.strip():
            lines.append(text.strip())
        for sub in scene.get("subScenes") or []:
            if isinstance(sub, dict):
                collect(sub)

    scenes = _scenes_of(script)
    if not scenes:
        # Already plain text, or a shape we do not recognise — pass it through
        # rather than silently returning nothing.
        return script if isinstance(script, str) else ""

    for scene in scenes:
        collect(scene)
    return "\n\n".join(lines)


_HEX_COLOR = re.compile(r"^#?([0-9a-fA-F]{6}|[0-9a-fA-F]{3})$")

BRAND_COLOR_FIELDS = ("primary", "secondary", "on_primary")


def normalise_brand_colors(
    settings: Settings,
    primary: str = "",
    secondary: str = "",
    on_primary: str = "",
) -> dict[str, str]:
    """Per-reel brand colours, falling back field by field to the configured ones.

    Arturo *requires* all three on every generate call and never reads them from
    the tenant record, so an unspecified colour has to come from somewhere —
    which is why the fallback is per field rather than all-or-nothing.
    """
    supplied = {"primary": primary, "secondary": secondary, "on_primary": on_primary}
    resolved: dict[str, str] = {}
    for field in BRAND_COLOR_FIELDS:
        value = (supplied[field] or "").strip()
        if not value:
            resolved[field] = settings.brand_colors[field]
            continue
        if not _HEX_COLOR.match(value):
            raise InvalidArgument(
                f"brand colour {field} must be a hex value like #E82028, got {value!r}"
            )
        resolved[field] = value if value.startswith("#") else f"#{value}"
    return resolved


def build_generate_payload(
    *,
    settings: Settings,
    reel_title: str,
    script: str,
    voice_id: str,
    culture_context: str | None = None,
    art_style: str | None = None,
    aspect_ratio: str | None = None,
    video_frequency: str | None = None,
    creative_guidance: str | None = None,
    enable_voice_response: bool = False,
    enable_hand_raise: bool = False,
    brand_colors: dict[str, str] | None = None,
) -> dict[str, Any]:
    context = (culture_context or settings.culture_context).strip().lower()
    api_culture_context = CULTURE_CONTEXT_MAP.get(context, context)
    language = LANGUAGE_BY_CONTEXT.get(context, "en-us")

    style = (art_style or settings.art_style).strip().lower()
    ratio = (aspect_ratio or "16:9").strip()
    canvas = ASPECT_RATIO_TO_CANVAS.get(ratio)
    if canvas is None:
        raise InvalidArgument(
            f"Unsupported aspect_ratio {ratio!r}. Supported: "
            f"{', '.join(sorted(ASPECT_RATIO_TO_CANVAS))}"
        )

    frequency = (video_frequency or "medium").strip().lower()
    modes = [TEXT_RESPONSE, VOICE_RESPONSE] if enable_voice_response else [TEXT_RESPONSE]

    return {
        "reelTitle": reel_title.strip(),
        "script": script.strip(),
        "language": language,
        "cultureContext": api_culture_context,
        "voiceId": voice_id.strip(),
        "modelId": settings.model_id,
        "creativeGuidance": (creative_guidance or DEFAULT_CREATIVE_GUIDANCE).strip(),
        "brandVisualGuidance": BRAND_VISUAL_BY_ART_STYLE.get(
            style, BRAND_VISUAL_BY_ART_STYLE["soft-gouache"]
        ),
        "artStyle": style,
        "videoFrequency": frequency,
        "interactionModes": modes,
        "reelSettings": {
            "intro_bgmusic": settings.intro_bgmusic,
            "aspect_ratio": ratio,
            "target_canvas": canvas,
            "language": language,
            "art_style": style,
            "video_frequency": frequency,
            "enableHandRaise": bool(enable_hand_raise),
            # canvas is forwarded to Author untouched (unlike the top-level
            # aspect_ratio/target_canvas, which Arturo strips), and Author
            # rejects target_canvas inside it. Send aspect_ratio only, exactly
            # as the web app does.
            "canvas": {"aspect_ratio": ratio},
            "default_safe_area_pc": 4,
            "brand_colors": dict(brand_colors or settings.brand_colors),
        },
    }
