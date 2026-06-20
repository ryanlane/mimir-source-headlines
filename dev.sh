#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# Mimir source-plugin dev helper
#
# Usage:
#   ./dev.sh link     Link this plugin to a running Mimir dev server
#   ./dev.sh unlink   Remove the dev link (does not delete files)
#   ./dev.sh reload   Force an immediate reload of the plugin
#   ./dev.sh status   Show current link status
#   ./dev.sh logs     Tail the Mimir server log (if running locally via dev.sh)
#
# Config (env vars or .mimir-dev in this directory):
#   MIMIR_API_URL   Full base URL of the Mimir API  (default: http://localhost:8000)
#   MIMIR_API_KEY   API key if auth is enabled       (default: empty)
#
# The server watches for .py/.js/.json/.css changes and auto-reloads — no
# polling needed. Just run `./dev.sh link` once and start editing.
# ──────────────────────────────────────────────────────────────────────────────
set -euo pipefail

# ── Resolve paths ─────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Auto-detect the channel directory: first subdir of channels/ that has a plugin.json
CHANNEL_DIR=""
if [[ -d "$SCRIPT_DIR/channels" ]]; then
    for d in "$SCRIPT_DIR/channels"/*/; do
        if [[ -f "${d}plugin.json" ]]; then
            CHANNEL_DIR="$(cd "$d" && pwd)"
            break
        fi
    done
fi

if [[ -z "$CHANNEL_DIR" ]]; then
    echo "ERROR: Could not find a channels/*/plugin.json in $SCRIPT_DIR"
    exit 1
fi

PLUGIN_ID="$(python3 -c "import json; print(json.load(open('$CHANNEL_DIR/plugin.json'))['id'])" 2>/dev/null || echo "")"
if [[ -z "$PLUGIN_ID" ]]; then
    echo "ERROR: Could not read plugin id from $CHANNEL_DIR/plugin.json"
    exit 1
fi

# ── Load local config ─────────────────────────────────────────────────────────
if [[ -f "$SCRIPT_DIR/.mimir-dev" ]]; then
    # shellcheck source=/dev/null
    source "$SCRIPT_DIR/.mimir-dev"
fi

MIMIR_API_URL="${MIMIR_API_URL:-http://localhost:8000}"
MIMIR_API_URL="${MIMIR_API_URL%/}"  # strip trailing slash
API_KEY="${MIMIR_API_KEY:-}"

# ── Helpers ───────────────────────────────────────────────────────────────────
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
DIM='\033[2m'
NC='\033[0m'

log()  { echo -e "${CYAN}[mimir-dev]${NC} $*"; }
ok()   { echo -e "${GREEN}✓${NC} $*"; }
warn() { echo -e "${YELLOW}⚠${NC} $*"; }
err()  { echo -e "${RED}✗${NC} $*" >&2; }

auth_header() {
    if [[ -n "$API_KEY" ]]; then
        echo "-H" "Authorization: Bearer $API_KEY"
    fi
}

api() {
    local method="$1"; shift
    local path="$1";   shift
    local url="$MIMIR_API_URL/api/admin${path}"
    local extra_args=("$@")

    if [[ -n "$API_KEY" ]]; then
        curl -sf -X "$method" "$url" \
            -H "Content-Type: application/json" \
            -H "Authorization: Bearer $API_KEY" \
            "${extra_args[@]}"
    else
        curl -sf -X "$method" "$url" \
            -H "Content-Type: application/json" \
            "${extra_args[@]}"
    fi
}

check_server() {
    if ! curl -sf "$MIMIR_API_URL/api/health" > /dev/null 2>&1; then
        err "Mimir server not reachable at $MIMIR_API_URL"
        echo -e "  ${DIM}Start it with: cd mimir-server/mimir-api && ./dev.sh start${NC}"
        echo -e "  ${DIM}Or set MIMIR_API_URL to point to your running instance${NC}"
        exit 1
    fi
}

