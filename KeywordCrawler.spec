# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for KeywordCrawler — single-file portable .exe."""

a = Analysis(
    ["run_gui.py"],
    pathex=[],
    binaries=[],
    datas=[
        ("crawler/icon.ico", "crawler"),
    ],
    hiddenimports=[
        "googlenewsdecoder",
        "matplotlib.backends.backend_tkagg",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="KeywordCrawler",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=["crawler/icon.ico"],
)
