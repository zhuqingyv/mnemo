#!/bin/bash
# Build mnemo binary for current platform using PyInstaller
set -e

cd "$(dirname "$0")/.."
VENV=".venv/bin"

echo "Building mnemo binary..."
$VENV/pyinstaller \
  --onefile \
  --name mnemo \
  --strip \
  --exclude-module torch \
  --exclude-module transformers \
  --exclude-module sentence_transformers \
  --exclude-module numpy \
  --exclude-module scipy \
  --exclude-module sklearn \
  --exclude-module matplotlib \
  --exclude-module PIL \
  --exclude-module tkinter \
  --hidden-import mnemo.cli.main \
  --hidden-import mnemo.server.app \
  --hidden-import mnemo.mcp.server \
  --hidden-import aiosqlite \
  --hidden-import sqlite_vec \
  --hidden-import uvicorn \
  --hidden-import uvicorn.logging \
  --hidden-import uvicorn.loops.auto \
  --hidden-import uvicorn.protocols.http.auto \
  --hidden-import uvicorn.protocols.http.httptools_impl \
  --hidden-import uvicorn.protocols.websockets.auto \
  --hidden-import uvicorn.lifespan.on \
  --hidden-import fastapi \
  --hidden-import starlette \
  --hidden-import httptools \
  --hidden-import jieba \
  --hidden-import typer \
  --hidden-import click \
  --hidden-import rich \
  --hidden-import pydantic \
  --hidden-import anyio \
  --hidden-import anyio._backends._asyncio \
  --add-data "$VENV/../lib/python3.12/site-packages/sqlite_vec/vec0.dylib:sqlite_vec" \
  --add-data "$VENV/../lib/python3.12/site-packages/jieba/dict.txt:jieba" \
  --collect-data fastmcp \
  --noconfirm \
  scripts/pyinstaller_entry.py

ARCH=$(uname -m)
OS=$(uname -s | tr '[:upper:]' '[:lower:]')
mv dist/mnemo "dist/mnemo-${OS}-${ARCH}"

echo "Done: dist/mnemo-${OS}-${ARCH}"
ls -lh "dist/mnemo-${OS}-${ARCH}"
