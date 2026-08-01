#!/usr/bin/env python3
"""
bridge.common.binproto - Protocol v3: binary frames + zstd compression

Key improvements over v2 (JSON + Base64):
  1. zstd compression: 3-5x for text, 1.2-2x for binary
  2. Binary frames: eliminates JSON overhead and Base64 bloat
  3. QR M-level (15%): capacity ~1273B -> ~2331B
  4. Works with fountain codes, no multi-loop needed

Frame format (v3 binary):
  Offset Size Field
  0      2    Magic: 0x51 0x52
  2      1    Version: 3
  3      1    Type: 0=start, 1=data, 2=end
  4      1    Flags: bit0=compressed, bit1=fountain
  5      3    SID: raw session ID bytes
  8      4    Seed: uint32 BE
  12     2    K: uint16 BE (source block count)
  14     4    FileSize: uint32 BE
  18     1    NameLen
  19     N    Name: UTF-8
  19+N   ...  Payload: raw bytes

Fixed header: 19 bytes + filename
"""

from __future__ import annotations

import os
import secrets
import struct
import zlib
from pathlib import Path
from typing import Dict, List, Optional, Tuple

_zstd = None


def _ensure_zstd():
    global _zstd
    if _zstd is None:
        import zstandard as _z
        _zstd = _z
    return _zstd


PROTOCOL_VERSION = 3

FRAME_MAGIC = b"\x51\x52"

FT_START = 0
FT_DATA = 1
FT_END = 2

FLAG_COMPRESSED = 0x01
FLAG_FOUNTAIN = 0x02

_HEADER_FMT = ">2sBBB3sIHBI"
HEADER_SIZE = struct.calcsize(_HEADER_FMT)

DEFAULT_CHUNK_SIZE_V3 = 1800

QR_V40_M_BYTE_CAPACITY = 2331
QR_SAFE_PAYLOAD_LIMIT = 2200


def new_sid_bytes() -> bytes:
    return secrets.token_bytes(3)


def sid_to_hex(sid: bytes) -> str:
    return sid.hex()


def sid_from_hex(s: str) -> bytes:
    return bytes.fromhex(s)


def compress(data: bytes, level: int = 9) -> bytes:
    zstd = _ensure_zstd()
    return zstd.ZstdCompressor(level=level).compress(data)


def decompress(data: bytes) -> bytes:
    zstd = _ensure_zstd()
    return zstd.ZstdDecompressor().decompress(data)


def crc32(data: bytes) -> int:
    return zlib.crc32(data) & 0xFFFFFFFF


def pack_data_frame(
    sid: bytes,
    seed: int,
    k: int,
    filesize: int,
    filename: str,
    payload: bytes,
    flags: int = 0,
) -> bytes:
    name_bytes = filename.encode("utf-8")
    if len(name_bytes) > 255:
        name_bytes = name_bytes[:255]
    header = struct.pack(
        _HEADER_FMT,
        FRAME_MAGIC,
        PROTOCOL_VERSION,
        FT_DATA,
        flags,
        sid,
        seed,
        k,
        len(name_bytes),
        filesize,
    )
    return header + name_bytes + payload


def pack_start_frame(sid: bytes, manifest_json: str) -> bytes:
    name_bytes = b"@start"
    header = struct.pack(
        _HEADER_FMT,
        FRAME_MAGIC,
        PROTOCOL_VERSION,
        FT_START,
        0,
        sid,
        0,
        0,
        len(name_bytes),
        0,
    )
    return header + name_bytes + manifest_json.encode("utf-8")


def pack_end_frame(sid: bytes) -> bytes:
    name_bytes = b"@end"
    header = struct.pack(
        _HEADER_FMT,
        FRAME_MAGIC,
        PROTOCOL_VERSION,
        FT_END,
        0,
        sid,
        0,
        0,
        len(name_bytes),
        0,
    )
    return header + name_bytes


def unpack_frame(data: bytes) -> Optional[Dict]:
    if len(data) < HEADER_SIZE:
        return None
    try:
        (magic, ver, ftype, flags, sid, seed, k,
         namelen, filesize) = struct.unpack_from(_HEADER_FMT, data, 0)
    except struct.error:
        return None

    if magic != FRAME_MAGIC:
        return None
    if ver != PROTOCOL_VERSION:
        return None

    name_end = HEADER_SIZE + namelen
    if len(data) < name_end:
        return None

    try:
        filename = data[HEADER_SIZE:name_end].decode("utf-8")
    except UnicodeDecodeError:
        filename = data[HEADER_SIZE:name_end].decode("utf-8", errors="replace")

    payload = data[name_end:]

    return {
        "v": ver,
        "type": ftype,
        "flags": flags,
        "sid": sid,
        "sid_hex": sid.hex(),
        "seed": seed,
        "k": k,
        "filesize": filesize,
        "filename": filename,
        "payload": payload,
        "compressed": bool(flags & FLAG_COMPRESSED),
        "fountain": bool(flags & FLAG_FOUNTAIN),
    }


def is_v3_frame(data: bytes) -> bool:
    return len(data) >= 2 and data[:2] == FRAME_MAGIC


def encode_file_v3(
    path: str | os.PathLike,
    chunk_size: int = DEFAULT_CHUNK_SIZE_V3,
    sid: Optional[bytes] = None,
    compress_level: int = 9,
) -> Tuple[bytes, List[bytes], int, str]:
    if sid is None:
        sid = new_sid_bytes()

    p = Path(path)
    file_name = p.name
    file_size = p.stat().st_size

    with open(p, "rb") as f:
        raw = f.read()

    compressed = compress(raw, level=compress_level) if raw else b""

    if not compressed:
        return sid, [b""], file_size, file_name

    total = (len(compressed) + chunk_size - 1) // chunk_size
    chunks = []
    for i in range(total):
        start = i * chunk_size
        end = min(start + chunk_size, len(compressed))
        chunks.append(compressed[start:end])

    return sid, chunks, file_size, file_name


def assemble_v3(chunks: List[bytes]) -> bytes:
    compressed = b"".join(chunks)
    if not compressed:
        return b""
    return decompress(compressed)


def safe_chunk_size_v3(
    desired: int,
    filename: str = "",
    extra_overhead: int = 0,
) -> int:
    name_len = len(filename.encode("utf-8"))
    overhead = HEADER_SIZE + min(name_len, 255) + extra_overhead
    max_payload = QR_SAFE_PAYLOAD_LIMIT - overhead
    return min(desired, max(100, max_payload))
