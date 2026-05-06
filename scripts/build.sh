#!/usr/bin/env bash
# Build mnemo binary for the current platform via PyInstaller.
#
# All build details live in mnemo.spec — this script is just a thin wrapper
# that runs PyInstaller and renames the artifact with an OS+ARCH suffix.
#
# Usage:
#   scripts/build.sh                    # uses python3 from PATH
#   PYTHON=path/to/python scripts/build.sh
#
# Requires: pyinstaller installed in the active environment.
set -euo pipefail

cd "$(dirname "$0")/.."

PY="${PYTHON:-python3}"

# Sanity check: PyInstaller available
if ! "$PY" -c "import PyInstaller" >/dev/null 2>&1; then
  echo "PyInstaller not installed. Run: $PY -m pip install pyinstaller" >&2
  exit 1
fi

echo "==> Building mnemo (spec: mnemo.spec)"
"$PY" -m PyInstaller mnemo.spec --noconfirm --clean

# Rename to platform-specific filename so multiple matrix jobs can upload
# distinct artifacts without colliding.
OS_RAW="$(uname -s)"
ARCH_RAW="$(uname -m)"

case "$OS_RAW" in
  Darwin)  OS=darwin ;;
  Linux)   OS=linux ;;
  MINGW*|MSYS*|CYGWIN*) OS=windows ;;
  *)       OS="$(echo "$OS_RAW" | tr '[:upper:]' '[:lower:]')" ;;
esac

case "$ARCH_RAW" in
  x86_64|amd64) ARCH=x86_64 ;;
  arm64|aarch64) ARCH=arm64 ;;
  *) ARCH="$ARCH_RAW" ;;
esac

if [ "$OS" = "windows" ]; then
  SRC="dist/mnemo.exe"
  DEST="dist/mnemo-${OS}-${ARCH}.exe"
else
  SRC="dist/mnemo"
  DEST="dist/mnemo-${OS}-${ARCH}"
fi

mv "$SRC" "$DEST"
echo "==> Done: $DEST"
ls -lh "$DEST"
