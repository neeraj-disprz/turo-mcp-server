"""Stripping Arturo's KB document header.

Every write wraps the text in a header; `get_concept_kb` returns that wrapper
as part of the content. Without stripping it, read-change-write nests a fresh
wrapper per pass and the KB grows ~350 characters each time — observed live,
50,724 -> 51,071 -> 51,418 on successive round trips.

The fixture below is the real layout captured from the API: BLANK-LINE
separated, so `Updated:` lands around line 8. An earlier version of this file
used consecutive lines, which passed while the live behaviour was still broken.
"""

from __future__ import annotations

from turo_mcp.kb import strip_all_kb_wrappers, strip_kb_wrapper

CONTENT = "The real content starts here.\n\nAnd continues here."


def wrap(body: str, pitstop: str = "The Store Needs a New Growth Engine") -> str:
    """Reproduce Arturo's header exactly as the live API returns it."""
    return (
        "Updated Knowledge Base Content\n\n"
        "Learning Pitstop ID: 058e4eb8-45fc-4cdb-a902-896c90a6b232\n\n"
        f"Original Pitstop: {pitstop}\n\n"
        "Description: A strategic thinking scenario\n\n"
        "Updated: 2026-10-21 07:58:11\n\n" + body
    )


def test_the_header_is_removed_and_the_content_kept():
    assert strip_kb_wrapper(wrap(CONTENT)) == CONTENT


def test_stripping_is_idempotent():
    once = strip_kb_wrapper(wrap(CONTENT))
    assert strip_kb_wrapper(once) == once


def test_one_call_peels_exactly_one_layer():
    assert strip_kb_wrapper(wrap(wrap(CONTENT))) == wrap(CONTENT)


def test_strip_all_peels_every_layer():
    # Content in the wild is several deep, from writes made before stripping
    # existed. A push must normalise however many it arrives with.
    deep = wrap(wrap(wrap(wrap(CONTENT))))
    assert strip_all_kb_wrappers(deep) == CONTENT


def test_strip_all_is_stable_on_clean_content():
    assert strip_all_kb_wrappers(CONTENT) == CONTENT


def test_the_generation_formats_own_header_is_left_alone():
    # "Knowledge Base for:" is Arturo's generation output, not our boilerplate.
    text = "Knowledge Base for: The current model is plateauing\n\nBody."
    assert strip_kb_wrapper(text) == text
    assert strip_all_kb_wrappers(text) == text


def test_a_heading_without_a_terminator_is_left_alone():
    # Better to keep boilerplate than guess where content starts and truncate.
    text = "Updated Knowledge Base Content\n\nSomething unexpected\n\nMore text"
    assert strip_kb_wrapper(text) == text


def test_empty_input_survives():
    assert strip_kb_wrapper("") == ""
    assert strip_all_kb_wrappers("") == ""


def test_the_heading_must_be_first_to_count():
    text = f"Real content.\n\n{wrap(CONTENT)}"
    assert strip_kb_wrapper(text) == text
