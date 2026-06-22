#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_NAME="qr-terminal-browser"
TARGET_NAME="${APP_NAME}-linux-x86_64"
DIST_ROOT="${PROJECT_ROOT}/dist-linux-x86_64"
PACKAGE_DIR="${DIST_ROOT}/${TARGET_NAME}"
ARCHIVE_PATH="${DIST_ROOT}/${TARGET_NAME}.tar.gz"
VENV_DIR="${PROJECT_ROOT}/.venv-build"

echo "==> Building ${APP_NAME} on native Linux x86_64..."

ARCH="$(uname -m)"
if [ "${ARCH}" != "x86_64" ]; then
  echo "错误: 当前机器架构是 ${ARCH}，请在 Linux x86_64 服务器上运行。"
  exit 1
fi

if ! command -v objdump >/dev/null 2>&1; then
  echo "错误: 未找到 objdump。PyInstaller 在 Linux 上需要 binutils。"
  echo "Debian/Ubuntu: sudo apt-get update && sudo apt-get install -y binutils python3-venv"
  echo "CentOS/RHEL/Rocky: sudo yum install -y binutils python3"
  exit 1
fi

cd "${PROJECT_ROOT}"

python3 -m venv "${VENV_DIR}"
"${VENV_DIR}/bin/python" -m pip install --upgrade pip
"${VENV_DIR}/bin/python" -m pip install -r requirements.txt pyinstaller

rm -rf build dist
"${VENV_DIR}/bin/pyinstaller" \
  --clean \
  --onefile \
  --name "${APP_NAME}" \
  --hidden-import file_to_qr \
  --hidden-import qrcode \
  --hidden-import PIL \
  terminal_qr_browser.py

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
EOF

tar -czf "${ARCHIVE_PATH}" -C "${DIST_ROOT}" "${TARGET_NAME}"

echo ""
echo "==> Done"
echo "    Executable: ${PACKAGE_DIR}/${APP_NAME}"
echo "    Archive:    ${ARCHIVE_PATH}"
