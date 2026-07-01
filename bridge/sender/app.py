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
    }

    def render_pixmaps(frames_json, box, border):
        """预渲染所有 QR 帧为 QPixmap。"""
        import qrcode
        pixmaps = []
        for i, payload in enumerate(frames_json):
            qr = qrcode.QRCode(
                version=None,
                error_correction=qrcode.constants.ERROR_CORRECT_H,
                box_size=box, border=border,
            )
            qr.add_data(payload)
            qr.make(fit=True)
            img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
            buf = io.BytesIO(); img.save(buf, format="PNG"); buf.seek(0)
            pm = QPixmap()
            pm.loadFromData(buf.read(), "PNG")
            pixmaps.append(pm)
            if (i + 1) % 20 == 0:
                play_info.setText(f"渲染二维码 {i+1}/{len(frames_json)} …")
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
        qr_label.clear()
        status_label.setText("传输完成 ✓" if completed else "传输已停止。")
        start_btn.setText("▶  开始传输")
        start_btn.clicked.disconnect()
        start_btn.clicked.connect(start_transfer)
        start_btn.setEnabled(True)
        if completed:
            QMessageBox.information(window, "完成", "传输完成,接收端应已收到文件。")

    def stop_playback():
        finish_playback(False)

    def start_transfer():
        if not selected_paths:
            QMessageBox.warning(window, "提示", "请先选择要传输的文件或目录。")
            return
        status_label.setText("正在构建二维码帧…")
        app.processEvents()
        try:
            result = builder.build(
                list(selected_paths),
                chunk_size=chunk_spin.value(),
                use_fec=fec_check.isChecked(),
                fec_redundancy=redundancy_spin.value() / 100.0,
            )
        except Exception as e:
            QMessageBox.critical(window, "构建失败", str(e))
            status_label.setText("构建失败。")
            return

        # 切到播放页
        ctrl_page.setVisible(False)
        play_page.setVisible(True)
        window.showFullScreen()
        app.processEvents()  # 让 qr_label 拿到实际尺寸
        play_info.setText(f"渲染二维码 0/{len(result.frames)} …")

        # 预渲染
        pixmaps = render_pixmaps(result.frames, box=10, border=4)
        play_state.update({
            "pixmaps": pixmaps,
            "fps": fps_spin.value(),
            "loops": loops_spin.value(),
            "round": 0,
            "idx": 0,
            "total": len(pixmaps),
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
