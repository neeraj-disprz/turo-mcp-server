"""End-to-end live test of save_reel / regenerate_scene_narration.

Edits one scene's narration, saves, re-reads to confirm it stuck, then puts the
original text back. The reel is left exactly as it was found.

    PYTHONPATH=. python scripts/live_reel_edit_test.py <reel_id> [--audio]

--audio additionally re-records that scene using its ORIGINAL narration, so the
rendered audio is unchanged in content while still exercising the async path.
"""

from __future__ import annotations

import asyncio
import sys

from turo_mcp.config import Settings
from turo_mcp.server import build_server

MARKER = " [mcp-edit-test]"


async def call(server, name: str, **arguments):
    return (await server.call_tool(name, arguments)).structured_content


def first_scene(document):
    reel = document.get("reel") if isinstance(document.get("reel"), dict) else document
    scenes = reel.get("scenes") or []
    if not scenes:
        raise SystemExit("reel has no scenes")
    return reel, scenes[0]


async def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    reel_id = sys.argv[1]
    do_audio = "--audio" in sys.argv

    server = build_server(Settings.from_env())

    print("1. reading reel")
    document = await call(server, "get_reel3_status", learning_pitstop_id=reel_id)
    reel, scene = first_scene(document)
    scene_id = scene.get("scene_id")
    original = scene.get("narration_text") or ""
    print(f"   scene_id={scene_id}")
    print(f"   narration starts: {original[:70]!r}")
    print(f"   settings keys: {sorted(reel.get('settings', {}))}")

    if not scene_id:
        raise SystemExit("scene has no scene_id — regenerate_scene_narration cannot target it")

    print("\n2. editing narration and saving")
    scene["narration_text"] = original + MARKER
    saved = await call(server, "save_reel", learning_pitstop_id=reel_id, reel=document)
    print(f"   saved={saved.get('saved')} response={str(saved.get('response'))[:120]}")

    print("\n3. re-reading to confirm the edit persisted")
    after = await call(server, "get_reel3_status", learning_pitstop_id=reel_id)
    _, after_scene = first_scene(after)
    stuck = (after_scene.get("narration_text") or "").endswith(MARKER)
    print(f"   edit persisted: {stuck}")
    if not stuck:
        print(f"   got: {(after_scene.get('narration_text') or '')[:90]!r}")

    print("\n4. restoring the original text")
    after_scene["narration_text"] = original
    await call(server, "save_reel", learning_pitstop_id=reel_id, reel=after)
    restored = await call(server, "get_reel3_status", learning_pitstop_id=reel_id)
    _, restored_scene = first_scene(restored)
    ok = (restored_scene.get("narration_text") or "") == original
    print(f"   restored cleanly: {ok}")

    if do_audio:
        settings_block = reel.get("settings", {})
        voice_id = (
            settings_block.get("voice_id")
            or settings_block.get("voiceId")
            or settings_block.get("voice")
        )
        print(f"\n5. re-recording scene audio (voice_id={voice_id})")
        if not voice_id:
            print("   no voice id in settings — skipping")
        else:
            done = await call(
                server,
                "regenerate_scene_narration",
                learning_pitstop_id=reel_id,
                scene_id=scene_id,
                script=original,
                voice_id=voice_id,
            )
            # Synchronous: the updated reel comes back directly, no polling.
            print(f"   recorded={done.get('recorded')}")
            returned = done.get("reel") or {}
            inner = returned.get("reel") if isinstance(returned.get("reel"), dict) else returned
            scenes = inner.get("scenes") if isinstance(inner, dict) else None
            print(f"   returned a reel document: {isinstance(scenes, list)}")
            if isinstance(scenes, list) and scenes:
                print(f"   scenes returned: {len(scenes)}")
                print(f"   scene 1 audio field: {str(scenes[0].get('audio'))[:90]}")

    print("\nreel left as found" if ok else "\nWARNING: reel may not be restored — check it")
    return 0 if (stuck and ok) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
