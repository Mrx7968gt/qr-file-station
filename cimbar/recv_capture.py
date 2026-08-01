#!/usr/bin/env python3
"""Cimbar capture card receiver.

Reads frames from a video device, decodes cimbar tiles, reconstructs file.
"""

from __future__ import annotations

import argparse
import os
import struct
import sys
import time

import cv2
import numpy as np

from .config import FOUNTAIN_CHUNK_SIZE, FOUNTAIN_OVERHEAD
from .decoder import decode_frame

FOUNTAIN_HEADER_SIZE = 6


def parse_fountain_header(data):
    if len(data) < FOUNTAIN_HEADER_SIZE:
        return {}
    block_id = struct.unpack_from(">I", data, 0)[0]
    extra = struct.unpack_from(">H", data, 4)[0]
    return {"block_id": block_id, "extra": extra}


class FountainCollector:
    def __init__(self, chunk_size, total_chunks):
        self.chunk_size = chunk_size
        self.total_chunks = total_chunks
        self.chunks = {}
        self.completed = False
        self.result = None

    def add_chunk(self, block_id, data):
        if self.completed or block_id in self.chunks:
            return
        self.chunks[block_id] = data
        if len(self.chunks) >= self.total_chunks:
            self._try_decode()

    def _try_decode(self):
        try:
            ordered = []
            for i in range(self.total_chunks):
                if i not in self.chunks:
                    return
                ordered.append(self.chunks[i])
            self.result = b"".join(ordered)
            self.completed = True
        except Exception:
            pass

    @property
    def progress(self):
        return (len(self.chunks), self.total_chunks)


def zstd_decompress(data):
    import zstandard
    return zstandard.ZstdDecompressor().decompress(data)


def find_cimbar_grid(img):
    h, w = img.shape[:2]
    if abs(w - 1024) < 50 and abs(h - 1024) < 50:
        ox = max(0, (w - 1024) // 2)
        oy = max(0, (h - 1024) // 2)
        return (ox, oy, 1024, 1024)
    if w >= 1024 and h >= 1024:
        return ((w - 1024) // 2, (h - 1024) // 2, 1024, 1024)
    return None


def receive(device=0, out_dir="./recv", fps=15, show_window=True):
    cap = cv2.VideoCapture(device)
    if not cap.isOpened():
        print(f"Error: cannot open device {device}")
        sys.exit(1)

    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    print(f"[cimbar-recv] device={device}  {w}x{h}")
    print(f"[cimbar-recv] output: {os.path.abspath(out_dir)}")
    print("[cimbar-recv] waiting for transmission... (q/ESC to quit)\n")

    os.makedirs(out_dir, exist_ok=True)
    attempts = 0
    frame_count = 0
    collector = None
    last_status = 0.0

    while True:
        ok, frame = cap.read()
        if not ok:
            continue

        attempts += 1
        grid = find_cimbar_grid(frame)

        if grid is None:
            if show_window:
                cv2.putText(frame, "No cimbar grid", (10, 30),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)
                cv2.imshow("cimbar (q to quit)", frame)
                key = cv2.waitKey(1) & 0xFF
                if key in (ord("q"), 27):
                    break
            continue

        ox, oy, gw, gh = grid
        crop = frame[oy:oy + gh, ox:ox + gw]

        payload = decode_frame(crop)
        if payload is None:
            if show_window:
                cv2.rectangle(frame, (ox, oy), (ox + gw, oy + gh), (0, 128, 0), 2)
                cv2.imshow("cimbar (q to quit)", frame)
                key = cv2.waitKey(1) & 0xFF
                if key in (ord("q"), 27):
                    break
            continue

        frame_count += 1
        chunk_size = FOUNTAIN_CHUNK_SIZE
        stride = chunk_size + FOUNTAIN_OVERHEAD
        n_chunks = len(payload) // stride

        for i in range(n_chunks):
            off = i * stride
            header = payload[off:off + FOUNTAIN_OVERHEAD]
            chunk_data = payload[off + FOUNTAIN_OVERHEAD:off + stride]
            info = parse_fountain_header(header)
            bid = info.get("block_id", i)

            if collector is None:
                collector = FountainCollector(chunk_size, max(n_chunks * 3, 10))

            collector.add_chunk(bid, chunk_data)

        now = time.time()
        if collector and now - last_status > 2.0:
            last_status = now
            got, total = collector.progress
            pct = got / total * 100 if total > 0 else 0
            print(f"  frames={frame_count} chunks={got}/{total} ({pct:.0f}%)")

        if collector and collector.completed:
            try:
                raw = zstd_decompress(collector.result)
                fname = f"received_{int(time.time())}.bin"
                path = os.path.join(out_dir, fname)
                with open(path, "wb") as f:
                    f.write(raw)
                print(f"\n  RECEIVED: {fname} ({len(raw)}B) -> {path}\n")
                collector = None
            except Exception as e:
                print(f"  decompress error: {e}")

        if show_window:
            cv2.rectangle(frame, (ox, oy), (ox + gw, oy + gh), (0, 255, 0), 2)
            s = f"decoded: {frame_count} frames"
            if collector:
                g, t = collector.progress
                s += f"  chunks: {g}/{t}"
            cv2.putText(frame, s, (10, h - 20),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 200, 255), 2)
            cv2.imshow("cimbar (q to quit)", frame)
            key = cv2.waitKey(1) & 0xFF
            if key in (ord("q"), 27):
                break

    cap.release()
    if show_window:
        cv2.destroyAllWindows()
    print(f"\n[cimbar-recv] done. {attempts} captured, {frame_count} decoded.")


def main():
    ap = argparse.ArgumentParser(description="cimbar capture card receiver")
    ap.add_argument("--device", type=int, default=0)
    ap.add_argument("--out", type=str, default="./recv")
    ap.add_argument("--fps", type=int, default=15)
    ap.add_argument("--no-window", action="store_true")
    args = ap.parse_args()
    receive(device=args.device, out_dir=args.out, fps=args.fps,
            show_window=not args.no_window)


if __name__ == "__main__":
    main()