# ── Commands ──────────────────────────────────────────────────────────────────
cmd_link() {
    check_server
    log "Linking ${CYAN}$PLUGIN_ID${NC}"
    log "  Path: $CHANNEL_DIR"

    response="$(api POST /dev/channels -d "{\"path\": \"$CHANNEL_DIR\"}" 2>&1)" || {
        # Try to extract a useful error message
        if echo "$response" | grep -q "already loaded\|already linked"; then
            warn "Plugin '$PLUGIN_ID' is already linked."
            echo -e "  Run ${CYAN}./dev.sh status${NC} to confirm, or ${CYAN}./dev.sh reload${NC} to force a fresh load."
        else
            err "Link failed: $response"
            exit 1
        fi
        return
    }

    ok "Linked!  The server will now auto-reload on file changes."
    echo -e "  ${DIM}Plugin: $PLUGIN_ID${NC}"
    echo -e "  ${DIM}Server: $MIMIR_API_URL${NC}"
    echo
    echo -e "  ${GREEN}Edit any .py / .js / .json file and the server reloads automatically.${NC}"
    echo -e "  ${DIM}Run ./dev.sh unlink when you're done.${NC}"
}

cmd_unlink() {
    check_server
    log "Unlinking $PLUGIN_ID ..."
    response="$(api DELETE "/dev/channels/$PLUGIN_ID" 2>&1)" || {
        err "Unlink failed: $response"
        exit 1
    }
    ok "Unlinked.  Files on disk are untouched."
}

cmd_reload() {
    check_server
    log "Reloading $PLUGIN_ID ..."
    response="$(api POST "/dev/channels/$PLUGIN_ID/reload" 2>&1)" || {
        err "Reload failed: $response"
        echo -e "  ${DIM}Is the plugin linked? Run ./dev.sh status to check.${NC}"
        exit 1
    }
    ok "Reloaded."
}

cmd_status() {
    check_server
    log "Dev channel status"
    echo
    response="$(api GET /dev/channels 2>&1)" || {
        err "Could not fetch status: $response"
        exit 1
    }

    if command -v python3 &>/dev/null; then
        python3 - "$response" "$PLUGIN_ID" <<'PYEOF'
import json, sys
data = json.loads(sys.argv[1])
target = sys.argv[2]
channels = data.get("dev_channels", [])
if not channels:
    print("  No dev channels linked.")
    sys.exit(0)
for ch in channels:
    marker = " ◀ this plugin" if ch.get("plugin_id") == target else ""
    print(f"  • {ch.get('plugin_id', '?')}  →  {ch.get('path', '?')}{marker}")
PYEOF
    else
        echo "$response"
    fi
    echo
}

cmd_help() {
    echo -e "${CYAN}Mimir plugin dev helper${NC}  —  ${DIM}$PLUGIN_ID${NC}"
    echo
    echo "  ./dev.sh link     Link this plugin to the dev server (auto-reloads on changes)"
    echo "  ./dev.sh unlink   Remove the dev link"
    echo "  ./dev.sh reload   Force an immediate reload"
    echo "  ./dev.sh status   Show all linked dev channels"
    echo
    echo -e "  Server: ${YELLOW}$MIMIR_API_URL${NC}  (override with MIMIR_API_URL env var)"
    echo -e "  Config: create ${DIM}.mimir-dev${NC} in this directory to persist settings"
    echo
    echo -e "  Example ${DIM}.mimir-dev${NC}:"
    echo -e "    ${DIM}MIMIR_API_URL=http://localhost:8000${NC}"
    echo -e "    ${DIM}MIMIR_API_KEY=your-key-here${NC}"
}

# ── Dispatch ──────────────────────────────────────────────────────────────────
COMMAND="${1:-help}"

case "$COMMAND" in
    link)    cmd_link ;;
    unlink)  cmd_unlink ;;
    reload)  cmd_reload ;;
    status)  cmd_status ;;
    help|-h|--help) cmd_help ;;
    *)
        err "Unknown command: $COMMAND"
        echo
        cmd_help
        exit 1
        ;;
esac
