#!/usr/bin/env python3
"""
bridge.sender.app — 发送端 PyQt6 主程序(GUI + CLI)

工作流(每个文件独立控制):
  - 控制页:文件列表(QTableWidget),每行带「转换」「播放」「显帧」「保存」「加载」按钮
    · 转换:多进程并行生成该文件二维码(可逐个触发)
    · 播放:单文件循环播放(边渲染边播放,缓冲领先)
    · 显帧:输入帧号 → 全屏静态显示(左右键翻页,补扫漏掉的)
    · 保存/加载:渲染结果持久化为 PNG,重启后可恢复
  - 播放页:全屏 QR 显示(QLabel+QPixmap+QTimer)

形态:命令行有参数 → 走 CLI;无参数 → 启动 GUI。

用法:
    python -m bridge.sender.app              # 启动 GUI
    python -m bridge.sender.app report.zip   # 等价 CLI(走 cli.main)
"""

from __future__ import annotations

import datetime
import io
import json
import logging
import os
import sys

# ===== 文件日志(写到程序所在目录,便于调试显帧等问题)=====
# PyInstaller 打包后 sys.executable 是 exe 路径,源码运行用 __file__
_LOG_DIR = os.path.dirname(os.path.abspath(getattr(sys, "frozen", False)
                                           and sys.executable or __file__))
_LOG_PATH = os.path.join(_LOG_DIR, "sender_debug.log")
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(_LOG_PATH, encoding="utf-8"),
    ],
)
log = logging.getLogger("sender")
log.info("=" * 50)
log.info(f"程序启动,日志文件: {_LOG_PATH}")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))


# ============================================================================
# 模块级函数(供多进程子进程 pickle 调用,不能是闭包/嵌套)
# ============================================================================

def render_matrix_bytes(payload, box=10, border=4):
    """
    帧 JSON → QR 矩阵的灰度字节(已按 box 放大,边缘锐利)。纯计算,无 Qt 依赖。

    ★ 关键:在渲染时就把每个模块重复 box 次(近邻放大),而不是生成 1px/模块
    的小图再用 SmoothTransformation 插值放大 —— 插值会让黑白边界变灰,二维码
    无法识别。这里生成 box 放大后的位图,边缘是硬切的纯黑白。

    Args:
        payload: 帧 JSON 字符串
        box: 每个模块的像素数(放大倍数,越大越清晰,但内存/时间略增)
        border: QR 静区(白边)模块数

    Returns:
        (n, bytes):n=放大后的矩阵边长(像素),bytes=灰度(黑0白255)
    """
    import qrcode
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_H,
        box_size=box, border=border,
    )
    qr.add_data(payload)
    qr.make(fit=True)
    matrix = qr.get_matrix()  # bool 二维矩阵,True=黑模块(box_size 在 get_matrix 不生效)
    modules = len(matrix)     # 模块数(含 border)
    n = modules * box         # ★ 放大后的像素边长
    # 直接构造放大后的位图:每个模块占 box×box 像素,值相同(近邻,边缘锐利)
    data = bytearray(n * n)
    for my in range(modules):
        row = matrix[my]
        # 该模块行对应的 box 个像素行
        for by in range(box):
            py = my * box + by
            base = py * n
            for mx in range(modules):
                val = 0 if row[mx] else 255
                # 填充该模块的 box 个像素
                off = base + mx * box
                for _ in range(box):
                    data[off] = val
                    off += 1
    return n, bytes(data)


# ============================================================================
# 入口
# ============================================================================

def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if argv:
        from bridge.sender import cli
        return cli.main(argv)
    return run_gui()


