#!/usr/bin/env python3
"""
bridge.receiver.decoder — 解码层

职责:
    1. 把 pyzbar 解码出的原始字节,解析成帧 dict
    2. 校验 data 帧的 crc32
    3. 暴露帧类型判断辅助函数

本模块不依赖 OpenCV,只依赖 pyzbar 的解码结果(或任意字节串),
便于单测和复用。
"""

from __future__ import annotations

from typing import Dict, List, Optional

from bridge.common import protocol


def parse_frame(raw_bytes: bytes) -> Optional[Dict]:
    """
    把 pyzbar 解码出的字节解析成帧 dict。

    Args:
        raw_bytes: pyzbar 返回的 o.data

    Returns:
        帧 dict;解析失败返回 None
    """
    try:
        text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError:
        return None
    return protocol.loads(text)


def decode_all(raw_results: List) -> List[Dict]:
    """
    批量解析 pyzbar 的解码结果列表。

    Args:
        raw_results: pyzbar.decode(frame) 的返回值(列表,每项含 .data)

    Returns:
        成功解析的帧 dict 列表(跳过解析失败的)
    """
    frames: List[Dict] = []
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
    """是 data 帧 且 checksum 通过。"""
    return is_data(frame) and protocol.verify_chunk(frame)
