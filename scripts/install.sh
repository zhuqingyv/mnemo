#!/usr/bin/env bash
# mnemo installer — POSIX (macOS + Linux).
#
# Downloads the prebuilt binary matching this host's OS+ARCH from a GitHub
# Release, verifies its sha256, drops it into ~/.mnemo/bin, ensures that
# directory is on PATH for future shells, and runs `mnemo setup --auto` so
# every detected AI client (Claude Code / Cursor / Codex CLI / Claude
# Desktop) has its MCP config and system prompt configured in one shot.
#
# Usage:
#   curl -fsSL https://github.com/zhuqingyv/mnemo/releases/latest/download/install.sh | sh
#
# Environment variables:
#   MNEMO_VERSION        Pin a specific version tag (e.g. v0.2.1). Default: latest.
#   MNEMO_REPO           Override repo slug. Default: zhuqingyv/mnemo.
#   MNEMO_INSTALL_DIR    Where the binary lands. Default: $HOME/.mnemo/bin.
#   MNEMO_NO_SETUP=1     Skip the post-install `mnemo setup --auto` step.
#   MNEMO_NO_PATH=1      Skip writing PATH export to shell rc files.
set -eu

REPO="${MNEMO_REPO:-zhuqingyv/mnemo}"
VERSION="${MNEMO_VERSION:-latest}"
INSTALL_DIR="${MNEMO_INSTALL_DIR:-$HOME/.mnemo/bin}"

log()  { printf '\033[1;32m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33mwarn:\033[0m %s\n' "$*" >&2; }
fail() { printf '\033[1;31merror:\033[0m %s\n' "$*" >&2; exit 1; }

# --- detect OS + ARCH -------------------------------------------------------
os_raw="$(uname -s)"
arch_raw="$(uname -m)"

case "$os_raw" in
  Darwin) os=darwin ;;
  Linux)  os=linux ;;
  *) fail "unsupported OS: $os_raw (use install.ps1 on Windows)" ;;
esac

case "$arch_raw" in
  x86_64|amd64) arch=x86_64 ;;
  arm64|aarch64) arch=arm64 ;;
  *) fail "unsupported architecture: $arch_raw" ;;
esac

# Linux-arm64 builds aren't shipped (yet) — fail with a clear message.
if [ "$os" = "linux" ] && [ "$arch" = "arm64" ]; then
  fail "no prebuilt linux-arm64 binary yet. Build from source or open an issue."
fi

asset="mnemo-${os}-${arch}"

# --- pick download URL ------------------------------------------------------
if [ "$VERSION" = "latest" ]; then
  base="https://github.com/${REPO}/releases/latest/download"
else
  base="https://github.com/${REPO}/releases/download/${VERSION}"
fi

binary_url="${base}/${asset}"
sums_url="${base}/SHA256SUMS"

# --- download ---------------------------------------------------------------
need() { command -v "$1" >/dev/null 2>&1 || fail "missing required tool: $1"; }
need curl
need mkdir
need install
need rm

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

log "Downloading ${asset} (${VERSION}) from ${REPO}"
curl --fail --silent --show-error --location -o "$tmp/$asset" "$binary_url" \
  || fail "download failed: $binary_url"

# --- verify sha256 (best-effort) -------------------------------------------
if curl --fail --silent --show-error --location -o "$tmp/SHA256SUMS" "$sums_url" 2>/dev/null; then
  expected=""
  while IFS= read -r line; do
    case "$line" in
      *"  $asset"|*"  ./$asset")
        expected="${line%% *}"
        break
        ;;
    esac
  done < "$tmp/SHA256SUMS"

  if [ -n "$expected" ]; then
    if command -v shasum >/dev/null 2>&1; then
      actual="$(shasum -a 256 "$tmp/$asset" | awk '{print $1}')"
    elif command -v sha256sum >/dev/null 2>&1; then
      actual="$(sha256sum "$tmp/$asset" | awk '{print $1}')"
    else
      actual=""
    fi
    if [ -n "$actual" ] && [ "$actual" != "$expected" ]; then
      fail "sha256 mismatch for $asset (expected $expected, got $actual)"
    fi
    [ -n "$actual" ] && log "sha256 verified"
  else
    warn "no SHA256 entry for $asset, skipping verification"
  fi
else
  warn "SHA256SUMS not found at $sums_url, skipping verification"
fi

# --- install ----------------------------------------------------------------
mkdir -p "$INSTALL_DIR"
install -m 0755 "$tmp/$asset" "$INSTALL_DIR/mnemo"
log "Installed: $INSTALL_DIR/mnemo"

# Strip macOS quarantine bit so users don't have to "right-click Open" the
# first time. No-op on Linux. Errors are non-fatal (file may not be quarantined).
if [ "$os" = "darwin" ] && command -v xattr >/dev/null 2>&1; then
  xattr -d com.apple.quarantine "$INSTALL_DIR/mnemo" >/dev/null 2>&1 || true
fi

# --- ensure PATH ------------------------------------------------------------
add_path_line='export PATH="$HOME/.mnemo/bin:$PATH"'

ensure_path() {
  rc="$1"
  [ -f "$rc" ] || return 0
  if grep -Fq "$add_path_line" "$rc" 2>/dev/null; then
    return 0
  fi
  printf '\n# mnemo (https://github.com/%s)\n%s\n' "$REPO" "$add_path_line" >> "$rc"
  log "Added mnemo to PATH in $rc"
}

if [ -z "${MNEMO_NO_PATH:-}" ]; then
  case ":$PATH:" in
    *":$INSTALL_DIR:"*) ;; # already on PATH
    *)
      [ -n "${ZDOTDIR:-}" ] && ensure_path "$ZDOTDIR/.zshrc"
      ensure_path "$HOME/.zshrc"
      ensure_path "$HOME/.bashrc"
      ensure_path "$HOME/.bash_profile"
      ensure_path "$HOME/.profile"
      # fish uses a different syntax; write a config snippet only if fish dir exists
      if [ -d "$HOME/.config/fish" ] && [ ! -f "$HOME/.config/fish/conf.d/mnemo.fish" ]; then
        printf 'set -gx PATH $HOME/.mnemo/bin $PATH\n' \
          > "$HOME/.config/fish/conf.d/mnemo.fish"
        log "Added mnemo to PATH for fish"
      fi
      ;;
  esac
fi

# --- run setup --------------------------------------------------------------
if [ -n "${MNEMO_NO_SETUP:-}" ]; then
  log "Skipping setup (MNEMO_NO_SETUP set)"
else
  log "Running 'mnemo setup --auto' to configure detected AI clients"
  if "$INSTALL_DIR/mnemo" setup --auto; then
    :
  else
    warn "mnemo setup exited non-zero — you can re-run it manually later:"
    warn "    $INSTALL_DIR/mnemo setup --auto"
  fi
fi

# --- final hint -------------------------------------------------------------
log "All done."
log "Verify:    $INSTALL_DIR/mnemo --version"
case ":$PATH:" in
  *":$INSTALL_DIR:"*) ;;
  *) log "Open a new terminal (or 'source ~/.zshrc') so 'mnemo' is on PATH." ;;
esac
log "Restart your AI client (Claude Code / Cursor / Codex / Claude Desktop) to activate mnemo."
