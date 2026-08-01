#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUILD_IMAGE="python:3.12-slim"
APP_NAME="qr-encoder"
TARGET_NAME="${APP_NAME}-linux-x86_64"
DIST_ROOT="${PROJECT_ROOT}/dist-linux-x86_64"
PACKAGE_DIR="${DIST_ROOT}/${TARGET_NAME}"
ARCHIVE_PATH="${DIST_ROOT}/${TARGET_NAME}.tar.gz"

echo "==> Building ${APP_NAME} for Linux x86_64 with Docker..."
echo "    Project: ${PROJECT_ROOT}"

docker run --rm \
  --platform linux/amd64 \
  -v "${PROJECT_ROOT}:/src" \
  -w /src \
  "${BUILD_IMAGE}" \
  bash -lc '
    set -euo pipefail
    apt-get update && apt-get install -y --no-install-recommends binutils
    rm -rf /var/lib/apt/lists/*
    python -m pip install --upgrade pip
    python -m pip install -r requirements.txt pyinstaller
    rm -rf build dist
    pyinstaller --clean --onefile \
      --name qr-encoder \
      --hidden-import qrcode \
      --hidden-import PIL \
      --hidden-import zstandard \
      --hidden-import reedsolo \
      --add-data "bridge:bridge" \
      qr_encoder.py
  '

rm -rf "${PACKAGE_DIR}"
mkdir -p "${PACKAGE_DIR}"
cp "${PROJECT_ROOT}/dist/${APP_NAME}" "${PACKAGE_DIR}/${APP_NAME}"
chmod +x "${PACKAGE_DIR}/${APP_NAME}"

cat > "${PACKAGE_DIR}/README.txt" <<'EOF'
QR File Station v3 Encoder - Linux x86_64

Optimizations:
  - zstd compression (3-5x for text)
  - Binary frames (no JSON/Base64 overhead)
  - QR M-level error correction (15%, more data per code)
  - LT fountain codes (no multi-loop, any-order decode)
  - 30 FPS default, 2x2 grid support

Usage:
  ./qr-encoder encode /path/to/files -o /path/to/output
  ./qr-encoder stats /path/to/file.zip
  ./qr-encoder play /path/to/file.zip --fps 30 --grid 2

Requires glibc-based Linux x86_64. Not for Alpine/musl.
EOF

tar -czf "${ARCHIVE_PATH}" -C "${DIST_ROOT}" "${TARGET_NAME}"

echo ""
echo "==> Done"
echo "    Executable: ${PACKAGE_DIR}/${APP_NAME}"
echo "    Archive:    ${ARCHIVE_PATH}"
