"""Resolving a simulation's real storage location.

A generated simulation does not live under the concept id. When it is generated
for any locale other than en-us/en-us, Arturo appends `-{lang}-{cult_context}`
to form the blob location, and every read and write must use that exact string.

This matters because the save endpoint does **not** validate it: saving to the
unsuffixed id returns HTTP 200 and writes nothing the reader will ever see.
Verified against a live activity — saving with the concept id did not stick,
saving with `<id>-en-us-ind` did. Every simulation tool resolves first.
"""

from __future__ import annotations

import re
from typing import Any

from .errors import RequestRejected

# Blob locations are a UUID optionally followed by a locale suffix.
_UUID_PREFIX = re.compile(
    r"^(?P<base>[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})"
)


def base_id(activity_id: str) -> str:
    """The concept/activity id with any locale suffix stripped."""
    match = _UUID_PREFIX.match(str(activity_id).strip())
    return match.group("base") if match else str(activity_id).strip()


async def resolve_blob_location(
    client: Any,
    activity_id: str,
    *,
    lang: str = "",
    cult_context: str = "",
) -> tuple[str, dict[str, Any]]:
    """Map any form of the id to the blob location the content actually lives at.

    Returns (blob_location, chosen_language_row). Falls back to the id as given
    when the activity has no language records, so a standalone simulation still
    works.
    """
    try:
        info = await client.request(
            "POST",
            "/active-learning/get-activity-languages/",
            json={"learning_pitstop_id": base_id(activity_id)},
        )
    except Exception:
        return str(activity_id).strip(), {}

    languages = [row for row in (info or {}).get("languages") or [] if row.get("blob_location")]
    if not languages:
        return str(activity_id).strip(), {}

    if lang or cult_context:
        for row in languages:
            if (not lang or row.get("lang") == lang) and (
                not cult_context or row.get("culture_context") == cult_context
            ):
                return row["blob_location"], row
        available = ", ".join(
            f"{r.get('lang')}/{r.get('culture_context')}" for r in languages
        )
        raise RequestRejected(
            f"No {lang or 'any'}/{cult_context or 'any'} version of this simulation. "
            f"Available: {available}."
        )

    # An exact match on what the caller passed wins, so a deliberate choice of
    # one locale is never silently redirected to another.
    for row in languages:
        if row["blob_location"] == str(activity_id).strip():
            return row["blob_location"], row

    return languages[0]["blob_location"], languages[0]
