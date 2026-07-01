#!/usr/bin/env python3
"""
bridge.common.protocol 的单元测试。

运行:
    ./bin/python -m pytest bridge/tests/test_protocol.py -v
    # 或无 pytest 时:
    ./bin/python bridge/tests/test_protocol.py
"""

import os
import random
import subprocess
import sys
import tempfile

# 让本测试无论从哪里运行都能 import bridge
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from bridge.common import protocol as P


def _write_tmp(content: bytes) -> str:
    fd, path = tempfile.mkstemp(suffix=".bin")
    with os.fdopen(fd, "wb") as f:
        f.write(content)
    return path


def test_checksum_stable_across_processes():
    """★ 关键:crc32 必须跨进程稳定(原 file_to_qr.py 用 hash() 不稳定)。"""
    data_b64 = "SGVsbG8sIOS4lueVjOe8kOe7n+eUqA=="
    expected = P.checksum(data_b64)

    # 在子进程里重新计算,确认一致
    code = (
        "import zlib; "
        f"print(zlib.crc32({data_b64!r}.encode()) & 0xFFFFFFFF)"
    )
    out = subprocess.check_output(
        [sys.executable, "-c", code]
    ).decode().strip()
    assert int(out) == expected, "crc32 跨进程不稳定!"


def test_encode_assemble_roundtrip():
    """编码 → 拼装字节级一致。"""
    content = ("采集卡传输测试 · Hello世界 " * 200).encode("utf-8")
    path = _write_tmp(content)
    try:
        sid = P.new_sid()
        chunks = P.encode_file(path, chunk_size=1000, sid=sid)

        # 基本结构
        assert all(c["sid"] == sid for c in chunks)
        assert all(c["type"] == "data" for c in chunks)
        assert chunks[0]["index"] == 0
        assert chunks[-1]["index"] == len(chunks) - 1
        assert chunks[0]["total"] == len(chunks)
        assert all(c["v"] == P.PROTOCOL_VERSION for c in chunks)

        # 校验
        assert all(P.verify_chunk(c) for c in chunks)

        # 拼装还原
        fn, sz, raw = P.assemble(chunks)
        assert fn == os.path.basename(path)
        assert sz == len(content)
        assert raw == content
    finally:
        os.unlink(path)


def test_assemble_handles_shuffled_order():
    """乱序的块也能正确拼装(采集卡抓帧顺序不保证)。"""
    content = os.urandom(5000)
    path = _write_tmp(content)
    try:
        chunks = P.encode_file(path, chunk_size=777)
        random.shuffle(chunks)
        _, _, raw = P.assemble(chunks)
        assert raw == content
    finally:
        os.unlink(path)


def test_assemble_detects_missing_chunk():
    """缺块必须报错。"""
    content = os.urandom(3000)
    path = _write_tmp(content)
    try:
        chunks = P.encode_file(path, chunk_size=500)
        try:
            P.assemble(chunks[:-1])
            assert False, "缺块未报错"
        except ValueError:
            pass
    finally:
        os.unlink(path)


def test_empty_file():
    """空文件仍能产出 1 块并拼回空字节。"""
    path = _write_tmp(b"")
    try:
        chunks = P.encode_file(path)
        assert len(chunks) == 1
        assert chunks[0]["total"] == 1
        _, _, raw = P.assemble(chunks)
        assert raw == b""
    finally:
        os.unlink(path)


def test_serialization_roundtrip():
    """JSON 序列化往返一致,非法输入返回 None。"""
    chunk = P.make_data_chunk("a.bin", 10, 0, 1, "AAAA", "abcdef")
    s = P.dumps(chunk)
    assert P.loads(s) == chunk
    assert P.loads("not json") is None
    assert P.loads("") is None


def test_sentinel_frames():
    """start/end 哨兵帧结构正确。"""
    sid = "aabbcc"
    start = P.make_start_frame(sid, [{"filename": "x", "size": 1, "chunks": 1}], 1)
    end = P.make_end_frame(sid)
    assert start["type"] == "start"
    assert start["sid"] == sid
    assert start["total"] == 1
    assert end["type"] == "end"
    assert end["sid"] == sid


def test_corrupted_chunk_fails_verification():
    """篡改 data 后 checksum 校验失败。"""
    chunk = P.make_data_chunk("a.bin", 4, 0, 1, "AAAA", "sid")
    assert P.verify_chunk(chunk) is True
    chunk["data"] = "BBBB"  # 篡改
    assert P.verify_chunk(chunk) is False


def test_chunk_size_controls_block_count():
    """chunk_size 越大块越少。"""
    content = os.urandom(10000)
    path = _write_tmp(content)
    try:
        small = P.encode_file(path, chunk_size=500)
        large = P.encode_file(path, chunk_size=2000)
        assert len(small) > len(large)
        # 都能完整还原
        assert P.assemble(small)[2] == content
        assert P.assemble(large)[2] == content
    finally:
        os.unlink(path)


# ---------------------------------------------------------------------------
# 允许直接运行(无 pytest 时)
# ---------------------------------------------------------------------------
def _run_all():
    tests = [
        globals()[name]
        for name in sorted(globals())
        if name.startswith("test_") and callable(globals()[name])
    ]
    passed = 0
    failed = 0
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
