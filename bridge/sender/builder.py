#!/usr/bin/env python3
"""
bridge.sender.builder - v3 build layer

Pipeline: file -> zstd compress -> chunk -> (optional LT fountain) -> binary frames

Protocol v3 improvements:
  - zstd compression before chunking
  - Binary frames (no JSON/Base64 overhead)
  - QR M-level error correction (more capacity per code)
  - LT fountain codes (no multi-loop needed)
  - Backward-compatible v2 path retained
"""

from __future__ import annotations

import io
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

import qrcode

from bridge.common import protocol as v2proto
from bridge.common import binproto
from bridge.fec import fountain as lt

QR_ERROR_CORRECT = qrcode.constants.ERROR_CORRECT_M


@dataclass
class BuildResult:
    sid: str
    sid_bytes: bytes
    frames: List
    file_count: int
    total_data_chunks: int
    manifest: dict = field(default_factory=dict)
    protocol_version: int = 3


def _qr_matrix_bytes(payload, box: int = 10, border: int = 4):
    qr = qrcode.QRCode(
        version=None, error_correction=QR_ERROR_CORRECT,
        box_size=box, border=border,
    )
    qr.add_data(payload)
    qr.make(fit=True)
    matrix = qr.get_matrix()
    modules = len(matrix)
    n = modules * box
    data = bytearray(n * n)
    for r in range(modules):
        row_base = r * box
        for c in range(modules):
            val = 0 if matrix[r][c] else 255
            for dr in range(box):
                base = (row_base + dr) * n
                for dc in range(box):
                    data[base + c * box + dc] = val
    return n, bytes(data)


def _qr_to_image(payload, box: int = 10, border: int = 4):
    qr = qrcode.QRCode(
        version=None, error_correction=QR_ERROR_CORRECT,
        box_size=box, border=border,
    )
    qr.add_data(payload)
    qr.make(fit=True)
    return qr.make_image(fill_color="black", back_color="white")


def payload_to_png_bytes(payload, box: int = 10, border: int = 4) -> bytes:
    img = _qr_to_image(payload, box, border).convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _collect_files(paths: List[str]) -> List[Path]:
    files: List[Path] = []
    for p in paths:
        pp = Path(p)
        if pp.is_dir():
            for root, _dirs, fnames in os.walk(pp):
                for fn in sorted(fnames):
                    if not fn.startswith("."):
                        files.append(Path(root) / fn)
        elif pp.is_file():
            files.append(pp)
    return files


def build(
    paths: List[str],
    chunk_size: int = binproto.DEFAULT_CHUNK_SIZE_V3,
    use_fountain: bool = True,
    fountain_redundancy: float = 0.5,
    use_fec: bool = False,
    compress_level: int = 9,
    box: int = 10,
    border: int = 4,
    protocol_version: int = 3,
    fec_redundancy: float = 0.1,
) -> BuildResult:
    if protocol_version == 2:
        return _build_v2(paths, chunk_size or v2proto.DEFAULT_CHUNK_SIZE,
                         use_fec, fec_redundancy, box, border)
    return _build_v3(paths, chunk_size, use_fountain,
                     fountain_redundancy, compress_level, box, border)


