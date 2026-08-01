#!/usr/bin/env python3
"""
bridge.sender.cli - sender CLI entry (v3)

v3 defaults: binary frames, zstd, LT fountain, 30fps, 2x2 grid, M-level QR
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from bridge.common import binproto
from bridge.sender import builder, player
from bridge.version import VERSION


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=f"QR File Station sender v{VERSION}",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("paths", nargs="+", help="files or directories to transmit")
    ap.add_argument("--chunk-size", type=int, default=binproto.DEFAULT_CHUNK_SIZE_V3,
                    help=f"bytes per chunk (default {binproto.DEFAULT_CHUNK_SIZE_V3})")
    ap.add_argument("--fps", type=int, default=30, help="frame rate (default 30)")
    ap.add_argument("--loops", type=int, default=1,
                    help="loop count (default 1 with fountain; use 3+ without)")
    ap.add_argument("--grid", type=int, default=2,
                    help="NxN QR grid per screen (default 2 = 2x2)")
    ap.add_argument("--display", type=int, default=0, help="monitor index")
    ap.add_argument("--box", type=int, default=10, help="QR module pixels")
    ap.add_argument("--no-fountain", action="store_true",
                    help="disable LT fountain codes (sequential mode)")
    ap.add_argument("--fountain-redundancy", type=float, default=0.5,
                    help="fountain redundancy ratio (default 0.15)")
    ap.add_argument("--compress-level", type=int, default=9,
                    help="zstd compression level 1-22 (default 9)")
    ap.add_argument("--v2", action="store_true",
                    help="use legacy v2 protocol (JSON+Base64+RS FEC)")
    ap.add_argument("--no-fec", action="store_true",
                    help="(v2 only) disable RS FEC")
    ap.add_argument("--fec-redundancy", type=float, default=0.1,
                    help="(v2 only) FEC redundancy ratio")
    ap.add_argument("--headless", action="store_true", help="no display (testing)")
    args = ap.parse_args(argv)

    valid_paths = []
    for p in args.paths:
        if os.path.exists(p):
            valid_paths.append(p)
        else:
            print(f"warning: path not found, skipping: {p}", file=sys.stderr)
    if not valid_paths:
        print("error: no valid file/directory paths", file=sys.stderr)
        return 1

    proto_ver = 2 if args.v2 else 3
    print("=" * 60)
    print(f"QR File Station sender v{VERSION} (protocol v{proto_ver})")
    print("=" * 60)

    if proto_ver == 3:
        mode = "sequential" if args.no_fountain else "LT fountain"
        print(f"paths: {valid_paths}")
        print(f"chunk={args.chunk_size} fps={args.fps} grid={args.grid}x{args.grid} "
              f"loops={args.loops} mode={mode} compress=zstd({args.compress_level})")
    else:
        print(f"paths: {valid_paths}")
        print(f"chunk={args.chunk_size} fps={args.fps} loops={args.loops} "
              f"fec={'off' if args.no_fec else 'on'}")
    print("=" * 60)

    try:
        result = builder.build(
            valid_paths,
            chunk_size=args.chunk_size,
            use_fountain=not args.no_fountain,
            fountain_redundancy=args.fountain_redundancy,
            use_fec=not args.no_fec,
            compress_level=args.compress_level,
            protocol_version=proto_ver,
            fec_redundancy=args.fec_redundancy,
        )
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    print(f"\nbuild complete: {result.file_count} files, "
          f"{result.total_data_chunks} blocks, {len(result.frames)} frames")
    print(f"  sid={result.sid}")
    print(f"\nstarting playback (ESC/Q to quit)...\n")

    def on_progress(rnd, idx, total):
        if idx == 0 or (idx + 1) % 10 == 0:
            print(f"  round {rnd+1}/{args.loops} frame {idx+1}/{total}")

    completed = player.play(
        result,
        fps=args.fps,
        loops=args.loops,
        display=args.display,
        box=args.box,
        grid_size=args.grid,
        on_progress=on_progress,
        headless=args.headless,
    )

    if completed:
        print("\ntransmission complete.")
        return 0
    else:
        print("\ntransmission interrupted.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
