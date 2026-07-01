#!/usr/bin/env python3
"""
bridge.sender.app — 发送端 PyQt6 主程序(GUI + CLI)

形态:命令行有参数 → 走 CLI;无参数 → 启动 GUI。

GUI 用纯 PyQt6 实现(★ 不用 pygame):
  - 控制模式:文件选择 + 参数 + 启动按钮
  - 播放模式:同一个主窗口切到全屏 QR 循环播放(QTimer 驱动翻页)
  两个框架不能在同一线程共存,所以播放层也用 PyQt6(QLabel+QPixmap+QTimer)。

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
        # 有命令行参数 → 走 CLI
        from bridge.sender import cli
        return cli.main(argv)
    return run_gui()


def run_gui() -> int:
    """启动 PyQt6 GUI。"""
    try:
        from PyQt6.QtWidgets import (
            QApplication, QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
            QLineEdit, QLabel, QSpinBox, QCheckBox, QFileDialog,
            QGroupBox, QFormLayout, QMessageBox, QListWidget,
        )
        from PyQt6.QtCore import Qt, QTimer
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
    window.resize(720, 600)

    # 用 QStackedLayout 风格:隐藏/显示两组控件实现"模式切换"
    # 控制页
    ctrl_page = QWidget()
    ctrl_layout = QVBoxLayout(ctrl_page)

    # ---- 文件选择 ----
    file_group = QGroupBox("传输内容")
    file_layout = QVBoxLayout()
    file_list = QListWidget()
    file_list.setMaximumHeight(110)
    file_btn_row = QHBoxLayout()
    add_file_btn = QPushButton("添加文件")
    add_dir_btn = QPushButton("添加目录")
    clear_btn = QPushButton("清空列表")
    file_btn_row.addWidget(add_file_btn)
    file_btn_row.addWidget(add_dir_btn)
    file_btn_row.addWidget(clear_btn)
    file_layout.addWidget(QLabel("待传输列表:"))
    file_layout.addWidget(file_list)
    file_layout.addLayout(file_btn_row)
    file_group.setLayout(file_layout)
    ctrl_layout.addWidget(file_group)

    selected_paths: list = []

    def add_files_dialog():
        paths, _ = QFileDialog.getOpenFileNames(window, "选择文件", "")
        for p in paths:
            if p not in selected_paths:
                selected_paths.append(p)
                file_list.addItem(p)

    def add_dir_dialog():
        d = QFileDialog.getExistingDirectory(window, "选择目录", "")
        if d and d not in selected_paths:
            selected_paths.append(d)
            file_list.addItem(d)

    add_file_btn.clicked.connect(add_files_dialog)
    add_dir_btn.clicked.connect(add_dir_dialog)
    clear_btn.clicked.connect(lambda: (selected_paths.clear(), file_list.clear()))

    # ---- 参数 ----
    param_group = QGroupBox("传输参数")
    form = QFormLayout()
    chunk_spin = QSpinBox(); chunk_spin.setRange(50, 2953); chunk_spin.setValue(protocol.DEFAULT_CHUNK_SIZE)
    fps_spin = QSpinBox(); fps_spin.setRange(1, 60); fps_spin.setValue(12)
    loops_spin = QSpinBox(); loops_spin.setRange(1, 20); loops_spin.setValue(3)
    display_spin = QSpinBox(); display_spin.setRange(0, 4); display_spin.setValue(0)
    fec_check = QCheckBox("启用 FEC 前向纠错(建议开启,丢帧可恢复)")
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

    # ---- 启动按钮 + 状态 ----
    start_btn = QPushButton("▶  开始传输")
    start_btn.setStyleSheet("QPushButton{font-size:16px;padding:10px;font-weight:bold;}")
    status_label = QLabel("就绪。选择文件后点击「开始传输」。")
    ctrl_layout.addWidget(start_btn)
    ctrl_layout.addWidget(status_label)
    ctrl_layout.addStretch(1)

    # 播放页(全屏 QR)
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

    # ---- 播放状态机 ----
    # 渲染在后台线程(纯计算,生成 QR 矩阵字节),主线程流式消费转 QPixmap 并显示。
    # 这样第一帧渲染好就立刻开播,无需等全部渲染完;UI 也不卡顿。
    play_state: dict = {
        "frames": [],        # 所有帧 JSON
        "pixmaps": {},       # index -> QPixmap(已渲染的,主线程填充)
        "fps": 12,
        "loops": 3,
        "round": 0,
        "idx": 0,
        "total": 0,
        "timer": None,
        "render_worker": None,
        "result": None,      # 当前会话的 BuildResult
        "cache_key": None,   # 当前缓存 key
        "rendering": False,  # 是否还在后台渲染
    }
    # ★ 缓存:key=(内容md5 + 参数) → {"pixmaps":{idx:pm}, "frames":[...], "result":...}
    # 重试/重复传输同一内容时复用,秒级启动。
    render_cache: dict = {}

    # 渲染一帧 QR 矩阵为字节(纯函数,可在任意线程安全调用)
    def render_matrix_bytes(payload, box=10, border=4):
        """返回 (n, bytes),n=矩阵边长,bytes=灰度字节(黑0白255)。纯计算,无 Qt 依赖。"""
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

    # 后台渲染线程:逐帧生成矩阵字节,通过信号发回主线程
    def make_render_worker(frames_json, box, border):
        from PyQt6.QtCore import QThread, pyqtSignal
        class _RenderWorker(QThread):
            frame_ready = pyqtSignal(int, int, bytes)  # (index, n, data)
            progress = pyqtSignal(int, int)            # (done, total)
            def __init__(self, frames, box, border):
                super().__init__()
                self.frames = frames
                self.box = box
                self.border = border
            def run(self):
                total = len(self.frames)
                for i, payload in enumerate(self.frames):
                    if self.isInterruptionRequested():
                        return
                    n, data = render_matrix_bytes(payload, self.box, self.border)
                    self.frame_ready.emit(i, n, data)
                    self.progress.emit(i + 1, total)
        return _RenderWorker(frames_json, box, border)

    def scale_pixmap_to_label(pm: QPixmap) -> QPixmap:
        """等比放大 pixmap 填满 qr_label 区域。"""
        avail = qr_label.size()
        if avail.width() < 2 or avail.height() < 2:
            return pm
        return pm.scaled(
            avail, Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )

    def on_tick():
        """QTimer 回调:翻到下一帧。流式消费:该帧未渲染好则等待下一个 tick。"""
        st = play_state
        idx = st["idx"]
        # 该帧还没渲染好 → 不前进,下个 tick 再试(渲染通常比播放快,会很快跟上)
        if idx not in st["pixmaps"]:
            return
        pm = st["pixmaps"][idx]
        qr_label.setPixmap(scale_pixmap_to_label(pm))
        done = st["round"] * st["total"] + idx + 1
        allframes = st["total"] * st["loops"]
        pct = int(done / allframes * 100) if allframes else 0
        play_info.setText(
            f"第 {st['round']+1}/{st['loops']} 轮   帧 {idx+1}/{st['total']}   {pct}%"
        )
        st["idx"] += 1
        # 到本轮末尾
        if st["idx"] >= st["total"]:
            st["round"] += 1
            st["idx"] = 0
            if st["round"] >= st["loops"]:
                finish_playback(True)
                return

    def finish_playback(completed: bool):
        # 停止播放定时器
        t = play_state.get("timer")
        if t:
            t.stop()
            play_state["timer"] = None
        # 停止后台渲染线程(若仍在运行)
        rw = play_state.get("render_worker")
        if rw is not None:
            rw.requestInterruption()
            rw.quit()
            rw.wait(2000)
            play_state["render_worker"] = None
        play_state["rendering"] = False

        play_page.setVisible(False)
        ctrl_page.setVisible(True)
        window.showNormal()
        qr_label.clear()

        if not completed:
            # 被手动停止 → 回到控制页,让用户选新文件或重传
            status_label.setText("传输已停止。可重新选择文件或调整参数。")
            _reset_start_button()
            return

        # ★ 优化:所有轮次播完后,询问用户是否需要重试
        # 采集卡是单向通道,发送端无法确认接收端是否收齐,
        # 所以让用户根据接收端状态决定是否重发(重发复用缓存,秒级启动)。
        result = play_state.get("result")
        info = (
            f"传输完成 ✓\n\n"
            f"已循环播放 {play_state['loops']} 轮,共 {play_state['total']} 帧。\n"
            f"会话 sid: {result.sid if result else '?'}\n\n"
            f"请到接收端(Mac)确认是否已收到完整文件。\n\n"
            f"· 若已收到 → 点击「完成」\n"
            f"· 若未收齐或想再传一次 → 点击「重试」(秒级启动,复用缓存)"
        )
        msg = QMessageBox(window)
        msg.setWindowTitle("传输完成")
        msg.setText(info)
        retry_btn = msg.addButton("🔄 重试", QMessageBox.ButtonRole.AcceptRole)
        msg.addButton("✓ 完成", QMessageBox.ButtonRole.RejectRole)
        msg.exec()
        if msg.clickedButton() is retry_btn:
            status_label.setText("重试中(复用缓存)…")
            start_transfer(is_retry=True)
        else:
            # 完成后回到控制页:用户可选新文件做下一次传输(程序不退出)
            status_label.setText("✓ 上一次传输完成。可选择新文件开始下一次传输。")
            _reset_start_button()

    def _reset_start_button():
        start_btn.setText("▶  开始传输")
        start_btn.clicked.disconnect()
        start_btn.clicked.connect(start_transfer)
        start_btn.setEnabled(True)

    def stop_playback():
        finish_playback(False)

    def compute_cache_key(paths, chunk_size, use_fec, fec_redundancy):
        """
        ★ 缓存 key:对所有待传文件的内容 md5 + 路径 + 参数。
        内容不变 + 参数不变 → 命中缓存,跳过重新生成二维码。
        """
        import hashlib
        h = hashlib.md5()
        for p in sorted(paths):
            h.update(p.encode("utf-8"))
            # 目录递归,文件读内容 md5
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
        # 参数也纳入 key
        h.update(f"|cs={chunk_size}|fec={use_fec}|r={fec_redundancy}".encode())
        return h.hexdigest()

    def on_render_frame(idx, n, data):
        """后台渲染线程每完成一帧时回调(主线程):矩阵字节 → QPixmap。"""
        from PyQt6.QtGui import QImage
        # ★ data 来自信号(后台线程的 bytes),QImage 不复制会引用外部缓冲。
        # .copy() 让 QImage 持有独立数据副本,避免野指针。
        img = QImage(data, n, n, n, QImage.Format.Format_Grayscale8).copy()
        pm = QPixmap.fromImage(img)
        st = play_state
        st["pixmaps"][idx] = pm
        # 同步写入缓存(若正在缓存这次传输)
        ck = st.get("cache_key")
        if ck and ck in render_cache:
            render_cache[ck]["pixmaps"][idx] = pm

    def on_render_progress(done, total):
        play_info.setText(f"渲染中 {done}/{total} …  (边渲染边播放)")

    def on_render_finished():
        """后台渲染全部完成。"""
        play_state["rendering"] = False
        # 若缓存待存,标记完成
        ck = play_state.get("cache_key")
        if ck and ck in render_cache:
            render_cache[ck]["rendered"] = True

    def start_transfer(is_retry: bool = False):
        paths = list(selected_paths)
        if not paths:
            QMessageBox.warning(window, "提示", "请先选择要传输的文件或目录。")
            return

        chunk_size = chunk_spin.value()
        use_fec = fec_check.isChecked()
        fec_redundancy = redundancy_spin.value() / 100.0
        cache_key = compute_cache_key(paths, chunk_size, use_fec, fec_redundancy)

        status_label.setText("正在构建二维码帧…")
        app.processEvents()
        try:
            result = builder.build(
                paths, chunk_size=chunk_size,
                use_fec=use_fec, fec_redundancy=fec_redundancy,
            )
        except Exception as e:
            QMessageBox.critical(window, "构建失败", str(e))
            status_label.setText("构建失败。")
            return

        # 切到播放页 + 全屏
        ctrl_page.setVisible(False)
        play_page.setVisible(True)
        window.showFullScreen()
        app.processEvents()

        cached = render_cache.get(cache_key)
        if cached is not None and cached.get("rendered"):
            # ★ 缓存命中且已渲染完:秒级启动,无需后台渲染
            status_label.setText("✓ 命中缓存,秒级启动…")
            pixmaps = dict(cached["pixmaps"])  # 复制引用
            rendering = False
        else:
            # 首次或缓存未渲染完:后台渲染 + 边渲染边播放
            status_label.setText("渲染中…(边渲染边播放)")
            pixmaps = {}
            rendering = True
            # 预占缓存槽,后台渲染过程中逐步填充
            render_cache[cache_key] = {"result": result, "pixmaps": pixmaps, "rendered": False}

        play_state.update({
            "frames": result.frames,
            "pixmaps": pixmaps,
            "fps": fps_spin.value(),
            "loops": loops_spin.value(),
            "round": 0,
            "idx": 0,
            "total": len(result.frames),
            "result": result,
            "cache_key": cache_key,
            "rendering": rendering,
        })

        # 启动后台渲染(若需要)
        if rendering:
            worker = make_render_worker(result.frames, box=10, border=4)
            worker.frame_ready.connect(on_render_frame)
            worker.progress.connect(on_render_progress)
            worker.finished.connect(on_render_finished)
            play_state["render_worker"] = worker
            worker.start()

        # 启动 QTimer(流式播放:第一帧渲染好就开播)
        timer = QTimer(window)
        interval = int(1000 / fps_spin.value()) if fps_spin.value() > 0 else 100
        timer.timeout.connect(on_tick)
        timer.start(interval)
        play_state["timer"] = timer

        # 切换按钮为"停止"
        start_btn.setText("■  停止传输")
        start_btn.clicked.disconnect()
        start_btn.clicked.connect(stop_playback)

    start_btn.clicked.connect(start_transfer)

    # ESC 停止播放(不退出程序)
    esc_sc = QShortcut(QKeySequence("Escape"), window)
    esc_sc.activated.connect(stop_playback)

    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
