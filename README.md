---
title: Turo MCP Server
emoji: 🐳
colorFrom: purple
colorTo: gray
sdk: docker
app_port: 8080
pinned: false
---

# Turo MCP Server

MCP server over the Arturo API for **pathway editing**, **Reel 3.0 generation** and
**Single-Turn Simulation generation and editing**. 32 tools. The specification this
implements lives in the Arturo-API repo at `docs/turo-mcp-tools.md`.

This is a standalone deployment copy — the server talks to Arturo over HTTP and needs
nothing from that repo at runtime.

Not in scope: pathway creation from documents, and publishing. Those stay in the Turo UI.
KB *authoring* also stays in the UI; `set_concept_kb` exists only because a concept
created through the API has no KB at all and script generation fails without one.

## How authentication works

In its supported mode the server is **stateless** and holds no credential. Each caller
supplies their own Arturo session cookie through the `X-Arturo-Session` MCP connection
header, which the server forwards to Arturo as `Cookie: arturo_session=…`.

This matters for two reasons:

- The cookie is **never a tool argument**, so it never enters the model's context,
  transcripts or logs. (`set_session`, below, deliberately breaks this for clients that
  cannot send headers — a demo affordance, not this path.)
- An open endpoint is not an open door to an account — a caller without a valid cookie
  gets a `401`. That removes the need for a secret header or IP allowlist in front.

Arturo sessions last **24 hours from login and do not renew on use**. Mint the cookie
from a dedicated login (incognito, or `POST /api/auth/login/`) rather than copying one
out of a browser you might log out of — logging out kills it instantly.

### The `set_session` escape hatch

Some clients cannot send connection headers at all. For those there is a `set_session`
tool: the cookie is handed over **in the conversation**, verified, and held in memory.
`SessionExpired` names the tool in its message, so when a cookie dies the model asks for
a new one and carries on without anyone editing a config file.

The cookie is deliberately **not persisted**. The conversation is the durable copy — after
a restart the next call fails, the model re-sends the cookie it already has, and the user
sees nothing. Persisting it would add storage to buy something the transcript gives free.

Know what this trades away. A cookie pasted into chat is in the transcript, in tool-call
logs, and in any export or screen-share of that conversation, for as long as it is valid.
That is the exact property the header path exists to protect, so `set_session` is a
**demo affordance**: fine for a short-lived cookie on an internal tool, not for anything
real. It also means the server now holds a live credential, so an unguessable `MCP_PATH`
matters whether or not `ARTURO_SESSION` is set.

The connection header always wins over a `set_session` cookie. Otherwise, on a shared
deployment, one caller's `set_session` would silently run everyone else's requests as
that account.

## Configuration

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `ARTURO_API_BASE_URL` | **yes** | — | Arturo host root, e.g. `https://turo.disprz.ai` (no path) |
| `ARTURO_API_PREFIX` | no | `/ui/api` | Ingress path prefix. Django serves `/api`, deployments expose `/ui/api` |
| `ARTURO_SESSION_HEADER` | no | `X-Arturo-Session` | Connection header carrying the cookie |
| `ARTURO_SESSION` | no | — | Fallback cookie for single-user/stdio use. Leave unset for a stateless deployment |
| `ARTURO_TIMEOUT_SECONDS` | no | `60` | Per-request timeout |
| `ARTURO_WEB_URL` | no | `ARTURO_API_BASE_URL` | Host humans open pathways on, if not the API host |
| `ARTURO_PATHWAY_PATH` | no | `/ui/workspace/documentToLearning/` | Workspace route the pathway link points at |
| `MCP_ALLOWED_HOSTS` | hosted only | localhost | Public hostname(s), comma-separated, or `*`. **Required for any hosted deployment** — the SDK returns `421` for unknown `Host` headers |
| `MCP_PATH` | hosted only | `/mcp` | Mount path. Make it unguessable whenever `ARTURO_SESSION` is set |
| `REEL3_BRAND_PRIMARY` / `_SECONDARY` / `_ON_PRIMARY` | no | Disprz brand | Brand colours injected into every reel |
| `REEL3_ART_STYLE` | no | `soft-gouache` | Default art style |
| `REEL3_MODEL_ID` | no | `eleven_v3` | TTS model |
| `REEL3_CULTURE_CONTEXT` | no | `ind` | Default culture context |

