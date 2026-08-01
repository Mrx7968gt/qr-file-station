# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec - QR File Station v3 encoder (cross-platform)

Lightweight encoder: zstd + LT fountain + binary frames.
No GUI framework (unlike sender.spec which bundles PyQt6).

Usage:
    pyinstaller build/encoder.spec --clean --noconfirm
Product: dist/qr-encoder (Linux) or dist/qr-encoder.exe (Windows)
"""

import os
import importlib.util as _ilu

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

block_cipher = None

_ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
_ENTRY = os.path.join(_ROOT, "qr_encoder.py")

_spec = _ilu.spec_from_file_location("version", os.path.join(_ROOT, "bridge", "version.py"))
_mod = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
VERSION = _mod.VERSION
_EXE_NAME = f"qr-encoder-v{VERSION}"

datas = []
datas += collect_data_files("qrcode")
datas += collect_data_files("PIL")
datas.append((os.path.join(_ROOT, "bridge"), "bridge"))

hiddenimports = []
hiddenimports += collect_submodules("qrcode")
hiddenimports += collect_submodules("bridge")
hiddenimports += ["reedsolo", "zstandard", "concurrent.futures"]

a = Analysis(
    [_ENTRY],
    pathex=[_ROOT],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "PyQt6", "PyQt5", "PySide6", "pygame",
        "cv2", "numpy", "pyzbar",
        "tests", "pytest", "tkinter", "unittest", "pydoc",
    ],
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz, a.scripts, a.binaries, a.zipfiles, a.datas, [],
    name=_EXE_NAME, debug=False, bootloader_ignore_signals=False,
    strip=False, upx=True, upx_exclude=[], runtime_tmpdir=None,
    console=True, disable_windowed_traceback=False,
    target_arch=None, codesign_identity=None, entitlements_file=None,
    icon=None,
)
