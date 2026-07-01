#!/usr/bin/env python3
"""
bridge.common.protocol — 采集卡传输帧协议(发送端与接收端共享)

核心职责:
  1. 文件 → Base64 分块 → 附元数据的"帧"(dict)
  2. 帧的 crc32 校验(★ 修复了原 file_to_qr.py 用 hash() 跨进程不稳定的坑)
  3. 帧的 JSON 序列化(塞进二维码)
  4. 接收端:按 index 去重拼装、Base64 解码还原

帧字段(向后兼容现有前端 QRChunk 接口):
  v         协议版本(int,新增)
  sid       会话ID(str,新增)
  type      帧类型 "start"|"data"|"end"(新增)
  filename  文件名(复用)
  size      原始文件字节数(复用)
  index     块序号 0-based(复用)
  total     总块数(复用)
  data      Base64 片段(复用)
  checksum  crc32(data)(★ 修复,替代 hash())
"""

from __future__ import annotations

import base64
import json
import os
import secrets
import zlib
from pathlib import Path
from typing import Dict, List, Optional

# 协议版本
PROTOCOL_VERSION = 2

# 单块最大"有效字节"数(Base64 编码后塞进二维码)。
# 默认标准档:Version~20 的二维码可承载,采集卡 1080p 下清晰可解。
DEFAULT_CHUNK_SIZE = 1000


# ---------------------------------------------------------------------------
# 会话 ID
# ---------------------------------------------------------------------------
def new_sid() -> str:
    """生成新会话 ID(6 位十六进制)。"""
    return secrets.token_hex(3)


# ---------------------------------------------------------------------------
# 校验和(★ 关键修复:用 crc32 替代 hash())
# ---------------------------------------------------------------------------
def checksum(data_b64: str) -> int:
    """对 Base64 字符串计算 crc32,返回无符号 32 位整数。"""
    return zlib.crc32(data_b64.encode("ascii")) & 0xFFFFFFFF


def verify_checksum(data_b64: str, expected: int) -> bool:
    return checksum(data_b64) == expected


# ---------------------------------------------------------------------------
# 帧构造
# ---------------------------------------------------------------------------
def make_data_chunk(
    filename: str,
    size: int,
    index: int,
    total: int,
    data_b64: str,
    sid: str,
    extra: Optional[Dict] = None,
) -> Dict:
    """构造一个 data 帧。extra 用于追加 FEC 元信息等。"""
    chunk: Dict = {
        "v": PROTOCOL_VERSION,
        "sid": sid,
        "type": "data",
        "filename": filename,
        "size": size,
        "index": index,
        "total": total,
        "data": data_b64,
        "checksum": checksum(data_b64),
    }
    if extra:
        chunk.update(extra)
    return chunk


def make_start_frame(
    sid: str,
    files: List[Dict],
    total_chunks: int,
    extra: Optional[Dict] = None,
) -> Dict:
    """会话起始哨兵帧。files 为 [{filename, size, chunks}, ...] 清单。"""
    frame: Dict = {
        "v": PROTOCOL_VERSION,
        "sid": sid,
        "type": "start",
        "files": files,
        "total": total_chunks,
    }
    if extra:
        frame.update(extra)
    return frame


def make_end_frame(sid: str) -> Dict:
    """会话结束哨兵帧。"""
    return {"v": PROTOCOL_VERSION, "sid": sid, "type": "end"}


# ---------------------------------------------------------------------------
# 编码:文件 → 分块
# ---------------------------------------------------------------------------
def encode_file(
    path: str | os.PathLike,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    sid: Optional[str] = None,
) -> List[Dict]:
    """
    读取单个文件,切成多个 data 帧。

    Args:
        path: 文件路径
        chunk_size: 单块有效字节数(Base64 编码前)
        sid: 会话 ID;None 时新建

    Returns:
        data 帧列表(已按 index 排序)
    """
    if sid is None:
        sid = new_sid()

    p = Path(path)
    file_name = p.name
    file_size = p.stat().st_size

    with open(p, "rb") as f:
        raw = f.read()

    if not raw:
        # 空文件:仍产出 1 个空块,保证 total>=1,接收端能拼回空文件
        return [
            make_data_chunk(file_name, file_size, 0, 1, "", sid)
        ]

    b64 = base64.b64encode(raw).decode("ascii")
    total = (len(b64) + chunk_size - 1) // chunk_size
    chunks: List[Dict] = []
    for i in range(total):
        start = i * chunk_size
        end = min(start + chunk_size, len(b64))
        chunks.append(
            make_data_chunk(file_name, file_size, i, total, b64[start:end], sid)
        )
    return chunks


# ---------------------------------------------------------------------------
# 解码:校验 + 拼装
# ---------------------------------------------------------------------------
def verify_chunk(chunk: Dict) -> bool:
    """校验单个 data 帧的 checksum。"""
    try:
        return verify_checksum(chunk["data"], int(chunk["checksum"]))
    except (KeyError, TypeError, ValueError):
        return False


def assemble(chunks: List[Dict]) -> tuple:
    """
    把一组 data 帧拼装回原始字节。

    Args:
        chunks: data 帧列表(顺序可乱,内部按 index 排序)

    Returns:
        (filename, size, raw_bytes)

    Raises:
        ValueError: 缺块或 index 不连续
    """
    if not chunks:
        raise ValueError("没有可拼装的块")

    by_index = {c["index"]: c for c in chunks}
    total = chunks[0]["total"]
    filename = chunks[0]["filename"]
    size = chunks[0]["size"]

    ordered_b64_parts: List[str] = []
    for i in range(total):
        if i not in by_index:
            raise ValueError(f"缺少第 {i + 1}/{total} 块")
        ordered_b64_parts.append(by_index[i]["data"])

    raw = base64.b64decode("".join(ordered_b64_parts))
    return filename, size, raw


# ---------------------------------------------------------------------------
# 序列化(塞进二维码)
# ---------------------------------------------------------------------------
def dumps(chunk: Dict) -> str:
    """帧 dict → JSON 字符串(紧凑,塞进二维码)。"""
    return json.dumps(chunk, ensure_ascii=False, separators=(",", ":"))


def loads(text: str) -> Optional[Dict]:
    """JSON 字符串 → 帧 dict;解析失败返回 None。"""
    try:
        return json.loads(text)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