Reel 3.0 defaults mirror Arturo-Web so MCP-generated reels match UI-generated ones.

## Running

```bash
python -m venv .venv && .venv/Scripts/pip install -e ".[dev]"

# Local, single user — the client launches the process
ARTURO_API_BASE_URL=https://turo-api.example.com \
ARTURO_SESSION=<cookie> \
  .venv/Scripts/turo-mcp --transport stdio

# Hosted, multi-user — each caller brings their own cookie
ARTURO_API_BASE_URL=https://turo-api.example.com \
  .venv/Scripts/turo-mcp --transport streamable-http --host 0.0.0.0 --port 8080
```

Client config for the hosted mode:

```json
{
  "mcpServers": {
    "turo": {
      "type": "http",
      "url": "https://turo-mcp.example.com/mcp",
      "headers": { "X-Arturo-Session": "<your arturo_session cookie>" }
    }
  }
}
```

Terminate TLS at the ingress. The request carries a credential and the responses carry
tenant content.

## Hosting it for a demo

### Hugging Face Spaces

Free, no card, and a private Space keeps the code off the open web. The YAML block
at the top of this README is what makes it a Docker Space (`app_port: 8080` must
match `MCP_PORT` in the Dockerfile).

1. [New Space](https://huggingface.co/new-space) → SDK **Docker** → visibility
   **Private**.
2. Push this repo to the Space's git remote.
3. Space **Settings → Variables and secrets** → add secret `MCP_PATH` = a long
   random string. **Do this before the first boot.** Without it the server mounts
   at `/mcp`, and since `MCP_ALLOWED_HOSTS` is `*`, anyone who finds the Space URL
   can call `set_session` and then act as whatever cookie is loaded.
4. Leave `ARTURO_SESSION` unset so callers supply their own cookie.

Endpoint: `https://<user>-<space>.hf.space/<MCP_PATH>`

Spaces sleep when idle and the container's disk is wiped on restart. Neither
matters here — nothing is persisted, and a restart just costs one silent
`set_session` re-send.

### Render

Free tier on [Render](https://render.com), via `render.yaml` in this directory.

1. Push this repo to GitHub.
2. Render → **New → Blueprint** → select the repo. It reads `render.yaml` and
   creates a free web service from `mcp-server/Dockerfile`.
3. In the service's **Environment** tab set `ARTURO_SESSION` to the
   `arturo_session` cookie value, and `MCP_ALLOWED_HOSTS` to the hostname Render
   assigned (`turo-mcp-xxxx.onrender.com`). Render generates `MCP_PATH` for you.
   Without `MCP_ALLOWED_HOSTS` every request fails with `421 Misdirected Request`.
4. Your URL is `https://turo-mcp-xxxx.onrender.com/<MCP_PATH>` — copy `MCP_PATH`
   from the Environment tab.

**Why the random path.** Any deployment where the server holds a cookie — set via
`ARTURO_SESSION` or handed over by `set_session` — turns the endpoint into a door
into that Arturo account, and the unguessable path is what keeps it shut. The
server logs a warning if you set `ARTURO_SESSION` while still serving on the
default `/mcp`.

This is a demo posture, not a deployment posture. For anything real, hold no
cookie server-side and let each caller supply their own through the
`X-Arturo-Session` header — then the mount path stops being a secret.

Free-tier caveat: instances sleep after ~15 minutes idle and take ~50s to wake,
so the first tool call of a demo is slow. The cookie also expires 24h after login,
so re-paste it before demoing.

### Connecting Claude

**Claude Code** — supports headers natively, so it can use the stateless mode:

```bash
claude mcp add --transport http turo https://turo-mcp-xxxx.onrender.com/<path> \
  --header "X-Arturo-Session: <cookie>"
```

**Claude Desktop, hosted** — Desktop reaches remote servers through Custom
Connectors, which *do* support fixed credentials as request headers. But many
orgs disable adding custom connectors by policy, and `claude_desktop_config.json`
itself only supports stdio (`command`/`args`). The way through is a stdio bridge:
[`mcp-remote`](https://www.npmjs.com/package/mcp-remote) is an ordinary local
server that proxies to the hosted URL, so it goes under Settings → Developer and
never touches connector policy. Node is the only prerequisite — no clone, no venv.

```json
{
  "mcpServers": {
    "turo": {
      "command": "npx",
      "args": [
        "-y", "mcp-remote",
        "https://turo-mcp-xxxx.onrender.com/<path>",
        "--transport", "http-only",
        "--header", "X-Arturo-Session:${ARTURO_SESSION}"
      ],
      "env": { "ARTURO_SESSION": "<cookie>" }
    }
  }
}
```

No space after the colon in `--header`: Claude Desktop on Windows does not escape
spaces inside `args` when it invokes `npx`, so the value goes through `env`
instead. Drop the `--header` and `env` lines entirely to use `set_session` instead
and refresh the cookie from the chat.

**Claude Desktop, local** — merged into `%APPDATA%\Claude\claude_desktop_config.json`
under `mcpServers.turo`. Fully quit the app (it stays in the system tray) and
reopen; local servers appear under Settings → Developer, *not* in the Connectors
list, which only shows remote org connectors.

**Claude Code, local** — this repo's `.mcp.json` points at the venv and
`.claude/settings.local.json` pre-approves it.

Both local modes read the cookie from `mcp-server/.env` (gitignored).

**Throwaway public URL** — `scripts/demo-tunnel.sh` runs the server behind a
Cloudflare quick tunnel: public HTTPS, no account, nothing deployed. Run it from
your own terminal, not inside an agent session — the processes must outlive the
shell. Hostname and path change on every run.

## Tools

**Discovery** — `whoami`, `set_session`, `list_pathways`, `get_pathway`
**Pathway editing** — `create_chapter`, `update_chapter`, `delete_chapters`,
`create_concept`, `update_concept`, `delete_concepts`, `reorder_pathway`
**Reel 3.0** — `get_concept_kb`, `set_concept_kb`, `generate_reel_script`, `get_reel_script`,
`list_reel_voices`, `generate_reel3`, `get_reel3_status`
**Reel 3.0 editing** — `save_reel`, `regenerate_scene_narration`, `get_reel_audio_status`
**Reel 3.0 images** — `list_reel_images`, `get_scene_image_prompt`,
`regenerate_scene_image`, `repair_scene_image`
**Simulation** — `generate_simulation_script`, `generate_simulation`, `get_simulation`
**Simulation editing** — `save_simulation`, `regenerate_simulation_image`,
`replace_simulation_audio`, `get_simulation_audio`

### Design decisions worth knowing

- **Scene narration re-recording is synchronous, and it writes.** Unlike every other
  generation call here, `POST .../scenes/{id}/audio/mp3` does not queue: Author renders
  the clip, re-times the scene, saves reel.json and returns the updated reel. So
  `regenerate_scene_narration` must not be polled, and any reel document read before it
  is stale — saving that copy afterwards would revert the new timings. The tool lifts
  the returned reel out of the response so it arrives in `get_reel3_status` shape, ready
  for `save_reel`.
- **`get_reel_audio_status` is not its partner.** That endpoint tracks whole-reel audio
  jobs (`POST .../audio/mp3`, started from the Turo UI) and answers "audio generation was
  not found" for a reel whose scenes were re-recorded individually. Pairing the two was
  the first thing the live check disproved.
- **Arturo never reads brand colours from the tenant record.** `reelSettings.brand_colors`
  is *required* on every generate call and the generation path does no tenant lookup —
  the only tenant-colour code attaches branding to the *status/playback* response. So a
  reel generated without explicit colours is branded with whatever the server's
  `REEL3_BRAND_*` config says, which on a multi-tenant deployment is the wrong company.
  `generate_reel3` takes `brand_primary`/`brand_secondary`/`brand_on_primary` for this,
  falling back per field. Resolving them automatically is not an option: `user-details`
  returns no colours, and `tenants/get-by-id/` needs `configure_tenant_settings`, which a
  content author does not have.
- **Image asset tokens exist nowhere in the reel document.** The only trace of
  `{{ASSET:SC001:A2}}` is an `ASSET_SC001_A2` fragment inside each image URL, and Author
  rejects the unbraced form. `list_reel_images` derives them, which is why it is not
  optional sugar — without it a model cannot address an image at all.
- **Image transform returns the candidate as inline base64.** A 1536x1024 PNG is roughly
  a megabyte of it, so the image tools decode and commit internally and return only
  metadata plus the new URL. They also fold transform and accept into one call: there is
  nowhere stateless to park a candidate between two tool calls, and regeneration is
  non-deterministic, so a discarded preview cannot be recreated anyway.
- **`ai_image_url` is required by Author on BOTH image calls** despite Arturo's layer
  marking it optional. Omitting it on `accept` fails *after* the image is generated, so
  the credits are already spent when the error appears. The tools always send it,
  defaulting to the image being replaced.
- **Reel scenes key off `scene_id`, not `id`,** and spoken text lives in `narration_text`.
  Both tool descriptions name the real fields, because a model told to look for `id`
  finds nothing and gives up.

- **New chapters and concepts are placed last.** `display_order` defaults to 0 and neither
  create endpoint accepts it, so Turo puts new items first. The tools create and then
  reorder; if the reorder fails the item still exists and the result says so.
- **Both reorder endpoints are PUT.** A POST returns 405.
- **Simulation blob locations are resolved, never assumed.** A simulation generated for
  a non-default locale lives at `<concept-id>-<lang>-<cult>`. Reads 404 against the bare
  concept id and — worse — writes to it return HTTP 200 and change nothing. Every
  simulation tool resolves the real location first and reports it as `blob_location`.
- **Guardrails on destructive actions.** `delete_chapters`, `delete_concepts` and
  `update_concept(reset_contents=True)` refuse to run without `confirm=true`, and nothing
  reaches Arturo until it is set. `generate_pitstops` — which wipes a chapter's concepts
  *and* their generated reels — is never sent at all.
- **`generate_reel3` needs 4 arguments, not 12.** The API requires eight non-empty strings
  plus a settings block; art style, brand colours, canvas, language, culture context and
  model are all derived server-side.
- **Generation tools return a poll handle, never a blocking wait.** Reel 3.0 takes 5–15
  minutes, simulations 2–5.
- **Errors are written for the model.** A `502` from the Reel Author service says "retry,
  this is not a content failure"; an expired session says exactly how to fix it. All error
  types subclass the SDK's `ToolError`, because anything else is reported to the caller as
  a bare "Error executing tool X".

## Tests

```bash
.venv/Scripts/python -m pytest
```

108 tests, no network required. `test_http_integration.py` runs a real MCP server and a
fake Arturo over real sockets to prove the connection-header credential path end to end,
including that two concurrent callers never share a session. `test_live_shapes.py` pins
response shapes captured from the live API, including which endpoints wrap their payload
in a `{"success", "data"}` envelope and which do not.

### Live check

```bash
ARTURO_API_BASE_URL=https://turo.disprz.ai ARTURO_SESSION=<cookie> python scripts/smoke.py
```

Read-only; performs no writes and starts no generation. Verified against
`turo.disprz.ai` on 2026-10-07: all read tools pass, and a create → update → delete
chapter round-trip completed with clean-up. Reel 3.0 and simulation *generation* have
not been run end to end — they cost real credits and take 5–15 minutes.
