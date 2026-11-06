"""The voice catalog shared by Reel 3.0 and Single-Turn Simulation.

Both surfaces draw from the same ElevenLabs collection, served by
`GET /reels/voices`. Simulations used to take one of five built-in names
(adam, alice, charlie, arnold, rachel); the authoring UI retired those in
October 2026 when it moved its voice cast onto the collection, so a bare name no
longer identifies a voice anywhere in the product. Arturo itself validates
nothing — it forwards whatever it is given to the content service — which makes
a retired name fail silently downstream rather than loudly here.
"""

from __future__ import annotations

import re
from typing import Any

from .client import ArturoClient
from .errors import InvalidArgument

# The authoring UI's own test for a usable voice (isElevenLabsVoiceId). No
# retired name and no legacy alias ("en-us-m-adam") can match it.
_VOICE_ID = re.compile(r"^[A-Za-z0-9]{10,}$")

RETIRED_VOICE_NAMES = ("adam", "alice", "charlie", "arnold", "rachel")


def is_voice_id(value: str) -> bool:
    return bool(_VOICE_ID.match(str(value or "").strip()))


def voice_gender(voice: dict[str, Any]) -> str:
    """"male", "female", or "" when the catalog does not say."""
    labels = voice.get("labels") if isinstance(voice.get("labels"), dict) else {}
    raw = str(voice.get("gender") or labels.get("gender") or "").strip().lower()
    if raw in ("m", "male"):
        return "male"
    if raw in ("f", "female"):
        return "female"
    return ""


async def fetch_voices(client: ArturoClient, search: str = "") -> list[dict[str, Any]]:
    params = {"search": search} if search else None
    body = await client.request("GET", "/reels/voices", params=params)
    voices = (body or {}).get("voices")
    if not isinstance(voices, list):
        return []
    return [v for v in voices if isinstance(v, dict) and str(v.get("id") or "").strip()]


def cast_voices(
    voices: list[dict[str, Any]],
    genders: tuple[str, str, str],
    preset: tuple[str, str, str],
) -> list[str]:
    """Fill the empty slots of `preset` with distinct catalog voices.

    Each slot prefers a voice matching that speaker's gender and falls back to
    any unused one, mirroring the UI's getDefaultSimulationVoice. The narrator
    has no gender of its own, so it passes "" and takes whatever is left.
    """
    cast = list(preset)
    used = {value for value in cast if value}
    for index, gender in enumerate(genders):
        if cast[index]:
            continue
        free = [v for v in voices if v["id"] not in used]
        if not free:
            raise InvalidArgument(
                "Arturo does not offer enough distinct voices to cast two characters "
                "and a narrator. Check list_reel_voices."
            )
        want = str(gender or "").strip().lower()
        match = next((v for v in free if voice_gender(v) == want), None) if want else None
        chosen = (match or free[0])["id"]
        cast[index] = chosen
        used.add(chosen)
    return cast


def validate_cast(cast: list[str], names: tuple[str, ...]) -> None:
    """Reject anything the content service cannot turn into audio."""
    for name, value in zip(names, cast):
        if is_voice_id(value):
            continue
        retired = (
            " The five built-in names were retired when the voice cast moved to the "
            "shared catalog."
            if str(value).strip().lower() in RETIRED_VOICE_NAMES
            else ""
        )
        raise InvalidArgument(
            f"{name}={value!r} is not a voice id. Simulations take the id of a voice "
            f"from list_reel_voices, such as 'pNInz6obpgDQGcFmaJgB'.{retired}"
        )
    if len(set(cast)) != len(cast):
        raise InvalidArgument(
            "The two characters and the narrator each need a different voice, but "
            f"{', '.join(names)} came to {cast}."
        )
