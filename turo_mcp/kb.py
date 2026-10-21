"""Writing a concept's knowledge base.

Shared by reel and simulation generation, both of which store the source
material on the concept before rendering.

Arturo wraps every write in a document header — heading, pitstop id, original
pitstop, description, timestamp — and `get_concept_kb` returns that wrapper as
part of the text. So the obvious edit loop (read, change, write) nests a fresh
wrapper on each pass and the KB grows a few hundred characters every time. The
wrapper is stripped here so pushing the same content twice is a no-op rather
than an accumulation.
"""

from __future__ import annotations

import re
from typing import Any

from .client import ArturoClient

# The heading Arturo's own writer puts at the top of every KB document.
_WRAPPER_HEADING = "Updated Knowledge Base Content"
# Its last metadata line, after which the real content begins.
_WRAPPER_END = re.compile(r"^Updated:\s*\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}\s*$")
# Header is heading + 4 metadata lines; allow a little slack for format drift.
_WRAPPER_MAX_META_LINES = 6
# Guard against pathological input while still unwinding real nesting.
_WRAPPER_MAX_NESTING = 20


def strip_kb_wrapper(text: str) -> str:
    """Remove one layer of Arturo's KB document header, if present.

    The real header is blank-line separated — heading, blank, pitstop id, blank,
    original pitstop, blank, description, blank, `Updated: <timestamp>` — so the
    terminator sits around line 8, not line 4. Scanning by non-blank metadata
    lines rather than raw line count keeps that from mattering.

    Deliberately conservative: it only strips the header this server's own
    writes produce, and only when the heading is the first non-blank line. The
    generation path uses a different format ("Knowledge Base for: …") which is
    left alone, since that is authored structure rather than our boilerplate.
    """
    if not text:
        return text
    lines = text.splitlines()
    first = next((i for i, line in enumerate(lines) if line.strip()), None)
    if first is None or lines[first].strip() != _WRAPPER_HEADING:
        return text

    seen = 0
    for index in range(first + 1, len(lines)):
        stripped = lines[index].strip()
        if not stripped:
            continue
        if _WRAPPER_END.match(stripped):
            rest = lines[index + 1 :]
            while rest and not rest[0].strip():
                rest.pop(0)
            return "\n".join(rest)
        seen += 1
        if seen > _WRAPPER_MAX_META_LINES:
            break
    # Heading present but no terminator within the metadata block — leave it
    # alone rather than guess where content starts and risk truncating text.
    return text


def strip_all_kb_wrappers(text: str) -> str:
    """Peel every nested header layer.

    A read-change-write loop with the old un-stripping push nests one layer per
    pass, so content in the wild can be several deep. Stripping to the core
    means a push stores exactly one wrapper however many it arrived with.
    """
    previous = text
    for _ in range(_WRAPPER_MAX_NESTING):
        peeled = strip_kb_wrapper(previous)
        if peeled == previous:
            return peeled
        previous = peeled
    return previous


async def push_concept_kb(
    client: ArturoClient, learning_pitstop_id: str, kb_content: str
) -> dict[str, Any]:
    """Store `kb_content` as the concept's knowledge base.

    NOTE: this does not mark the concept as having a KB. Arturo sets
    `is_knowledge_base_generated` only inside its own generation paths and
    exposes no endpoint to set it, so the authoring UI will still offer to
    "generate" a knowledge base for this concept — and accepting that replaces
    what was written here. That is an Arturo-side gap, not something this
    server can close.
    """
    return await client.request(
        "POST",
        "/kb/text/update/",
        json={
            "learning_pitstop_id": learning_pitstop_id,
            "text_content": strip_all_kb_wrappers(kb_content).strip(),
        },
    )
