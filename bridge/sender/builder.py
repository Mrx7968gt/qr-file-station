#!/usr/bin/env python3
"""
bridge.sender.builder — 发送端构建层

职责:
    把文件/目录 → QR 帧 Surface 序列(待播放)。
    这是 GUI 和 CLI 共用的核心逻辑,与 pygame 渲染解耦,便于单测。

流程:
    1. 文件分块(protocol.encode_file)
    2. 可选 FEC 加冗余(fec.encode),冗余字节 base64 编码进帧
    3. 拼装 start + data + (fec) + end 帧序列
    4. 每帧 → JSON → QR 矩阵 → pygame Surface
"""

from __future__ import annotations

import base64
import io
import os
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

import qrcode

from bridge.common import protocol
from bridge.fec import rs_codec as fec


@dataclass
class BuildResult:
    """一次构建的结果。"""
    sid: str
    frames: List[str]          # 每帧的 JSON 字符串(塞进 QR)
    file_count: int
    total_data_chunks: int


def _qr_to_image(payload: str, box: int = 10, border: int = 4):
    """生成 QR Image(PIL)。"""
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_H,
        box_size=box,
        border=border,
    )
    qr.add_data(payload)
    qr.make(fit=True)
    return qr.make_image(fill_color="black", back_color="white")


def payload_to_png_bytes(payload: str, box: int = 10, border: int = 4) -> bytes:
    """帧 JSON → QR PNG 字节(供播放器加载或落盘)。"""
    img = _qr_to_image(payload, box, border).convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def build(
    paths: List[str],
    chunk_size: int = protocol.DEFAULT_CHUNK_SIZE,
    use_fec: bool = True,
    fec_redundancy: float = 0.1,
    box: int = 10,
    border: int = 4,
) -> BuildResult:
    """
    构建一个会话的所有 QR 帧(不含 pygame Surface,只产 JSON 字符串)。

    Args:
        paths: 文件路径列表(目录会被展开)
        chunk_size: 单块有效字节
        use_fec: 是否启用 FEC
        fec_redundancy: FEC 冗余比例
        box/border: QR 渲染参数

    Returns:
        BuildResult(frames=[JSON 字符串,...])
    """
    # 展开目录,收集所有文件
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

    if not files:
        raise ValueError("没有找到可传输的文件")

    # ★ 安全降级:chunk_size 过大时帧 JSON 会超 QR Version 40 容量(2953B)。
    # 自动降到安全值,避免生成超大二维码导致编码失败。
    safe_max = protocol.safe_chunk_size_for_payload(chunk_size)
    if chunk_size > safe_max:
        chunk_size = safe_max

    sid = protocol.new_sid()

    # 逐文件分块
    # FEC 采用"文件级"分组:每个文件独立做 RS 纠错,manifest 记录每文件元信息。
    # FEC 元信息按文件存进 per_file_fec,最终一次性放进 start 帧下发,
    # 避免每帧重复携带(导致 lengths 数组膨胀、可能超 QR 容量)。
    all_data_chunks: List[dict] = []   # 每个含 filename/size/index/total/data
    file_manifest: List[dict] = []     # [{filename, size, chunks}]
    per_file_fec: dict = {}            # filename -> FECMeta.to_dict()

    # 注意:为了让 FEC 能跨文件恢复,我们给所有块的 index 做全局编号。
    # 但接收端 assembler 按 (sid, filename) 分组,且期望 index 在文件内 0-based。
    # 因此 FEC 采用"文件级"分组:每个文件独立做 FEC,manifest 记录每文件元信息。
    # start 帧只下发该文件的 fec 元信息的话,需要按文件分别存。
    # 简化:每个文件独立 encode_file + 独立 FEC,帧里带 filename,接收端自然分文件拼装。

    frames_json: List[str] = []
    total_data_chunks = 0

    for fp in files:
        data_chunks = protocol.encode_file(fp, chunk_size=chunk_size, sid=sid)
        k = len(data_chunks)
        total_data_chunks += k
        file_manifest.append({
            "filename": fp.name,
            "size": data_chunks[0]["size"] if data_chunks else 0,
            "chunks": k,
        })

        # FEC:对该文件的 base64 片段做块级 RS
        fec_meta: Optional[fec.FECMeta] = None
        fec_payloads: List[tuple] = []  # (j, base64_of_redundancy)
        if use_fec and k > 1:
            frag_bytes = [c["data"].encode("ascii") for c in data_chunks]
            try:
                fec_chunks, fec_meta = fec.encode(frag_bytes, redundancy=fec_redundancy)
                fec_payloads = [
                    (j, base64.b64encode(fc).decode("ascii"))
                    for j, fc in enumerate(fec_chunks)
                ]
            except fec.FECError:
                fec_meta = None  # 块太多超 RS 上限,降级为无 FEC

        # 构造该文件的 data 帧 + fec 帧
        # ★ FEC 元信息只放进 start 帧一次(避免每帧重复携带 lengths 数组导致膨胀/超 QR 容量)。
        # 接收端在 start 帧拿到 FECMeta 后,后续 data/fec 帧无需再带。
        # data 帧:不带 fec extra
        for c in data_chunks:
            frame = protocol.make_data_chunk(
                c["filename"], c["size"], c["index"], c["total"],
                c["data"], sid,
            )
            frames_json.append(protocol.dumps(frame))

        # fec 帧:只标 is_fec,不带完整 fec meta
        for j, payload in fec_payloads:
            frame = protocol.make_data_chunk(
                fp.name, c["size"], j, k, payload, sid,
                extra={"is_fec": True},
            )
            frames_json.append(protocol.dumps(frame))

        # 记录该文件的 FEC 元信息,放进 start 帧
        if fec_meta:
            per_file_fec[fp.name] = fec_meta.to_dict()

    # 前后插哨兵帧。start 帧携带每文件的 FEC 元信息(一次性下发)
    start_extra = {"fec": per_file_fec} if per_file_fec else None
    start_frame = protocol.make_start_frame(
        sid, file_manifest, total_data_chunks, extra=start_extra
    )
    end_frame = protocol.make_end_frame(sid)
    frames_json.insert(0, protocol.dumps(start_frame))
    frames_json.append(protocol.dumps(end_frame))

    return BuildResult(
        sid=sid,
        frames=frames_json,
        file_count=len(files),
        total_data_chunks=total_data_chunks,
    )
