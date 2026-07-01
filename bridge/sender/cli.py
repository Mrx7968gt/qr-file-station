#!/usr/bin/env python3
"""
bridge.sender.cli — 发送端命令行入口

用法:
    # 传输单个文件(默认全屏播放,12fps,3轮,FEC开启)
    python -m bridge.sender.cli report.zip

    # 传输整个目录
    python -m bridge.sender.cli ./my_folder

    # 自定义参数
    python -m bridge.sender.cli report.zip --chunk-size 500 --fps 8 --loops 5

    # 关闭 FEC(仅靠循环重发)
    python -m bridge.sender.cli report.zip --no-fec

    # 指定第二块显示器(双屏扩展时把画面送到采集卡那块屏)
    python -m bridge.sender.cli report.zip --display 1
"""

from __future__ import annotations

import argparse
import os
import sys

# 让作为模块或脚本运行都能 import
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from bridge.common import protocol
from bridge.sender import builder, player


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="采集卡文件传输 · 发送端(Windows)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    ap.add_argument("paths", nargs="+", help="要传输的文件或目录路径(可多个)")
    ap.add_argument("--chunk-size", type=int, default=protocol.DEFAULT_CHUNK_SIZE,
                    help=f"单块有效字节(默认 {protocol.DEFAULT_CHUNK_SIZE},越大越快但码越密)")
    ap.add_argument("--fps", type=int, default=12, help="播放帧率(默认 12)")
    ap.add_argument("--loops", type=int, default=3, help="循环轮数(默认 3)")
    ap.add_argument("--display", type=int, default=0,
                    help="渲染到第几块显示器(双屏扩展时设 1)")
    ap.add_argument("--box", type=int, default=10, help="QR 模块像素(默认 10)")
    ap.add_argument("--no-fec", action="store_true", help="关闭 FEC(仅靠循环重发)")
    ap.add_argument("--fec-redundancy", type=float, default=0.1,
                    help="FEC 冗余比例(默认 0.1=10%%)")
    ap.add_argument("--headless", action="store_true",
                    help="不开窗口(测试用,真实传输勿用)")
    args = ap.parse_args(argv)

    # 校验路径
    valid_paths = []
    for p in args.paths:
        if os.path.exists(p):
            valid_paths.append(p)
        else:
            print(f"⚠ 路径不存在,跳过: {p}", file=sys.stderr)
    if not valid_paths:
        print("错误:没有有效的文件/目录路径", file=sys.stderr)
        return 1

    # 构建
    print("=" * 60)
    print("采集卡文件传输 · 发送端")
    print("=" * 60)
    print(f"路径: {valid_paths}")
    print(f"chunk-size={args.chunk_size} fps={args.fps} loops={args.loops} "
          f"fec={'关' if args.no_fec else '开(' + str(int(args.fec_redundancy*100)) + '%)'}")
    print("=" * 60)

    try:
        result = builder.build(
            valid_paths,
            chunk_size=args.chunk_size,
            use_fec=not args.no_fec,
            fec_redundancy=args.fec_redundancy,
            box=args.box,
        )
    except ValueError as e:
        print(f"错误: {e}", file=sys.stderr)
        return 1

    print(f"\n✓ 构建完成: 文件 {result.file_count} 个, "
          f"数据块 {result.total_data_chunks}, 总帧 {len(result.frames)}")
    print(f"  会话 sid={result.sid}")
    print(f"\n开始播放(按 ESC/Q 退出)…\n")

    # 进度回调
    def on_progress(rnd, idx, total):
        if idx == 0 or (idx + 1) % 10 == 0:
            print(f"  第 {rnd+1}/{args.loops} 轮  帧 {idx+1}/{total}")

    completed = player.play(
        result,
        fps=args.fps,
        loops=args.loops,
        display=args.display,
        box=args.box,
        on_progress=on_progress,
        headless=args.headless,
    )

    if completed:
        print("\n✓ 传输完成。")
        return 0
    else:
        print("\n⚠ 传输被中断。", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
