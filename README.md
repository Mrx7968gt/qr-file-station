# 文件二维码转换站 · QR File Station

把任意文件编码成一系列二维码图片，再用手机/摄像头扫描、由前端 Web 应用还原成原始文件的离线传输工具站。

适合**两台物理隔离、无法联网**的设备之间传文件：一台负责把文件「变成二维码」，另一台负责「扫二维码拼回文件」。全程不需要网络，只依赖摄像头和屏幕。

> 适用场景：内网/隔离环境、跨安全域单向传文件、无 USB 外设时的小体积文件传输。

---

## ✨ 功能特性

- 🔄 **文件 → 二维码**：将文件夹下所有文件分块编码为二维码 PNG（自动处理大文件分块、带校验和）。
- 🖥️ **终端二维码浏览器**：直接在 Linux 终端里逐张展示二维码，无需图形界面，可编译为单文件可执行程序。
- 📷 **Web 扫码还原**：浏览器实时扫码，按顺序拼接分块、Base64 解码、自动校验，还原原始文件。
- 🐳 **Docker 一键部署**：前端 Nginx 服务 + Python 编码器，`docker compose` 一条命令拉起。
- 🔌 **完全离线**：扫码、解码、还原全部在浏览器本地完成，无需联网。

---

## 📦 项目结构

```
文件二维码转换站/
├── app/                        # 前端 Web 应用(扫码 + 文件还原)
│   ├── src/                    #   React 源码
│   ├── dist/                   #   构建产物(已忽略)
│   ├── Dockerfile              #   前端容器镜像
│   └── package.json
├── file_to_qr.py               # Python 编码器:文件 → 分块 → 二维码 PNG
├── terminal_qr_browser.py      # 终端二维码浏览器(可编译成单文件)
├── Dockerfile.python           # 编码器容器镜像
├── docker-compose.yml          # 编排:前端 + 编码器
├── nginx.conf                  # 前端 Nginx 配置
├── build_linux_x86_64.sh       # ARM 上交叉编译 Linux x86_64 可执行文件
├── build_native_linux_x86_64.sh# Linux x86_64 上原生编译
├── requirements.txt            # Python 依赖
├── BUILD_LINUX_X86_64.txt      # 编译说明
└── README.txt                  # 命令速查
```

---

## 🚀 快速开始

### 方式一：Docker Compose（推荐）

```bash
# 1. 启动前端 Web 服务（扫码 + 还原），访问 http://localhost:8080
docker compose up -d web

# 2. 把文件转成二维码（输出到 ./qr_output）
docker compose run --rm \
  -e INPUT_DIR=/data/input -e OUTPUT_DIR=/data/output \
  -v "$(pwd)/test_files":/data/input:ro \
  -v "$(pwd)/qr_output":/data/output \
  encoder
```

### 方式二：本地直接运行

```bash
# 编码器(Python)
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python file_to_qr.py ./test_files ./qr_output
```

### 方式三：终端直接展示二维码

```bash
# 直接对原始文件目录分块并在终端逐张展示
python terminal_qr_browser.py --input-dir ./test_files

# 或展示已生成的 PNG 二维码
python terminal_qr_browser.py --image-dir ./qr_output
```

| 参数 | 说明 |
|------|------|
| `--input-dir <dir>` | 输入原始文件目录（会现场分块生成二维码） |
| `--image-dir <dir>` | 输入已生成的二维码 PNG 目录 |
| `--chunk-size <bytes>` | 单块字节数，二维码太密/太宽时调小（如 `100`） |
| `--module-width <n>` | 终端每个模块宽度列数，终端窄时用 `1` |
| `--image-max-width <cols>` | `--image-dir` 浏览 PNG 时的最大显示列宽 |

---

## 🔨 编译成 Linux 单文件可执行程序

适合在离线 Linux 服务器上直接跑，无需安装 Python 环境。

**在 Linux x86_64 上原生编译：**

```bash
chmod +x build_native_linux_x86_64.sh
./build_native_linux_x86_64.sh
# 产物:dist-linux-x86_64/qr-terminal-browser-linux-x86_64/qr-terminal-browser
```

**在 ARM 机器（如 Apple Silicon）上用 Docker 交叉编译：**

```bash
chmod +x build_linux_x86_64.sh
./build_linux_x86_64.sh
```

详细说明见 [`BUILD_LINUX_X86_64.txt`](./BUILD_LINUX_X86_64.txt)。

---

## 📖 工作原理

1. **编码（`file_to_qr.py`）**：读取文件 → Base64 编码 → 按 `MAX_CHUNK_SIZE`（默认 384 字节）切块 → 每块附带 `{filename, size, index, total, data, checksum}` 元数据 → 用最高容错率（H，30%）生成二维码 PNG。
2. **传输**：通过终端浏览器逐张展示，或打印 PNG，由另一台机器的摄像头扫描。
3. **解码（前端 Web 应用）**：`html5-qrcode` 逐张识别 → 校验 `checksum` → 按序拼接所有分块 → Base64 解码 → 还原原始文件。

---

## 🧰 技术栈

| 模块 | 技术 |
|------|------|
| 前端 | React 19、TypeScript、Vite 7、Tailwind CSS、shadcn/ui、html5-qrcode、jszip |
| 编码器 | Python 3、[qrcode](https://github.com/lincolnloop/python-qrcode)、Pillow |
| 终端浏览器 | Python 3.7+、PyInstaller（打包成单文件） |
| 部署 | Docker、Docker Compose、Nginx |

---

## ⚠️ 限制与说明

- 大文件会产生**非常多**张二维码（384 字节/张），传输耗时与文件体积线性相关，建议仅用于小体积文本/配置/凭证等。
- 二维码容错率设为最高（H），但仍受屏幕分辨率、摄像头对焦、环境光线影响。
- 终端浏览器当前主要面向 **Linux x86_64**，其它平台可直接用 Python 运行源码。

---

## 📄 许可证

[MIT License](./LICENSE)
