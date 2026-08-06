# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller onedir sidecar — more reliable on macOS than onefile."""

from pathlib import Path

# SPEC is injected by PyInstaller as the absolute path to this .spec file
ROOT = Path(SPEC).resolve().parent.parent  # type: ignore[name-defined]  # noqa: F821
CONFIG = ROOT / "config"

datas = [
    (str(CONFIG / "default_rules.json"), "config"),
    (str(CONFIG / "audit_questions.json"), "config"),
]

hiddenimports = [
    "uvicorn.logging",
    "uvicorn.loops",
    "uvicorn.loops.auto",
    "uvicorn.protocols",
    "uvicorn.protocols.http",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan",
    "uvicorn.lifespan.on",
    "audit_api",
    "audit_api.main",
    "audit_engine",
    "duckdb",
    "pyarrow",
    "openpyxl",
    "pandas",
    "httpx",
    "multipart",
]

a = Analysis(  # noqa: F821
    [str(ROOT / "apps" / "api" / "audit_api" / "__main__.py")],
    pathex=[str(ROOT / "apps" / "api"), str(ROOT / "packages" / "engine")],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "pygments"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=None,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=None)  # noqa: F821

exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="audit-api",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(  # noqa: F821
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="audit-api",
)
