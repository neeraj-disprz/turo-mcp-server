#!/usr/bin/env bash
# Public HTTPS URL for a demo, no account and no deploy.
#
# Run this from your own terminal, not from inside an agent session — both
# processes must outlive the shell that started them, and the tunnel hostname
# is regenerated every run.
#
# The tunnel starts first because the MCP SDK rejects any Host header it was not
# told about (DNS-rebinding protection), so the server can only be configured
# once the public hostname exists.
#
# By default the server starts with NO cookie, so the first tool call fails and
# the caller hands one over with `set_session` — the same path a teammate takes.
# Set DEMO_HOLD_COOKIE=1 to serve with the cookie from .env instead, which skips
# that handshake but makes the URL alone enough to act as that Arturo account.
#
# Either way the mount path is a generated secret, because once a cookie reaches
# the server — from .env or from set_session — the path is what keeps the
# endpoint shut. Treat the printed URL as a credential.
set -euo pipefail

cd "$(dirname "$0")/.."

PORT="${MCP_PORT:-8790}"
PATH_SECRET="$(python -c 'import secrets; print(secrets.token_hex(12))')"
LOG_DIR=".tools"
mkdir -p "$LOG_DIR"

[ -f .env ] || { echo "mcp-server/.env is missing (needs ARTURO_API_BASE_URL)." >&2; exit 1; }

# Empty-but-set beats .env: config.py loads the file with setdefault, so a value
# already in the environment wins and the server comes up holding no cookie.
if [ "${DEMO_HOLD_COOKIE:-}" = "1" ]; then
  SESSION_MODE="serving the cookie from .env"
  export_session() { :; }
else
  SESSION_MODE="no stored cookie — callers use set_session"
  export_session() { export ARTURO_SESSION=""; }
fi

CLOUDFLARED="$LOG_DIR/cloudflared.exe"
[ -x "$CLOUDFLARED" ] || CLOUDFLARED="$(command -v cloudflared || true)"
[ -n "$CLOUDFLARED" ] || { echo "cloudflared not found. Put it at $LOG_DIR/cloudflared.exe" >&2; exit 1; }

cleanup() {
  [ -n "${TUNNEL_PID:-}" ] && kill "$TUNNEL_PID" 2>/dev/null || true
  [ -n "${SERVER_PID:-}" ] && kill "$SERVER_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

echo "Starting tunnel…"
: > "$LOG_DIR/tunnel.log"
"$CLOUDFLARED" tunnel --url "http://127.0.0.1:$PORT" --no-autoupdate \
  > "$LOG_DIR/tunnel.log" 2>&1 &
TUNNEL_PID=$!

PUBLIC=""
for _ in $(seq 1 60); do
  PUBLIC="$(grep -ohE 'https://[a-z0-9-]+\.trycloudflare\.com' "$LOG_DIR/tunnel.log" | head -1 || true)"
  [ -n "$PUBLIC" ] && break
  sleep 1
done
[ -n "$PUBLIC" ] || { echo "Tunnel did not come up. See $LOG_DIR/tunnel.log" >&2; exit 1; }

HOSTNAME_ONLY="${PUBLIC#https://}"
echo "Starting server (allowed host: $HOSTNAME_ONLY, $SESSION_MODE)…"

(
  export_session
  MCP_PATH="$PATH_SECRET" MCP_PORT="$PORT" MCP_ALLOWED_HOSTS="$HOSTNAME_ONLY" \
    exec .venv/Scripts/python.exe -m turo_mcp.server \
    --transport streamable-http --host 127.0.0.1 \
    > "$LOG_DIR/server.log" 2>&1
) &
SERVER_PID=$!

for _ in $(seq 1 40); do
  grep -q "Application startup complete" "$LOG_DIR/server.log" 2>/dev/null && break
  sleep 0.5
done

cat <<EOF

  MCP endpoint:  $PUBLIC/$PATH_SECRET

  Mode: $SESSION_MODE

  Claude Desktop -> Settings -> Developer -> Edit Config, then restart it fully
  (it stays in the system tray):

  {
    "mcpServers": {
      "turo": {
        "command": "npx",
        "args": ["-y", "mcp-remote", "$PUBLIC/$PATH_SECRET", "--transport", "http-only"]
      }
    }
  }

  Then ask it to run whoami. It will say the session expired; paste your
  arturo_session cookie into the chat and it will call set_session and carry on.

  Once a cookie reaches this server, anyone with the URL acts as that Arturo
  account — read, edit and delete across the tenant. Share it narrowly and
  Ctrl+C when you are done. Hostname and path change on every run.

EOF

wait "$SERVER_PID"
