# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for the portable CourseAssistant.exe
#
# Build with:
#   .venv\Scripts\pyinstaller.exe course_assistant.spec
#
# Output lands in dist/CourseAssistant/ — zip that folder to distribute.
# The user's data (transcripts/) sits next to the .exe and survives upgrades.

block_cipher = None

a = Analysis(
    ["run.py"],
    pathex=["."],
    binaries=[],
    datas=[
        # Static web assets (HTML/JS/CSS) — served by FastAPI at runtime.
        ("static", "static"),
    ],
    hiddenimports=[
        # uvicorn internals that are imported dynamically at startup
        "uvicorn.logging",
        "uvicorn.loops",
        "uvicorn.loops.auto",
        "uvicorn.loops.asyncio",
        "uvicorn.protocols",
        "uvicorn.protocols.http",
        "uvicorn.protocols.http.auto",
        "uvicorn.protocols.http.h11_impl",
        "uvicorn.protocols.websockets",
        "uvicorn.protocols.websockets.auto",
        "uvicorn.protocols.websockets.websockets_impl",
        "uvicorn.lifespan",
        "uvicorn.lifespan.on",
        # FastAPI / starlette
        "starlette.routing",
        "starlette.staticfiles",
        "starlette.responses",
        "starlette.middleware",
        "starlette.middleware.cors",
        # pydantic v2 validators
        "pydantic.v1",
        "pydantic_core",
        # anyio backend
        "anyio",
        "anyio._backends",
        "anyio._backends._asyncio",
        # http
        "h11",
        "multipart",
        "python_multipart",
        # stdlib extras often missed
        "email.mime.text",
        "email.mime.multipart",
        "sqlite3",
        "_sqlite3",
        "winreg",
        "tkinter",
        "tkinter.messagebox",
        # app modules (ensure they're all bundled)
        "app.main",
        "app.core",
        "app.database",
        "app.jobs",
        "app.transcribe",
        "app.exports",
        "app.flashcards",
        "app.study",
        "app.secrets",
        "app.settings_store",
        "app.search",
        "app.llm",
        "app.ai",
        "app.study_planner",
        "app.imageextract",
        "app.analytics",
        "app.backup",
        "app.courses",
        "app.sources",
        "app.notion",
        "app.sso_protocol",
        "app.imports.moodle_api",
        "app.imports.moodle_web",
        "app.imports.moodle_resources",
        "app.imports.moodle_sso",
        "app.imports.folder",
        "app.imports.preflight",
        "app.integrations.notion",
        "app.integrations.anki",
        "app.integrations.state",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # Exclude heavy optional deps — installed separately via _packages/ at runtime
    excludes=[
        "whisper",
        "faster_whisper",
        "yt_dlp",
        "torch",
        "torchvision",
        "torchaudio",
        "transformers",
        "numpy",
        "pandas",
        "scipy",
        "matplotlib",
        "IPython",
        "jupyter",
        "notebook",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="CourseAssistant",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,          # console window shows startup logs + port info
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="CourseAssistant",
)
