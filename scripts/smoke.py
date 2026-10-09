"""Live smoke check against a real Arturo instance.

The automated suite mocks Arturo at the HTTP boundary, so request and response
shapes are only as right as the reading of the Django source they were derived
from. This script is the bridge: it exercises the read-only tools against the
real API and reports what actually came back.

It performs no writes and starts no generation.

    export ARTURO_API_BASE_URL=https://turo-api.example.com
    export ARTURO_SESSION=<arturo_session cookie value>
    python scripts/smoke.py

The cookie is read from the environment and never printed.
"""

from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from turo_mcp.config import Settings  # noqa: E402
from turo_mcp.errors import TuroMcpError  # noqa: E402
from turo_mcp.server import build_server  # noqa: E402

PASS, FAIL, SKIP = "PASS", "FAIL", "SKIP"


def report(step: str, outcome: str, detail: str = "") -> None:
    print(f"  [{outcome:4}] {step}{f' — {detail}' if detail else ''}")


async def main() -> int:
    # .env is the normal place for these; load it before checking.
    from turo_mcp.config import _load_env_file

    _load_env_file()
    if not os.environ.get("ARTURO_API_BASE_URL"):
        print("ARTURO_API_BASE_URL is not set.")
        return 2
    if not os.environ.get("ARTURO_SESSION"):
        print("ARTURO_SESSION is not set. Paste the arturo_session cookie value into it.")
        return 2

    settings = Settings.from_env()
    server = build_server(settings)
    print(f"\nSmoke check against {settings.base_url}\n")

    failures = 0

    async def run(step: str, tool: str, **args):
        nonlocal failures
        try:
            result = await server.call_tool(tool, args)
            return result.structured_content
        except TuroMcpError as exc:
            report(step, FAIL, str(exc))
            failures += 1
        except Exception as exc:  # noqa: BLE001 - surface the cause, whatever it is
            report(step, FAIL, f"{type(exc).__name__}: {exc.__cause__ or exc}")
            failures += 1
        return None

    # 1. Session is live.
    me = await run("whoami", "whoami")
    if me is None:
        print("\nSession check failed — nothing else will work. Refresh the cookie.\n")
        return 1
    report("whoami", PASS, f'{me.get("email", "?")} ({me.get("role", "?")})')

    # 2. Workspace is readable.
    listing = await run("list_pathways", "list_pathways", limit=5)
    pathway_id = None
    if listing is not None:
        results = listing.get("results") or []
        report("list_pathways", PASS, f"{len(results)} pathway(s) returned")
        for row in results:
            candidate = row.get("pathway_id") or row.get("learning_pathway_id") or row.get("id")
            if candidate:
                pathway_id = candidate
                break
        if not results:
            print(
                "\n  Note: the workspace is empty, so the pathway-shaped checks below "
                "cannot run. Create a pathway in the UI and re-run.\n"
            )

    # 3. Pathway tree matches what the tools expect.
    concept_id = None
    if pathway_id:
        pathway = await run("get_pathway", "get_pathway", learning_pathway_id=pathway_id)
        if pathway is not None:
            chapters = (pathway.get("learning_pathway") or {}).get("chapters") or []
            concepts = [c for ch in chapters for c in (ch.get("concepts") or [])]
            report("get_pathway", PASS, f"{len(chapters)} chapter(s), {len(concepts)} concept(s)")
            reel_concepts = [c for c in concepts if c.get("content_type") == "Reel 3.0"]
            concept_id = (reel_concepts or concepts or [{}])[0].get("learning_pitstop_id")
    else:
        report("get_pathway", SKIP, "no pathway id available")

    # 4. Reel 3.0 read path.
    if concept_id:
        kb = await run("get_concept_kb", "get_concept_kb", learning_pitstop_id=concept_id)
        if kb is not None:
            content = kb.get("kb_content") or ""
            report("get_concept_kb", PASS, f"{len(str(content))} chars of KB")

        script = await run("get_reel_script", "get_reel_script", learning_pitstop_id=concept_id)
        if script is not None:
            report("get_reel_script", PASS, f"status={script.get('status')}")
    else:
        report("get_concept_kb", SKIP, "no concept id available")
        report("get_reel_script", SKIP, "no concept id available")

    # 5. Voice catalogue — the one Reel 3.0 dependency with an external service behind it.
    voices = await run("list_reel_voices", "list_reel_voices")
    if voices is not None:
        catalogue = voices.get("voices") or []
        report("list_reel_voices", PASS, f"{len(catalogue)} voice(s)")
        if catalogue:
            first = catalogue[0]
            print(f"         sample voice: {first.get('name')} ({first.get('id')})")

    print(f"\n{'All checks passed.' if not failures else f'{failures} check(s) failed.'}\n")
    if not failures:
        print("Read paths verified. Writes and generation are untested by design —")
        print("exercise those on a throwaway pathway.\n")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
