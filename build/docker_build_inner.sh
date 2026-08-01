#!/usr/bin/env bash
set -euo pipefail
export MIRROR=""

echo "[1/6] Fixing CentOS 7 EOL repos + installing build tools..."
sed -i 's/mirrorlist/#mirrorlist/g' /etc/yum.repos.d/CentOS-*.repo
sed -i 's|#baseurl=http://mirror.centos.org|baseurl=http://vault.centos.org|g' /etc/yum.repos.d/CentOS-*.repo
yum install -y gcc make openssl-devel bzip2-devel libffi-devel \
  zlib-devel readline-devel xz-devel tk-devel wget tar binutils file 2>&1 | tail -3

echo ""
echo "[2/6] Compiling Python 3.12.3 (--enable-shared, no PGO)..."
cd /tmp
if [ ! -f Python-3.12.3.tgz ]; then
  wget -q "https://www.python.org/ftp/python/3.12.3/Python-3.12.3.tgz"
fi
tar xzf Python-3.12.3.tgz
cd Python-3.12.3
./configure --prefix=/opt/py312 --enable-shared \
  LDFLAGS="-Wl,-rpath,/opt/py312/lib" > /dev/null 2>&1
make -j$(nproc) > /dev/null 2>&1
make install > /dev/null 2>&1
ldconfig /opt/py312/lib

export PATH="/opt/py312/bin:$PATH"
PY="/opt/py312/bin/python3"
echo "  Python: $($PY --version)"

echo ""
echo "[3/6] Installing Python deps..."
$PY -m pip install --upgrade pip -q 2>&1 | tail -1
$PY -m pip install --prefer-binary \
  "qrcode[pil]" "pillow>=10.0.0" zstandard reedsolo pyinstaller 2>&1 | tail -3

echo ""
echo "[4/6] Running tests..."
cd /src
$PY bridge/tests/test_binproto.py
$PY bridge/tests/test_fountain.py
$PY bridge/tests/test_loopback_v3.py

echo ""
echo "[5/6] Building binary with PyInstaller..."
$PY -m PyInstaller build/encoder.spec --clean --noconfirm 2>&1 | tail -8

chmod +x dist/qr-encoder-v*

echo ""
echo "=== Smoke test ==="
echo "hello world compression test data repeated many times" > /tmp/smoke.txt
./dist/qr-encoder-v* stats /tmp/smoke.txt

echo ""
echo "=== Binary info ==="
file dist/qr-encoder-v*
ls -lh dist/qr-encoder-v*

echo ""
echo "=== glibc requirement ==="
objdump -T dist/qr-encoder-v* 2>/dev/null | grep -oP 'GLIBC_[0-9.]+' | sort -Vu | tail -3

echo ""
echo "INNER_BUILD_DONE"
