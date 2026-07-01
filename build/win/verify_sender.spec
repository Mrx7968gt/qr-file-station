# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec · verify_sender.exe

把 verify_sender.py 打包成 Windows 单文件可执行程序。
- 单文件模式(onefile),双击即可运行,无需安装 Python。
- 显式收集 pygame / qrcode / pillow 的数据文件,避免缺资源。
- 窗口模式(无控制台黑框):console=False。如需看报错,临时改 console=True。

用法(在仓库根目录执行,或在 build/win 下执行都行——spec 会自动定位根目录的脚本):
    pyinstaller build/win/verify_sender.spec --clean --noconfirm
产物: dist/verify_sender.exe
"""

import os
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

block_cipher = None

# 定位仓库根目录:本 spec 位于 <root>/build/win/ 下。
# PyInstaller 注入 SPECPATH = spec 所在目录;根目录是其上两级。
_ROOT = os.path.abspath(os.path.join(SPECPATH, "..", ".."))
# 入口脚本 verify_sender.py 位于仓库根目录。
_ENTRY = os.path.join(_ROOT, "verify_sender.py")

# 收集隐式数据文件(字体、图标、二进制资源)
datas = []
datas += collect_data_files("pygame")
datas += collect_data_files("qrcode")
datas += collect_data_files("PIL")

# 隐式导入(pygame 的某些子模块是动态加载的)
hiddenimports = []
hiddenimports += collect_submodules("pygame")
hiddenimports += collect_submodules("qrcode")

a = Analysis(
    [_ENTRY],
    # 把仓库根目录加入搜索路径,确保 verify_sender.py 的依赖能被找到
    pathex=[_ROOT],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
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
    name="verify_sender",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,                 # 用 UPX 压缩,缩小 exe 体积
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,            # 窗口程序,无黑框;调试时改 True
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,                # 可选:放个 .ico 路径进来
)
