# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for mnemo (cross-platform, single source of truth).

Builds one self-contained executable that bundles:
  - the Python runtime
  - the mnemo package
  - sqlite_vec native library (.dylib / .so / .dll)
  - jieba dictionaries
  - fastmcp data files
  - the visualization front-end (docs/demo/) under `mnemo_viz/`
  - the agent prompt markdown files (src/mnemo/setup/prompts/*.md)

No host-specific path is hard-coded; PyInstaller's collect_* helpers locate
each dependency relative to the active Python environment, so this file
works identically in CI matrix jobs (macOS / Linux / Windows) and in local
developer builds.
"""

from pathlib import Path

from PyInstaller.utils.hooks import (
    collect_data_files,
    collect_dynamic_libs,
    copy_metadata,
)

PROJECT_ROOT = Path(SPECPATH).resolve()
VIZ_SRC = PROJECT_ROOT / "docs" / "demo"

# ---------------------------------------------------------------------------
# Resource collection
# ---------------------------------------------------------------------------

# sqlite_vec ships a platform-specific shared library. collect_dynamic_libs
# picks the correct one (.dylib on macOS, .so on Linux, .dll on Windows).
binaries = collect_dynamic_libs("sqlite_vec")

datas = []

# jieba dictionaries (pure data — fine to ship across platforms)
datas += collect_data_files("jieba")

# fastmcp ships JSON schemas / templates — needed at runtime
datas += collect_data_files("fastmcp")

# fastmcp and mcp both call importlib.metadata.version() at import time;
# without the dist-info metadata PyInstaller binaries crash on startup.
datas += copy_metadata("fastmcp")
datas += copy_metadata("mcp")

# Bundled prompt markdown — single source of truth for setup + MCP instructions
datas += collect_data_files(
    "mnemo.setup.prompts",
    includes=["*.md"],
)

# Visualization front-end — copied to <bundle>/mnemo_viz so server.app's
# _resolve_viz_dir() can find it via sys._MEIPASS at runtime.
if VIZ_SRC.is_dir():
    for path in VIZ_SRC.rglob("*"):
        if not path.is_file():
            continue
        # Skip transient / build artifacts if any sneak in
        if any(part.startswith(".") for part in path.relative_to(VIZ_SRC).parts):
            continue
        rel_dir = path.relative_to(VIZ_SRC).parent
        dest = str(Path("mnemo_viz") / rel_dir) if str(rel_dir) != "." else "mnemo_viz"
        datas.append((str(path), dest))


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------

a = Analysis(
    [str(PROJECT_ROOT / "scripts" / "pyinstaller_entry.py")],
    pathex=[str(PROJECT_ROOT / "src")],
    binaries=binaries,
    datas=datas,
    hiddenimports=[
        # CLI / app entrypoints
        "mnemo.cli.main",
        "mnemo.server.app",
        "mnemo.mcp.server",
        "mnemo.setup.command",
        "mnemo.setup.prompt_template",
        # Async SQLite stack
        "aiosqlite",
        "sqlite_vec",
        # uvicorn dynamic dispatch chain
        "uvicorn",
        "uvicorn.logging",
        "uvicorn.loops.auto",
        "uvicorn.loops.asyncio",
        "uvicorn.protocols.http.auto",
        "uvicorn.protocols.http.h11_impl",
        "uvicorn.protocols.http.httptools_impl",
        "uvicorn.protocols.websockets.auto",
        "uvicorn.protocols.websockets.websockets_impl",
        "uvicorn.lifespan.on",
        # Web stack
        "fastapi",
        "starlette",
        "httptools",
        "h11",
        # CLI / TUI
        "typer",
        "click",
        "rich",
        # Data
        "jieba",
        "pydantic",
        "pydantic_settings",
        # Async runtime
        "anyio",
        "anyio._backends._asyncio",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Heavy ML deps we don't actually use at runtime — exclude to keep
        # the binary small. If you add a code path that imports any of these,
        # remove the corresponding line here.
        "torch",
        "transformers",
        "sentence_transformers",
        "numpy",
        "scipy",
        "sklearn",
        "matplotlib",
        "PIL",
        "tkinter",
    ],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="mnemo",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,  # `strip` not available on Windows; macOS/Linux save < 5MB
    upx=False,    # UPX often triggers AV false positives on Windows
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
