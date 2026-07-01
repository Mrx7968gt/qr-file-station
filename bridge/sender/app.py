#!/usr/bin/env python3
"""
bridge.sender.app — 发送端 PyQt6 GUI 主程序

形态:命令行有参数 → 走 CLI;无参数 → 启动 GUI。
GUI:文件选择 + 参数(chunk/fps/loops/FEC) + 启动按钮 + 进度条。

用法:
    python -m bridge.sender.app              # 启动 GUI
    python -m bridge.sender.app report.zip   # 等价 CLI(走 cli.main)
"""

from __future__ import annotations

import os
import sys
import threading

# 让作为模块或脚本运行都能 import
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]

    # 有命令行参数 → 走 CLI
    if argv:
        from bridge.sender import cli
        return cli.main(argv)

    # 无参数 → 启动 GUI
    return run_gui()


def run_gui() -> int:
    """启动 PyQt6 GUI。"""
    try:
        from PyQt6.QtWidgets import (
            QApplication, QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
            QLineEdit, QLabel, QSpinBox, QCheckBox, QProgressBar, QFileDialog,
            QGroupBox, QFormLayout, QMessageBox, QListWidget,
        )
        from PyQt6.QtCore import Qt, QThread, pyqtSignal
    except ImportError:
        print(
            "错误:未安装 PyQt6。\n"
            "  pip install PyQt6\n"
            "或使用命令行模式:python -m bridge.sender.cli <文件>",
            file=sys.stderr,
        )
        return 1

    from bridge.common import protocol
    from bridge.sender import builder, player

    app = QApplication(sys.argv)
    app.setApplicationName("采集卡文件传输 · 发送端")

    window = QWidget()
    window.setWindowTitle("采集卡文件传输 · 发送端")
    window.resize(560, 520)
    layout = QVBoxLayout(window)

    # ---- 文件选择 ----
    file_group = QGroupBox("传输内容")
    file_layout = QVBoxLayout()
    file_row = QHBoxLayout()
    file_edit = QLineEdit()
    file_edit.setPlaceholderText("点击「添加文件」或「添加目录」选择要传输的内容")
    add_file_btn = QPushButton("添加文件")
    add_dir_btn = QPushButton("添加目录")
    file_row.addWidget(file_edit, 1)
    file_row.addWidget(add_file_btn)
    file_row.addWidget(add_dir_btn)
    file_layout.addLayout(file_row)

    file_list = QListWidget()
    file_list.setMaximumHeight(100)
    file_layout.addWidget(QLabel("待传输列表:"))
    file_layout.addWidget(file_list)
    clear_btn = QPushButton("清空列表")
    file_layout.addWidget(clear_btn)
    file_group.setLayout(file_layout)
    layout.addWidget(file_group)

    selected_paths: list = []

    def add_files_dialog():
        paths, _ = QFileDialog.getOpenFileNames(window, "选择文件", "")
        for p in paths:
            if p not in selected_paths:
                selected_paths.append(p)
                file_list.addItem(p)

    def add_dir_dialog():
        d = QFileDialog.getExistingDirectory(window, "选择目录", "")
        if d:
            if d not in selected_paths:
                selected_paths.append(d)
                file_list.addItem(d)

    def clear_list():
        selected_paths.clear()
        file_list.clear()

    add_file_btn.clicked.connect(add_files_dialog)
    add_dir_btn.clicked.connect(add_dir_dialog)
    clear_btn.clicked.connect(clear_list)

    # ---- 参数 ----
    param_group = QGroupBox("传输参数")
    form = QFormLayout()
    chunk_spin = QSpinBox()
    chunk_spin.setRange(50, 2953)
    chunk_spin.setValue(protocol.DEFAULT_CHUNK_SIZE)
    fps_spin = QSpinBox()
    fps_spin.setRange(1, 60)
    fps_spin.setValue(12)
    loops_spin = QSpinBox()
    loops_spin.setRange(1, 20)
    loops_spin.setValue(3)
    display_spin = QSpinBox()
    display_spin.setRange(0, 4)
    display_spin.setValue(0)
    fec_check = QCheckBox("启用 FEC 前向纠错(建议开启,丢帧可恢复)")
    fec_check.setChecked(True)
    redundancy_spin = QSpinBox()
    redundancy_spin.setRange(1, 50)
    redundancy_spin.setValue(10)
    redundancy_spin.setSuffix("%")
    form.addRow("单块字节:", chunk_spin)
    form.addRow("帧率 fps:", fps_spin)
    form.addRow("循环轮数:", loops_spin)
    form.addRow("显示器:", display_spin)
    form.addRow(fec_check)
    form.addRow("FEC 冗余:", redundancy_spin)
    param_group.setLayout(form)
    layout.addWidget(param_group)

    # ---- 控制按钮 + 进度 ----
    start_btn = QPushButton("▶  开始传输")
    start_btn.setStyleSheet("QPushButton{font-size:16px;padding:10px;font-weight:bold;}")
    progress = QProgressBar()
    progress.setRange(0, 100)
    status_label = QLabel("就绪。选择文件后点击「开始传输」。")
    layout.addWidget(start_btn)
    layout.addWidget(progress)
    layout.addWidget(status_label)

    # ---- 后台播放线程 ----
    class Worker(QThread):
        progress_sig = pyqtSignal(int, str)
        finished_sig = pyqtSignal(bool, str)

        def __init__(self, result, opts):
            super().__init__()
            self.result = result
            self.opts = opts
            self.stop_event = threading.Event()

        def run(self):
            total = len(self.result.frames) * self.opts["loops"]
            def on_progress(rnd, idx, n):
                if self.stop_event.is_set():
                    return
                done = rnd * n + idx + 1
                pct = int(done / total * 100) if total else 0
                self.progress_sig.emit(
                    min(100, pct),
                    f"第 {rnd+1}/{self.opts['loops']} 轮  帧 {idx+1}/{n}",
                )
            ok = player.play(
                self.result,
                fps=self.opts["fps"],
                loops=self.opts["loops"],
                display=self.opts["display"],
                box=self.opts["box"],
                on_progress=on_progress,
                stop_event=self.stop_event,
            )
            self.finished_sig.emit(ok, "传输完成" if ok else "传输被中断")

    worker_holder: dict = {"thread": None}

    def start_transfer():
        if not selected_paths:
            QMessageBox.warning(window, "提示", "请先选择要传输的文件或目录。")
            return
        # 构建
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

        status_label.setText(
            f"构建完成:文件 {result.file_count} 个, 总帧 {len(result.frames)}。开始播放…"
        )
        opts = {
            "fps": fps_spin.value(),
            "loops": loops_spin.value(),
            "display": display_spin.value(),
            "box": 10,
        }
        worker = Worker(result, opts)
        worker.progress_sig.connect(
            lambda pct, txt: (progress.setValue(pct), status_label.setText(txt))
        )
        worker.finished_sig.connect(on_transfer_finished)
        worker_holder["thread"] = worker
        worker.start()
        start_btn.setText("■  停止")
        start_btn.clicked.disconnect()
        start_btn.clicked.connect(stop_transfer)

    def stop_transfer():
        t = worker_holder.get("thread")
        if t and t.isRunning():
            t.stop_event.set()
            status_label.setText("正在停止…")

    def on_transfer_finished(ok, msg):
        progress.setValue(100 if ok else progress.value())
        status_label.setText(msg + "。" if not msg.endswith("。") else msg)
        start_btn.setText("▶  开始传输")
        start_btn.clicked.disconnect()
        start_btn.clicked.connect(start_transfer)
        worker_holder["thread"] = None
        if ok:
            QMessageBox.information(window, "完成", "传输完成,接收端应已收到文件。")

    start_btn.clicked.connect(start_transfer)

    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
