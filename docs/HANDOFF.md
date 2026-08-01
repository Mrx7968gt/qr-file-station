# cimbar 项目交接文档

> 最后更新: 2026-08-02
> 前一个 Agent 的交接说明,供下一个 Agent 接手

## 一、项目概述

### 背景
用户有一个 **QR File Station** (GitHub: Mrx7968gt/qr-file-station),原本是基于标准 QR 码的离线文件传输工具。
用户了解到开源项目 [libcimbar](https://github.com/sz3/libcimbar) (6.2k stars) 后,决定引入 cimbar 彩色色块矩阵编码来提升传输效率。

### 目标场景
- **发送端**: 一台设备(内网)运行 `send-v2.html`,显示 cimbar 色块矩阵动画
- **传输介质**: USB 采集卡 (HDMI 输入) 或 手机摄像头直拍屏幕
- **接收端**: Mac 上运行 HTTPS Web 解码器 (`https://<mac-ip>:8443/index.html`)
- **手机接收**: iPhone Safari 访问同一 URL,用后置摄像头扫描

### 关键约束
- 用户的 USB 采集卡是 MacroSilicon MS2109 (USB 2.0, MJPEG 硬件编码),型号 `534d:2109`
- **采集卡对 cimbar 高频全屏色彩突变支持极差**,只有 Bu 微型模式 (mode 66, 80x69 tile) 勉强能稳定工作
- 发送端在内网环境,不便于频繁更新文件,改动需最小化
- iPhone iOS Safari 访问需要 HTTPS (自签证书)

## 二、当前架构

### 文件位置
所有代码在 `/Users/mrx/Desktop/vibeCoding/GPT/文件二维码转换站/` 下:

```
文件二维码转换站/
├── cimbar/                           # cimbar 解码端 (核心)
│   ├── index.html                    # Web 解码器 (手机+Mac)
│   ├── cimbar_js.2026-07-13T0523.js  # WASM JS 胶水 (作者原版,含编解码器)
│   ├── cimbar_js.2026-07-13T0523.wasm # WASM 二进制 (1.8MB)
│   ├── recv.js                       # 作者原版接收逻辑
│   ├── recv-worker.js                # 作者原版 Worker
│   ├── zstd.js                       # zstd 解压
│   ├── cert.pem                      # 自签 SSL 证书
│   ├── key.pem                       # SSL 私钥
│   └── ...
├── bridge/                           # 原有 QR 码传输方案 (Protocol v3)
│   ├── common/binproto.py            # v3 二进制协议
│   ├── fec/fountain.py               # LT 喷泉码
│   ├── sender/                       # 发送端
│   ├── receiver/                     # 接收端
│   └── ...
├── .github/workflows/
│   ├── build-encoder.yml             # QR 编码器跨平台编译
│   ├── build-wasm-decoder.yml        # WASM 解码器编译 (有问题,见下)
│   └── build-windows-exe.yml         # Windows sender.exe 编译
├── docs/HANDOFF.md                   # 本文档
└── ...
```

### 发送端
- 文件: `/Users/mrx/Downloads/send-v2.html` (2.5MB 自包含单文件,内联 WASM)
- 来源: 基于 [xPeiPeix/cimbar-bigfile](https://github.com/xPeiPeix/cimbar-bigfile) 的 standalone 版本
- 功能: 文件 -> zstd -> 分块 -> wirehair 喷泉编码 -> cimbar WASM 渲染
- 可调参数:
  - cimbar 模式: Bu(66) / Bm(67) / B(68)
  - 块大小: 64KB ~ 1MB
  - 帧率: 1~15 fps
  - 码图尺寸: 320~1024 px (CSS 缩放)
  - 冗余倍数: 1.0~3.0x
- 用户在内网部署了一份,不便于频繁修改

### 接收端 (Web 解码器)
- 文件: `cimbar/index.html`
- 部署: Mac 上通过 Python HTTPS server 运行在 `https://<mac-ip>:8443/`
- 功能:
  - 全屏摄像头画面 (`getUserMedia`, 后置摄像头)
  - `VideoFrame` API 抓帧 -> WASM `cimbard_scan_extract_decode` 解码
  - `cimbard_fountain_decode` 喷泉码收集
  - `cimbard_decompress_read` zstd 解压
  - 自动下载还原文件
  - 底部毛玻璃信息栏 (进度/帧数/速率/文件名)
  - 日志面板
  - **模式选择器**: Bu(66) / Bm(67) / B(68),必须和发送端一致
- **重要**: WASM 来自 libcimbar 官方 release `cimbar.wasm.tar.gz` (v0.6.7c),不需要自己编译

### HTTPS 服务启动方式
```bash
cd "/Users/mrx/Desktop/vibeCoding/GPT/文件二维码转换站/cimbar"
# 如果 IP 变了,重新生成证书
openssl req -x509 -newkey rsa:2048 -keyout key.pem -out cert.pem -days 365 -nodes \
  -subj "/CN=<当前IP>" -addext "subjectAltName=IP:<当前IP>"
# 启动服务
/tmp/qr-fs-venv/bin/python3 -u -c "
import http.server, ssl
httpd = http.server.HTTPServer(('0.0.0.0', 8443), http.server.SimpleHTTPRequestHandler)
ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
ctx.load_cert_chain('cert.pem', 'key.pem')
httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)
print('HTTPS running')
httpd.serve_forever()
"
```

## 三、已完成的工作

### 1. QR File Station v3 协议优化 (bridge/)
- zstd 压缩 + 二进制帧 + LT 喷泉码 + QR M级容错
- 26 个测试全部通过
- GitHub Actions 跨平台编译 (Linux/Windows) 成功
- 验证报告: `VERIFICATION_REPORT.md`

### 2. cimbar 解码器 (cimbar/)
- 使用作者官方 WASM 二进制 (不需要自己编译)
- 完整解码管线验证通过: 屏幕捕获 `部署方案.docx` (1MB) 成功解码下载
- 手机端布局优化完成 (iPhone Safari 适配)

### 3. cimbar 发送端优化
- `send-v2.html`: 添加了模式选择器 + 可调参数
- `feedAllBytes` 改为 async 防止大文件阻塞主线程
- GitHub Actions 尝试编译 WASM 解码器但失败 (OpenCV WASM header 路径问题)
  - 最终直接使用作者 release 的预编译 WASM,问题绕过

## 四、当前存在的问题

### 问题 1: 采集卡硬件瓶颈 (最高优先级)
- USB 采集卡 (MS2109) 的 MJPEG 硬件编码器无法处理 cimbar 的高频全屏色彩突变
- 现象: 发送端一显示码图,采集卡画面就冻结/黑屏,需拔插恢复
- **唯一稳定配置**: Bu 微型模式 (mode 66) + 320~480px 码图 + 低帧率 (3~5fps)
- Bm 和 B 模式下采集卡全部卡死
- **可能的解决方案**:
  1. 换无压缩 YUY2 采集卡 (USB 3.0, 绕开 MJPEG 硬件编码)
  2. 不用采集卡,直接手机摄像头对屏幕扫
  3. 用 cfc Android APP 接收

### 问题 2: 发送端码图尺寸与接收端识别的矛盾
- 码图小 (320px) -> 采集卡稳定,但接收端 tile 太密解不出来
- 码图大 (768px) -> 接收端能解,但采集卡卡死
- **需要找到两者的平衡点**, 或通过采集卡升级解决

### 问题 3: WASM 解码模式匹配
- 发送端切换模式后,接收端也必须手动切换到相同模式
- 目前已添加模式选择器,但用户需要记得切换
- **可改进**: 自动检测模式 (作者的 recv.html 通过模式轮换实现)

### 问题 4: GitHub Actions WASM 编译
- `.github/workflows/build-wasm-decoder.yml` 有 OpenCV WASM header 路径问题
- 目前用作者预编译 WASM 绕过,但如果需要修改 C++ 代码就必须修复
- 修复方向: Docker emscripten 容器内完整构建 (包括 clone OpenCV)

## 五、关键技术细节

### cimbar 模式参数
| 模式 | 值 | 网格 | 图片尺寸 | tile 数 | 每帧字节 | 适用 |
|------|---|------|----------|---------|---------|------|
| Bu 微型 | 66 | 80x69 | 736x637 | ~4032B | ~3000 | 采集卡兼容 |
| Bm 小型 | 67 | 112x78 | 1024x720 | ~5376B | ~5000 | 手机直拍 |
| B 标准 | 68 | 112x112 | 1024x1024 | ~7500B | ~7500 | 手机直拍(最佳) |

### WASM API (cimbar_js.js)
编码端 (cimbare_*):
- `_cimbare_configure(mode, ecc)` - 配置模式
- `_cimbare_init_encode(filename_ptr, len, encode_id)` - 初始化编码
- `_cimbare_encode(data_ptr, len)` - 喂入数据 (len=0 时 flush)
- `_cimbare_render()` - 渲染一帧到 canvas (WebGL)
- `_cimbare_next_frame(shakycam)` - 前进一帧
- `_cimbare_get_aspect_ratio()` - 获取码图宽高比

解码端 (cimbard_*):
- `_cimbard_configure_decode(mode)` - 配置解码模式
- `_cimbard_scan_extract_decode(img_ptr, w, h, format, fountain_ptr, fountain_size)` - 扫描+提取+解码
- `_cimbard_fountain_decode(data_ptr, size)` - 喷泉解码,返回 >0 表示文件完成
- `_cimbard_get_filesize(id)` - 获取压缩文件大小
- `_cimbard_get_filename(id, buf, bufsize)` - 获取文件名
- `_cimbard_decompress_read(id, buf, bufsize)` - zstd 解压读取
- `_cimbard_get_bufsize()` - 获取 fountain buffer 大小

### VideoFrame API
- Chrome 94+ / Safari 16.4+ 支持
- `new VideoFrame(video_element, {timestamp})` 从 video 元素抓帧
- `vf.format` 返回像素格式 (NV12, I420, RGBA 等)
- `vf.copyTo(buffer, {format})` 拷贝像素数据
- `vf.close()` 释放资源 (重要,不 close 会内存泄漏)

### 接收端帧处理流程
1. `requestVideoFrameCallback` 回调触发
2. `new VideoFrame(video)` 抓取当前帧
3. `vf.copyTo()` 拷贝到 Uint8Array
4. `Module.HEAPU8.set()` 复制到 WASM 内存
5. `_cimbard_scan_extract_decode()` 解码
6. 如果 len > 0: `_cimbard_fountain_decode()` 收集
7. 如果 fountain 返回 >0: 文件完成,解压+下载

## 六、用户环境和偏好

- Mac (Apple Silicon, macOS), IP 通常 `10.1.49.172` (会随网络变化)
- iPhone (iOS), 通过 Safari 访问 Mac 上的 HTTPS 服务
- Python 虚拟环境在 `/tmp/qr-fs-venv/`
- 用户倾向最小改动, 内网部署不便频繁更新
- 用户中文沟通, 界面也需要中文

## 七、下一步建议

1. **优先**: 建议用户购买无压缩 USB 3.0 采集卡 (如圆刚 CV710),彻底解决采集卡瓶颈
2. **短期**: 继续优化 Bu 微型模式的参数组合,找到采集卡和接收端的平衡点
3. **备选**: 推动手机直拍方案 (iPhone 摄像头 -> Mac 屏幕的 cimbar 码图),已验证可工作
4. **改进**: 接收端添加自动模式检测,不需要用户手动选模式
5. **工程**: 如果需要修改 C++ 解码逻辑,需修复 GitHub Actions 的 WASM 编译问题
