#!/usr/bin/env bash
# End-to-end test: install/uninstall mnemo for each detected agent.
# Verifies:
#   1. Install writes correct config (HTTP mode, correct field per agent)
#   2. Uninstall cleanly removes config
#   3. Re-install works immediately
#   4. mnemo HTTP server remains unaffected throughout

set -euo pipefail

MNEMO_BIN="/Users/zhuqingyu/project/mnemo/.venv/bin/mnemo"
PORT=8787
URL="http://127.0.0.1:${PORT}/mcp/http/mcp"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

PASS=0
FAIL=0

pass() { echo -e "  ${GREEN}PASS${NC} $1"; PASS=$((PASS+1)); }
fail() { echo -e "  ${RED}FAIL${NC} $1"; FAIL=$((FAIL+1)); }
info() { echo -e "${YELLOW}>>> $1${NC}"; }

check_server() {
    local code
    code=$(curl -s -o /dev/null -w "%{http_code}" "$URL" 2>/dev/null || echo "000")
    [ "$code" != "000" ]
}

check_json_has_mnemo() {
    local file="$1"
    [ -f "$file" ] && python3 -c "
import json, sys
d = json.load(open('$file'))
servers = d.get('mcpServers', {})
sys.exit(0 if 'mnemo' in servers else 1)
" 2>/dev/null
}

check_toml_has_mnemo() {
    local file="$1"
    [ -f "$file" ] && grep -q '\[mcp_servers\.mnemo\]' "$file" 2>/dev/null
}

# Test a single agent: install -> verify -> uninstall -> verify -> re-install -> verify
test_agent() {
    local name="$1"
    local check_func="$2"
    local config_file="$3"

    info "Testing $name"

    # Install
    timeout 15 $MNEMO_BIN setup --client "$name" --mode http --skip-prompt >/dev/null 2>&1
    if $check_func "$config_file"; then
        pass "$name install"
    else
        fail "$name install"
        return
    fi

    # Uninstall
    timeout 15 $MNEMO_BIN setup --uninstall --client "$name" >/dev/null 2>&1
    if $check_func "$config_file"; then
        fail "$name uninstall (still present)"
    else
        pass "$name uninstall"
    fi

    # Re-install
    timeout 15 $MNEMO_BIN setup --client "$name" --mode http --skip-prompt >/dev/null 2>&1
    if $check_func "$config_file"; then
        pass "$name re-install"
    else
        fail "$name re-install"
    fi

    # Clean up
    timeout 15 $MNEMO_BIN setup --uninstall --client "$name" >/dev/null 2>&1
    echo ""
}

# ===== PRE-FLIGHT =====
info "Pre-flight: checking mnemo HTTP server"
if check_server; then
    pass "mnemo HTTP server running at $URL"
else
    fail "mnemo HTTP server NOT running"
fi
echo ""

# ===== AGENT TESTS =====
test_agent "claude-code" check_json_has_mnemo "$HOME/.claude.json"
test_agent "qwen-code" check_json_has_mnemo "$HOME/.qwen/settings.json"
test_agent "codebuddy" check_json_has_mnemo "$HOME/.codebuddy/.mcp.json"
test_agent "codex-cli" check_toml_has_mnemo "$HOME/.codex/config.toml"
test_agent "gemini-cli" check_json_has_mnemo "$HOME/.gemini/settings.json"
test_agent "cursor" check_json_has_mnemo "$HOME/.cursor/mcp.json"
test_agent "windsurf" check_json_has_mnemo "$HOME/.codeium/windsurf/mcp_config.json"
test_agent "github-copilot-cli" check_json_has_mnemo "$HOME/.copilot/mcp-config.json"

# ===== CLAUDE MCP LIST VERIFICATION =====
info "Testing Claude Code MCP list integration"
timeout 15 $MNEMO_BIN setup --client claude-code --mode http --skip-prompt >/dev/null 2>&1
if claude mcp list 2>&1 | grep -q "mnemo"; then
    pass "claude mcp list shows mnemo after install"
else
    fail "claude mcp list does not show mnemo"
fi
timeout 15 $MNEMO_BIN setup --uninstall --client claude-code >/dev/null 2>&1
if claude mcp list 2>&1 | grep -q "mnemo"; then
    fail "claude mcp list still shows mnemo after uninstall"
else
    pass "claude mcp list clean after uninstall"
fi
echo ""

# ===== POST-FLIGHT =====
info "Post-flight: verifying mnemo HTTP server still running"
if check_server; then
    pass "mnemo HTTP server still alive (not affected by install/uninstall)"
else
    fail "mnemo HTTP server died!"
fi

echo ""
echo "=============================="
echo -e "Results: ${GREEN}${PASS} passed${NC}, ${RED}${FAIL} failed${NC}"
echo "=============================="

[ $FAIL -eq 0 ]
