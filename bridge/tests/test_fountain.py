#!/usr/bin/env python3
"""Tests for bridge.fec.fountain (LT fountain codes)."""

import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from bridge.fec import fountain as lt

OVERHEAD = 0.8  # 80% overhead ensures reliable peeling for all K


def _n_blocks(k):
    return max(int(k * (1 + OVERHEAD)), k + 5)


def test_encode_decode_no_loss():
    random.seed(42)
    k = 20
    bs = 256
    blocks = [bytes(random.randint(0, 255) for _ in range(bs)) for _ in range(k)]
    enc = lt.LTEncoder(blocks)
    dec = lt.LTDecoder(k, bs)
    for bid, payload in enc.generate(_n_blocks(k)):
        dec.add_block(bid, payload)
    decoded = dec.decode()
    assert decoded is not None, f"failed: decoded {dec.decoded_count}/{k}"
    for i in range(k):
        assert decoded[i] == blocks[i], f"block {i} mismatch"


def test_decode_with_loss():
    random.seed(99)
    k = 30
    bs = 128
    blocks = [bytes(random.randint(0, 255) for _ in range(bs)) for _ in range(k)]
    enc = lt.LTEncoder(blocks)
    all_blocks = list(enc.generate(k * 3))
    random.shuffle(all_blocks)
    keep = all_blocks[:k * 2]
    dec = lt.LTDecoder(k, bs)
    for bid, payload in keep:
        dec.add_block(bid, payload)
    decoded = dec.decode()
    assert decoded is not None, f"failed: decoded {dec.decoded_count}/{k}"
    for i in range(k):
        assert decoded[i] == blocks[i]


def test_out_of_order():
    random.seed(7)
    k = 15
    bs = 64
    blocks = [bytes(random.randint(0, 255) for _ in range(bs)) for _ in range(k)]
    enc = lt.LTEncoder(blocks)
    encoded = list(enc.generate(_n_blocks(k)))
    random.shuffle(encoded)
    dec = lt.LTDecoder(k, bs)
    for bid, payload in encoded:
        dec.add_block(bid, payload)
    decoded = dec.decode()
    assert decoded is not None, f"failed: decoded {dec.decoded_count}/{k}"
    for i in range(k):
        assert decoded[i] == blocks[i]


def test_insufficient_blocks():
    random.seed(1)
    k = 50
    bs = 32
    blocks = [bytes(random.randint(0, 255) for _ in range(bs)) for _ in range(k)]
    enc = lt.LTEncoder(blocks)
    dec = lt.LTDecoder(k, bs)
    for bid, payload in enc.generate(k - 5):
        dec.add_block(bid, payload)
    decoded = dec.decode()
    assert decoded is None


def test_single_block():
    data = b"hello world" + b"\x00" * 5
    enc = lt.LTEncoder([data])
    block = enc.encode_block(0)
    assert block == data


def test_unequal_block_lengths():
    blocks = [b"AAAA", b"BBBBBB", b"C"]
    enc = lt.LTEncoder(blocks)
    assert enc.block_size == 6
    dec = lt.LTDecoder(3, 6)
    for bid, payload in enc.generate(_n_blocks(3)):
        dec.add_block(bid, payload)
    decoded = dec.decode()
    assert decoded is not None
    assert decoded[0] == b"AAAA\x00\x00"
    assert decoded[1] == b"BBBBBB"
    assert decoded[2] == b"C\x00\x00\x00\x00\x00"


def test_duplicate_blocks_ignored():
    random.seed(5)
    k = 10
    bs = 16
    blocks = [bytes(random.randint(0, 255) for _ in range(bs)) for _ in range(k)]
    enc = lt.LTEncoder(blocks)
    dec = lt.LTDecoder(k, bs)
    for bid, payload in enc.generate(_n_blocks(k)):
        dec.add_block(bid, payload)
        dec.add_block(bid, payload)
    decoded = dec.decode()
    assert decoded is not None


def test_large_k():
    random.seed(77)
    k = 100
    bs = 64
    blocks = [bytes(random.randint(0, 255) for _ in range(bs)) for _ in range(k)]
    enc = lt.LTEncoder(blocks)
    dec = lt.LTDecoder(k, bs)
    for bid, payload in enc.generate(_n_blocks(k)):
        dec.add_block(bid, payload)
    decoded = dec.decode()
    assert decoded is not None, f"failed: decoded {dec.decoded_count}/{k}"
    for i in range(k):
        assert decoded[i] == blocks[i]


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