def _build_v3(
    paths, chunk_size, use_fountain, fountain_redundancy,
    compress_level, box, border,
) -> BuildResult:
    files = _collect_files(paths)
    if not files:
        raise ValueError("No files found to transmit")

    sid_bytes = binproto.new_sid_bytes()
    sid_hex = binproto.sid_to_hex(sid_bytes)

    frames: List[bytes] = []
    manifest_files = []
    total_source_blocks = 0

    for fp in files:
        safe_cs = binproto.safe_chunk_size_v3(chunk_size, fp.name)
        _, chunks, orig_size, fname = binproto.encode_file_v3(
            fp, chunk_size=safe_cs, sid=sid_bytes,
            compress_level=compress_level,
        )
        k = len(chunks)
        total_source_blocks += k
        block_size = max(len(c) for c in chunks) if chunks else 0
        compressed_len = sum(len(c) for c in chunks)

        if use_fountain and k > 1:
            enc = lt.LTEncoder(chunks)
            num_blocks = max(int(k * (1.0 + fountain_redundancy)), k + 1)
            for block_id, payload in enc.generate(num_blocks):
                frame = binproto.pack_data_frame(
                    sid_bytes, block_id, k, orig_size, fname, payload,
                    flags=binproto.FLAG_COMPRESSED | binproto.FLAG_FOUNTAIN,
                )
                frames.append(frame)
        else:
            for i, chunk in enumerate(chunks):
                frame = binproto.pack_data_frame(
                    sid_bytes, i, k, orig_size, fname, chunk,
                    flags=binproto.FLAG_COMPRESSED,
                )
                frames.append(frame)

        manifest_files.append({
            "filename": fname,
            "size": orig_size,
            "k": k,
            "block_size": block_size,
            "compressed_len": compressed_len,
        })

    manifest = {
        "v": binproto.PROTOCOL_VERSION,
        "files": manifest_files,
        "chunk_size": chunk_size,
        "fountain": use_fountain,
        "total_source_blocks": total_source_blocks,
    }
    start_frame = binproto.pack_start_frame(
        sid_bytes, json.dumps(manifest, ensure_ascii=False)
    )
    end_frame = binproto.pack_end_frame(sid_bytes)
    frames.insert(0, start_frame)
    frames.append(end_frame)

    return BuildResult(
        sid=sid_hex, sid_bytes=sid_bytes, frames=frames,
        file_count=len(files), total_data_chunks=total_source_blocks,
        manifest=manifest, protocol_version=3,
    )


def _build_v2(
    paths, chunk_size, use_fec, fec_redundancy, box, border,
) -> BuildResult:
    import base64
    from bridge.fec import rs_codec as fec

    files = _collect_files(paths)
    if not files:
        raise ValueError("No files found to transmit")

    safe_max = v2proto.safe_chunk_size_for_payload(chunk_size)
    if chunk_size > safe_max:
        chunk_size = safe_max

    sid = v2proto.new_sid()
    frames_json: List[str] = []
    file_manifest = []
    per_file_fec = {}
    total_data_chunks = 0

    for fp in files:
        data_chunks = v2proto.encode_file(fp, chunk_size=chunk_size, sid=sid)
        k = len(data_chunks)
        total_data_chunks += k
        file_manifest.append({
            "filename": fp.name,
            "size": data_chunks[0]["size"] if data_chunks else 0,
            "chunks": k,
        })

        fec_meta = None
        fec_payloads = []
        if use_fec and k > 1:
            frag_bytes = [c["data"].encode("ascii") for c in data_chunks]
            try:
                fec_chunks, fec_meta = fec.encode(frag_bytes, redundancy=fec_redundancy)
                fec_payloads = [
                    (j, base64.b64encode(fc).decode("ascii"))
                    for j, fc in enumerate(fec_chunks)
                ]
            except fec.FECError:
                fec_meta = None

        for c in data_chunks:
            frame = v2proto.make_data_chunk(
                c["filename"], c["size"], c["index"], c["total"],
                c["data"], sid,
            )
            frames_json.append(v2proto.dumps(frame))

        for j, payload in fec_payloads:
            frame = v2proto.make_data_chunk(
                fp.name, data_chunks[0]["size"], j, k, payload, sid,
                extra={"is_fec": True},
            )
            frames_json.append(v2proto.dumps(frame))

        if fec_meta:
            per_file_fec[fp.name] = fec_meta.to_dict()

    start_extra = {"fec": per_file_fec} if per_file_fec else None
    start_frame = v2proto.make_start_frame(
        sid, file_manifest, total_data_chunks, extra=start_extra
    )
    end_frame = v2proto.make_end_frame(sid)
    frames_json.insert(0, v2proto.dumps(start_frame))
    frames_json.append(v2proto.dumps(end_frame))

    return BuildResult(
        sid=sid, sid_bytes=b"", frames=frames_json,
        file_count=len(files), total_data_chunks=total_data_chunks,
        protocol_version=2,
    )
