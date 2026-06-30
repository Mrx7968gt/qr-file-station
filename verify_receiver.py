#!/usr/bin/env python3
"""
采集卡链路 · 最小验证脚本(接收端 / Mac 端)

用途:打开采集卡摄像头,逐帧抓取并用 pyzbar 解码。读到「verify-0001」载荷就
     说明「Windows 屏幕 → 采集卡 → Mac」链路全程打通。

使用(Mac 上):
    brew install zbar            # pyzbar 依赖的系统库
    pip install opencv-python pyzbar
    python verify_receiver.py
    python verify_receiver.py --device 1   # 0 常常是 Facetime 摄像头

运行后:
    - 实时显示采集画面和当前尝试解码次数。
    - 读到测试载荷会高亮提示 SUCCESS,并打印完整内容。
    - 按 q / ESC 退出。
"""

import argparse
import sys
from datetime import datetime

import cv2
from pyzbar.pyzbar import decode

# 与 verify_sender.py 一致
MAGIC = "CAPTURE_LINK_PROBE"
SUCCESS_MARK = "verify-0001"


def list_devices(up_to: int = 5) -> None:
    """Mac 上没有可靠的摄像头枚举 API,只能逐个 open 试探,打印可用 index。"""
    print("[接收端] 探测摄像头设备号(逐个尝试打开):")
    for i in range(up_to):
        cap = cv2.VideoCapture(i)
        ok = cap.isOpened()
        if ok:
            w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            print(f"  device {i}: 可用  {w}x{h}")
        cap.release()
    print("  (若采集卡不在列表里,可能是驱动问题;选错 index 画面会是黑屏/别的摄像头)")


def main() -> None:
    ap = argparse.ArgumentParser(description="采集卡链路验证 · 接收端")
    ap.add_argument("--device", type=int, default=1, help="采集卡摄像头 index(默认 1)")
    ap.add_argument("--probe", action="store_true", help="只列出设备号后退出")
    ap.add_argument("--show", action="store_true", default=True,
                    help="显示实时画面窗口")
    args = ap.parse_args()

    if args.probe:
        list_devices()
        return

    cap = cv2.VideoCapture(args.device)
    if not cap.isOpened():
        print(f"[接收端] 无法打开摄像头 device={args.device}", file=sys.stderr)
        print("[接收端] 建议:先跑 python verify_receiver.py --probe 看哪个 index 是采集卡",
              file=sys.stderr)
        sys.exit(1)

    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    print(f"[接收端] 打开 device={args.device}  {w}x{h}")
    print(f"[接收端] 等待发送端的「{MAGIC}」载荷…按 q/ESC 退出")

    attempts = 0
    success = False
    last_log = 0.0

    while True:
        ok, frame = cap.read()
        if not ok:
            continue

        attempts += 1
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        objs = decode(gray)

        now = time_now()
        if objs:
            # 周期性打印,避免刷屏
            if now - last_log > 1.0:
                last_log = now
            for o in objs:
                text = o.data.decode("utf-8", errors="replace")
                pts = [[(p.x, p.y) for p in o.polygon]]
                # 画框 + 截断显示
                cv2.polylines(frame, pts, True, (0, 255, 0), 3)
                short = text if len(text) <= 60 else text[:57] + "..."
                cv2.putText(frame, short, (10, 30),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
                if MAGIC in text:
                    success = True
                    print("\n" + "=" * 60)
                    print(f"✅ 成功!采集卡链路已打通。尝试 {attempts} 帧后读出载荷。")
                    print(f"完整载荷:\n{text}")
                    print("=" * 60 + "\n")

        status = "SUCCESS ✓" if success else f"scanning... attempts={attempts}"
        color = (0, 255, 0) if success else (0, 200, 255)
        cv2.putText(frame, status, (10, h - 20),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)

        if args.show:
            cv2.imshow("capture-link probe (q to quit)", frame)
            key = cv2.waitKey(1) & 0xFF
            if key in (ord("q"), 27):  # q or ESC
                break

    cap.release()
    if args.show:
        cv2.destroyAllWindows()
    print(f"[接收端] 结束。共尝试 {attempts} 帧,"
          f"{'链路验证通过 ✅' if success else '未读到载荷 ❌(检查方向/分辨率/对焦)'}")
    sys.exit(0 if success else 2)


def time_now() -> float:
    return datetime.now().timestamp()


if __name__ == "__main__":
    main()
