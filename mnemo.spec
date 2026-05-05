# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_data_files

datas = [('.venv/bin/../lib/python3.12/site-packages/sqlite_vec/vec0.dylib', 'sqlite_vec'), ('.venv/bin/../lib/python3.12/site-packages/jieba/dict.txt', 'jieba')]
datas += collect_data_files('fastmcp')


a = Analysis(
    ['scripts/pyinstaller_entry.py'],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=['mnemo.cli.main', 'mnemo.server.app', 'mnemo.mcp.server', 'aiosqlite', 'sqlite_vec', 'uvicorn', 'uvicorn.logging', 'uvicorn.loops.auto', 'uvicorn.protocols.http.auto', 'uvicorn.protocols.http.httptools_impl', 'uvicorn.protocols.websockets.auto', 'uvicorn.lifespan.on', 'fastapi', 'starlette', 'httptools', 'jieba', 'typer', 'click', 'rich', 'pydantic', 'anyio', 'anyio._backends._asyncio'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['torch', 'transformers', 'sentence_transformers', 'numpy', 'scipy', 'sklearn', 'matplotlib', 'PIL', 'tkinter'],
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
    name='mnemo',
    debug=False,
    bootloader_ignore_signals=False,
    strip=True,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