def run_gui() -> int:
    """启动 PyQt6 GUI。"""
    try:
        from PyQt6.QtWidgets import (
            QApplication, QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
            QLabel, QSpinBox, QCheckBox, QFileDialog, QGroupBox, QFormLayout,
            QMessageBox, QTableWidget, QTableWidgetItem, QHeaderView,
            QAbstractItemView, QInputDialog,
        )
        from PyQt6.QtCore import Qt, QTimer, QThread, pyqtSignal, QThreadPool, QRunnable
        from PyQt6.QtGui import QPixmap, QKeySequence, QShortcut, QImage
    except ImportError:
        print(
            "错误:未安装 PyQt6。\n"
            "  pip install PyQt6\n"
            "或使用命令行模式:python -m bridge.sender.cli <文件>",
            file=sys.stderr,
        )
        return 1

    from bridge.common import protocol
    from bridge.sender import builder

    app = QApplication(sys.argv)
    app.setApplicationName("采集卡文件传输 · 发送端")

    # 读取版本号(窗口标题显示)
    try:
        from bridge.version import VERSION
    except Exception:
        VERSION = "?"

    window = QWidget()
    window.setWindowTitle(f"采集卡文件传输 · 发送端 v{VERSION}")
    window.resize(900, 660)

    # 持久快捷键(防 GC,存为 window 属性)
    window._nav_shortcuts = []

    # ===== 控制页 =====
    ctrl_page = QWidget()
    ctrl_layout = QVBoxLayout(ctrl_page)

    # ---- 文件选择 ----
    file_group = QGroupBox("传输内容(每个文件独立转换/播放)")
    file_layout = QVBoxLayout()
    file_btn_row = QHBoxLayout()
    add_file_btn = QPushButton("＋ 添加文件")
    add_dir_btn = QPushButton("＋ 添加目录")
    clear_btn = QPushButton("清空列表")
    file_btn_row.addWidget(add_file_btn)
    file_btn_row.addWidget(add_dir_btn)
    file_btn_row.addStretch(1)
    file_btn_row.addWidget(clear_btn)
    file_layout.addLayout(file_btn_row)

    # 文件表格:列 = 文件名 | 状态 | 块数 | 转换 | 播放 | 显帧 | 保存 | 加载
    COLS = ["文件", "状态", "块数", "转换", "播放", "显帧", "保存", "加载"]
    file_table = QTableWidget(0, len(COLS))
    file_table.setHorizontalHeaderLabels(COLS)
    file_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
    for c in range(1, len(COLS)):
        file_table.horizontalHeader().setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
    file_table.verticalHeader().setVisible(False)
    file_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    file_table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
    file_layout.addWidget(file_table)
    file_group.setLayout(file_layout)
    ctrl_layout.addWidget(file_group)

    # ---- 传输参数 ----
    param_group = QGroupBox("传输参数(影响新转换的文件)")
    form = QFormLayout()
    chunk_spin = QSpinBox(); chunk_spin.setRange(50, 2953); chunk_spin.setValue(protocol.DEFAULT_CHUNK_SIZE)
    fps_spin = QSpinBox(); fps_spin.setRange(1, 60); fps_spin.setValue(12)
    loops_spin = QSpinBox(); loops_spin.setRange(1, 20); loops_spin.setValue(3)
    display_spin = QSpinBox(); display_spin.setRange(0, 4); display_spin.setValue(0)
    fec_check = QCheckBox("启用 FEC 前向纠错(建议开启)")
    fec_check.setChecked(True)
    redundancy_spin = QSpinBox(); redundancy_spin.setRange(1, 50); redundancy_spin.setValue(10); redundancy_spin.setSuffix("%")
    form.addRow("单块字节:", chunk_spin)
    form.addRow("帧率 fps:", fps_spin)
    form.addRow("循环轮数:", loops_spin)
    form.addRow("显示器:", display_spin)
    form.addRow(fec_check)
    form.addRow("FEC 冗余:", redundancy_spin)
    param_group.setLayout(form)
    ctrl_layout.addWidget(param_group)

    status_label = QLabel("就绪。添加文件后,逐个点「转换」生成二维码,再点「播放」或「显帧」。")
    status_label.setWordWrap(True)
    ctrl_layout.addWidget(status_label)
    ctrl_layout.addStretch(1)

    # ===== 播放页(全屏 QR)=====
    play_page = QWidget()
    play_layout = QVBoxLayout(play_page)
    play_layout.setContentsMargins(0, 0, 0, 0)
    qr_label = QLabel()
    qr_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    qr_label.setStyleSheet("background-color:white;")
    play_info = QLabel("准备中…")
    play_info.setStyleSheet("color:#333;font-size:14px;padding:4px;background-color:#eee;")
    play_layout.addWidget(play_info, 0)
    play_layout.addWidget(qr_label, 1)
    play_page.setVisible(False)

    root_layout = QVBoxLayout(window)
    root_layout.setContentsMargins(8, 8, 8, 8)
    root_layout.addWidget(ctrl_page)
    root_layout.addWidget(play_page)

    # ===== 数据结构 =====
    file_rows: list = []
    # 缓存:cache_key → {"result":..., "pixmaps":{idx:pm}, "rendered":bool}
    render_cache: dict = {}

    # 持久化目录
    RENDER_DIR = os.path.join(os.getcwd(), "renders")

    play_state: dict = {
        "mode": None,          # "play" | "show_single"
        "pixmaps": {},         # 当前播放/显帧用的 {idx: QPixmap}
        "frames": [],
        "fps": 12, "loops": 3,
        "round": 0, "idx": 0, "total": 0,
        "timer": None,
        "render_done": 0,      # 已渲染帧数(缓冲判断)
        "cache_key": None,
        "result": None,
        "current_row": None,
        "single_idx": 0,
    }

    # ===== 表格行管理 =====
    def add_files(paths):
        for p in paths:
            if any(r["path"] == p for r in file_rows):
                continue
            row = file_table.rowCount()
            file_table.insertRow(row)
            name = os.path.basename(p)
            file_table.setItem(row, 0, QTableWidgetItem(name))
            file_table.setItem(row, 1, QTableWidgetItem("未转换"))
            file_table.setItem(row, 2, QTableWidgetItem("-"))
            btns = {}
            for col, text in ((3, "转换"), (4, "播放"), (5, "显帧"), (6, "保存"), (7, "加载")):
                btn = QPushButton(text)
                file_table.setCellWidget(row, col, btn)
                btns[col] = btn
            fr = {"path": p, "cache_key": None, "result": None, "rendered": False}
            file_rows.append(fr)
            btns[3].clicked.connect(lambda _, r=row: convert_file(r))
            btns[4].clicked.connect(lambda _, r=row: play_file(r))
            btns[5].clicked.connect(lambda _, r=row: show_frame_dialog(r))
            btns[6].clicked.connect(lambda _, r=row: save_render(r))
            btns[7].clicked.connect(lambda _, r=row: load_render(r))

    def add_files_dialog():
        paths, _ = QFileDialog.getOpenFileNames(window, "选择文件", "")
        add_files(paths)

    def add_dir_dialog():
        d = QFileDialog.getExistingDirectory(window, "选择目录", "")
        if d:
            add_files([d])

    def clear_all():
        stop_playback()
        file_table.setRowCount(0)
        file_rows.clear()
        status_label.setText("已清空列表。")

    add_file_btn.clicked.connect(add_files_dialog)
    add_dir_btn.clicked.connect(add_dir_dialog)
    clear_btn.clicked.connect(clear_all)

    # ===== 工具函数 =====
    def scale_pixmap_to_label(pm: QPixmap) -> QPixmap:
        avail = qr_label.size()
        if avail.width() < 2 or avail.height() < 2:
            return pm
        # ★ 用 FastTransformation(最近邻)而非 SmoothTransformation(双线性):
        # 二维码是纯黑白方格,插值会让边缘变灰导致识别率下降。
        # 最近邻保持锐利的黑白边界,即使放大也不模糊。
        return pm.scaled(avail, Qt.AspectRatioMode.KeepAspectRatio,
                         Qt.TransformationMode.FastTransformation)

    def compute_cache_key(paths, chunk_size, use_fec, fec_redundancy):
        import hashlib
        h = hashlib.md5()
        for p in sorted(paths):
            h.update(p.encode("utf-8"))
            if os.path.isfile(p):
                with open(p, "rb") as f:
                    h.update(f.read())
            elif os.path.isdir(p):
                for root, _d, files in os.walk(p):
                    for fn in sorted(files):
                        fp = os.path.join(root, fn)
                        h.update(fp.encode("utf-8"))
                        with open(fp, "rb") as f:
                            h.update(f.read())
        h.update(f"|cs={chunk_size}|fec={use_fec}|r={fec_redundancy}".encode())
        return h.hexdigest()

    def pixmap_from_bytes(n, data) -> QPixmap:
        """灰度字节 → QPixmap(主线程调用)。"""
        img = QImage(data, n, n, n, QImage.Format.Format_Grayscale8).copy()
        return QPixmap.fromImage(img)

    # ===== LRU 缓存:防止大文件所有帧常驻内存导致爆内存 =====
    # 单帧 box=10 时约 5MB,1000帧=5GB 必爆。
    # LRU 限制缓存的帧数,超出淘汰最旧的,内存恒定。
    from collections import OrderedDict as _OrderedDict

    class PixmapLRUCache:
        """带容量上限的 LRU 缓存。超出 max_size 淘汰最久未访问的帧。"""
        def __init__(self, max_size=100):
            self._data = _OrderedDict()
            self.max_size = max_size
        def __contains__(self, idx):
            return idx in self._data
        def __len__(self):
            return len(self._data)
        def get(self, idx):
            """访问 idx,标记为最近使用。返回 pixmap 或 None。"""
            if idx not in self._data:
                return None
            self._data.move_to_end(idx)
            return self._data[idx]
        def put(self, idx, pm):
            """存入 pixmap,超出容量淘汰最旧的。"""
            if idx in self._data:
                self._data.move_to_end(idx)
            self._data[idx] = pm
            while len(self._data) > self.max_size:
                self._data.popitem(last=False)  # 淘汰最旧的
        def keys(self):
            return list(self._data.keys())

    def ensure_rendered(idx, pixmaps, result):
        """
        确保某帧已渲染并在缓存中(若没有则同步渲染单帧)。
        pixmaps 是 PixmapLRUCache。返回 True 表示已就绪。
        """
        if pixmaps.get(idx) is not None:
            return True
        try:
            n, data = render_matrix_bytes(result.frames[idx])
            pixmaps.put(idx, pixmap_from_bytes(n, data))
            return True
        except Exception:
            return False

    # ===== 多线程渲染 worker(QThreadPool,同进程无 spawn 问题)=====
    # ★ 为什么用多线程而非多进程:
    #   多进程(ProcessPoolExecutor)在 PyInstaller 打包后,Windows spawn 模式
    #   会在子进程的 __main__ 找不到 worker 函数(_mp_render_one)而崩溃。
    #   多线程同进程,无此问题,PyInstaller 打包零适配。
    #   代价:GIL 限制下不能多核并行,但 qrcode 的部分计算会释放 GIL,
    #   且关键价值是「UI 不卡 + 边渲染边播放」,多线程已足够。
    class MultiThreadRenderWorker(QThread):
        frame_ready = pyqtSignal(int, int, bytes)   # (idx, n, data)
        progress = pyqtSignal(int, int)             # (done, total)
        all_done = pyqtSignal()
        def __init__(self, frames, box=10, border=4, nthreads=0):
            super().__init__()
            self.frames = frames
            self.box = box
            self.border = border
            # 线程数:默认用 CPU 核数(qrcode 部分计算释放 GIL,多线程仍有益)
            self.nthreads = nthreads or max(1, (os.cpu_count() or 2))
            self._stop = False
        def stop(self):
            self._stop = True
        def run(self):
            from concurrent.futures import ThreadPoolExecutor
            total = len(self.frames)
            done_count = 0
            fail_count = 0
            # 用 ThreadPoolExecutor 并发渲染;帧完成后立即发信号(边渲染边播放)
            with ThreadPoolExecutor(max_workers=self.nthreads) as pool:
                futs = {}
                for i, payload in enumerate(self.frames):
                    if self._stop:
                        break
                    futs[pool.submit(render_matrix_bytes, payload, self.box, self.border)] = i
                # 收集结果。失败的帧重试一次,仍失败则记录(不再静默丢弃)
                for fut in futs:
                    if self._stop:
                        break
                    idx = futs[fut]
                    try:
                        n, data = fut.result()
                    except Exception as e:
                        # 首次失败:重试一次
                        log.warning(f"帧 {idx} 首次渲染失败({e}),重试中…")
                        try:
                            n, data = render_matrix_bytes(self.frames[idx], self.box, self.border)
                        except Exception as e2:
                            log.error(f"帧 {idx} 重试仍失败: {e2}")
                            fail_count += 1
                            continue
                    self.frame_ready.emit(idx, n, data)
                    done_count += 1
                    self.progress.emit(done_count, total)
            if fail_count > 0:
                log.error(f"渲染完成但有 {fail_count}/{total} 帧失败(已跳过)")
            else:
                log.info(f"全部 {total} 帧渲染完成")
            self.all_done.emit()

    # ===== 转换单个文件(只 build 帧序列,不预渲染——按需渲染防爆内存)=====
    def convert_file(row):
        if row < 0 or row >= len(file_rows):
            return
        fr = file_rows[row]
        # 已转换则跳过
        if fr.get("rendered") and fr.get("cache_key") in render_cache:
            status_label.setText(f"{os.path.basename(fr['path'])} 已转换。")
            return
        chunk_size = chunk_spin.value()
        use_fec = fec_check.isChecked()
        fec_redundancy = redundancy_spin.value() / 100.0
        ck = compute_cache_key([fr["path"]], chunk_size, use_fec, fec_redundancy)
        fr["cache_key"] = ck

        # 缓存命中(已 build 过)?
        cached = render_cache.get(ck)
        if cached:
            fr["result"] = cached["result"]
            fr["rendered"] = True
            file_table.item(row, 1).setText("已转换")
            file_table.item(row, 2).setText(str(cached["result"].total_data_chunks))
            status_label.setText(f"✓ {os.path.basename(fr['path'])} 命中缓存。")
            return

        status_label.setText(f"构建帧序列 {os.path.basename(fr['path'])}…")
        app.processEvents()
        try:
            result = builder.build([fr["path"]], chunk_size=chunk_size,
                                   use_fec=use_fec, fec_redundancy=fec_redundancy)
        except Exception as e:
            file_table.item(row, 1).setText("转换失败")
            QMessageBox.critical(window, "转换失败", str(e))
            return
        fr["result"] = result
        # ★ 不再全量预渲染!只存帧序列,渲染推迟到播放/显帧时按需进行(LRU 缓存防爆内存)
        # 这样 convert 秒级完成,内存恒定(不随帧数增长)
        render_cache[ck] = {
            "result": result,
            "pixmaps": PixmapLRUCache(max_size=100),  # LRU:最多缓存100帧(~500MB)
            "rendered": True,  # 标记"已转换"(帧序列就绪,可播放/显帧)
        }
        file_table.item(row, 1).setText("已转换")
        file_table.item(row, 2).setText(str(result.total_data_chunks))
        status_label.setText(
            f"✓ {os.path.basename(fr['path'])} 就绪:数据{result.total_data_chunks}块/共{len(result.frames)}帧。"
            f"点「播放」开始(边播边渲染)或「显帧」补扫。"
        )
        log.info(f"[转换] {os.path.basename(fr['path'])} 帧序列就绪 {len(result.frames)} 帧(按需渲染)")
        ck = compute_cache_key([fr["path"]], chunk_size, use_fec, fec_redundancy)
        fr["cache_key"] = ck

        # 缓存命中?
        cached = render_cache.get(ck)
        if cached and cached.get("rendered"):
            fr["result"] = cached["result"]
            fr["rendered"] = True
            file_table.item(row, 1).setText("已转换")
            file_table.item(row, 2).setText(str(cached["result"].total_data_chunks))
            status_label.setText(f"✓ {os.path.basename(fr['path'])} 命中缓存。")
            return

    # ===== 播放单个文件(循环,边渲染边播放 + 缓冲)=====
    def play_file(row):
        if row < 0 or row >= len(file_rows):
            return
        fr = file_rows[row]
        ck = fr.get("cache_key")
        cached = render_cache.get(ck) if ck else None
        if not cached:
            QMessageBox.information(window, "提示", "请先点「转换」生成该文件的二维码。")
            return

        result = cached["result"]
        pixmaps = cached["pixmaps"]  # PixmapLRUCache(按需渲染,内存恒定)

        play_state.update({
            "mode": "play", "current_row": row,
            "pixmaps": pixmaps, "frames": result.frames,
            "fps": fps_spin.value(), "loops": loops_spin.value(),
            "round": 0, "idx": 0, "total": len(result.frames),
            "cache_key": ck, "result": result,
            "render_done": len(pixmaps),
        })

        # 进入播放页
        ctrl_page.setVisible(False)
        play_page.setVisible(True)
        window.showFullScreen()
        app.processEvents()

        # ★ 不预渲染全部帧(会爆内存)。播放时 on_tick 按需即时渲染当前帧(LRU 缓存)。
        # 可选:后台预热当前帧之后的少量帧(预读,让播放更顺)。这里保持简单,纯按需。

        # 启动播放定时器
        timer = QTimer(window)
        interval = int(1000 / fps_spin.value()) if fps_spin.value() > 0 else 100
        timer.timeout.connect(on_tick)
        timer.start(interval)
        play_state["timer"] = timer
        play_info.setText(f"播放 {os.path.basename(fr['path'])}  第 1/{loops_spin.value()} 轮")

    # ===== 播放循环(按需即时渲染当前帧,LRU 缓存防爆内存 + 后台预读)=====
    # 预读:显示当前帧后,后台预热后续 N 帧,让播放始终领先、循环不卡顿
    play_state["_prefetching"] = False  # 预读进行中标志(防重复启动)

    def prefetch_ahead(current_idx, pixmaps, result, ahead=5):
        """后台预读 ahead 帧(若未在缓存),放进 LRU。已在预读则跳过。"""
        if play_state.get("_prefetching"):
            return
        total = len(result.frames)
        # 收集需要预读的帧号(未在缓存)
        need = []
        for off in range(1, ahead + 1):
            ni = (current_idx + off) % total
            if pixmaps.get(ni) is None:
                need.append(ni)
        if not need:
            return
        play_state["_prefetching"] = True
        # 用后台线程预读(不阻塞当前 tick)
        worker = MultiThreadRenderWorker([result.frames[ni] for ni in need],
                                         box=10, border=4, nthreads=2)
        # need[i] 对应 worker.frames[i];渲染完成回填到正确 idx
        def _frame(i, n, data):
            real_idx = need[i]
            pixmaps.put(real_idx, pixmap_from_bytes(n, data))
        def _done():
            play_state["_prefetching"] = False
        worker.frame_ready.connect(_frame)
        worker.all_done.connect(_done)
        worker.start()
        play_state["_prefetch_worker"] = worker  # 防 GC

    def on_tick():
        if play_state.get("mode") != "play":
            return
        idx = play_state["idx"]
        pixmaps = play_state["pixmaps"]
        result = play_state["result"]
        # ★ 即时渲染当前帧(若不在 LRU 缓存),单帧 ~50ms 很快
        pm = pixmaps.get(idx)
        if pm is None:
            if not ensure_rendered(idx, pixmaps, result):
                play_info.setText(f"第 {idx+1} 帧渲染失败,跳过")
                play_state["idx"] += 1
                if play_state["idx"] >= play_state["total"]:
                    play_state["round"] += 1
                    play_state["idx"] = 0
                    if play_state["round"] >= play_state["loops"]:
                        finish_playback(True)
                return
            pm = pixmaps.get(idx)
        qr_label.setPixmap(scale_pixmap_to_label(pm))
        done = play_state["round"] * play_state["total"] + idx + 1
        allf = play_state["total"] * play_state["loops"]
        pct = int(done / allf * 100) if allf else 0
        play_info.setText(f"第 {play_state['round']+1}/{play_state['loops']} 轮   帧 {idx+1}/{play_state['total']}   {pct}%")
        # ★ 显示完当前帧后,后台预读后续帧(消除循环/连续播放的卡顿)
        prefetch_ahead(idx, pixmaps, result, ahead=5)
        play_state["idx"] += 1
        if play_state["idx"] >= play_state["total"]:
            play_state["round"] += 1
            play_state["idx"] = 0
            if play_state["round"] >= play_state["loops"]:
                finish_playback(True)

    # ===== 显帧(输入帧号 → 全屏静态显示 + 左右键翻页)=====
    def show_frame_dialog(row):
        log.info(f"[显帧] 点击显帧, row={row}")
        try:
            if row < 0 or row >= len(file_rows):
                log.warning(f"[显帧] row 越界: {row} / {len(file_rows)}")
                return
            fr = file_rows[row]
            ck = fr.get("cache_key")
            log.info(f"[显帧] cache_key={ck}")
            cached = render_cache.get(ck) if ck else None
            if not cached:
                log.warning(f"[显帧] 缓存为空,未转换")
                QMessageBox.information(window, "提示", "请先点「转换」生成该文件的二维码。")
                return
            result = cached["result"]
            total = len(result.frames)
            log.info(f"[显帧] 总帧数={total}, 已渲染={len(cached['pixmaps'])}, rendered={cached.get('rendered')}")
            # ★ PyQt6 的 getInt 参数名是 min/max(非 PyQt5 的 minValue/maxValue)
            idx, ok = QInputDialog.getInt(
                window, "显示指定帧",
                f"输入要显示的帧号(1~{total}):\n(用于补扫接收端漏掉的某帧)\n显示后可用 ← → 翻页",
                value=1, min=1, max=total,
            )
            if not ok:
                log.info("[显帧] 用户取消输入")
                return
            log.info(f"[显帧] 用户输入帧号={idx}, 进入显示")
            enter_show_single(row, idx - 1)
        except Exception as e:
            log.exception(f"[显帧] show_frame_dialog 异常: {e}")
            QMessageBox.critical(window, "显帧错误", f"显帧出错: {e}\n详情见 sender_debug.log")

    def enter_show_single(row, idx):
        """进入单帧显示模式。"""
        log.info(f"[显帧] enter_show_single row={row} idx={idx}")
        try:
            fr = file_rows[row]
            ck = fr["cache_key"]
            cached = render_cache.get(ck)
            if not cached:
                log.error(f"[显帧] 缓存丢失 ck={ck}")
                return
            result = cached["result"]
            pixmaps = cached["pixmaps"]
            log.info(f"[显帧] result.frames={len(result.frames)}, pixmaps={len(pixmaps)}, idx={idx}")

            play_state.update({
                "mode": "show_single", "current_row": row,
                "pixmaps": pixmaps, "frames": result.frames,
                "total": len(result.frames), "result": result,
                "cache_key": ck, "single_idx": idx,
                "timer": None,
            })

            # 切到播放页全屏
            ctrl_page.setVisible(False)
            play_page.setVisible(True)
            window.showFullScreen()
            app.processEvents()
            log.info("[显帧] 已切全屏")

            # 确保该帧已渲染(没有就同步渲染单帧,很快)
            if not ensure_rendered(idx, pixmaps, result):
                log.error(f"[显帧] 帧渲染失败 idx={idx}")
                play_info.setText("该帧渲染失败。")
                return
            log.info(f"[显帧] 帧已就绪 idx={idx}, 显示中")
            display_single(idx)
            # 启用持久快捷键(防 GC)
            _enable_nav_shortcuts()
            log.info("[显帧] 快捷键已启用,等待用户操作")
        except Exception as e:
            log.exception(f"[显帧] enter_show_single 异常: {e}")
            # 异常时回到控制页,不退出程序
            try:
                play_page.setVisible(False)
                ctrl_page.setVisible(True)
                window.showNormal()
            except Exception:
                pass
            QMessageBox.critical(window, "显帧错误", f"显帧出错: {e}\n详情见 sender_debug.log")

    def display_single(idx):
        """显示单帧(已确保渲染)。pixmaps 是 PixmapLRUCache。"""
        pixmaps = play_state["pixmaps"]
        pm = pixmaps.get(idx)
        if pm is None:
            return
        qr_label.setPixmap(scale_pixmap_to_label(pm))
        total = play_state["total"]
        play_info.setText(f"显示第 {idx+1}/{total} 帧(← → 翻页,ESC 返回)")

    def nav_single(delta):
        if play_state.get("mode") != "show_single":
            return
        total = play_state["total"]
        new_idx = (play_state["single_idx"] + delta) % total
        play_state["single_idx"] = new_idx
        ensure_rendered(new_idx, play_state["pixmaps"], play_state["result"])
        display_single(new_idx)

    def _enable_nav_shortcuts():
        """启用左右键翻页快捷键(持久存为 window 属性防 GC)。"""
        # 先禁用旧的
        for sc in window._nav_shortcuts:
            sc.setEnabled(False)
        scs = []
        left = QShortcut(QKeySequence(Qt.Key.Key_Left), window)
        left.activated.connect(lambda: nav_single(-1))
        right = QShortcut(QKeySequence(Qt.Key.Key_Right), window)
        right.activated.connect(lambda: nav_single(1))
        scs.extend([left, right])
        window._nav_shortcuts = scs

    def _disable_nav_shortcuts():
        for sc in window._nav_shortcuts:
            sc.setEnabled(False)
        window._nav_shortcuts = []

    # ===== 持久化:保存/加载渲染结果(PNG 序列)=====
    def save_render(row):
        if row < 0 or row >= len(file_rows):
            return
        fr = file_rows[row]
        ck = fr.get("cache_key")
        cached = render_cache.get(ck) if ck else None
        if not cached:
            QMessageBox.information(window, "提示", "请先「转换」并等渲染完成后再保存。")
            return
        if not cached.get("rendered"):
            QMessageBox.information(window, "提示", "渲染尚未完成,请等状态变为「已转换」再保存。")
            return
        result = cached["result"]
        pixmaps = cached["pixmaps"]
        # 选保存目录
        default_dir = os.path.join(RENDER_DIR, os.path.basename(fr["path"]))
        d = QFileDialog.getExistingDirectory(window, "选择保存目录", default_dir)
        if not d:
            return
        os.makedirs(d, exist_ok=True)
        # 存元数据 + 配置
        meta = {
            "path": fr["path"],
            "chunk_size": chunk_spin.value(),
            "use_fec": fec_check.isChecked(),
            "fec_redundancy": redundancy_spin.value() / 100.0,
            "total_frames": len(result.frames),
            "total_data_chunks": result.total_data_chunks,  # 数据帧数(前端口径)
            "frames": result.frames,  # 所有帧 JSON
            "sid": result.sid,
        }
        with open(os.path.join(d, "meta.json"), "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False)
        # ★ 存每帧 PNG:按需渲染(LRU 可能没缓存该帧),渲染完即存盘即释放
        from PyQt6.QtCore import QBuffer, QByteArray, QIODevice
        saved = 0
        total_frames = len(result.frames)
        for idx in range(total_frames):
            # ensure_rendered 会渲染并放进 LRU(可能淘汰旧的,内存恒定)
            if not ensure_rendered(idx, pixmaps, result):
                log.warning(f"[保存] 帧 {idx} 渲染失败,跳过")
                continue
            pm = pixmaps.get(idx)
            if pm is None:
                continue
            buf = QBuffer()
            buf.open(QIODevice.OpenModeFlag.WriteOnly)
            pm.save(buf, "PNG")
            with open(os.path.join(d, f"frame_{idx:05d}.png"), "wb") as pf:
                pf.write(bytes(buf.buffer()))
            saved += 1
            if saved % 50 == 0:
                status_label.setText(f"保存中 {saved}/{total_frames} …(按需渲染,内存安全)")
                app.processEvents()
        status_label.setText(f"✓ 已保存 {saved} 帧到 {d}")

    def load_render(row):
        if row < 0 or row >= len(file_rows):
            return
        d = QFileDialog.getExistingDirectory(window, "选择已保存的渲染目录(含 meta.json)")
        if not d:
            return
        meta_path = os.path.join(d, "meta.json")
        if not os.path.isfile(meta_path):
            QMessageBox.critical(window, "错误", "该目录没有 meta.json,不是有效的渲染保存。")
            return
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                meta = json.load(f)
        except Exception as e:
            QMessageBox.critical(window, "错误", f"读取 meta.json 失败: {e}")
            return
        frames = meta["frames"]
        total = meta["total_frames"]
        # 数据帧数:优先用 meta 里的 total_data_chunks,老文件回退计算
        total_data_chunks = meta.get("total_data_chunks")
        if total_data_chunks is None:
            # 老保存文件没有此字段:从 frames 里统计数据帧
            import json as _json
            total_data_chunks = sum(
                1 for fj in frames
                if _json.loads(fj).get("type") == "data"
                and not _json.loads(fj).get("is_fec")
            )
        # 重建 result(用 builder 的 BuildResult)
        from bridge.sender.builder import BuildResult
        result = BuildResult(sid=meta.get("sid", ""), frames=frames,
                             file_count=1, total_data_chunks=total_data_chunks)
        # ★ 用 LRU 缓存(防爆内存),并记录帧 PNG 目录供缓存未命中时从磁盘回源
        load_dir = d
        class _DiskBackedLRU(PixmapLRUCache):
            """LRU 缓存,未命中时从磁盘加载 PNG(而非重新渲染)。"""
            def get(self, idx):
                pm = super().get(idx)
                if pm is not None:
                    return pm
                # 从磁盘加载
                png_path = os.path.join(load_dir, f"frame_{idx:05d}.png")
                if os.path.isfile(png_path):
                    pm = QPixmap()
                    if pm.load(png_path):
                        self.put(idx, pm)
                        return pm
                return None
        pixmaps = _DiskBackedLRU(max_size=100)
        # 预加载前几帧(让首屏快速显示)
        loaded = 0
        for idx in range(min(total, 30)):
            png_path = os.path.join(d, f"frame_{idx:05d}.png")
            if not os.path.isfile(png_path):
                continue
            pm = QPixmap()
            if pm.load(png_path):
                pixmaps.put(idx, pm)
                loaded += 1
        # 统计总可用帧
        total_avail = sum(1 for idx in range(total)
                          if os.path.isfile(os.path.join(d, f"frame_{idx:05d}.png")))
        if total_avail == 0:
            QMessageBox.critical(window, "错误", "没有加载到任何帧 PNG。")
            return
        loaded = total_avail
        # 用 meta 的路径+参数算 cache_key,存入缓存
        ck = compute_cache_key([meta["path"]], meta["chunk_size"],
                               meta["use_fec"], meta["fec_redundancy"])
        render_cache[ck] = {"result": result, "pixmaps": pixmaps, "rendered": True}
        fr = file_rows[row]
        fr["cache_key"] = ck
        fr["result"] = result
        fr["rendered"] = (loaded >= total)
        file_table.item(row, 1).setText(f"已加载 {loaded}/{total}帧")
        file_table.item(row, 2).setText(str(total_data_chunks))
        status_label.setText(f"✓ 从 {d} 加载 {loaded}/{total} 帧(数据{total_data_chunks}块),可直接播放/显帧。")

    # ===== 结束/停止 =====
    def finish_playback(completed: bool):
        # 停定时器
        t = play_state.get("timer")
        if t:
            t.stop(); play_state["timer"] = None
        # 停预读 worker(若在运行)
        play_state["_prefetching"] = False
        pw = play_state.get("_prefetch_worker")
        if pw is not None:
            try:
                pw.stop(); pw.wait(1000)
            except Exception:
                pass
            play_state["_prefetch_worker"] = None
        _disable_nav_shortcuts()
        play_state["mode"] = None

        play_page.setVisible(False)
        ctrl_page.setVisible(True)
        window.showNormal()
        qr_label.clear()

        if not completed:
            status_label.setText("已停止。可继续转换其它文件或重新播放。")
            return

        row = play_state.get("current_row")
        name = os.path.basename(file_rows[row]["path"]) if row is not None and row < len(file_rows) else "?"
        info = (
            f"「{name}」已循环播放 {play_state['loops']} 轮。\n\n"
            f"请到接收端确认是否收齐。\n"
            f"· 已收齐 → 「完成」\n"
            f"· 漏了几帧 → 用「显帧」单独补扫\n"
            f"· 想再循环 → 「重试」(秒级,复用缓存)"
        )
        msg = QMessageBox(window)
        msg.setWindowTitle("播放完成")
        msg.setText(info)
        retry_btn = msg.addButton("🔄 重试", QMessageBox.ButtonRole.AcceptRole)
        msg.addButton("✓ 完成", QMessageBox.ButtonRole.RejectRole)
        msg.exec()
        if msg.clickedButton() is retry_btn and row is not None:
            play_file(row)
        else:
            status_label.setText(f"✓ {name} 播放完成。可选择其它文件继续。")

    def stop_playback():
        if play_state.get("mode") is not None:
            finish_playback(False)

    # ESC 停止/返回
    esc_sc = QShortcut(QKeySequence("Escape"), window)
    esc_sc.activated.connect(stop_playback)

    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
