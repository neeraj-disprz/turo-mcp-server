"""Turo MCP server.

Stateless by design: the Arturo session cookie arrives on each request via the
`X-Arturo-Session` connection header and is never stored, never logged and never
exposed as a tool argument (which would put it in the model's context).
"""

from __future__ import annotations

import argparse
import logging
import os

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.transport_security import TransportSecuritySettings

from .client import ArturoClient, headers_from, resolve_session_cookie
from .config import Settings
from .tools import discovery, pathway, reel3, reel_edit, simulation, simulation_edit

INSTRUCTIONS = """\
Tools for authoring Turo learning content: editing pathways, generating Reel 3.0
videos and generating single-turn simulations.

Rules that matter:
- Call `whoami` once at the start. If the session is dead, everything else fails.
- If any tool reports the session expired, call `set_session` with a valid
  arturo_session cookie. Reuse one already given in this conversation before
  asking the user — the server forgets the cookie when it restarts, and silently
  re-sending a still-valid one is the expected recovery.
- Call `get_pathway` before editing so you act on current ids and generation state.
- Generation is asynchronous. Start it, then poll the matching status tool every
  10-15 seconds. Never assume a start call means the content is ready.
- Reel 3.0 has a strict order: concept -> knowledge base -> script -> reel. Check
  `get_concept_kb`, run `generate_reel_script`, review via `get_reel_script`, then
  `generate_reel3`.
- Regenerating overwrites previous output and costs real generation credits.
  Check status before retrying.
- Deletions and `reset_contents` destroy generated content. Confirm with the user
  before passing confirm=true.
- A hand-created concept has NO knowledge base (Turo derives KBs from the uploaded
  source document). Script generation fails without one — call `set_concept_kb` first.
- Simulations and reels can both be edited after generation. Each save REPLACES the
  whole document, so always read first and send the full object back: `get_simulation`
  before `save_simulation`, `get_reel3_status` before `save_reel`.
- Editing narration text never re-records audio. After changing spoken words in a
  reel, call `regenerate_scene_narration` for that scene and poll
  `get_reel_audio_status`, or the video keeps saying the old words.
- This server cannot publish. That is done in the Turo UI.
"""


def build_server(settings: Settings | None = None) -> MCPServer:
    settings = settings or Settings.from_env()
    server = MCPServer(name="turo", version="0.1.0", instructions=INSTRUCTIONS)

    def client_for(ctx: Context) -> ArturoClient:
        cookie = resolve_session_cookie(headers_from(ctx), settings)
        return ArturoClient(settings, cookie)

    for module in (discovery, pathway, reel3, reel_edit, simulation, simulation_edit):
        module.register(server, settings, client_for)

    return server


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Turo MCP server")
    parser.add_argument(
        "--transport",
        choices=("stdio", "streamable-http"),
        default=os.environ.get("MCP_TRANSPORT", "stdio"),
    )
    parser.add_argument("--host", default=os.environ.get("MCP_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("MCP_PORT", "8080")))
    parser.add_argument(
        "--path",
        default=os.environ.get("MCP_PATH", "/mcp"),
        help=(
            "Mount path. When the server holds a fallback ARTURO_SESSION, give this a "
            "random suffix (e.g. /mcp/9f2c...) so the endpoint is not guessable — the "
            "stored cookie is otherwise reachable by anyone who finds the URL."
        ),
    )
    args = parser.parse_args()
    # Hosts that generate a random value for MCP_PATH emit a bare token, but
    # Starlette needs a rooted path.
    args.path = "/" + args.path.strip().strip("/")

    # httpx logs every request at INFO. On stdio that floods the client's error
    # pane with one line per tool call, and the URLs are not useful there.
    logging.getLogger("httpx").setLevel(logging.WARNING)

    server = build_server()
    if args.transport == "stdio":
        server.run("stdio")
    else:
        # stateless_http keeps every request self-contained, which matches the
        # per-request credential model and makes horizontal scaling trivial.
        if os.environ.get("ARTURO_SESSION") and args.path == "/mcp":
            logging.getLogger(__name__).warning(
                "Serving at the default /mcp path while holding a stored ARTURO_SESSION. "
                "Anyone who reaches this URL acts as that Arturo account — set MCP_PATH "
                "to something unguessable, or drop ARTURO_SESSION and let callers supply "
                "their own cookie via the X-Arturo-Session header."
            )
        server.run(
            "streamable-http",
            host=args.host,
            port=args.port,
            streamable_http_path=args.path,
            stateless_http=True,
            transport_security=_transport_security(),
        )


def _transport_security() -> TransportSecuritySettings:
    """Host allowlist for the streamable-HTTP transport.

    The SDK rejects any Host header it does not recognise, as DNS-rebinding
    protection. That is right for a server bound to localhost, but behind a
    tunnel or a platform ingress the Host is the public name — so every hosted
    deployment must declare it or every request answers 421.
    """
    raw = os.environ.get("MCP_ALLOWED_HOSTS", "").strip()
    if not raw:
        return TransportSecuritySettings()
    if raw == "*":
        # Any host accepted. Safe only because reaching the endpoint is not
        # enough on its own: a caller still needs the session cookie, and when
        # the server holds one, the unguessable MCP_PATH is the gate.
        return TransportSecuritySettings(enable_dns_rebinding_protection=False)

    hosts = [h.strip() for h in raw.split(",") if h.strip()]
    return TransportSecuritySettings(
        allowed_hosts=hosts,
        allowed_origins=[f"https://{h}" for h in hosts] + [f"http://{h}" for h in hosts],
    )


if __name__ == "__main__":
    main()
