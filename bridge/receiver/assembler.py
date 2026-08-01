#!/usr/bin/env python3
"""
bridge.receiver.assembler - Frame assembly layer (v2 + v3)

v3: fountain (LT) decode or sequential collect -> zstd decompress -> save
v2: base64 assemble + RS FEC recovery (legacy)
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from bridge.common import protocol as v2proto
from bridge.common import binproto
from bridge.fec import fountain as lt
from bridge.fec import rs_codec as fec


@dataclass
class V3FileBuffer:
    filename: str
    k: int
    block_size: int
    compressed_len: int
    orig_size: int
    fountain: bool
    received_count: int = 0
    lt_decoder: Optional[lt.LTDecoder] = None
    seq_chunks: Dict[int, bytes] = field(default_factory=dict)
    completed: bool = False

    def init_decoder(self):
        if self.fountain and self.lt_decoder is None and self.k > 0:
            self.lt_decoder = lt.LTDecoder(self.k, self.block_size)

    def add_block(self, seed: int, payload: bytes) -> bool:
        if self.completed:
            return False
        self.received_count += 1
        if self.fountain:
            self.init_decoder()
            if self.lt_decoder is not None:
                self.lt_decoder.add_block(seed, payload)
                if self.received_count >= self.k:
                    return self.try_decode()
            return False
        else:
            self.seq_chunks[seed] = payload
            if len(self.seq_chunks) >= self.k:
                return self.try_assemble_seq()
            return False

    def try_decode(self) -> bool:
        if self.lt_decoder is None or self.completed:
            return False
        decoded = self.lt_decoder.decode()
        if decoded is None:
            return False
        return self._finalize(decoded)

    def try_assemble_seq(self) -> bool:
        if len(self.seq_chunks) < self.k or self.completed:
            return False
        ordered = []
        for i in range(self.k):
            c = self.seq_chunks.get(i)
            if c is None:
                return False
            ordered.append(c)
        return self._finalize(ordered)

    def _finalize(self, chunks: List[bytes]) -> bool:
        try:
            raw_padded = b"".join(chunks)
            compressed = raw_padded[:self.compressed_len]
            if self.orig_size > 0:
                raw = binproto.decompress(compressed)
            else:
                raw = b""
        except Exception:
            return False
        self._decoded_raw = raw
        self.completed = True
        return True

    def get_raw(self) -> Optional[bytes]:
        return getattr(self, "_decoded_raw", None)


@dataclass
class FileBuffer:
    filename: str
    total: int
    received: Dict[int, str] = field(default_factory=dict)
    size: int = 0
    fec_chunks: Dict[int, bytes] = field(default_factory=dict)
    fec_meta: Optional[fec.FECMeta] = None

    @property
    def have_data_count(self) -> int:
        return len(self.received)

    @property
    def is_complete_by_count(self) -> bool:
        return len(self.received) >= self.total

    def recovered_raw_chunks(self) -> Optional[List[bytes]]:
        if self.fec_meta is None or self.fec_meta.n == self.fec_meta.k:
            return None
        meta = self.fec_meta
        received: Dict[int, bytes] = {}
        for idx, b64frag in self.received.items():
            received[idx] = b64frag.encode("ascii")
        for idx, chunk in self.fec_chunks.items():
            received[meta.k + idx] = chunk
        if len(received) < meta.k:
            return None
        recovered = fec.decode(received, meta)
        if recovered is None:
            return None
        return recovered


@dataclass
class SessionState:
    sid: str
    proto: int = 3
    files: Dict = field(default_factory=dict)
    expected_files: List = field(default_factory=list)
    completed: List[str] = field(default_factory=list)
    manifest: Optional[dict] = None
    _fec_by_file: Dict = field(default_factory=dict)
    _fec_meta: Optional[fec.FECMeta] = None


class Assembler:
    def __init__(self, out_dir="./recv", on_file_done=None):
        self.out_dir = out_dir
        self.on_file_done = on_file_done
        self.sessions: Dict[str, SessionState] = {}
        os.makedirs(out_dir, exist_ok=True)

    def handle_frame(self, frame: Dict) -> List[str]:
        if not isinstance(frame, dict):
            return []
        ftype = frame.get("type")
        sid = frame.get("sid", "")
        if not sid:
            return []
        proto = frame.get("proto", 2)
        if proto == 3:
            return self._handle_v3(frame, sid, ftype)
        return self._handle_v2(frame, sid, ftype)

    def _handle_v3(self, frame, sid, ftype):
        if ftype == "start":
            return self._v3_start(frame, sid)
        if ftype == "data":
            return self._v3_data(frame, sid)
        if ftype == "end":
            return self._v3_end(frame, sid)
        return []

    def _v3_start(self, frame, sid):
        sess = SessionState(sid=sid, proto=3)
        self.sessions[sid] = sess
        manifest = frame.get("manifest", {})
        sess.manifest = manifest
        use_fountain = manifest.get("fountain", True)
        for f_info in manifest.get("files", []):
            fname = f_info["filename"]
            buf = V3FileBuffer(
                filename=fname,
                k=f_info.get("k", 0),
                block_size=f_info.get("block_size", 0),
                compressed_len=f_info.get("compressed_len", 0),
                orig_size=f_info.get("size", 0),
                fountain=use_fountain,
            )
            sess.files[fname] = buf
        return []

    def _v3_data(self, frame, sid):
        sess = self.sessions.get(sid)
        if sess is None:
            sess = SessionState(sid=sid, proto=3)
            self.sessions[sid] = sess
        fname = frame.get("filename", "")
        buf = sess.files.get(fname)
        if buf is None:
            buf = V3FileBuffer(
                filename=fname,
                k=frame.get("k", 0),
                block_size=len(frame.get("payload", b"")),
                compressed_len=0,
                orig_size=frame.get("filesize", 0),
                fountain=frame.get("fountain", False),
            )
            sess.files[fname] = buf
        just_done = buf.add_block(frame.get("seed", 0), frame.get("payload", b""))
        if just_done and fname not in sess.completed:
            return self._save_v3(sess, buf)
        return []

    def _v3_end(self, frame, sid):
        sess = self.sessions.get(sid)
        if sess is None:
            return []
        saved = []
        for fname, buf in sess.files.items():
            if fname in sess.completed:
                continue
            if buf.fountain:
                buf.try_decode()
            else:
                buf.try_assemble_seq()
            if buf.completed:
                saved += self._save_v3(sess, buf)
        return saved

    def _save_v3(self, sess, buf):
        raw = buf.get_raw()
        if raw is None:
            return []
        safe_name = os.path.basename(buf.filename)
        path = os.path.join(self.out_dir, safe_name)
        with open(path, "wb") as f:
            f.write(raw)
        sess.completed.append(buf.filename)
        if self.on_file_done:
            self.on_file_done(safe_name, path)
        return [path]

    def _handle_v2(self, frame, sid, ftype):
        if ftype == "start":
            self._v2_start(frame, sid)
            return []
        if ftype == "data":
            return self._v2_data(frame, sid)
        if ftype == "end":
            return self._v2_end(frame, sid)
        return []

    def _v2_start(self, frame, sid):
        sess = SessionState(sid=sid, proto=2)
        self.sessions[sid] = sess
        sess.expected_files = frame.get("files", [])
        fec_field = frame.get("fec")
        fec_by_file = {}
        if fec_field:
            if isinstance(fec_field, dict) and all(
                isinstance(v, dict) for v in fec_field.values()
            ):
                for fname, meta_d in fec_field.items():
                    try:
                        fec_by_file[fname] = fec.FECMeta.from_dict(meta_d)
                    except Exception:
                        pass
            else:
                try:
                    sess._fec_meta = fec.FECMeta.from_dict(fec_field)
                except Exception:
                    pass
        sess._fec_by_file = fec_by_file

    def _v2_data(self, frame, sid):
        sess = self.sessions.get(sid)
        if sess is None:
            sess = SessionState(sid=sid, proto=2)
            self.sessions[sid] = sess
        if not v2proto.verify_chunk(frame):
            return []
        fname = frame["filename"]
        buf = sess.files.get(fname)
        if buf is None:
            fec_by_file = getattr(sess, "_fec_by_file", {}) or {}
            fec_meta = fec_by_file.get(fname)
            if fec_meta is None:
                fec_meta = getattr(sess, "_fec_meta", None)
            buf = FileBuffer(
                filename=fname, total=frame["total"],
                size=frame.get("size", 0), fec_meta=fec_meta,
            )
            sess.files[fname] = buf
        if frame.get("is_fec"):
            import base64 as _b64
            buf.fec_chunks[frame["index"]] = _b64.b64decode(frame["data"])
        else:
            buf.received[frame["index"]] = frame["data"]
        if buf.is_complete_by_count and fname not in sess.completed:
            return self._v2_assemble(sess, fname)
        return []

    def _v2_end(self, frame, sid):
        sess = self.sessions.get(sid)
        if sess is None:
            return []
        saved = []
        for fname, buf in sess.files.items():
            if fname in sess.completed:
                continue
            if buf.is_complete_by_count:
                saved += self._v2_assemble(sess, fname)
            else:
                saved += self._v2_fec_assemble(sess, fname)
        return saved

    def _v2_assemble(self, sess, fname):
        buf = sess.files[fname]
        try:
            chunks = [
                {"index": i, "total": buf.total, "filename": fname,
                 "size": buf.size, "data": buf.received[i], "checksum": 0}
                for i in range(buf.total)
            ]
            _, _, raw = v2proto.assemble(chunks)
        except ValueError:
            return []
        return self._save(sess, fname, raw)

    def _v2_fec_assemble(self, sess, fname):
        buf = sess.files[fname]
        recovered = buf.recovered_raw_chunks()
        if recovered is None:
            return []
        try:
            full_b64 = "".join(c.decode("ascii") for c in recovered)
            import base64
            raw = base64.b64decode(full_b64)
        except Exception:
            return []
        return self._save(sess, fname, raw)

    def _save(self, sess, fname, raw):
        safe_name = os.path.basename(fname)
        path = os.path.join(self.out_dir, safe_name)
        with open(path, "wb") as f:
            f.write(raw)
        sess.completed.append(fname)
        if self.on_file_done:
            self.on_file_done(safe_name, path)
        return [path]

    def progress(self):
        result = {}
        for sess in self.sessions.values():
            for fname, buf in sess.files.items():
                if isinstance(buf, V3FileBuffer):
                    received = buf.received_count
                    total = buf.k
                else:
                    received = buf.have_data_count
                    total = buf.total
                result[fname] = {
                    "received": received,
                    "total": total,
                    "done": fname in sess.completed,
                }
        return result
