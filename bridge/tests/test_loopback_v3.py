#!/usr/bin/env python3
"""End-to-end v3 loopback: build -> decode -> assemble -> verify."""

import hashlib
import os
import random
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from bridge.common import binproto
from bridge.receiver import assembler as asm
from bridge.receiver import decoder
from bridge.sender import builder


def _run_one(label, files_dict, chunk_size=800, use_fountain=True,
             drop_ratio=0.0, seed=0):
    random.seed(seed)
    with tempfile.TemporaryDirectory() as td:
        paths = []
        for name, content in files_dict.items():
            p = os.path.join(td, name)
            with open(p, "wb") as f:
                f.write(content)
            paths.append(p)
        result = builder.build(
            paths, chunk_size=chunk_size,
            use_fountain=use_fountain, fountain_redundancy=0.8,
            protocol_version=3,
        )
        all_frames = result.frames
        middle = all_frames[1:-1]
        if drop_ratio > 0 and len(middle) > 4:
            n_drop = int(len(middle) * drop_ratio)
            drop_set = set(random.sample(range(len(middle)), n_drop))
            middle = [f for i, f in enumerate(middle) if i not in drop_set]
        transmitted = [all_frames[0]] + middle + [all_frames[-1]]
        outdir = os.path.join(td, "recv")
        done = []
        a = asm.Assembler(out_dir=outdir,
                          on_file_done=lambda fn, path: done.append((fn, path)))
        for raw_bytes in transmitted:
            frame = decoder.parse_frame(raw_bytes)
            if frame is not None:
                a.handle_frame(frame)
        if len(done) != len(files_dict):
            print(f"  [{label}] count mismatch: exp {len(files_dict)} got {len(done)}")
            return False
        got = {fn: open(path, "rb").read() for fn, path in done}
        for name, expected in files_dict.items():
            if name not in got:
                print(f"  [{label}] missing: {name}")
                return False
            if got[name] != expected:
                print(f"  [{label}] mismatch: {name}")
                return False
        drop_pct = int(drop_ratio * 100)
        print(f"  [{label}] OK: {len(files_dict)} files, "
              f"{result.total_data_chunks} blocks, {drop_pct}% drop")
        return True


def test_text_fountain_no_loss():
    assert _run_one("text+fountain", {"note.txt": b"Hello World " * 200}, seed=1)


def test_binary_fountain_no_loss():
    assert _run_one("binary+fountain", {"data.bin": os.urandom(5000)}, seed=2)


def test_fountain_with_loss():
    content = os.urandom(10000)
    ok = True
    for trial in range(3):
        if not _run_one(f"fountain+loss+trial{trial}", {"blob.bin": content},
                        drop_ratio=0.1, seed=trial + 10):
            ok = False
    assert ok


def test_sequential_no_loss():
    assert _run_one("sequential", {"file.bin": os.urandom(3000)},
                    use_fountain=False, seed=3)


def test_multiple_files():
    assert _run_one("multi-file", {
        "readme.md": b"# Title\nContent line\n" * 50,
        "data.bin": os.urandom(2000),
        "config.json": b'{"key":"value","n":42}' * 30,
    }, seed=4)


def test_empty_file():
    assert _run_one("empty", {"empty.dat": b""}, seed=5)


def test_large_text_compression():
    content = ("Lorem ipsum dolor sit amet " * 500).encode("utf-8")
    assert _run_one("large-text", {"lorem.txt": content}, seed=6)


def test_compression_effective():
    text = ("config=value data=stuff " * 1000).encode("utf-8")
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "big.txt")
        with open(p, "wb") as f:
            f.write(text)
        result = builder.build([p], protocol_version=3)
        raw_blocks = len(text) // 800 + 1
        assert result.total_data_chunks < raw_blocks * 0.5
    print(f"  [compression] {result.total_data_chunks} blocks for "
          f"{len(text)}B ({len(text)/result.total_data_chunks:.0f}B/blk)")


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
    print("=" * 60)
    print("v3 End-to-end loopback tests")
    print("=" * 60)
    sys.exit(0 if _run_all() else 1)
