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

import io
import json
import os
import sys

# ★ 多进程 + PyInstaller 兼容:必须在最顶层调用
# Windows spawn 模式下,子进程会重新执行本模块,freeze_support 处理这个情况。
import multiprocessing
if getattr(sys, "frozen", False):  # 仅 PyInstaller 打包后才调用
    multiprocessing.freeze_support()

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))


# ============================================================================
# 模块级函数(供多进程子进程 pickle 调用,不能是闭包/嵌套)
# ============================================================================

def render_matrix_bytes(payload, box=10, border=4):
    """
    帧 JSON → QR 矩阵的灰度字节。纯计算,无 Qt 依赖。
    返回 (n, bytes):n=矩阵边长,bytes=灰度(黑0白255)。
    可在任意线程/进程安全调用。模块级以便多进程 pickle。
    """
    import qrcode
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_H,
        box_size=box, border=border,
    )
    qr.add_data(payload)
    qr.make(fit=True)
    matrix = qr.get_matrix()
    n = len(matrix)
    data = bytearray(n * n)
    for y in range(n):
        row = matrix[y]
        base = y * n
        for x in range(n):
            data[base + x] = 0 if row[x] else 255
    return n, bytes(data)


def _mp_render_one(args):
    """多进程 worker:渲染单帧。args=(index, payload, box, border)。返回 (index, n, bytes)。"""
    index, payload, box, border = args
    n, data = render_matrix_bytes(payload, box, border)
    return (index, n, data)


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

    window = QWidget()
    window.setWindowTitle("采集卡文件传输 · 发送端")
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
        return pm.scaled(avail, Qt.AspectRatioMode.KeepAspectRatio,
                         Qt.TransformationMode.SmoothTransformation)

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

    def ensure_rendered(idx, pixmaps, result):
        """确保某帧已渲染(若没有则同步渲染单帧,很快)。返回 True 表示已就绪。"""
        if idx in pixmaps:
            return True
        try:
            n, data = render_matrix_bytes(result.frames[idx])
            pixmaps[idx] = pixmap_from_bytes(n, data)
            return True
        except Exception:
            return False

    # ===== 多进程渲染 worker(QRunnable,提交到 ProcessPool)=====
    # 用 QThread 包装 ProcessPoolExecutor 的提交,通过信号回主线程
    class MultiProcessRenderWorker(QThread):
        frame_ready = pyqtSignal(int, int, bytes)   # (idx, n, data)
        progress = pyqtSignal(int, int)             # (done, total)
        all_done = pyqtSignal()
        def __init__(self, frames, box=10, border=4):
            super().__init__()
            self.frames = frames
            self.box = box
            self.border = border
            self._stop = False
        def stop(self):
            self._stop = True
        def run(self):
            import concurrent.futures
            total = len(self.frames)
            nproc = max(1, (os.cpu_count() or 2) - 1)
            args = [(i, payload, self.box, self.border)
                    for i, payload in enumerate(self.frames)]
            try:
                with concurrent.futures.ProcessPoolExecutor(max_workers=nproc) as pool:
                    futs = {pool.submit(_mp_render_one, a): a[0] for a in args}
                    done_count = 0
                    for fut in concurrent.futures.as_completed(futs):
                        if self._stop:
                            return
                        idx, n, data = fut.result()
                        self.frame_ready.emit(idx, n, data)
                        done_count += 1
                        self.progress.emit(done_count, total)
            except Exception:
                # 多进程失败(如 PyInstaller 环境)→ 回退单进程
                for i, payload in enumerate(self.frames):
                    if self._stop:
                        return
                    n, data = render_matrix_bytes(payload, self.box, self.border)
                    self.frame_ready.emit(i, n, data)
                    self.progress.emit(i + 1, total)
            self.all_done.emit()

    # ===== 转换单个文件(多进程并行渲染)=====
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

        # 缓存命中?
        cached = render_cache.get(ck)
        if cached and cached.get("rendered"):
            fr["result"] = cached["result"]
            fr["rendered"] = True
            file_table.item(row, 1).setText("已转换")
            file_table.item(row, 2).setText(str(len(cached["result"].frames)))
            status_label.setText(f"✓ {os.path.basename(fr['path'])} 命中缓存。")
            return

        try:
            result = builder.build([fr["path"]], chunk_size=chunk_size,
                                   use_fec=use_fec, fec_redundancy=fec_redundancy)
        except Exception as e:
            file_table.item(row, 1).setText("转换失败")
            QMessageBox.critical(window, "转换失败", str(e))
            return
        fr["result"] = result
        file_table.item(row, 2).setText(str(len(result.frames)))
        file_table.item(row, 1).setText("渲染中 0/%d" % len(result.frames))

        pixmaps = {}
        render_cache[ck] = {"result": result, "pixmaps": pixmaps, "rendered": False}
        status_label.setText(f"多进程渲染 {os.path.basename(fr['path'])}({len(result.frames)} 帧)…")

        worker = MultiProcessRenderWorker(result.frames)

        def _frame(idx, n, data):
            pixmaps[idx] = pixmap_from_bytes(n, data)
            file_table.item(row, 1).setText(f"渲染 {len(pixmaps)}/{len(result.frames)}")

        def _done():
            render_cache[ck]["rendered"] = True
            fr["rendered"] = True
            file_table.item(row, 1).setText("已转换")
            status_label.setText(f"✓ {os.path.basename(fr['path'])} 渲染完成,{len(result.frames)} 帧。")

        worker.frame_ready.connect(_frame)
        worker.all_done.connect(_done)
        worker.start()
        fr["worker"] = worker  # 防 GC

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
        pixmaps = cached["pixmaps"]

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

        # 若渲染未完成,继续后台渲染
        if not cached.get("rendered"):
            worker = MultiProcessRenderWorker(result.frames)
            def _frame(idx, n, data):
                pixmaps[idx] = pixmap_from_bytes(n, data)
                play_state["render_done"] = len(pixmaps)
            def _progress(done, total):
                if play_state["mode"] == "play":
                    play_info.setText(f"渲染中 {done}/{total} + 播放中…")
            def _done():
                cached["rendered"] = True
                fr["rendered"] = True
                file_table.item(row, 1).setText("已转换")
            worker.frame_ready.connect(_frame)
            worker.progress.connect(_progress)
            worker.all_done.connect(_done)
            worker.start()
            play_state["render_worker"] = worker

        # 启动播放定时器
        timer = QTimer(window)
        interval = int(1000 / fps_spin.value()) if fps_spin.value() > 0 else 100
        timer.timeout.connect(on_tick)
        timer.start(interval)
        play_state["timer"] = timer
        play_info.setText(f"播放 {os.path.basename(fr['path'])}  第 1/{loops_spin.value()} 轮")

    # ===== 播放循环(缓冲策略:渲染领先才播,否则等下一 tick)=====
    def on_tick():
        if play_state.get("mode") != "play":
            return
        idx = play_state["idx"]
        pixmaps = play_state["pixmaps"]
        # 该帧未就绪 → 等下个 tick(渲染会很快补上)
        if idx not in pixmaps:
            play_info.setText(f"等待渲染第 {idx+1}/{play_state['total']} 帧…")
            return
        qr_label.setPixmap(scale_pixmap_to_label(pixmaps[idx]))
        done = play_state["round"] * play_state["total"] + idx + 1
        allf = play_state["total"] * play_state["loops"]
        pct = int(done / allf * 100) if allf else 0
        play_info.setText(f"第 {play_state['round']+1}/{play_state['loops']} 轮   帧 {idx+1}/{play_state['total']}   {pct}%")
        play_state["idx"] += 1
        if play_state["idx"] >= play_state["total"]:
            play_state["round"] += 1
            play_state["idx"] = 0
            if play_state["round"] >= play_state["loops"]:
                finish_playback(True)

    # ===== 显帧(输入帧号 → 全屏静态显示 + 左右键翻页)=====
    def show_frame_dialog(row):
        if row < 0 or row >= len(file_rows):
            return
        fr = file_rows[row]
        ck = fr.get("cache_key")
        cached = render_cache.get(ck) if ck else None
        if not cached:
            QMessageBox.information(window, "提示", "请先点「转换」生成该文件的二维码。")
            return
        total = len(cached["result"].frames)
        idx, ok = QInputDialog.getInt(
            window, "显示指定帧",
            f"输入要显示的帧号(1~{total}):\n(用于补扫接收端漏掉的某帧)\n显示后可用 ← → 翻页",
            value=1, minValue=1, maxValue=total,
        )
        if not ok:
            return
        enter_show_single(row, idx - 1)

    def enter_show_single(row, idx):
        """进入单帧显示模式。"""
        fr = file_rows[row]
        ck = fr["cache_key"]
        cached = render_cache[ck]
        result = cached["result"]
        pixmaps = cached["pixmaps"]

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

        # 确保该帧已渲染(没有就同步渲染单帧,很快)
        if not ensure_rendered(idx, pixmaps, result):
            play_info.setText("该帧渲染失败。")
            return
        display_single(idx)
        # 启用持久快捷键(防 GC)
        _enable_nav_shortcuts()

    def display_single(idx):
        """显示单帧(已确保渲染)。"""
        pixmaps = play_state["pixmaps"]
        if idx not in pixmaps:
            return
        qr_label.setPixmap(scale_pixmap_to_label(pixmaps[idx]))
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
            "frames": result.frames,  # 所有帧 JSON
            "sid": result.sid,
        }
        with open(os.path.join(d, "meta.json"), "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False)
        # 存每帧 PNG
        from PyQt6.QtCore import QBuffer, QByteArray, QIODevice
        saved = 0
        for idx in range(len(result.frames)):
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
                status_label.setText(f"保存中 {saved}/{len(result.frames)} …")
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
        # 重建 result(用 builder 的 BuildResult)
        from bridge.sender.builder import BuildResult
        result = BuildResult(sid=meta.get("sid", ""), frames=frames,
                             file_count=1, total_data_chunks=total)
        # 加载 PNG
        pixmaps = {}
        from PyQt6.QtCore import QBuffer, QByteArray, QIODevice
        loaded = 0
        for idx in range(total):
            png_path = os.path.join(d, f"frame_{idx:05d}.png")
            if not os.path.isfile(png_path):
                continue
            pm = QPixmap()
            if pm.load(png_path):
                pixmaps[idx] = pm
                loaded += 1
            if loaded % 50 == 0:
                status_label.setText(f"加载中 {loaded}/{total} …")
                app.processEvents()
        if loaded == 0:
            QMessageBox.critical(window, "错误", "没有加载到任何帧 PNG。")
            return
        # 用 meta 的路径+参数算 cache_key,存入缓存
        ck = compute_cache_key([meta["path"]], meta["chunk_size"],
                               meta["use_fec"], meta["fec_redundancy"])
        render_cache[ck] = {"result": result, "pixmaps": pixmaps, "rendered": True}
        fr = file_rows[row]
        fr["cache_key"] = ck
        fr["result"] = result
        fr["rendered"] = (loaded >= total)
        file_table.item(row, 1).setText(f"已加载 {loaded}/{total}")
        file_table.item(row, 2).setText(str(total))
        status_label.setText(f"✓ 从 {d} 加载 {loaded}/{total} 帧,可直接播放/显帧。")

    # ===== 结束/停止 =====
    def finish_playback(completed: bool):
        # 停定时器
        t = play_state.get("timer")
        if t:
            t.stop(); play_state["timer"] = None
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
    # 多进程入口:freeze_support 已在模块顶部调用
    sys.exit(main())
