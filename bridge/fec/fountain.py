#!/usr/bin/env python3
"""
bridge.fec.fountain - LT (Luby Transform) fountain codes

Practical implementation with degree-1 floor for reliable peeling.
Sender generates unlimited blocks; receiver collects K+epsilon (any order).
"""

from __future__ import annotations

import math
import random
from typing import Dict, List, Optional, Set, Tuple


def _xor_inplace(a: bytearray, b: bytes, length: int) -> None:
    for i in range(length):
        a[i] ^= b[i]


def _degree_distribution(k: int) -> List[float]:
    """
    Practical degree distribution: ideal soliton with degree-1 floor.

    The floor ensures ~10% degree-1 blocks for reliable peeling start.
    Higher degrees follow 1/(i*(i-1)) for good cascade coverage.
    """
    if k <= 1:
        return [1.0]
    probs = [0.0] * k
    for i in range(1, k + 1):
        if i == 1:
            probs[0] = max(1.0 / k, 0.15)
        else:
            probs[i - 1] = 1.0 / (i * (i - 1))
    total = sum(probs)
    return [p / total for p in probs]


_cumul_cache: Dict[int, List[float]] = {}


def _get_cumulative(k: int) -> List[float]:
    if k not in _cumul_cache:
        probs = _degree_distribution(k)
        cumul = []
        running = 0.0
        for p in probs:
            running += p
            cumul.append(running)
        if cumul:
            cumul[-1] = 1.0
        _cumul_cache[k] = cumul
    return _cumul_cache[k]


def _sample_block(block_id: int, k: int) -> Tuple[int, List[int]]:
    rng = random.Random(block_id * 1000003 + k)
    cumul = _get_cumulative(k)
    r = rng.random()
    degree = k
    for i, cv in enumerate(cumul):
        if r <= cv:
            degree = i + 1
            break
    degree = max(1, min(degree, k))
    indices = sorted(rng.sample(range(k), degree))
    return degree, indices


class LTEncoder:
    def __init__(self, source_blocks: List[bytes]):
        if not source_blocks:
            raise ValueError("No source blocks")
        self.K = len(source_blocks)
        self.block_size = max(len(b) for b in source_blocks)
        self.padded = [
            b + b"\x00" * (self.block_size - len(b))
            if len(b) < self.block_size else b
            for b in source_blocks
        ]

    def encode_block(self, block_id: int) -> bytes:
        degree, indices = _sample_block(block_id, self.K)
        if not indices:
            return b"\x00" * self.block_size
        result = bytearray(self.padded[indices[0]])
        for idx in indices[1:]:
            _xor_inplace(result, self.padded[idx], self.block_size)
        return bytes(result)

    def generate(self, count: int, start_id: int = 0):
        for i in range(count):
            bid = start_id + i
            yield bid, self.encode_block(bid)


class LTDecoder:
    def __init__(self, k: int, block_size: int):
        self.K = k
        self.block_size = block_size
        self.decoded: Dict[int, bytearray] = {}
        self.active: Dict[int, Tuple[Set[int], bytearray]] = {}
        self._seen: Set[int] = set()

    @property
    def decoded_count(self) -> int:
        return len(self.decoded)

    @property
    def active_count(self) -> int:
        return len(self.active)

    def add_block(self, block_id: int, payload: bytes) -> None:
        if self.decoded_count >= self.K:
            return
        if block_id in self._seen:
            return
        self._seen.add(block_id)
        degree, indices = _sample_block(block_id, self.K)
        neighbors: Set[int] = set(indices)
        data = bytearray(payload[:self.block_size])
        if len(data) < self.block_size:
            data.extend(b"\x00" * (self.block_size - len(data)))
        resolved = neighbors & set(self.decoded.keys())
        for idx in resolved:
            _xor_inplace(data, self.decoded[idx], self.block_size)
            neighbors.discard(idx)
        if not neighbors:
            return
        if len(neighbors) == 1:
            idx = neighbors.pop()
            self.decoded[idx] = data
            self._ripple(idx)
        else:
            self.active[block_id] = (neighbors, data)

    def _ripple(self, start_idx: int) -> None:
        queue = [start_idx]
        while queue:
            si = queue.pop(0)
            sd = self.decoded.get(si)
            if sd is None:
                continue
            done = []
            for bid, (neighbors, data) in list(self.active.items()):
                if si in neighbors:
                    _xor_inplace(data, sd, self.block_size)
                    neighbors.discard(si)
                    if len(neighbors) == 1:
                        ni = neighbors.pop()
                        self.decoded[ni] = data
                        done.append(bid)
                        queue.append(ni)
            for bid in done:
                self.active.pop(bid, None)

    def decode(self) -> Optional[List[bytes]]:
        if self.decoded_count >= self.K:
            return self._result()
        progress = True
        while progress:
            progress = False
            done = []
            for bid, (neighbors, data) in list(self.active.items()):
                if len(neighbors) == 1:
                    idx = next(iter(neighbors))
                    self.decoded[idx] = data
                    done.append((bid, idx))
                    progress = True
            for bid, idx in done:
                self.active.pop(bid, None)
                self._ripple(idx)
        if self.decoded_count >= self.K:
            return self._result()
        return None

    def _result(self) -> Optional[List[bytes]]:
        out = []
        for i in range(self.K):
            d = self.decoded.get(i)
            if d is None:
                return None
            out.append(bytes(d))
        return out

    @property
    def progress(self) -> Tuple[int, int]:
        return (self.decoded_count, self.K)
