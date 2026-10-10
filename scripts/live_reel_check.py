"""Read-only probe: can a real reel round-trip through save_reel?

`save_reel` PUTs whatever `get_reel3_status` returned. That only works if the
status payload actually carries the reel document. Nothing here writes — it
reads one real reel and reports whether the shapes line up, so the answer comes
from live data instead of an assumption.

Run from the repo root with a valid ARTURO_SESSION in .env:
    .venv/Scripts/python.exe scripts/live_reel_check.py <learning_pathway_id>
"""

from __future__ import annotations

import asyncio
import json
import sys

from turo_mcp.config import Settings
from turo_mcp.server import build_server


async def call(server, name: str, **arguments):
    result = await server.call_tool(name, arguments)
    return result.structured_content


def summarise(value, depth: int = 0) -> str:
    """Key names and types only — never values, which carry tenant content."""
    if isinstance(value, dict):
        if depth >= 2:
            return f"{{{len(value)} keys}}"
        return (
            "{"
            + ", ".join(f"{k}: {summarise(v, depth + 1)}" for k, v in list(value.items())[:12])
            + ("…" if len(value) > 12 else "")
            + "}"
        )
    if isinstance(value, list):
        inner = summarise(value[0], depth + 1) if value else ""
        return f"[{len(value)} × {inner}]" if value else "[]"
    return type(value).__name__


async def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    pathway_id = sys.argv[1]

    settings = Settings.from_env()
    if not settings.session_cookie:
        print("No ARTURO_SESSION in .env — add one and re-run.")
        return 2

    server = build_server(settings)

    who = await call(server, "whoami")
    print(f"session OK: {who.get('email', '?')} ({who.get('role', '?')})\n")

    pathway = await call(server, "get_pathway", learning_pathway_id=pathway_id)
    print(f"pathway: {pathway.get('pathway_details', {}).get('pathway_title', '?')}")
    print(f"url: {pathway.get('url')}\n")

    # Find a concept that actually has a generated reel to inspect.
    candidates = []
    for chapter in pathway.get("learning_pathway", {}).get("chapters", []):
        for concept in chapter.get("concepts", []):
            candidates.append(concept)

    generated = [c for c in candidates if c.get("is_content_generated")]
    print(f"concepts: {len(candidates)}, with generated content: {len(generated)}")
    for c in generated[:10]:
        print(
            f"  - {c.get('concept_title', '?')[:50]:52} "
            f"id={c.get('learning_pitstop_id')} type={c.get('content_type')}"
        )
    if not generated:
        print("\nNo generated reel in this pathway — nothing to inspect.")
        return 1

    target = generated[0]
    reel_id = target.get("learning_pitstop_id")
    print(f"\ninspecting reel: {target.get('concept_title')} ({reel_id})\n")

    status = await call(server, "get_reel3_status", learning_pitstop_id=reel_id)
    print("get_reel3_status shape:")
    print(" ", summarise(status))

    # The question that decides the design.
    inner = status.get("reel") if isinstance(status.get("reel"), dict) else status
    has_settings = isinstance(inner, dict) and "settings" in inner
    has_scenes = isinstance(inner, dict) and "scenes" in inner
    print(f"\n  carries 'settings': {has_settings}")
    print(f"  carries 'scenes':   {has_scenes}")

    if has_scenes:
        scenes = inner["scenes"]
        print(f"  scene count: {len(scenes)}")
        if scenes:
            print(f"  first scene keys: {sorted(scenes[0])[:15]}")
            print(f"  scene id field present: {'id' in scenes[0]}")

    verdict = (
        "ROUND-TRIP OK — save_reel can take get_reel3_status output directly"
        if (has_settings or has_scenes)
        else "NO ROUND-TRIP — status does not carry the reel document; save_reel needs another read"
    )
    print(f"\n{verdict}")

    with open("scripts/.live-reel-shape.json", "w", encoding="utf-8") as fh:
        json.dump({"status_keys": sorted(status) if isinstance(status, dict) else None}, fh, indent=2)
    print("(top-level key names written to scripts/.live-reel-shape.json)")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
