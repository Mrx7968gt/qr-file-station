# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec · 采集卡文件传输发送端 sender.exe

把 bridge/sender/app.py 打包成 Windows 单文件可执行程序(GUI)。
- 单文件模式(onefile),双击启动 GUI;带命令行参数则走 CLI。
- 收集 PyQt6 / pygame / qrcode / pillow / reedsolo 的数据文件。
- 窗口模式(console=False)。

用法(在 Windows 上):
    pyinstaller build/win/sender.spec --clean --noconfirm
产物: dist/sender.exe
"""

import os
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

block_cipher = None

# 定位仓库根目录:本 spec 位于 <root>/build/win/ 下
_ROOT = os.path.abspath(os.path.join(SPECPATH, "..", ".."))
# 入口:发送端 GUI/CLI 主程序
_ENTRY = os.path.join(_ROOT, "bridge", "sender", "app.py")

# 收集隐式数据文件(字体、Qt 插件、翻译、图标等资源)
datas = []
datas += collect_data_files("qrcode")
datas += collect_data_files("PIL")
datas += collect_data_files("PyQt6")
datas += collect_data_files("PyQt6.QtCore")
datas += collect_data_files("PyQt6.QtGui")
datas += collect_data_files("PyQt6.QtWidgets")

# 隐式导入
hiddenimports = []
hiddenimports += collect_submodules("qrcode")
hiddenimports += collect_submodules("bridge")
# reedsolo 是纯 Python 单文件,显式声明
hiddenimports += ["reedsolo"]
# Qt 常见隐式依赖
hiddenimports += ["PyQt6.sip"]
# ★ 多进程并行渲染:Windows spawn 模式需要这些
hiddenimports += ["multiprocessing", "multiprocessing.spawn",
                  "multiprocessing.forkserver", "concurrent.futures"]

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
        # GUI 已改用纯 PyQt6 播放(QLabel+QPixmap+QTimer),不再需要 pygame
        "pygame",
        # 排除接收端才用的大库,缩小体积
        "cv2",
        "numpy",
        "pyzbar",
        "tests",
        "pytest",
        "tkinter",
        "unittest",
        "pydoc",
    ],
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="sender",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,                 # UPX 压缩
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,            # GUI 程序无黑框;调试改 True
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,                # 可选:放 .ico 路径
)
