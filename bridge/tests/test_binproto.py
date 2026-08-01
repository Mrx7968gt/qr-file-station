#!/usr/bin/env python3
"""Tests for bridge.common.binproto (v3 binary protocol)."""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from bridge.common import binproto as bp


def test_frame_pack_unpack_roundtrip():
    sid = bp.new_sid_bytes()
    payload = os.urandom(500)
    frame = bp.pack_data_frame(
        sid, seed=42, k=10, filesize=9999,
        filename="test.bin", payload=payload,
        flags=bp.FLAG_COMPRESSED | bp.FLAG_FOUNTAIN,
    )
    parsed = bp.unpack_frame(frame)
    assert parsed is not None
    assert parsed["v"] == 3
    assert parsed["type"] == bp.FT_DATA
    assert parsed["seed"] == 42
    assert parsed["k"] == 10
    assert parsed["filesize"] == 9999
    assert parsed["filename"] == "test.bin"
    assert parsed["payload"] == payload
    assert parsed["compressed"] is True
    assert parsed["fountain"] is True


def test_start_end_frames():
    sid = bp.new_sid_bytes()
    start = bp.pack_start_frame(sid, '{"files":[]}')
    parsed = bp.unpack_frame(start)
    assert parsed is not None
    assert parsed["type"] == bp.FT_START
    end = bp.pack_end_frame(sid)
    parsed = bp.unpack_frame(end)
    assert parsed is not None
    assert parsed["type"] == bp.FT_END


def test_magic_detection():
    assert bp.is_v3_frame(b"\x51\x52\x00") is True
    assert bp.is_v3_frame(b'{"type":"data"}') is False
    assert bp.is_v3_frame(b"") is False


def test_compression_roundtrip():
    data = ("Hello World " * 1000).encode("utf-8")
    compressed = bp.compress(data)
    assert len(compressed) < len(data)
    recovered = bp.decompress(compressed)
    assert recovered == data


def test_compression_ratio_text():
    text = ("config setting value data " * 500).encode("utf-8")
    compressed = bp.compress(text)
    ratio = len(text) / len(compressed)
    assert ratio > 3.0, f"compression ratio too low: {ratio:.1f}x"


def test_encode_file_v3_roundtrip():
    content = os.urandom(10000)
    with tempfile.NamedTemporaryFile(suffix=".bin", delete=False) as f:
        f.write(content)
        path = f.name
    try:
        sid, chunks, orig_size, fname = bp.encode_file_v3(path, chunk_size=1000)
        assert orig_size == len(content)
        assert len(chunks) > 1
        raw = bp.assemble_v3(chunks)
        assert raw == content
    finally:
        os.unlink(path)


def test_encode_file_compression_saves_space():
    content = ("A" * 100).encode() * 100
    with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as f:
        f.write(content)
        path = f.name
    try:
        sid, chunks, orig_size, fname = bp.encode_file_v3(path, chunk_size=500)
        total_compressed = sum(len(c) for c in chunks)
        assert total_compressed < orig_size
    finally:
        os.unlink(path)


def test_empty_file():
    with tempfile.NamedTemporaryFile(delete=False) as f:
        path = f.name
    try:
        sid, chunks, orig_size, fname = bp.encode_file_v3(path)
        assert len(chunks) == 1
        assert orig_size == 0
        raw = bp.assemble_v3(chunks)
        assert raw == b""
    finally:
        os.unlink(path)


def test_safe_chunk_size():
    s = bp.safe_chunk_size_v3(5000, "test.bin")
    assert s < 5000
    assert s > 100
    s2 = bp.safe_chunk_size_v3(100, "test.bin")
    assert s2 == 100


def test_invalid_frame_rejected():
    assert bp.unpack_frame(b"") is None
    assert bp.unpack_frame(b"\x00\x00") is None
    assert bp.unpack_frame(b"\x51\x52") is None
    assert bp.unpack_frame(os.urandom(10)) is None


def _run_all():
    tests = [globals()[n] for n in sorted(globals())
             if n.startswith("test_") and callable(globals()[n])]
    passed = failed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
            passed += 1
        except Exception as e:
            print(f"  FAIL  {t.__name__}: {e}")
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
    return failed == 0


if __name__ == "__main__":
    sys.exit(0 if _run_all() else 1)
