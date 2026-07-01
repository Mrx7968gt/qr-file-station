#!/usr/bin/env python3
"""
bridge.sender.app — 发送端 PyQt6 主程序(GUI + CLI)

工作流(每个文件独立控制):
  - 控制页:文件列表(QTableWidget),每行带「转换」「播放」「显帧」按钮
    · 转换:后台生成该文件二维码(可逐个触发)
    · 播放:单文件循环播放(只播这一个文件)
    · 显帧:输入帧号 → 全屏静态显示那一帧(补扫漏掉的)
  - 播放页:全屏 QR 显示(QLabel+QPixmap+QTimer,边渲染边播放)

形态:命令行有参数 → 走 CLI;无参数 → 启动 GUI。

用法:
    python -m bridge.sender.app              # 启动 GUI
    python -m bridge.sender.app report.zip   # 等价 CLI(走 cli.main)
"""

from __future__ import annotations

import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))


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
        from PyQt6.QtCore import Qt, QTimer, QThread, pyqtSignal
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
    window.resize(820, 640)

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

    # 文件表格:列 = 文件名 | 状态 | 块数 | 转换 | 播放 | 显帧
    file_table = QTableWidget(0, 6)
    file_table.setHorizontalHeaderLabels(["文件", "状态", "块数", "转换", "播放", "显帧"])
    file_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
    file_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
    file_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
    for c in (3, 4, 5):
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

    status_label = QLabel("就绪。添加文件后,逐个点「转换」生成二维码,再点「播放」。")
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

    # ===== 文件状态:每行 = {path, cache_key, result, rendered} =====
    file_rows: list = []   # 与表格行号一一对应
    # 缓存:cache_key → {"result":..., "pixmaps":{idx:pm}, "frames":[...], "rendered":bool}
    render_cache: dict = {}

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
            # 按钮单元格
            for col, text in ((3, "转换"), (4, "播放"), (5, "显帧")):
                btn = QPushButton(text)
                file_table.setCellWidget(row, col, btn)
            file_rows.append({
                "path": p, "cache_key": None, "result": None, "rendered": False,
                "convert_btn": file_table.cellWidget(row, 3),
                "play_btn": file_table.cellWidget(row, 4),
                "show_btn": file_table.cellWidget(row, 5),
            })
            # 绑定按钮(用默认参数捕获 row)
            file_table.cellWidget(row, 3).clicked.connect(lambda _, r=row: convert_file(r))
            file_table.cellWidget(row, 4).clicked.connect(lambda _, r=row: play_file(r))
            file_table.cellWidget(row, 5).clicked.connect(lambda _, r=row: show_frame_dialog(r))

    def add_files_dialog():
        paths, _ = QFileDialog.getOpenFileNames(window, "选择文件", "")
        add_files(paths)

    def add_dir_dialog():
        d = QFileDialog.getExistingDirectory(window, "选择目录", "")
        if d:
            add_files([d])

    def clear_all():
        # 停止任何进行中的播放/渲染
        stop_playback()
        file_table.setRowCount(0)
        file_rows.clear()
        status_label.setText("已清空列表。")

    add_file_btn.clicked.connect(add_files_dialog)
    add_dir_btn.clicked.connect(add_dir_dialog)
    clear_btn.clicked.connect(clear_all)

    # ===== 渲染引擎(后台线程,边渲染边播放)=====
    def render_matrix_bytes(payload, box=10, border=4):
        """返回 (n, bytes),纯计算,可在任意线程安全调用。"""
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

    class RenderWorker(QThread):
        """后台渲染:逐帧生成 QR 矩阵字节,信号回主线程。"""
        frame_ready = pyqtSignal(int, int, bytes)  # (index, n, data)
        progress = pyqtSignal(int, int)            # (done, total)
        def __init__(self, frames, box, border):
            super().__init__()
            self.frames = frames; self.box = box; self.border = border
        def run(self):
            total = len(self.frames)
            for i, payload in enumerate(self.frames):
                if self.isInterruptionRequested():
                    return
                n, data = render_matrix_bytes(payload, self.box, self.border)
                self.frame_ready.emit(i, n, data)
                self.progress.emit(i + 1, total)

    play_state: dict = {
        "frames": [], "pixmaps": {}, "fps": 12, "loops": 3,
        "round": 0, "idx": 0, "total": 0,
        "timer": None, "render_worker": None,
        "result": None, "cache_key": None, "rendering": False,
        "mode": None,  # "play" | "show_single"
        "single_idx": 0,
        "current_row": None,  # 当前播放/显帧的文件行
    }

    def scale_pixmap_to_label(pm: QPixmap) -> QPixmap:
        avail = qr_label.size()
        if avail.width() < 2 or avail.height() < 2:
            return pm
        return pm.scaled(avail, Qt.AspectRatioMode.KeepAspectRatio,
                         Qt.TransformationMode.SmoothTransformation)

    def on_render_frame(idx, n, data):
        """后台渲染每帧完成回调(主线程):矩阵字节 → QPixmap。"""
        img = QImage(data, n, n, n, QImage.Format.Format_Grayscale8).copy()
        pm = QPixmap.fromImage(img)
        st = play_state
        st["pixmaps"][idx] = pm
        ck = st.get("cache_key")
        if ck and ck in render_cache:
            render_cache[ck]["pixmaps"][idx] = pm

    def on_render_progress(done, total):
        if play_state.get("mode") == "play":
            play_info.setText(f"渲染中 {done}/{total} …(边渲染边播放)")

    def on_render_finished():
        play_state["rendering"] = False
        ck = play_state.get("cache_key")
        if ck and ck in render_cache:
            render_cache[ck]["rendered"] = True
        # 更新表格状态:该文件已渲染完
        row = play_state.get("current_row")
        if row is not None and 0 <= row < file_table.rowCount():
            file_table.item(row, 1).setText("已转换")

    # ===== 转换单个文件(后台渲染,但不自动播放)=====
    def convert_file(row):
        if row < 0 or row >= len(file_rows):
            return
        fr = file_rows[row]
        chunk_size = chunk_spin.value()
        use_fec = fec_check.isChecked()
        fec_redundancy = redundancy_spin.value() / 100.0
        ck = compute_cache_key([fr["path"]], chunk_size, use_fec, fec_redundancy)
        fr["cache_key"] = ck
        file_table.item(row, 1).setText("转换中…")

        # 缓存命中?
        cached = render_cache.get(ck)
        if cached and cached.get("rendered"):
            fr["result"] = cached["result"]
            fr["rendered"] = True
            file_table.item(row, 1).setText("已转换")
            file_table.item(row, 2).setText(str(len(cached["result"].frames)))
            status_label.setText(f"✓ {os.path.basename(fr['path'])} 命中缓存,已就绪。")
            return

        # 构建帧
        try:
            result = builder.build([fr["path"]], chunk_size=chunk_size,
                                   use_fec=use_fec, fec_redundancy=fec_redundancy)
        except Exception as e:
            file_table.item(row, 1).setText("转换失败")
            QMessageBox.critical(window, "转换失败", str(e))
            return
        fr["result"] = result
        file_table.item(row, 2).setText(str(len(result.frames)))

        # 预占缓存槽 + 启动后台渲染
        pixmaps = {}
        render_cache[ck] = {"result": result, "pixmaps": pixmaps,
                            "frames": result.frames, "rendered": False}
        file_table.item(row, 1).setText("渲染中…")
        status_label.setText(f"正在后台渲染 {os.path.basename(fr['path'])}…")

        # 复用通用渲染 worker,但把结果写入这个文件的缓存
        worker = RenderWorker(result.frames, box=10, border=4)

        def _frame(idx, n, data):
            img = QImage(data, n, n, n, QImage.Format.Format_Grayscale8).copy()
            pixmaps[idx] = QPixmap.fromImage(img)
            done = len(pixmaps)
            file_table.item(row, 1).setText(f"渲染 {done}/{len(result.frames)}")

        def _done():
            render_cache[ck]["rendered"] = True
            fr["rendered"] = True
            file_table.item(row, 1).setText("已转换")
            status_label.setText(f"✓ {os.path.basename(fr['path'])} 转换完成,{len(result.frames)} 帧。")

        worker.frame_ready.connect(_frame)
        worker.finished.connect(_done)
        worker.start()
        # 保存 worker 引用防 GC(随文件行存)
        fr["worker"] = worker

    # ===== 播放单个文件(循环)=====
    def play_file(row):
        if row < 0 or row >= len(file_rows):
            return
        fr = file_rows[row]
        ck = fr.get("cache_key")
        cached = render_cache.get(ck) if ck else None
        if not cached:
            QMessageBox.information(window, "提示", "请先点「转换」生成该文件的二维码。")
            return

        # 进入播放页
        play_state["mode"] = "play"
        play_state["current_row"] = row
        ctrl_page.setVisible(False)
        play_page.setVisible(True)
        window.showFullScreen()
        app.processEvents()

        result = cached["result"]
        pixmaps = dict(cached["pixmaps"])
        rendering = not cached.get("rendered", False)

        play_state.update({
            "frames": result.frames, "pixmaps": pixmaps,
            "fps": fps_spin.value(), "loops": loops_spin.value(),
            "round": 0, "idx": 0, "total": len(result.frames),
            "result": result, "cache_key": ck, "rendering": rendering,
        })

        if rendering:
            # 仍在渲染,继续后台渲染并流式播放
            worker = RenderWorker(result.frames, box=10, border=4)
            worker.frame_ready.connect(on_render_frame)
            worker.progress.connect(on_render_progress)
            worker.finished.connect(on_render_finished)
            play_state["render_worker"] = worker
            worker.start()

        play_info.setText(f"播放 {os.path.basename(fr['path'])}  第 1/{loops_spin.value()} 轮")
        timer = QTimer(window)
        interval = int(1000 / fps_spin.value()) if fps_spin.value() > 0 else 100
        timer.timeout.connect(on_tick)
        timer.start(interval)
        play_state["timer"] = timer

    # ===== 显示指定帧(静态,补扫漏掉的)=====
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
        # 输入帧号
        idx, ok = QInputDialog.getInt(
            window, "显示指定帧",
            f"输入要显示的帧号(1~{total}):\n(用于补扫接收端漏掉的某帧)",
            value=1, minValue=1, maxValue=total,
        )
        if not ok:
            return
        show_single_frame(row, idx - 1)  # 转 0-based

    def show_single_frame(row, idx):
        fr = file_rows[row]
        ck = fr["cache_key"]
        cached = render_cache[ck]
        result = cached["result"]
        pixmaps = cached["pixmaps"]
        total = len(result.frames)

        play_state["mode"] = "show_single"
        play_state["current_row"] = row
        play_state["single_idx"] = idx
        ctrl_page.setVisible(False)
        play_page.setVisible(True)
        window.showFullScreen()
        app.processEvents()

        play_state.update({
            "frames": result.frames, "pixmaps": pixmaps,
            "total": total, "cache_key": ck, "result": result,
            "timer": None, "render_worker": None,
        })

        # 该帧已渲染?
        if idx in pixmaps:
            qr_label.setPixmap(scale_pixmap_to_label(pixmaps[idx]))
            play_info.setText(f"显示第 {idx+1}/{total} 帧(静态,左右键翻页,ESC 返回)")
            _start_single_nav(idx, total)
        else:
            # 未渲染:临时渲染这一帧
            play_info.setText(f"渲染第 {idx+1}/{total} 帧…")
            n, data = render_matrix_bytes(result.frames[idx])
            img = QImage(data, n, n, n, QImage.Format.Format_Grayscale8).copy()
            pm = QPixmap.fromImage(img)
            pixmaps[idx] = pm
            qr_label.setPixmap(scale_pixmap_to_label(pm))
            play_info.setText(f"显示第 {idx+1}/{total} 帧(静态,左右键翻页,ESC 返回)")
            _start_single_nav(idx, total)

    def _start_single_nav(idx, total):
        """单帧模式下,左右键翻页;用一个短间隔 QTimer 检测按键(简单实现)。"""
        # 用 QShortcut 实现左右键翻页
        for sc in getattr(play_state, "_single_shortcuts", []):
            sc.setEnabled(False)
        scs = []
        left = QShortcut(QKeySequence(Qt.Key.Key_Left), window)
        left.activated.connect(lambda: _nav_single(-1))
        right = QShortcut(QKeySequence(Qt.Key.Key_Right), window)
        right.activated.connect(lambda: _nav_single(1))
        scs.extend([left, right])
        play_state["_single_shortcuts"] = scs

    def _nav_single(delta):
        if play_state.get("mode") != "show_single":
            return
        total = play_state["total"]
        new_idx = (play_state["single_idx"] + delta) % total
        if new_idx < 0:
            new_idx += total
        play_state["single_idx"] = new_idx
        idx = new_idx
        pixmaps = play_state["pixmaps"]
        if idx in pixmaps:
            qr_label.setPixmap(scale_pixmap_to_label(pixmaps[idx]))
        else:
            result = play_state["result"]
            n, data = render_matrix_bytes(result.frames[idx])
            img = QImage(data, n, n, n, QImage.Format.Format_Grayscale8).copy()
            pm = QPixmap.fromImage(img)
            pixmaps[idx] = pm
            qr_label.setPixmap(scale_pixmap_to_label(pm))
        play_info.setText(f"显示第 {idx+1}/{total} 帧(静态,左右键翻页,ESC 返回)")

    # ===== 播放循环 on_tick =====
    def on_tick():
        if play_state.get("mode") != "play":
            return
        st = play_state
        idx = st["idx"]
        if idx not in st["pixmaps"]:
            return  # 该帧未渲染好,等下个 tick
        pm = st["pixmaps"][idx]
        qr_label.setPixmap(scale_pixmap_to_label(pm))
        done = st["round"] * st["total"] + idx + 1
        allframes = st["total"] * st["loops"]
        pct = int(done / allframes * 100) if allframes else 0
        play_info.setText(
            f"第 {st['round']+1}/{st['loops']} 轮   帧 {idx+1}/{st['total']}   {pct}%"
        )
        st["idx"] += 1
        if st["idx"] >= st["total"]:
            st["round"] += 1
            st["idx"] = 0
            if st["round"] >= st["loops"]:
                finish_playback(True)

    def finish_playback(completed: bool):
        # 停止定时器 + 后台渲染
        t = play_state.get("timer")
        if t:
            t.stop(); play_state["timer"] = None
        rw = play_state.get("render_worker")
        if rw is not None:
            rw.requestInterruption(); rw.quit(); rw.wait(2000)
            play_state["render_worker"] = None
        # 禁用单帧翻页快捷键
        for sc in getattr(play_state, "_single_shortcuts", []):
            sc.setEnabled(False)
        play_state["rendering"] = False
        play_state["mode"] = None

        play_page.setVisible(False)
        ctrl_page.setVisible(True)
        window.showNormal()
        qr_label.clear()

        if not completed:
            status_label.setText("已停止。可继续转换其它文件或重新播放。")
            return

        # 播放完成:提示重试
        row = play_state.get("current_row")
        name = os.path.basename(file_rows[row]["path"]) if row is not None and row < len(file_rows) else "?"
        info = (
            f"「{name}」已循环播放 {play_state['loops']} 轮。\n\n"
            f"请到接收端确认是否收齐。\n"
            f"· 已收齐 → 「完成」(可选其它文件继续)\n"
            f"· 漏了几帧 → 「显帧」单独补扫\n"
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

    # ESC:停止播放/显帧,回到控制页
    esc_sc = QShortcut(QKeySequence("Escape"), window)
    esc_sc.activated.connect(stop_playback)

    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
