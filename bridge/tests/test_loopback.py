#!/usr/bin/env python3
"""
bridge.tests.test_loopback — 本机自环集成测试

不接采集卡,纯内存流转:发送构建 → (可选模拟丢帧) → 接收拼装/FEC恢复 → 落盘,
验证整条链路字节级一致。

这是采集卡传输方案最关键的验收测试:它在没有硬件的情况下证明
发送端和接收端的协议、FEC、拼装逻辑完全对得上。

运行:
    ./bin/python bridge/tests/test_loopback.py
"""

import base64
import hashlib
import os
import random
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from bridge.common import protocol as P
from bridge.fec import rs_codec as fec
from bridge.receiver import assembler as asm
from bridge.sender import builder


def _run_one(
    label: str,
    files: dict,                 # {name: bytes}
    chunk_size: int,
    use_fec: bool,
    drop_count: int = 0,         # 模拟丢多少中间帧(不含哨兵)
    seed: int = 0,
    fec_redundancy: float = 0.15,
) -> bool:
    """
    跑一次完整的发送→接收自环,返回是否字节级一致。
    """
    random.seed(seed)
    with tempfile.TemporaryDirectory() as tmpdir:
        # 1. 写测试文件
        paths = []
        for name, content in files.items():
            p = os.path.join(tmpdir, name)
            with open(p, "wb") as f:
                f.write(content)
            paths.append(p)

        # 2. 发送端构建
        try:
            result = builder.build(
                paths, chunk_size=chunk_size,
                use_fec=use_fec, fec_redundancy=fec_redundancy,
            )
        except Exception as e:
            print(f"  [{label}] 构建失败: {e}")
            return False

        # 3. 模拟丢帧(从中间帧里随机丢 drop_count 个)
        middle = result.frames[1:-1]  # 不丢哨兵
        kept_middle = list(middle)
        if drop_count > 0 and len(middle) > drop_count:
            lost = set(random.sample(range(len(middle)), drop_count))
            kept_middle = [f for i, f in enumerate(middle) if i not in lost]
        frames_to_feed = [result.frames[0]] + kept_middle + [result.frames[-1]]

        # 4. 接收端消费
        outdir = os.path.join(tmpdir, "recv")
        done_files = []
        a = asm.Assembler(out_dir=outdir,
                          on_file_done=lambda fn, path: done_files.append((fn, path)))
        for fj in frames_to_feed:
            a.handle_frame(P.loads(fj))

        # 5. 校验字节一致
        if len(done_files) != len(files):
            print(f"  [{label}] 落盘文件数不符: 期望 {len(files)}, 实际 {len(done_files)}")
            return False

        got_map = {fn: open(path, "rb").read() for fn, path in done_files}
        for name, expected in files.items():
            if name not in got_map:
                print(f"  [{label}] 缺文件: {name}")
                return False
            if got_map[name] != expected:
                em = hashlib.md5(expected).hexdigest()
                gm = hashlib.md5(got_map[name]).hexdigest()
                print(f"  [{label}] 字节不一致: {name} (期望 {em}, 实际 {gm})")
                return False

        print(f"  [{label}] ✓ {len(files)} 文件, {result.total_data_chunks} 块, "
              f"丢 {drop_count} 帧 → 字节级一致")
        return True


def _run_all() -> bool:
    tests = [g for name in sorted(globals())
             if name.startswith("test_") and callable(g := globals()[name])]
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


# ---------------------------------------------------------------------------
# 测试用例
# ---------------------------------------------------------------------------
def test_single_file_small_text_no_fec():
    """小文本文件,无 FEC,无丢帧。"""
    assert _run_one(
        "小文本·无FEC·无丢帧",
        {"note.txt": ("采集卡传输测试 · Hello世界\n" * 10).encode("utf-8")},
        chunk_size=300, use_fec=False, drop_count=0, seed=1,
    )


def test_single_file_binary_with_fec():
    """二进制文件,FEC 开启,无丢帧。"""
    assert _run_one(
        "二进制·有FEC·无丢帧",
        {"blob.bin": os.urandom(5000)},
        chunk_size=400, use_fec=True, drop_count=0, seed=2,
    )


def test_single_file_with_loss_and_fec():
    """★ 关键:丢帧 + FEC 恢复(丢 R 块以内应恢复)。"""
    content = os.urandom(8000)
    redundancy = 0.2
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "x.bin")
        with open(p, "wb") as f:
            f.write(content)
        # 用与 _run_one 完全相同的 redundancy 算 R,保证 drop_count ≤ 实际恢复能力
        result = builder.build([p], chunk_size=200, use_fec=True,
                               fec_redundancy=redundancy)
        # FEC 元信息现在放在 start 帧(frames[0])的 per_file_fec 字典里
        start_frame = P.loads(result.frames[0])
        fec_by_file = start_frame.get("fec", {})
        file_meta = fec_by_file.get("x.bin", {})
        R = file_meta.get("n", 0) - file_meta.get("k", 0)
        assert R >= 2, f"FEC 冗余块不足: R={R}"

    ok = True
    for trial in range(5):
        random.seed(trial * 10)
        drop = random.randint(1, R)  # drop 严格 ≤ R
        if not _run_one(
            f"丢帧恢复·轮{trial}",
            {"blob.bin": content},
            chunk_size=200, use_fec=True,
            drop_count=drop, seed=trial, fec_redundancy=redundancy,
        ):
            ok = False
    assert ok


def test_multiple_files():
    """多文件混合(文本+二进制+中文文件名),无丢帧。"""
    assert _run_one(
        "多文件·无丢帧",
        {
            "readme.md": "# 测试\n中文内容 🎉".encode("utf-8"),
            "data.bin": os.urandom(2000),
            "配置.json": '{"key":"值","n":42}'.encode("utf-8"),
        },
        chunk_size=500, use_fec=True, drop_count=0, seed=3,
    )


def test_empty_file():
    """空文件也能传输。"""
    assert _run_one(
        "空文件",
        {"empty.dat": b""},
        chunk_size=500, use_fec=False, drop_count=0, seed=4,
    )


def test_large_chunk_fewer_blocks():
    """大 chunk-size 产生更少块,仍正确。"""
    assert _run_one(
        "大chunk·少块",
        {"big.bin": os.urandom(10000)},
        chunk_size=2000, use_fec=True, drop_count=0, seed=5,
    )


def test_directory_recursive():
    """目录递归传输(含子目录)。"""
    with tempfile.TemporaryDirectory() as td:
        os.makedirs(os.path.join(td, "sub"))
        files = {
            "a.txt": b"file A " * 50,
            "sub/b.bin": os.urandom(1500),
        }
        for name, content in files.items():
            p = os.path.join(td, name)
            with open(p, "wb") as f:
                f.write(content)
        result = builder.build([td], chunk_size=400, use_fec=True)
        outdir = td + "_recv"
        done = []
        a = asm.Assembler(out_dir=outdir, on_file_done=lambda fn, path: done.append(fn))
        for fj in result.frames:
            a.handle_frame(P.loads(fj))
        # 至少收到两个文件
        assert len(done) >= 2, f"目录传输只收到 {len(done)} 个文件"
        print(f"  [目录递归] ✓ 收到 {len(done)} 个文件")


if __name__ == "__main__":
    print("=" * 60)
    print("本机自环集成测试(发送→接收闭环,不依赖采集卡)")
    print("=" * 60)
    sys.exit(0 if _run_all() else 1)
