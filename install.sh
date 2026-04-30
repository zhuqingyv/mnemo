#!/bin/sh
# mnemo installer — downloads the latest binary for your platform
set -e

REPO="zhuqingyv/mnemo"
INSTALL_DIR="${MNEMO_INSTALL_DIR:-/usr/local/bin}"

OS=$(uname -s | tr '[:upper:]' '[:lower:]')
ARCH=$(uname -m)

# Normalize arch
case "$ARCH" in
  x86_64|amd64) ARCH="x86_64" ;;
  arm64|aarch64) ARCH="arm64" ;;
  *) echo "Unsupported architecture: $ARCH"; exit 1 ;;
esac

BINARY="mnemo-${OS}-${ARCH}"
URL="https://github.com/${REPO}/releases/latest/download/${BINARY}"

echo "Downloading mnemo for ${OS}/${ARCH}..."
if command -v curl >/dev/null 2>&1; then
  curl -fsSL "$URL" -o "${INSTALL_DIR}/mnemo"
elif command -v wget >/dev/null 2>&1; then
  wget -qO "${INSTALL_DIR}/mnemo" "$URL"
else
  echo "Error: curl or wget required"
  exit 1
fi

chmod +x "${INSTALL_DIR}/mnemo"

echo ""
echo "mnemo installed to ${INSTALL_DIR}/mnemo"
echo ""
echo "Quick start:"
echo "  mnemo serve          # Start server + visualization"
echo "  mnemo setup          # Configure Claude Code / Cursor MCP"
echo "  open http://127.0.0.1:8787/viz/"
echo ""
echo "Done!"
