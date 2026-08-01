"""Cimbar frame decoder.

Given an image (from capture card or video), extracts tile data:
1. Detect grid position (via anchor markers or manual crop)
2. For each tile: decode symbol (4 bits) + color (2 bits)
3. Assemble into bytes
4. Apply Reed-Solomon error correction
5. Return decoded payload bytes

The fountain decode + zstd decompress happen at a higher level.
"""

from __future__ import annotations

import struct
from typing import List, Optional, Tuple

import numpy as np

from .config import (
    CELL_SIZE, CELL_SPACING_X, CELL_SPACING_Y, CELL_OFFSET,
    CELLS_PER_COL_X, CELLS_PER_COL_Y, CORNER_PADDING_X, CORNER_PADDING_Y,
    SYMBOL_BITS, COLOR_BITS, NUM_SYMBOLS, NUM_COLORS,
    ECC_BYTES, ECC_BLOCK_SIZE, ECC_DATA_BYTES,
    COLOR_PALETTE, SYMBOL_HASHES,
    DATA_CELL_POSITIONS, DATA_CELL_COUNT,
)


def hamming_distance(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def average_hash(cell_gray: np.ndarray) -> int:
    """Compute 8x8 average hash of a cell's grayscale pixels.

    The cell is already CELL_SIZE x CELL_SIZE (8x8).
    """
    threshold = int(cell_gray.mean())
    bits = 0
    flat = cell_gray.flatten()
    for px in flat:
        bits = (bits << 1) | (1 if int(px) > threshold else 0)
    return bits


def decode_symbol(cell_gray: np.ndarray) -> Tuple[int, int]:
    """Decode symbol from a grayscale cell. Returns (symbol_index, distance)."""
    h = average_hash(cell_gray)
    best_sym = 0
    best_dist = 1000
    for i, ref_hash in enumerate(SYMBOL_HASHES):
        d = hamming_distance(h, ref_hash & 0xFFFFFFFFFFFFFFFF)
        if d < best_dist:
            best_dist = d
            best_sym = i
            if d == 0:
                break
    return best_sym, best_dist


def decode_color(cell_rgb: np.ndarray) -> int:
    """Decode color index from an RGB cell. Uses relative color distance.

    Matches CimbDecoder::get_best_color with the von Kries-style
    relative color comparison.
    """
    # Average the center 6x6 region (skip outer ring)
    h, w = cell_rgb.shape[:2]
    cx0 = max(1, w // 2 - 3)
    cx1 = min(w, w // 2 + 3)
    cy0 = max(1, h // 2 - 3)
    cy1 = min(h, h // 2 + 3)
    center = cell_rgb[cy0:cy1, cx0:cx1]
    avg_r = float(center[:, :, 0].mean())
    avg_g = float(center[:, :, 1].mean())
    avg_b = float(center[:, :, 2].mean())

    # Normalize: stretch to [0, 255]
    mx = max(avg_r, avg_g, avg_b, 1.0)
    mn = min(avg_r, avg_g, avg_b, 48.0)
    if mn >= mx:
        mn = 0
    adjust = 255.0 / (mx - mn)
    r = (avg_r - mn) * adjust
    g = (avg_g - mn) * adjust
    b = (avg_b - mn) * adjust
    r = max(0, min(255, r))
    g = max(0, min(255, g))
    b = max(0, min(255, b))

    # Relative color: (r-g, g-b, b-r)
    best_fit = 0
    best_dist = 1e18
    for i in range(NUM_COLORS):
        cr, cg, cb = COLOR_PALETTE[i]
        # Relative colors
        dr1 = r - g
        dg1 = g - b
        db1 = b - r
        dr2 = cr - cg
        dg2 = cg - cb
        db2 = cb - cr
        dist = (dr1 - dr2) ** 2 + (dg1 - dg2) ** 2 + (db1 - db2) ** 2
        if dist < best_dist:
            best_dist = dist
            best_fit = i
    return best_fit


def extract_grid(img: np.ndarray, offset_x: int = 0, offset_y: int = 0) -> Optional[dict]:
    """
    Extract all tile data from a cimbar image.

    Args:
        img: RGB or BGR image (numpy array), should be >= 1024x1024
        offset_x/y: manual crop offset (if image has padding)

    Returns:
        dict with:
          'bits': list of (symbol_bits, color_bits) per data cell
          'bytes': assembled raw byte data (before RS)
          'positions': list of (x, y) pixel coords used
        or None if image too small
    """
    h, w = img.shape[:2]
    if w < 1024 or h < 1024:
        return None

    # Auto-center: if image is larger than 1024, center crop
    if w > 1024:
        offset_x = (w - 1024) // 2
    if h > 1024:
        offset_y = (h - 1024) // 2

    # Convert to grayscale for symbol decoding
    if img.ndim == 3 and img.shape[2] == 3:
        gray = np.dot(img[:, :, :3], [0.299, 0.587, 0.114]).astype(np.uint8)
    else:
        gray = img

    all_bits = []
    raw_bytes = bytearray()
    bit_buffer = 0
    bit_count = 0

    for (cx, cy) in DATA_CELL_POSITIONS:
        px = offset_x + cx
        py = offset_y + cy

        # Extract 8x8 cell
        cell_gray = gray[py:py + CELL_SIZE, px:px + CELL_SIZE]
        cell_rgb = img[py:py + CELL_SIZE, px:px + CELL_SIZE]

        if cell_gray.shape != (CELL_SIZE, CELL_SIZE):
            # Out of bounds, pad with zeros
            sym_bits = 0
            color_bits = 0
        else:
            sym_bits, _ = decode_symbol(cell_gray)
            color_bits = decode_color(cell_rgb)

        all_bits.append((sym_bits, color_bits))

        # Pack into bytes: 4 bits symbol + 2 bits color = 6 bits
        combined = (sym_bits << COLOR_BITS) | color_bits
        bit_buffer = (bit_buffer << BITS_PER_CELL_PER_CELL()) | combined
        bit_count += 6
        while bit_count >= 8:
            bit_count -= 8
            raw_bytes.append((bit_buffer >> bit_count) & 0xFF)

    return {
        "bits": all_bits,
        "bytes": bytes(raw_bytes),
    }


def BITS_PER_CELL_PER_CELL():
    return 6


def reed_solomon_decode(data: bytes, nsym: int = ECC_BYTES,
                         block_size: int = ECC_BLOCK_SIZE) -> Optional[bytes]:
    """Apply Reed-Solomon decoding to the extracted data.

    Data is organized as interleaved RS blocks of `block_size` bytes each.
    Each block has `nsym` ECC bytes and `block_size - nsym` data bytes.
    """
    try:
        import reedsolo
    except ImportError:
        return data[:len(data) * ECC_DATA_BYTES // ECC_BLOCK_SIZE]

    rs = reedsolo.RSCodec(nsym=nsym)
    data_bytes = block_size - nsym
    n_blocks = len(data) // block_size
    if n_blocks == 0:
        return None

    result = bytearray()
    for i in range(n_blocks):
        chunk = data[i * block_size:(i + 1) * block_size]
        try:
            decoded = bytes(rs.decode(chunk)[0])
            result.extend(decoded)
        except Exception:
 # RS failed, use raw data (first data_bytes)
            result.extend(chunk[:data_bytes])

    return bytes(result)


def decode_frame(img: np.ndarray, offset_x: int = 0, offset_y: int = 0) -> Optional[bytes]:
    """
    Full frame decode pipeline: extract -> RS correct.

    Returns the effective data payload (after RS), or None on failure.
    """
    extracted = extract_grid(img, offset_x, offset_y)
    if extracted is None:
        return None

    rs_decoded = reed_solomon_decode(extracted["bytes"])
    return rs_decoded
