# 🚀 v1.0.0 — 文件二维码转换站 首个正式版

把任意文件编码成一系列二维码图片,再用手机/摄像头扫描、由前端 Web 应用还原成原始文件的**离线传输工具站**。

适合两台物理隔离、无法联网的设备之间传文件 —— 一台把文件「变成二维码」,另一台「扫二维码拼回文件」,全程不需要网络,只依赖摄像头和屏幕。

## ✨ 主要能力

- 🔄 **文件 → 二维码**:文件夹批量分块编码为二维码 PNG,自动处理大文件、带校验和
- 🖥️ **终端二维码浏览器**:直接在 Linux 终端逐张展示二维码,无需图形界面
- 📷 **Web 扫码还原**:浏览器实时扫码、按序拼接、Base64 解码、自动校验
- 🐳 **Docker 一键部署**:前端 Nginx 服务 + Python 编码器
- 🔌 **完全离线**:扫码、解码、还原全部在浏览器本地完成

## 📦 下载说明

根据使用场景选择对应附件:

| 附件 | 适合人群 | 说明 |
|------|---------|------|
| **`qr-file-station.tar.gz`** (95 MB) | 想在 Linux 服务器上一键部署整套服务 | 包含前端 Docker 镜像、Python 编码器、部署脚本。解压后 `./qr-file-station.sh deploy && start` 即可 |
| **`qr-terminal-browser-linux-x86_64.tar.gz`** (9.4 MB) | 只想在 Linux 终端展示二维码,不想装 Python | 解压后直接 `./qr-terminal-browser --input-dir /path/to/files`,无需依赖 |
| **`qr-terminal-browser-source.tar.gz`** (11 KB) | 想在目标 Linux 机器上原生编译终端浏览器 | 含源码和 `build_native_linux_x86_64.sh` 脚本 |

> 💡 只想用 Python 源码或前端开发?直接 clone 本仓库即可,无需下载附件。

## 🚀 快速开始

### 方式一:离线部署整套服务(用 `qr-file-station.tar.gz`)

```bash
# 1. 下载并解压部署包
mkdir qr-file-station && tar -xzf qr-file-station.tar.gz -C qr-file-station
cd qr-file-station

# 2. 导入 Docker 镜像
./qr-file-station.sh deploy

# 3. 启动前端 Web 服务,访问 http://localhost:8080
./qr-file-station.sh start

# 4. 把文件转成二维码
./qr-file-station.sh encode /path/to/files /path/to/output
```

### 方式二:用预编译终端浏览器(用 `qr-terminal-browser-linux-x86_64.tar.gz`)

```bash
tar -xzf qr-terminal-browser-linux-x86_64.tar.gz
cd qr-terminal-browser-linux-x86_64

# 直接对原始文件分块并在终端展示
./qr-terminal-browser --input-dir /path/to/files

# 或浏览已生成的 PNG 二维码
./qr-terminal-browser --image-dir /path/to/output
```

常用参数:
- `--chunk-size <bytes>`:二维码太密/太宽时调小(如 `100`)
- `--module-width <n>`:终端窄时设为 `1` 压缩宽度
- `--image-max-width <cols>`:浏览 PNG 时的最大列宽

## 📖 工作原理

1. **编码** (`file_to_qr.py`):读取文件 → Base64 → 按 384 字节切块 → 每块附 `{filename, size, index, total, data, checksum}` 元数据 → 用最高容错率(H,30%)生成 PNG
2. **传输**:终端逐张展示或打印 PNG,由另一台机器扫描
3. **解码** (前端 Web):`html5-qrcode` 识别 → 校验 checksum → 按序拼接 → Base64 解码 → 还原文件

## ⚠️ 限制

- 大文件会产生非常多张二维码(384 字节/张),仅适合小体积文本/配置/凭证
- 二维码容错率最高(H),但仍受屏幕分辨率、摄像头对焦、光线影响
- 预编译二进制仅面向 **Linux x86-64**,其它平台请用源码运行

## 📄 许可证

MIT License — 详见 [LICENSE](https://github.com/Mrx7968gt/qr-file-station/blob/main/LICENSE)
