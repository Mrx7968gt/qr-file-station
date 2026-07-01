#!/usr/bin/env python3
"""
bridge.fec.rs_codec 的单元测试。

运行:
    ./bin/python bridge/tests/test_fec.py
"""

import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from bridge.fec import rs_codec as fec


def test_encode_returns_meta():
    """encode 返回 (fec_chunks, FECMeta),字段齐全。"""
    chunks = [b"aaaa", b"bbbb", b"c", b"dddddd"]
    fec_chunks, meta = fec.encode(chunks, redundancy=0.5)
    assert meta.k == 4
    assert meta.n == 4 + len(fec_chunks)
    assert meta.chunk_len == 6  # 最长块
    assert meta.lengths == [4, 4, 1, 6]
    # 冗余块等长
    assert all(len(c) == meta.chunk_len for c in fec_chunks)


def test_recover_within_redundancy():
    """丢 R 块以内,任意位置都能完整恢复(字节级一致)。"""
    random.seed(123)
    chunks = [bytes(random.randint(0, 255) for _ in range(100)) for _ in range(30)]
    fec_chunks, meta = fec.encode(chunks, redundancy=0.1)

    padded = {i: c for i, c in enumerate(chunks)}
    for j, c in enumerate(fec_chunks):
        padded[meta.k + j] = c

    # 测试丢 0..R 个块的各种情况
    for n_lost in range(0, meta.redundancy_count + 1):
        for _ in range(5):  # 每种丢块数试 5 次随机位置
            lost = random.sample(range(meta.n), n_lost)
            received = {i: b for i, b in padded.items() if i not in lost}
            recovered = fec.decode(received, meta)
            assert recovered is not None, f"丢 {n_lost} 块恢复失败"
            assert recovered == chunks, f"丢 {n_lost} 块数据不一致"


def test_recover_unequal_lengths():
    """不等长块也能精确恢复(原始长度)。"""
    chunks = [b"x" * 10, b"y" * 50, b"z" * 3, b"w" * 30]
    fec_chunks, meta = fec.encode(chunks, redundancy=0.5)

    padded = {i: c for i, c in enumerate(chunks)}
    for j, c in enumerate(fec_chunks):
        padded[meta.k + j] = c

    # 丢掉 1 个数据块 + 1 个冗余块
    received = {k: v for k, v in padded.items() if k not in (1, meta.k)}
    recovered = fec.decode(received, meta)
    assert recovered is not None
    assert recovered == chunks
    # 长度精确(不是对齐后的长度)
    assert [len(c) for c in recovered] == [10, 50, 3, 30]


def test_insufficient_chunks_returns_none():
    """收到不足 K 块时返回 None(无法恢复)。"""
    chunks = [b"a" * 20] * 10
    fec_chunks, meta = fec.encode(chunks, redundancy=0.2)
    # 只给 K-1 块
    received = {i: chunks[i] for i in range(meta.k - 1)}
    assert fec.decode(received, meta) is None


def test_meta_roundtrip():
    """FECMeta 序列化往返一致。"""
    meta = fec.FECMeta(k=20, n=22, chunk_len=187, lengths=[78, 151, 64])
    d = meta.to_dict()
    meta2 = fec.FECMeta.from_dict(d)
    assert meta2.k == 20
    assert meta2.n == 22
    assert meta2.chunk_len == 187
    assert meta2.lengths == [78, 151, 64]
    assert meta2.redundancy_count == 2


def test_single_chunk_no_fec():
    """单块不做 FEC(K=1),靠循环重发兜底。"""
    fec_chunks, meta = fec.encode([b"only one chunk"], redundancy=0.1)
    assert fec_chunks == []
    assert meta.k == 1 and meta.n == 1


def test_empty_chunks_raises():
    """空列表应报错。"""
    try:
        fec.encode([], redundancy=0.1)
        assert False, "空列表未报错"
    except fec.FECError:
        pass


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
