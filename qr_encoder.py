#!/usr/bin/env python3
"""
qr_encoder.py - QR File Station v3 encoder (standalone)

Usage:
  # Encode files to QR PNG images
  python qr_encoder.py encode input_dir/ -o qr_output/

  # Show transmission statistics (no output)
  python qr_encoder.py stats file.zip

  # Fullscreen playback (requires display)
  python qr_encoder.py play file.zip
"""

from __future__ import annotations

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from bridge.common import binproto
from bridge.sender import builder
from bridge.version import VERSION


def cmd_encode(args):
    """Encode files to QR PNG images."""
    paths = _resolve_paths(args.paths)
    if not paths:
        print("error: no valid input files", file=sys.stderr)
        return 1

    os.makedirs(args.output, exist_ok=True)

    result = builder.build(
        paths, chunk_size=args.chunk_size,
        use_fountain=not args.no_fountain,
        fountain_redundancy=args.redundancy,
        compress_level=args.compress_level,
        protocol_version=3,
    )

    print(f"\nProtocol v3 | {VERSION}")
    print(f"Files: {result.file_count}")
    print(f"Source blocks: {result.total_data_chunks}")
    print(f"Total frames (incl. start+end): {len(result.frames)}")
    print(f"Session ID: {result.sid}")

    # Generate PNGs
    from bridge.sender.builder import payload_to_png_bytes

    for i, frame_bytes in enumerate(result.frames):
        png_data = payload_to_png_bytes(
            frame_bytes, box=args.box, border=args.border
        )
        fname = f"frame_{i:05d}.png"
        with open(os.path.join(args.output, fname), "wb") as f:
            f.write(png_data)
        if (i + 1) % 20 == 0:
            print(f"  generated {i+1}/{len(result.frames)}")

    print(f"\nDone: {len(result.frames)} QR images in {args.output}/")

    _print_stats(result, paths)
    return 0


def cmd_stats(args):
    """Show transmission statistics without generating output."""
    paths = _resolve_paths(args.paths)
    if not paths:
        print("error: no valid input files", file=sys.stderr)
        return 1

    result = builder.build(
        paths, chunk_size=args.chunk_size,
        use_fountain=not args.no_fountain,
        fountain_redundancy=args.redundancy,
        compress_level=args.compress_level,
        protocol_version=3,
    )
    _print_stats(result, paths)
    return 0


def cmd_play(args):
    """Fullscreen playback."""
    from bridge.sender import player
    paths = _resolve_paths(args.paths)
    if not paths:
        print("error: no valid input files", file=sys.stderr)
        return 1

    result = builder.build(
        paths, chunk_size=args.chunk_size,
        use_fountain=not args.no_fountain,
        fountain_redundancy=args.redundancy,
        compress_level=args.compress_level,
        protocol_version=3,
    )
    _print_stats(result, paths)

    completed = player.play(
        result, fps=args.fps, loops=args.loops,
        grid_size=args.grid, display=args.display,
        box=args.box, headless=args.headless,
    )
    return 0 if completed else 2


def _resolve_paths(raw_paths):
    paths = []
    for p in raw_paths:
        if os.path.isdir(p):
            for root, _dirs, fnames in os.walk(p):
                for fn in sorted(fnames):
                    if not fn.startswith("."):
                        paths.append(os.path.join(root, fn))
        elif os.path.isfile(p):
            paths.append(p)
    return paths


def _print_stats(result, paths):
    total_orig = sum(os.path.getsize(p) for p in paths)
    total_compressed = 0
    for f_info in result.manifest.get("files", []):
        total_compressed += f_info.get("compressed_len", 0)

    ratio = total_orig / total_compressed if total_compressed > 0 else 0
    blocks = result.total_data_chunks
    frames = len(result.frames) - 2  # minus start+end

    print(f"\n{'='*50}")
    print(f"Transmission Statistics")
    print(f"{'='*50}")
    print(f"  Original size:      {_fmt(total_orig)}")
    print(f"  Compressed size:    {_fmt(total_compressed)}")
    print(f"  Compression ratio:  {ratio:.1f}x")
    print(f"  Source blocks (K):  {blocks}")
    print(f"  Transmit frames:    {frames}")
    if ratio > 0:
        eff_per_frame = total_orig / frames if frames > 0 else 0
        print(f"  Effective per frame: {_fmt(int(eff_per_frame))}")
        for fps in [12, 30]:
            tput = eff_per_frame * fps / 1024
            print(f"  Throughput @ {fps}fps: {tput:.1f} KB/s")
    print(f"{'='*50}")


def _fmt(n):
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n/1024:.1f} KB"
    return f"{n/(1024*1024):.2f} MB"


def main():
    ap = argparse.ArgumentParser(
        description=f"QR File Station v3 encoder ({VERSION})",
    )
    sub = ap.add_subparsers(dest="command")

    common_args = [
        ("paths", {"nargs": "+", "help": "input files/dirs"}),
        ("--chunk-size", {"type": int, "default": binproto.DEFAULT_CHUNK_SIZE_V3}),
        ("--no-fountain", {"action": "store_true"}),
        ("--redundancy", {"type": float, "default": 0.5}),
        ("--compress-level", {"type": int, "default": 9}),
        ("--box", {"type": int, "default": 10}),
        ("--border", {"type": int, "default": 4}),
    ]
    for name, help_text in [("encode", "encode to PNG"), ("stats", "show stats"),
                            ("play", "fullscreen playback")]:
        p = sub.add_parser(name, help=help_text)
        for arg_name, arg_kwargs in common_args:
            p.add_argument(arg_name, **arg_kwargs)
        if name == "encode":
            p.add_argument("-o", "--output", default="./qr_output")
        if name == "play":
            p.add_argument("--fps", type=int, default=30)
            p.add_argument("--loops", type=int, default=1)
            p.add_argument("--grid", type=int, default=2)
            p.add_argument("--display", type=int, default=0)
            p.add_argument("--headless", action="store_true")

    args = ap.parse_args()
    if args.command == "encode":
        return cmd_encode(args)
    elif args.command == "stats":
        return cmd_stats(args)
    elif args.command == "play":
        return cmd_play(args)
    else:
        ap.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(main())
