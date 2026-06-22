#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUILD_IMAGE="python:3.12-slim"
APP_NAME="qr-terminal-browser"
TARGET_NAME="${APP_NAME}-linux-x86_64"
DIST_ROOT="${PROJECT_ROOT}/dist-linux-x86_64"
PACKAGE_DIR="${DIST_ROOT}/${TARGET_NAME}"
ARCHIVE_PATH="${DIST_ROOT}/${TARGET_NAME}.tar.gz"

echo "==> Building ${APP_NAME} for Linux x86_64 with Docker..."
echo "    Project: ${PROJECT_ROOT}"
echo "    Image:   ${BUILD_IMAGE}"

docker run --rm \
  --platform linux/amd64 \
  -v "${PROJECT_ROOT}:/src" \
  -w /src \
  "${BUILD_IMAGE}" \
  bash -lc '
    set -euo pipefail
    apt-get update
    apt-get install -y --no-install-recommends binutils
    rm -rf /var/lib/apt/lists/*
    python -m pip install --upgrade pip
    python -m pip install -r requirements.txt pyinstaller
    rm -rf build dist
    pyinstaller \
      --clean \
      --onefile \
      --name qr-terminal-browser \
      --hidden-import file_to_qr \
      --hidden-import qrcode \
      --hidden-import PIL \
      terminal_qr_browser.py
  '

rm -rf "${PACKAGE_DIR}"
mkdir -p "${PACKAGE_DIR}"
cp "${PROJECT_ROOT}/dist/${APP_NAME}" "${PACKAGE_DIR}/${APP_NAME}"
chmod +x "${PACKAGE_DIR}/${APP_NAME}"

cat > "${PACKAGE_DIR}/README.txt" <<'EOF'
二维码终端浏览器 Linux x86_64 版

用法:
  ./qr-terminal-browser --input-dir /path/to/files
  ./qr-terminal-browser --image-dir /path/to/qr_output

快捷键:
  ↑/↓/j/k  选择二维码
  n        下一张
  p        上一张
  q        退出

说明:
  --input-dir 会直接读取原始文件目录，并按 file_to_qr.py 的格式生成二维码内容。
  --image-dir 会读取已经生成好的 PNG 二维码目录。
  该二进制适用于 Linux x86_64 glibc 环境，不适用于 Alpine/musl。
EOF

tar -czf "${ARCHIVE_PATH}" -C "${DIST_ROOT}" "${TARGET_NAME}"

echo ""
echo "==> Done"
echo "    Executable: ${PACKAGE_DIR}/${APP_NAME}"
echo "    Archive:    ${ARCHIVE_PATH}"
