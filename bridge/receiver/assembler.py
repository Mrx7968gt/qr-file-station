#!/usr/bin/env python3
"""
bridge.receiver.assembler — 拼装层

职责:
    维护"会话 → 文件块缓冲"状态机。接收解码后的帧,按 sid 分组,
    去重累积 data 帧;当某个文件收齐(或靠 FEC 恢复)时拼装落盘。

设计要点(移植自前端 FileAssembler.tsx,并增强 FEC):
    - 按 (sid, filename) 维护独立缓冲,received_chunks: {index -> data}
    - 重复 index 直接覆盖(去重)
    - 见 start 帧 → 重置该 sid 的缓冲
    - 见 end 帧 → 尝试收尾(检查是否所有文件都齐)
    - FEC:若 start 帧带 fec 元信息,数据块不足时尝试用冗余块恢复

落盘目录:./recv/(可配置)
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from bridge.common import protocol
from bridge.fec import rs_codec as fec


@dataclass
class FileBuffer:
    """单个文件的接收缓冲。"""
    filename: str
    total: int                                  # 数据块总数
    received: Dict[int, str] = field(default_factory=dict)   # index -> data(base64 片段)
    size: int = 0                               # 原始文件字节数
    # FEC 相关(可选)
    fec_chunks: Dict[int, bytes] = field(default_factory=dict)  # 冗余块序号 -> bytes
    fec_meta: Optional[fec.FECMeta] = None

    @property
    def have_data_count(self) -> int:
        return len(self.received)

    @property
    def is_complete_by_count(self) -> bool:
        """仅凭数据块数判断是否收齐。"""
        return len(self.received) >= self.total

    def recovered_raw_chunks(self) -> Optional[List[bytes]]:
        """
        尝试用 FEC 恢复缺失的数据块。

        Returns:
            恢复后的原始字节块列表(对应 protocol 的 base64 解码前),
            或 None(未启用 FEC / 收到的块不足以恢复)
        """
        if self.fec_meta is None or self.fec_meta.n == self.fec_meta.k:
            return None  # 未启用 FEC

        meta = self.fec_meta
        # 组装 {块序号 -> 字节}:数据块是 base64 片段编码后的字节
        received: Dict[int, bytes] = {}
        for idx, b64frag in self.received.items():
            received[idx] = b64frag.encode("ascii")
        # 冗余块
        for idx, chunk in self.fec_chunks.items():
            received[meta.k + idx] = chunk

        if len(received) < meta.k:
            return None  # 不足

        recovered = fec.decode(received, meta)
        if recovered is None:
            return None
        return recovered


@dataclass
class SessionState:
    """单个会话(sid)的状态。"""
    sid: str
    files: Dict[str, FileBuffer] = field(default_factory=dict)  # filename -> buffer
    expected_files: List[Dict] = field(default_factory=list)     # start 帧里的文件清单
    completed: List[str] = field(default_factory=list)           # 已落盘的文件名


class Assembler:
    """
    接收端拼装器。

    用法:
        asm = Assembler(out_dir="./recv")
        for frame in decoded_frames:
            done_files = asm.handle_frame(frame)
            # done_files 是本次新落盘的文件路径列表
    """

    def __init__(
        self,
        out_dir: str = "./recv",
        on_file_done: Optional[Callable[[str, str], None]] = None,
    ):
        """
        Args:
            out_dir: 落盘目录
            on_file_done: 回调(filename, saved_path),文件拼装落盘后调用
        """
        self.out_dir = out_dir
        self.on_file_done = on_file_done
        self.sessions: Dict[str, SessionState] = {}
        os.makedirs(out_dir, exist_ok=True)

    # ------------------------------------------------------------------
    # 帧处理
    # ------------------------------------------------------------------
    def handle_frame(self, frame: Dict) -> List[str]:
        """
        处理一帧,返回本次新落盘的文件路径列表(通常为空)。

        帧类型:
            start → 重置该 sid,记录文件清单和 FEC 元信息
            data  → 累积到对应文件缓冲,收齐则拼装落盘
            end   → 尝试对所有未完成的文件做 FEC 恢复收尾
        """
        ftype = frame.get("type")
        sid = frame.get("sid", "")
        if not sid:
            return []

        if ftype == "start":
            self._handle_start(frame)
            return []
        if ftype == "data":
            return self._handle_data(frame)
        if ftype == "end":
            return self._handle_end(frame)
        return []

    def _handle_start(self, frame: Dict) -> None:
        sid = frame["sid"]
        # 新会话:重置缓冲
        self.sessions[sid] = SessionState(sid=sid)
        sess = self.sessions[sid]
        sess.expected_files = frame.get("files", [])
        # FEC 元信息:新协议按文件名下发(per_file_fec = {filename: FECMeta.to_dict()})
        # 兼容旧协议:若 fec 是单个 dict(非 {filename:...}),则对所有文件用同一份
        fec_field = frame.get("fec")
        fec_by_file: Dict[str, "fec.FECMeta"] = {}
        if fec_field:
            if isinstance(fec_field, dict) and all(
                isinstance(v, dict) for v in fec_field.values()
            ):
                # 新协议:{filename: meta_dict}
                for fname, meta_d in fec_field.items():
                    try:
                        fec_by_file[fname] = fec.FECMeta.from_dict(meta_d)
                    except Exception:
                        pass
            else:
                # 旧协议:单个 meta dict,存为占位
                try:
                    sess._fec_meta = fec.FECMeta.from_dict(fec_field)  # noqa: SLF001
                except Exception:
                    pass
        sess._fec_by_file = fec_by_file  # noqa: SLF001

    def _handle_data(self, frame: Dict) -> List[str]:
        sid = frame["sid"]
        sess = self.sessions.get(sid)
        if sess is None:
            # 漏了 start,按需新建
            sess = SessionState(sid=sid)
            self.sessions[sid] = sess

        # 校验未通过则丢弃
        if not protocol.verify_chunk(frame):
            return []

        fname = frame["filename"]
        buf = sess.files.get(fname)
        if buf is None:
            # FEC 元信息优先取自 start 帧的 per_file_fec(按文件名);
            # 回退取自旧协议的 session._fec_meta
            fec_by_file = getattr(sess, "_fec_by_file", {}) or {}
            fec_meta = fec_by_file.get(fname)
            if fec_meta is None:
                fec_meta = getattr(sess, "_fec_meta", None)
            buf = FileBuffer(
                filename=fname,
                total=frame["total"],
                size=frame.get("size", 0),
                fec_meta=fec_meta,
            )
            sess.files[fname] = buf

        # 冗余块还是数据块?由发送端在 extra 字段标记 is_fec。
        # 帧里 data 字段始终是 base64 字符串:
        #   - 数据块:文件 base64 片段(直接用于拼装)
        #   - 冗余块:冗余二进制字节的 base64(需解码回字节再传给 FEC)
        if frame.get("is_fec"):
            import base64 as _b64
            buf.fec_chunks[frame["index"]] = _b64.b64decode(frame["data"])
        else:
            buf.received[frame["index"]] = frame["data"]

        # 收齐则落盘
        if buf.is_complete_by_count and fname not in sess.completed:
            return self._try_assemble(sess, fname)
        return []

    def _handle_end(self, frame: Dict) -> List[str]:
        """end 帧:对未完成的文件尝试 FEC 恢复后落盘。"""
        sid = frame["sid"]
        sess = self.sessions.get(sid)
        if sess is None:
            return []
        saved: List[str] = []
        for fname, buf in sess.files.items():
            if fname in sess.completed:
                continue
            if buf.is_complete_by_count:
                saved += self._try_assemble(sess, fname)
            else:
                # 尝试 FEC 恢复
                saved += self._try_fec_assemble(sess, fname)
        return saved

    # ------------------------------------------------------------------
    # 落盘
    # ------------------------------------------------------------------
    def _try_assemble(self, sess: SessionState, fname: str) -> List[str]:
        buf = sess.files[fname]
        try:
            chunks = [
                {"index": i, "total": buf.total, "filename": fname,
                 "size": buf.size, "data": buf.received[i], "checksum": 0}
                for i in range(buf.total)
            ]
            _, _, raw = protocol.assemble(chunks)
        except ValueError:
            return []
        return self._save(sess, fname, raw)

    def _try_fec_assemble(self, sess: SessionState, fname: str) -> List[str]:
        buf = sess.files[fname]
        recovered = buf.recovered_raw_chunks()
        if recovered is None:
            return []
        # 拼接所有恢复出的 base64 片段 → 解码
        try:
            full_b64 = "".join(c.decode("ascii") for c in recovered)
            import base64
            raw = base64.b64decode(full_b64)
        except Exception:
            return []
        return self._save(sess, fname, raw)

    def _save(self, sess: SessionState, fname: str, raw: bytes) -> List[str]:
        # 防目录穿越:只取文件名部分
        safe_name = os.path.basename(fname)
        path = os.path.join(self.out_dir, safe_name)
        with open(path, "wb") as f:
            f.write(raw)
        sess.completed.append(fname)
        if self.on_file_done:
            self.on_file_done(safe_name, path)
        return [path]

    # ------------------------------------------------------------------
    # 状态查询(供 UI 展示进度)
    # ------------------------------------------------------------------
    def progress(self) -> Dict[str, Dict]:
        """返回各文件的接收进度 {filename: {received, total, done}}。"""
        result: Dict[str, Dict] = {}
        for sess in self.sessions.values():
            for fname, buf in sess.files.items():
                result[fname] = {
                    "received": buf.have_data_count,
                    "total": buf.total,
                    "done": fname in sess.completed,
                }
        return result
