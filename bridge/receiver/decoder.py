#!/usr/bin/env python3
"""
bridge.receiver.decoder - Frame parsing layer (v2 + v3 auto-detect)

Detects whether raw bytes from pyzbar are a v3 binary frame or v2 JSON,
and returns a normalized dict for the assembler.
"""

from __future__ import annotations

import json
from typing import Dict, List, Optional

from bridge.common import protocol as v2proto
from bridge.common import binproto

_TYPE_MAP = {
    binproto.FT_START: "start",
    binproto.FT_DATA: "data",
    binproto.FT_END: "end",
}


def parse_frame(raw_bytes: bytes) -> Optional[Dict]:
    """
    Parse raw bytes from pyzbar into a frame dict.

    Auto-detects v3 binary frames (magic 0x51 0x52) vs v2 JSON.
    Returns None for unparseable data.
    """
    if not raw_bytes:
        return None

    # Try v3 binary first
    if binproto.is_v3_frame(raw_bytes):
        frame = binproto.unpack_frame(raw_bytes)
        if frame is None:
            return None
        return _normalize_v3(frame)

    # Fall back to v2 JSON
    try:
        text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError:
        return None
    parsed = v2proto.loads(text)
    if not isinstance(parsed, dict) or "type" not in parsed:
        return None
    parsed["proto"] = 2
    return parsed


def _normalize_v3(frame: Dict) -> Dict:
    """Convert a v3 binary frame dict to assembler-compatible format."""
    ftype_str = _TYPE_MAP.get(frame["type"], "unknown")
    result = {
        "proto": 3,
        "type": ftype_str,
        "sid": frame.get("sid_hex", ""),
    }

    if ftype_str == "start":
        # Parse manifest from payload
        try:
            manifest = json.loads(frame["payload"].decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError, KeyError):
            manifest = {}
        result["manifest"] = manifest
    elif ftype_str == "data":
        result["filename"] = frame.get("filename", "")
        result["filesize"] = frame.get("filesize", 0)
        result["payload"] = frame.get("payload", b"")
        result["seed"] = frame.get("seed", 0)
        result["k"] = frame.get("k", 0)
        result["fountain"] = frame.get("fountain", False)
        result["compressed"] = frame.get("compressed", False)
    elif ftype_str == "end":
        pass  # just sid + type

    return result


def decode_all(raw_results: List) -> List[Dict]:
    """Batch parse pyzbar results."""
    frames = []
    for r in raw_results:
        frame = parse_frame(r.data)
        if frame is not None:
            frames.append(frame)
    return frames


def is_start(frame: Dict) -> bool:
    return frame.get("type") == "start"


def is_end(frame: Dict) -> bool:
    return frame.get("type") == "end"


def is_data(frame: Dict) -> bool:
    return frame.get("type") == "data"


def is_valid_data(frame: Dict) -> bool:
    """Check data frame validity (v2: checksum, v3: always valid if parsed)."""
    if not is_data(frame):
        return False
    if frame.get("proto") == 2:
        return v2proto.verify_chunk(frame)
    return True  # v3 binary frames are structurally validated on parse
