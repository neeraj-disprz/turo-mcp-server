"""The Reel 3.0 generate payload must satisfy Arturo's validator on the first try.

Field lists here are copied from api/views/reel_v3_views.py; if that validator
changes these tests are the thing that should break.
"""

from __future__ import annotations

import pytest

from turo_mcp.errors import InvalidArgument
from turo_mcp.reel3_defaults import build_generate_payload

# REEL3_STRING_FIELDS — each must be a non-empty string or the API returns 400.
REQUIRED_STRINGS = (
    "reelTitle",
    "script",
    "language",
    "cultureContext",
    "creativeGuidance",
    "brandVisualGuidance",
    "artStyle",
    "videoFrequency",
)
# Resolved from reelSettings; all must be truthy.
REQUIRED_SETTINGS = ("aspect_ratio", "target_canvas", "language", "art_style", "video_frequency")
REQUIRED_BRAND_COLORS = ("primary", "secondary", "on_primary")
# Arturo rejects the request outright if any of these appear in the body.
FORBIDDEN_IDENTIFIERS = ("tenant", "reel_id", "reelId")


@pytest.fixture
def payload(settings):
    return build_generate_payload(
        settings=settings,
        reel_title="Handling objections",
        script="Scene one. Scene two.",
        voice_id="GzE4TcXfh9rYCU9gVgPp",
    )


def test_every_required_string_is_present_and_non_empty(payload):
    for field in REQUIRED_STRINGS:
        assert isinstance(payload.get(field), str) and payload[field].strip(), field


def test_reel_settings_satisfy_the_validator(payload):
    settings_block = payload["reelSettings"]
    for field in REQUIRED_SETTINGS:
        assert settings_block.get(field), field
    for colour in REQUIRED_BRAND_COLORS:
        assert settings_block["brand_colors"].get(colour, "").startswith("#"), colour


def test_identifiers_are_never_sent_in_the_body(payload):
    # Tenant comes from the session and reel id from the URL; sending either is a 400.
    for field in FORBIDDEN_IDENTIFIERS:
        assert field not in payload


def test_defaults_match_the_web_app(payload, settings):
    assert payload["modelId"] == "eleven_v3"
    assert payload["artStyle"] == "soft-gouache"
    assert payload["reelSettings"]["intro_bgmusic"] == "cinematic-corporate-bed.mp3"
    assert payload["reelSettings"]["brand_colors"]["primary"] == "#E82028"


@pytest.mark.parametrize(
    ("context", "expected_culture", "expected_language"),
    [
        ("ind", "india", "en-ind"),
        ("en-us", "global", "en-us"),
        ("ar", "middle-east", "en-ar"),
        ("id", "south-east-asia", "en-id"),
    ],
)
def test_culture_context_maps_to_author_values(settings, context, expected_culture, expected_language):
    payload = build_generate_payload(
        settings=settings, reel_title="t", script="s", voice_id="v", culture_context=context
    )
    assert payload["cultureContext"] == expected_culture
    assert payload["language"] == expected_language
    # language must agree in both places or the Author pipeline mismatches.
    assert payload["reelSettings"]["language"] == expected_language


@pytest.mark.parametrize(
    ("ratio", "canvas"), [("16:9", "1920x1080"), ("9:16", "1080x1920"), ("1:1", "1080x1080")]
)
def test_canvas_is_derived_from_aspect_ratio(settings, ratio, canvas):
    payload = build_generate_payload(
        settings=settings, reel_title="t", script="s", voice_id="v", aspect_ratio=ratio
    )
    assert payload["reelSettings"]["target_canvas"] == canvas
    assert payload["reelSettings"]["canvas"]["aspect_ratio"] == ratio


def test_unsupported_aspect_ratio_lists_the_valid_options(settings):
    with pytest.raises(InvalidArgument) as exc:
        build_generate_payload(
            settings=settings, reel_title="t", script="s", voice_id="v", aspect_ratio="3:2"
        )
    assert "16:9" in str(exc.value)


def test_text_response_is_the_default_interaction_mode(payload):
    assert payload["interactionModes"] == ["Text Response"]


def test_voice_response_is_additive_not_exclusive(settings):
    payload = build_generate_payload(
        settings=settings,
        reel_title="t",
        script="s",
        voice_id="v",
        enable_voice_response=True,
    )
    assert payload["interactionModes"] == ["Text Response", "Voice Response"]


def test_brand_visual_guidance_follows_art_style(settings):
    payload = build_generate_payload(
        settings=settings, reel_title="t", script="s", voice_id="v", art_style="photoreal"
    )
    assert "photographic" in payload["brandVisualGuidance"]


def test_unknown_art_style_still_produces_usable_guidance(settings):
    # An invented art style must not yield an empty string and fail validation.
    payload = build_generate_payload(
        settings=settings, reel_title="t", script="s", voice_id="v", art_style="hand-drawn-napkin"
    )
    assert payload["brandVisualGuidance"].strip()


class TestCanvasShape:
    """Author rejects extra keys inside reelSettings.canvas.

    Arturo strips the top-level aspect_ratio/target_canvas before forwarding,
    but `canvas` is passed through verbatim — so target_canvas inside it reaches
    Author and fails with "reelSettings.canvas contains unsupported properties".
    """

    def test_canvas_carries_aspect_ratio_only(self, payload):
        assert payload["reelSettings"]["canvas"] == {"aspect_ratio": "16:9"}

    def test_target_canvas_still_present_at_the_top_level(self, payload):
        # Arturo's own validator reads it there, then strips it.
        assert payload["reelSettings"]["target_canvas"] == "1920x1080"


class TestScriptFlattening:
    def test_narration_is_joined_and_subscenes_included(self):
        from turo_mcp.reel3_defaults import flatten_script

        script = {
            "overall_scenes": [
                {
                    "sceneId": 1,
                    "narration": ["[thoughtful] Fraud starts quietly.", "Watch the report."],
                    "subScenes": [{"narration": "A nested line."}],
                }
            ]
        }
        assert flatten_script(script) == (
            "[thoughtful] Fraud starts quietly. Watch the report.\n\nA nested line."
        )

    def test_a_json_string_is_parsed_before_flattening(self):
        import json as _json

        from turo_mcp.reel3_defaults import flatten_script

        assert flatten_script(_json.dumps({"overall_scenes": [{"narration": "One."}]})) == "One."

    def test_the_nested_scenes_shape_is_handled(self):
        from turo_mcp.reel3_defaults import flatten_script

        assert flatten_script({"overall_scenes": {"scenes": [{"narration": "Two."}]}}) == "Two."

    def test_plain_text_passes_straight_through(self):
        from turo_mcp.reel3_defaults import flatten_script

        assert flatten_script("Already narration.") == "Already narration."
