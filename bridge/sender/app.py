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

    # ---- 播放状态机(用 QTimer,不开线程) ----
    play_state: dict = {
        "pixmaps": [],       # 预渲染的 QPixmap 列表
        "fps": 12,
        "loops": 3,
        "round": 0,
        "idx": 0,
        "total": 0,
        "timer": None,
        "result": None,      # 当前会话的 BuildResult(重试时复用)
        "cache_key": None,   # 当前缓存 key(重试命中判断)
    }
    # ★ 缓存:key=(内容md5 + 参数) → {"pixmaps":..., "frames":..., "result":...}
    # 避免重试/重复传输时重新生成二维码(渲染是最慢的环节)
    render_cache: dict = {}

    def render_pixmaps(frames_json, box, border):
        """
        预渲染所有 QR 帧为 QPixmap。
        ★ 优化:直接从 QR 矩阵构造 QImage,跳过 PIL 序列化 + PNG 编解码
        (原 PIL 链路 ~170ms/帧,矩阵直构 ~2ms/帧,提速约 80 倍)。
        """
        import qrcode
        from PyQt6.QtGui import QImage
        pixmaps = []
        n_frames = len(frames_json)
        for i, payload in enumerate(frames_json):
            qr = qrcode.QRCode(
                version=None,
                error_correction=qrcode.constants.ERROR_CORRECT_H,
                box_size=box, border=border,
            )
            qr.add_data(payload)
            qr.make(fit=True)
            # get_matrix() 返回 bool 二维矩阵(True=黑模块)。0.1ms 级。
            matrix = qr.get_matrix()
            n = len(matrix)
            # 构造灰度字节数组:黑=0, 白=255
            data = bytearray(n * n)
            for y in range(n):
                row = matrix[y]
                base = y * n
                for x in range(n):
                    data[base + x] = 0 if row[x] else 255
            # QImage 直接吃灰度字节,Format_Grayscale8 每像素 1 字节
            img = QImage(bytes(data), n, n, n, QImage.Format.Format_Grayscale8)
            pixmaps.append(QPixmap.fromImage(img))
            if (i + 1) % 50 == 0 or i + 1 == n_frames:
                play_info.setText(f"渲染二维码 {i+1}/{n_frames} …")
                app.processEvents()
        return pixmaps

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
        """QTimer 回调:翻到下一帧。"""
        st = play_state
        if st["idx"] >= st["total"]:
            st["round"] += 1
            st["idx"] = 0
            if st["round"] >= st["loops"]:
                finish_playback(True)
                return
            play_info.setText(f"第 {st['round']+1}/{st['loops']} 轮")
        pm = st["pixmaps"][st["idx"]]
        qr_label.setPixmap(scale_pixmap_to_label(pm))
        done = st["round"] * st["total"] + st["idx"] + 1
        allframes = st["total"] * st["loops"]
        pct = int(done / allframes * 100) if allframes else 0
        play_info.setText(
            f"第 {st['round']+1}/{st['loops']} 轮   帧 {st['idx']+1}/{st['total']}   {pct}%"
        )
        st["idx"] += 1

    def finish_playback(completed: bool):
        t = play_state.get("timer")
        if t:
            t.stop()
            play_state["timer"] = None
        play_page.setVisible(False)
        ctrl_page.setVisible(True)
        window.showNormal()
        qr_label.clear()

        if not completed:
            # 被手动停止
            status_label.setText("传输已停止。")
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
            status_label.setText("传输完成 ✓")
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

    def start_transfer(is_retry: bool = False):
        paths = list(selected_paths)
        if not paths:
            QMessageBox.warning(window, "提示", "请先选择要传输的文件或目录。")
            return

        chunk_size = chunk_spin.value()
        use_fec = fec_check.isChecked()
        fec_redundancy = redundancy_spin.value() / 100.0
        cache_key = compute_cache_key(paths, chunk_size, use_fec, fec_redundancy)

        # ★ 缓存命中:重试或重复传输同一内容时,跳过 build + render(秒级启动)
        cached = render_cache.get(cache_key)
        if cached is not None:
            status_label.setText("✓ 命中缓存,秒级启动…")
            app.processEvents()
            result = cached["result"]
            pixmaps = cached["pixmaps"]
        else:
            # 缓存未命中:正常 build + render
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

            ctrl_page.setVisible(False)
            play_page.setVisible(True)
            window.showFullScreen()
            app.processEvents()
            play_info.setText(f"渲染二维码 0/{len(result.frames)} …(首次较慢,重试会秒开)")
            pixmaps = render_pixmaps(result.frames, box=10, border=4)

            # 存入缓存
            render_cache[cache_key] = {"result": result, "pixmaps": pixmaps}

        # 切到播放页(缓存命中时可能还没切)
        ctrl_page.setVisible(False)
        play_page.setVisible(True)
        window.showFullScreen()
        app.processEvents()

        play_state.update({
            "pixmaps": pixmaps,
            "fps": fps_spin.value(),
            "loops": loops_spin.value(),
            "round": 0,
            "idx": 0,
            "total": len(pixmaps),
            "result": result,
            "cache_key": cache_key,
        })

        # 启动 QTimer
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
