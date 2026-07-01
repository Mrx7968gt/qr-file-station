#!/usr/bin/env python3
"""
bridge.receiver.capture — 采集卡抓帧 + 解码 + 拼装主循环

这是 Mac 接收端的入口:打开采集卡,逐帧抓取 → pyzbar 解码 →
assembler 拼装 → 收齐自动落盘。

用法:
    ./bin/python -m bridge.receiver.capture --device 0
    ./bin/python -m bridge.receiver.capture --device 0 --out-dir ./recv
"""

from __future__ import annotations

import argparse
import os
import sys
import time

# 让作为模块或脚本运行都能 import
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

import cv2
import numpy as np
from pyzbar.pyzbar import decode as pyzbar_decode

from bridge.receiver import assembler as asm
from bridge.receiver import decoder


def open_capture(device: int):
    """打开采集卡,返回 VideoCapture。"""
    cap = cv2.VideoCapture(device)
    if not cap.isOpened():
        raise SystemExit(
            f"无法打开摄像头 device={device}。\n"
            f"  · 确认采集卡已 USB 插入\n"
            f"  · 用 probe_capture_card.py 确认正确的 device 号\n"
            f"  · 确认终端程序有摄像头权限"
        )
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    print(f"[接收端] 打开 device={device}  {w}x{h}")
    return cap


def receive(
    device: int = 0,
    out_dir: str = "./recv",
    show_window: bool = True,
    quit_keys=(ord("q"), 27),
):
    """
    主接收循环。

    Args:
        device: 采集卡 OpenCV 设备号
        out_dir: 落盘目录
        show_window: 是否显示实时画面窗口
        quit_keys: 退出键
    """
    cap = open_capture(device)

    def on_done(filename, path):
        print(f"\n✅ 文件已还原: {filename} → {path}\n")

    assembler = asm.Assembler(out_dir=out_dir, on_file_done=on_done)
    print(f"[接收端] 落盘目录: {os.path.abspath(out_dir)}")
    print("[接收端] 等待发送端传输…按 q/ESC 退出\n")

    attempts = 0
    last_progress_print = 0.0

    while True:
        ok, frame = cap.read()
        if not ok:
            continue

        attempts += 1
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        raw_objs = pyzbar_decode(gray)
        frames = decoder.decode_all(raw_objs)

        # 处理本帧解码出的所有帧
        for f in frames:
            assembler.handle_frame(f)

        # 画面标注:画框 + 进度
        annotated = frame
        for o in raw_objs:
            try:
                text = o.data.decode("utf-8", errors="replace")
            except Exception:
                text = ""
            if text:
                pts = np.array([[p.x, p.y] for p in o.polygon], dtype=np.int32)
                cv2.polylines(annotated, [pts], True, (0, 255, 0), 3)
                short = text if len(text) <= 50 else text[:47] + "..."
                cv2.putText(annotated, short, (10, 30),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)

        # 周期打印进度(每 2 秒)
        now = time.time()
        prog = assembler.progress()
        if prog and now - last_progress_print > 2.0:
            last_progress_print = now
            for fname, p in prog.items():
                mark = "✓" if p["done"] else "…"
                print(f"  {mark} {fname}: {p['received']}/{p['total']} 块")

        # 状态条
        h = annotated.shape[0]
        status = f"scanning... frames={attempts}"
        cv2.putText(annotated, status, (10, h - 20),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 200, 255), 2)

        if show_window:
            cv2.imshow("capture-link receiver (q to quit)", annotated)
            key = cv2.waitKey(1) & 0xFF
            if key in quit_keys:
                break

    cap.release()
    if show_window:
        cv2.destroyAllWindows()

    # 收尾:尝试 FEC 恢复所有未完成的文件
    print("\n[接收端] 退出前尝试 FEC 恢复未完成文件…")
    saved = 0
    for sess in assembler.sessions.values():
        for fname, buf in sess.files.items():
            if fname in sess.completed:
                continue
            recovered = buf.recovered_raw_chunks()
            if recovered is not None:
                import base64
                try:
                    raw = base64.b64decode("".join(c.decode("ascii") for c in recovered))
                    safe = os.path.basename(fname)
                    path = os.path.join(out_dir, safe)
                    with open(path, "wb") as fobj:
                        fobj.write(raw)
                    sess.completed.append(fname)
                    print(f"✅ FEC 恢复成功: {fname} → {path}")
                    saved += 1
                except Exception as e:
                    print(f"  FEC 恢复 {fname} 失败: {e}")
    if saved == 0:
        print("  (无文件靠 FEC 恢复)")
    print(f"\n[接收端] 结束。共抓 {attempts} 帧。")


def main():
    ap = argparse.ArgumentParser(description="采集卡文件传输 · 接收端(Mac)")
    ap.add_argument("--device", type=int, default=0, help="采集卡 OpenCV 设备号(默认 0)")
    ap.add_argument("--out-dir", type=str, default="./recv", help="落盘目录(默认 ./recv)")
    ap.add_argument("--no-window", action="store_true", help="不显示实时画面窗口")
    args = ap.parse_args()
    receive(device=args.device, out_dir=args.out_dir, show_window=not args.no_window)


if __name__ == "__main__":
    main()
