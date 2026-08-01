"""Cimbar format constants - extracted from libcimbar Config.h / GridConf.h.

Mode B (default, mode 68 = Conf8x8):
  Image: 1024x1024
  Tile: 8x8 pixels, spacing 9x9 (1px gap between tiles)
  Grid: 112x112 cells
  Corner padding: 6x6 cells per corner (reserved for anchor markers)
  Symbol bits: 4 (16 symbols)
  Color bits: 2 (4 colors)
  Bits per cell: 6
  ECC: Reed-Solomon 30/155
  Fountain: wirehair, 2 chunks per frame (mode B)
  Compression: zstd level 16
"""

# Image dimensions
IMAGE_SIZE = 1024
IMAGE_W = 1024
IMAGE_H = 1024

# Cell/tile geometry
CELL_SIZE = 8
CELL_SPACING_X = 9   # cell_size + 1
CELL_SPACING_Y = 9
CELL_OFFSET = 8       # starting pixel offset for first tile
CELLS_PER_COL_X = 112
CELLS_PER_COL_Y = 112

# Corner markers
ANCHOR_SIZE = 30      # pixels
CORNER_PADDING_X = 6  # cells reserved per corner (lrint(54/9))
CORNER_PADDING_Y = 6

# Encoding parameters
SYMBOL_BITS = 4
COLOR_BITS = 2
BITS_PER_CELL = 6     # symbol_bits + color_bits
NUM_SYMBOLS = 16
NUM_COLORS = 4

# Error correction
ECC_BYTES = 30
ECC_BLOCK_SIZE = 155
ECC_DATA_BYTES = ECC_BLOCK_SIZE - ECC_BYTES  # 125

# Total cells and capacity
TOTAL_CELLS = CELLS_PER_COL_X * CELLS_PER_COL_Y - 4 * CORNER_PADDING_X * CORNER_PADDING_Y
RAW_CAPACITY = TOTAL_CELLS * BITS_PER_CELL // 8  # ~9300 bytes
EFFECTIVE_CAPACITY = RAW_CAPACITY * ECC_DATA_BYTES // ECC_BLOCK_SIZE  # ~7500 bytes

# Fountain code
FOUNTAIN_CHUNKS_PER_FRAME = 12  # bits_per_cell * scalar (6*2)
FOUNTAIN_CHUNK_SIZE = EFFECTIVE_CAPACITY // FOUNTAIN_CHUNKS_PER_FRAME  # ~744 bytes
FOUNTAIN_OVERHEAD = 6           # bytes of metadata per chunk

# Max file size (wirehair constraint, after compression)
MAX_FILE_SIZE = 33_554_432  # 32 MiB

# 4-color palette (RGB) - mode B (non-legacy)
# From Common.cpp getColor4()
COLOR_PALETTE = [
    (0, 255, 0),      # 0: green
    (0, 255, 255),    # 1: cyan
    (255, 255, 0),    # 2: yellow
    (255, 0, 255),    # 3: magenta
]

# 16 symbol average hashes (8x8 threshold patterns)
# Extracted from bitmap/4/*.png in libcimbar source
SYMBOL_HASHES = [
    0x000000000000000000000000000000000000000000000000000103070f1f3f7f,
    0x0000000000000000000000000000000000000000000000007f3f1f0f07030100,
    0x0000000000000000000000000000000000000000000000000080c0e0f0f8fcfe,
    0x000000000000000000000000000000000000000000000000fefcf8f0e0c08000,
    0x000000000000000000000000000000000000000000000000e7e7e70000e7e7e7,
    0x000000000000000000000000000000000000000000000000991818ffff181899,
    0x000000000000000000000000000000000000000000000000c381183c3c1881c3,
    0x000000000000000000000000000000000000000000000000e7e7c3c381810000,
    0x0000000000000000000000000000000000000000000000003f0f030000030f3f,
    0x00000000000000000000000000000000000000000000000000030fffff0f0300,
    0x00000000000000000000000000000000000000000000000000c0f0fffff0c000,
    0x000000000000000000000000000000000000000000000000181818183c3c7e7e,
    0x0000000000000000000000000000000000000000000000007e7e3c3c18181818,
    0x000000000000000000000000000000000000000000000000ffff3c1881c3e7ff,
    0x000000000000000000000000000000000000000000000000f3e3c78f8fc7e3f3,
    0x000000000000000000000000000000000000000000000000e1e1c7c7e3e38787,
]


def cell_position(idx):
    """Convert linear cell index to (x, y) pixel coordinates.

    Mimics CellPositions layout: row-major, skipping corner regions.
    Returns the top-left pixel of the cell (including the gap offset).
    """
    # Grid is CELLS_PER_COL_X wide, CELLS_PER_COL_Y tall
    # Cells are laid out row by row, left to right
    # Corner padding cells are skipped
    cpx = CORNER_PADDING_X
    cpy = CORNER_PADDING_Y

    total_w = CELLS_PER_COL_X
    row = idx // total_w
    col = idx % total_w

    # Check if in corner region (skip these)
    in_tl = row < cpy and col < cpx
    in_tr = row < cpy and col >= total_w - cpx
    in_bl = row >= CELLS_PER_COL_Y - cpy and col < cpx
    in_br = row >= CELLS_PER_COL_Y - cpy and col >= total_w - cpx
    if in_tl or in_tr or in_bl or in_br:
        return None

    x = CELL_OFFSET + col * CELL_SPACING_X
    y = CELL_OFFSET + row * CELL_SPACING_Y
    return (x, y)


def data_cell_indices():
    """Return list of cell indices that are in the data region (not corners)."""
    indices = []
    cpx = CORNER_PADDING_X
    cpy = CORNER_PADDING_Y
    total = CELLS_PER_COL_X * CELLS_PER_COL_Y

    for i in range(total):
        row = i // CELLS_PER_COL_X
        col = i % CELLS_PER_COL_X
        if row < cpy and col < cpx:
            continue
        if row < cpy and col >= CELLS_PER_COL_X - cpx:
            continue
        if row >= CELLS_PER_COL_Y - cpy and col < cpx:
            continue
        if row >= CELLS_PER_COL_Y - cpy and col >= CELLS_PER_COL_X - cpx:
            continue
        indices.append(i)

    return indices


# Pre-compute the data cell positions
DATA_CELL_POSITIONS = []
for _i in range(CELLS_PER_COL_X * CELLS_PER_COL_Y):
    _pos = cell_position(_i)
    if _pos is not None:
        DATA_CELL_POSITIONS.append(_pos)

DATA_CELL_COUNT = len(DATA_CELL_POSITIONS)
DATA_CAPACITY_BYTES = DATA_CELL_COUNT * BITS_PER_CELL // 8
DATA_EFFECTIVE_BYTES = DATA_CAPACITY_BYTES * ECC_DATA_BYTES // ECC_BLOCK_SIZE
