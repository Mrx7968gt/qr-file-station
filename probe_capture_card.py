#!/usr/bin/env python3
"""
采集卡设备探测(按名匹配,不靠 OpenCV index 盲试)

Mac 上 OpenCV 的摄像头 index 顺序不固定,盲试不可靠。
本脚本用 AVFoundation 原生 API 列出所有摄像头及其名字,
并自动识别采集卡(名字含 "USB Video" 或 VID:PID 534d:2109),
找到它在 OpenCV 里对应的真实 index。

用法(在有摄像头权限的终端里跑):
    ./bin/python probe_capture_card.py
"""

import AVFoundation as av
import cv2


def list_avfoundation_devices():
    """用 AVFoundation 列出所有视频设备(带名字和唯一ID)。"""
    devs = av.AVCaptureDevice.devicesWithMediaType_("vide")
    result = []
    for i, d in enumerate(devs):
        result.append({
            "avf_index": i,
            "name": d.localizedName(),
            "uniqueID": d.uniqueID(),
            "modelID": d.modelID(),
        })
    return result


def is_capture_card(dev):
    """判断是否为采集卡:名字或型号含采集卡特征。"""
    blob = f"{dev['name']} {dev['modelID']} {dev['uniqueID']}".lower()
    # 534d:2109 是采集卡的 VID:PID;USB Video 是常见采集卡显示名
    return ("534d" in blob and "2109" in blob) or "usb video" in blob


def find_capture_card_in_opencv(target_name, target_uid):
    """
    OpenCV 的 index 顺序和 AVFoundation 不一致,
    靠逐个打开 + 读帧对比来定位采集卡对应的 OpenCV index。
    返回能抓到非空帧的 index 列表。
    """
    working = []
    for i in range(6):
        cap = cv2.VideoCapture(i)
        if not cap.isOpened():
            cap.release()
            continue
        frame = None
        for _ in range(15):  # AVFoundation 前几帧常是黑的
            ok, f = cap.read()
            if ok and f is not None:
                frame = f
        if frame is not None:
            h, w = frame.shape[:2]
            mean = frame.mean()
            # 存帧供肉眼确认 + 统计亮度(全黑=可能是没信号的采集卡)
            import os
            os.makedirs("recv_probe", exist_ok=True)
            cv2.imwrite(f"recv_probe/opencv_{i}.png", frame)
            working.append((i, w, h, round(float(mean), 1)))
        cap.release()
    return working


def main():
    print("=" * 60)
    print("AVFoundation 视频设备列表")
    print("=" * 60)
    devs = list_avfoundation_devices()
    card = None
    for d in devs:
        flag = "  ★ 疑似采集卡" if is_capture_card(d) else ""
        print(f"  [{d['avf_index']}] {d['name']!r}{flag}")
        print(f"      uniqueID = {d['uniqueID']}")
        print(f"      modelID  = {d['modelID']}")
        if is_capture_card(d) and card is None:
            card = d
    print()

    if card:
        print(f"✓ 识别到采集卡:{card['name']!r}  (uniqueID={card['uniqueID']})")
        print(f"  该设备在 AVFoundation 列表中的序号是 [{card['avf_index']}]")
        print()
    else:
        print("⚠ 未识别到采集卡(没找到含 534d:2109 或 'USB Video' 的设备)")
        print("  请确认采集卡已 USB 插入并识别。")
        print()

    print("=" * 60)
    print("现在逐个测试 OpenCV index(找到能抓帧的 index)")
    print("=" * 60)
    print("  每个能抓到帧的 index 都会存图到 recv_probe/opencv_<i>.png")
    print("  你打开图片看哪张是采集卡画面,那个 i 就是你要用的 --device")
    print()
    working = find_capture_card_in_opencv(
        card["name"] if card else "", card["uniqueID"] if card else ""
    )
    if not working:
        print("❌ 所有 OpenCV index 都抓不到帧")
        print("   最可能原因:运行本脚本的程序(终端/编辑器)没有摄像头权限")
        print("   解决:系统设置 → 隐私与安全性 → 摄像头 → 给你的终端程序授权 → 重启终端")
    else:
        print("能抓到帧的 OpenCV index:")
        for i, w, h, mean in working:
            print(f"  --device {i}   {w}x{h}  平均亮度={mean}"
                  f"   (亮度<10 说明采集卡没信号/没接好)")
        print()
        print("→ 打开 recv_probe/ 目录看图,确认采集卡对应的 index,")
        print("  然后: ./bin/python verify_receiver.py --device <那个index>")


if __name__ == "__main__":
    main()
