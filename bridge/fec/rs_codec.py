#!/usr/bin/env python3
"""
bridge.fec.rs_codec — 块级前向纠错(Reed-Solomon erasure)

用途:
    采集卡是单向通道,无法握手确认,丢帧只能靠冗余恢复。
    本模块对"块"做 erasure(已知位置删除)编码:K 个数据块 + R 个冗余块,
    接收端**任意**收齐 K 个块即可恢复全部 K 个(因为是 MDS 码)。

实现:
    跨块字节级 RS。把 K 个等长块视为 K 个"符号行",对每个字节列用 reedsolo
    编码产生 R 个冗余行。reedsolo 的 RSCodec 在 erasure 模式下,R 个冗余字节
    可纠正 R 个已知位置的擦除 —— 所以 R 个冗余块可恢复任意 R 个丢失块。

    之所以选 reedsolo:纯 Python、无外部依赖、易打包成单文件 exe。

约束(Reed-Solomon 固有):
    K + R <= 255  (GF(2^8) 符号空间上限)
    块长度需对齐(短的补 0,记录原长)
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import reedsolo

# GF(2^8) 下 RS 码的总符号数上限
MAX_TOTAL = 255


class FECError(Exception):
    """FEC 编解码错误。"""


class FECMeta:
    """FEC 编码元数据,需随帧传给接收端以正确恢复。"""

    __slots__ = ("k", "n", "chunk_len", "lengths")

    def __init__(self, k: int, n: int, chunk_len: int, lengths: List[int]):
        self.k = k          # 数据块数
        self.n = n          # 总块数 k+R
        self.chunk_len = chunk_len  # 块对齐长度(冗余块和数据块等长)
        self.lengths = lengths       # 各数据块的原始字节长度(截断补的 0 用)

    def to_dict(self) -> Dict:
        """序列化进帧的元信息字段。"""
        return {"k": self.k, "n": self.n, "cl": self.chunk_len, "ln": self.lengths}

    @classmethod
    def from_dict(cls, d: Dict) -> "FECMeta":
        return cls(d["k"], d["n"], d["cl"], list(d["ln"]))

    @property
    def redundancy_count(self) -> int:
        return self.n - self.k


def _pack_chunks(chunks: List[bytes]) -> Tuple[bytes, List[int], int]:
    """
    把不等长的块拼成一个连续字节串 + 各块原始长度表。

    Returns:
        (flat_bytes, lengths, max_len)
    """
    lengths = [len(c) for c in chunks]
    max_len = max(lengths) if lengths else 0
    flat = bytearray()
    for c in chunks:
        flat.extend(c)
        # 补 0 对齐到 max_len(短块的尾部填 0)
        flat.extend(b"\x00" * (max_len - len(c)))
    return bytes(flat), lengths, max_len


def _unpack(flat: bytes, lengths: List[int], max_len: int) -> List[bytes]:
    """逆操作:从连续字节串还原各块(按长度截断补的 0)。"""
    out: List[bytes] = []
    for i, ln in enumerate(lengths):
        start = i * max_len
        out.append(bytes(flat[start:start + ln]))
    return out


def _transpose(flat: bytes, rows: int, cols: int) -> bytes:
    """
    把 (rows x cols) 行优先字节布局转置成 (cols x rows) 列优先。
    转置后每一"列"就是所有块同一位置的字节,可独立做 RS。
    """
    # 用 bytearray 逐列取,比纯 Python 嵌套循环快
    out = bytearray(rows * cols)
    for c in range(cols):
        base_out = c * rows
        for r in range(rows):
            out[base_out + r] = flat[r * cols + c]
    return bytes(out)


def encode(
    data_chunks: List[bytes],
    redundancy: float = 0.1,
    max_total: int = MAX_TOTAL,
) -> Tuple[List[bytes], "FECMeta"]:
    """
    对 K 个数据块做 RS erasure 编码,产生 R 个冗余块。

    Args:
        data_chunks: K 个原始数据块(可不等长)
        redundancy: 冗余比例 R/K,如 0.1 表示 10%
        max_total: K+R 上限(默认 255,GF(2^8))

    Returns:
        (fec_chunks, meta)
            fec_chunks: R 个冗余块(等长,已补 0 对齐到 chunk_len)
            meta: FECMeta(k, n, chunk_len, lengths),需随帧传给接收端

    Raises:
        FECError: K 为 0,或 K+R 超过 max_total
    """
    k = len(data_chunks)
    if k == 0:
        raise FECError("没有数据块可编码")
    if k == 1:
        # 单块无法做块级 RS(没有"其它块"可组合);返回 0 冗余,靠循环重发兜底
        meta = FECMeta(k=1, n=1, chunk_len=len(data_chunks[0]),
                       lengths=[len(data_chunks[0])])
        return [], meta

    # 计算 R,保证 k+R <= max_total 且 R>=1
    r = max(1, round(k * redundancy))
    if k + r > max_total:
        r = max_total - k
        if r < 1:
            raise FECError(
                f"块数 {k} 已达 RS 上限 {max_total},无法加冗余;请增大 chunk-size 减少块数"
            )

    n = k + r
    rs = reedsolo.RSCodec(nsym=r)

    lengths = [len(c) for c in data_chunks]
    flat, _lengths, max_len = _pack_chunks(data_chunks)
    # 转置成列优先:每列 rows=k 个字节
    cols = _transpose(flat, rows=k, cols=max_len)

    # 对每列(长度 k)编码,得到长度 n=k+r,取后 r 个字节作为冗余列
    fec_flat = bytearray()
    for col_start in range(0, len(cols), k):
        column = cols[col_start:col_start + k]
        encoded = bytes(rs.encode(column))  # 长度 n
        fec_flat.extend(encoded[k:])  # 后 r 个 = 冗余字节

    # 冗余字节按"行"(块)拆回 R 个冗余块,每个长度 max_len
    # fec_flat 当前是 R 行 x max_len 列的行优先布局(每列贡献 R 字节)
    fec_chunks: List[bytes] = []
    for row in range(r):
        chunk = bytearray(max_len)
        for col in range(max_len):
            chunk[col] = fec_flat[col * r + row]
        fec_chunks.append(bytes(chunk))

    meta = FECMeta(k=k, n=n, chunk_len=max_len, lengths=lengths)
    return fec_chunks, meta


def decode(
    received: Dict[int, bytes],
    meta: "FECMeta",
) -> Optional[List[bytes]]:
    """
    从收到的块(数据块 + 冗余块)恢复原始 K 个数据块。

    Args:
        received: {块序号 0..n-1 -> 块字节};缺的序号视为擦除。
                  注意:块字节需已补 0 对齐到 meta.chunk_len(调用方负责)
        meta: 编码时的 FECMeta

    Returns:
        恢复出的 K 个数据块列表(已按 meta.lengths 精确截断,与原始一致);
        失败(收到不足 K 块)返回 None

    说明:
        RS erasure:收到任意 K 块即可恢复。块序号 0..k-1 是数据块,
        k..n-1 是冗余块。
    """
    k, n, chunk_len = meta.k, meta.n, meta.chunk_len
    have = [i for i in range(n) if i in received]
    if len(have) < k:
        return None  # 不足 K 块,无法恢复

    r = n - k
    rs = reedsolo.RSCodec(nsym=r)

    # 预处理:把每个收到的块补 0 对齐到 chunk_len,避免长度不齐导致误判擦除
    aligned: Dict[int, bytes] = {}
    for idx, blk in received.items():
        if len(blk) < chunk_len:
            aligned[idx] = blk + b"\x00" * (chunk_len - len(blk))
        else:
            aligned[idx] = blk

    # 按字节列处理:第 col 字节的 n 个符号组成一个 RS 码字
    recovered_flat = bytearray(k * chunk_len)

    for col in range(chunk_len):
        codeword = bytearray(n)
        erase_pos: List[int] = []
        for blk_idx in range(n):
            blk = aligned.get(blk_idx)
            if blk is not None:
                codeword[blk_idx] = blk[col]
            else:
                erase_pos.append(blk_idx)

        if not erase_pos:
            for row in range(k):
                recovered_flat[row * chunk_len + col] = codeword[row]
            continue

        try:
            decoded_msg, _decoded_ecc, _errata = rs.decode(
                codeword, erase_pos=erase_pos
            )
        except reedsolo.ReedSolomonError:
            return None

        for row in range(k):
            recovered_flat[row * chunk_len + col] = decoded_msg[row]

    # 切回 K 个块,并按原始长度截断(去掉补的 0)
    return [
        bytes(recovered_flat[row * chunk_len:row * chunk_len + meta.lengths[row]])
        for row in range(k)
    ]
