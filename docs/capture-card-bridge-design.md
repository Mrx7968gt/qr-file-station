# 采集卡桥接传输方案 · 详细设计文档

> 通过 HDMI 采集卡,把 **Windows 电脑**上的文件单向、离线、全自动地导出到 **Mac**。
> (Windows 上的文件本身来自 Linux,经 SSH/SFTP 已可达 Windows,该段不在本方案范围。)
>
> 状态:设计稿(v2,拓扑已纠正) · 作者:ZCode 协作 · 日期:2026-06-30

---

## 0. 版本说明

- **v1(已废弃)**:误以为发送端是 Linux console、采集卡接 Linux HDMI,引入了 DRM/KMS 全屏方案。
- **v2(当前)**:纠正拓扑——发送端是 **Windows(有桌面 GUI)**,接收端是 **Mac**,采集卡是 Windows→Mac 的唯一通道。砍掉 DRM/KMS,方案大幅简化、可靠性大幅提升。

---

## 1. 真实拓扑与气隙定位

```
Linux 机器
  │ SSH / SFTP        ← 已通,文件能到 Windows(不在本方案范围)
  ▼
Windows 电脑           ← 发送端:文件已在此,且有桌面 GUI
  │ HDMI 视频输出      ← 到 Mac 的【唯一】通道,反向无数据通路
  ▼
采集卡 (HDMI → USB)    ← Mac 看到就是一个普通 USB 摄像头
  │
  ▼
Mac 电脑               ← 接收端:你坐在这,OpenCV 抓帧解码
```

### 1.1 气隙在哪

气隙在 **Windows ↔ Mac** 这一段:两机之间无网络、无 USB 数据通道,**唯一连着的是采集卡这条单向视频线**(HDMI 出,USB 进,无反向)。

- **Linux → Windows**:SSH/SFTP 已解决,**本方案不再处理**。
- **Windows → Mac**:**本方案的核心**,只能走采集卡视频。✅ 采集卡 + 二维码在此是正解。

### 1.2 为什么采集卡是必需的(不是冗余)

Mac 拿不到 Windows 的任何数字通道(无网、无 USB),只能通过采集卡"看"Windows 的屏幕。要把文件字节塞进这条单向视频通路,**二维码视频流是标准做法**。

### 1.3 为什么这个工况特别好

采集卡是 HDMI→USB 的**纯数字视频透传**,不是摄像头拍屏幕:

- ❌ 无镜头 → 无对焦问题
- ❌ 无光学路径 → 无反光、眩光、环境光干扰
- ❌ 无几何畸变
- ✅ 像素级干净的视频帧 → pyzbar 解码率极高

这是用 QR 视频流传数据的**理想工况**。

---

## 2. 目标与非目标

| 目标 | 指标 |
|------|------|
| 全自动 | Mac 端启动后自动抓帧、解码、补缺、落盘 |
| 单向气隙 | Windows→Mac 仅 HDMI 视频,零网络零 USB 数据 |
| 量级 | 稳定 1~50MB |
| 可靠 | 多轮循环 + 可选 Reed-Solomon FEC |
| 复用现有 | `file_to_qr.py` 编码协议、`FileAssembler` 拼装逻辑沿用 |

非目标:双向确认(违背单向性)、Linux→Windows 段(已解决)、>50MB 高密度彩色码(未来优化)。

---

## 3. 整体架构

```
[文件已在 Windows]
      │
      │  file_to_qr.py(现有,纯 Python,Windows 可直接跑)
      ▼
[base64 分块 + 元数据]  ──(可选)Reed-Solomon 冗余──┐
      │                                              │
      │  sender/player.py(新增,Windows GUI)        │
      ▼                                              │
[pygame 全屏窗口,逐帧 QR 循环播放]                  │
      │ HDMI                                         │
      ▼                                              │
采集卡 ──USB──▶ Mac                                  │
      │                                              │
      │  receiver/capture.py(新增,Mac)             │
      ▼                                              │
[OpenCV 抓帧 → pyzbar 解码 → JSON 解析]             │
      │                                              │
      ├─ checksum 校验 ── 按 sid+index 去重 ─────────┘
      ▼
[收齐 total 块 → (可选)FEC 恢复 → 拼装 → base64 解码 → 落盘]
```

### 3.1 两端运行环境

| 端 | 机器 | 运行内容 | 依赖 |
|----|------|----------|------|
| 发送端 | **Windows** | `file_to_qr.py` 生成 PNG(或实时编码)+ `player.py` 全屏播放 | pygame、qrcode、Pillow |
| 接收端 | **Mac** | `capture.py` 抓采集卡帧 + 解码拼装 | opencv-python、pyzbar |

---

## 4. 发送端设计(Windows)

### 4.1 渲染方式:pygame 全屏窗口

Windows 有桌面 GUI,直接用 pygame 创建**无边框全屏窗口**,逐帧 blit QR 图像并按固定 FPS 翻页。无需 DRM/KMS、无需终端字符画。

```python
# bridge/sender/player.py(核心骨架)
def play(png_dir, fps=12, loops=3):
    pygame.init()
    screen = pygame.display.set_mode((0, 0), pygame.FULLSCREEN)
    imgs = [pygame.image.load(p) for p in sorted(glob(f"{png_dir}/*.png"))]
    for rnd in range(loops):
        show_sentinel(screen, "START"); pygame.time.wait(int(3000/fps))
        for img in imgs:
            screen.fill((255,255,255)); screen.blit(fit(img)); pygame.display.flip()
            pygame.time.wait(int(1000/fps))
        show_sentinel(screen, "END"); pygame.time.wait(int(3000/fps))
```

- **FPS**:建议 8~15。采集卡通常 30/60fps,发送 FPS 取其 1/3~1/2,避免采集端跨帧混叠。
- **全屏**:确保 QR 占满屏幕,采集卡拿到的是纯净的码,没有任务栏/其他窗口干扰。
- **白底**:QR 周围留白(border)充足,利于解码定位。

### 4.2 编码:直接复用 `file_to_qr.py`

`file_to_qr.py` 是纯 Python(qrcode + Pillow),**Windows 上直接 `python file_to_qr.py ./files ./qr_out` 即可生成 PNG**。只需小幅增强元数据(见 §5)。

> 也可做成"实时编码不落地 PNG":player 直接对每个 chunk 生成 QR 矩阵 blit,省磁盘 IO。两种都支持,优先落地 PNG(便于核对)。

---

## 5. 帧协议(向后兼容增强)

### 5.1 单帧 JSON 载荷

```json
{
  "v": 2,                       // 协议版本
  "sid": "a1b2c3",              // 会话 ID
  "type": "data",               // "start"|"data"|"end"
  "filename": "report.zip",
  "size": 2345678,
  "index": 12,
  "total": 320,
  "fec": "rs:K=200,R=20",       // FEC 元信息(可空)
  "data": "<base64 片段 ~384B>",
  "checksum": 3778912
}
```

> v1 旧接收端缺省新字段仍可工作;v2 接收端用 sid 区分会话、type 定位边界。

### 5.2 哨兵帧

- **START**:`{v:2,sid,type:"start",files:[...],totalChunks:N}` —— Mac 见此清空旧缓冲。
- **END**:`{v:2,sid,type:"end"}` —— Mac 检查是否收齐。

---

## 6. 接收端设计(Mac)

### 6.1 抓帧:OpenCV 打开采集卡

采集卡在 Mac 上枚举为摄像头。`cv2.VideoCapture(index)` 打开,index 通常是 1(0 常常是 Facetime 摄像头)。

```python
# bridge/receiver/capture.py
cap = cv2.VideoCapture(args.device)   # device=1 多为采集卡
while not done:
    ok, frame = cap.read()
    if not ok: continue
    for obj in pyzbar.decode(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)):
        handle(json.loads(obj.data.decode()))
```

### 6.2 解码与拼装:移植自 `FileAssembler.tsx`

算法与现有前端 `FileAssembler.tsx` 完全一致,改写为 Python:

```python
buffer = {}  # index -> data
while len(buffer) < total:
    frame = grab()
    for obj in pyzbar.decode(frame):
        m = json.loads(obj.data.decode())
        if m["sid"] != current_sid: continue
        if crc32(m["data"]) != m["checksum"]: continue   # 坏块丢弃
        buffer.setdefault(m["index"], m["data"])         # 重复即丢
# (可选 FEC) reedsolo 恢复
assemble(buffer)  # 按 index 排序拼接 → base64 解码 → 落盘
```

### 6.3 采集卡设备号探测

启动时枚举所有摄像头,打印分辨率,辅助用户选对 index:

```python
for i in range(5):
    c = cv2.VideoCapture(i); print(i, c.get(cv2.CAP_PROP_FRAME_WIDTH), ...)
```

---

## 7. 可靠性策略

### 7.1 第一层:多轮循环(默认)

发送端循环 `N=3` 轮,接收端按 `index` 去重。采集卡数字通路单轮丢帧率极低(<0.5%),3 轮后未收齐概率≈0。

### 7.2 第二层:Reed-Solomon FEC(可选,>10MB 启用)

- 每 `K=200` 数据块加 `R=20` 冗余块(reedsolo),任收齐 K 块即可恢复全部。
- 冗余 ~10%,换取突发丢帧可恢复、减少循环轮数。
- 小文件不启用,循环重发足矣。

### 7.3 单向无握手

全程无反向确认,靠冗余兜底。接收端收齐自动停止该会话、落盘、继续等下一个。

---

## 8. 目录结构(新增)

```
文件二维码转换站/
├── bridge/                         # ★ 新增:采集卡桥接
│   ├── sender/
│   │   ├── player.py               # Windows 全屏播放器(pygame)
│   │   └── protocol.py             # 帧封装(start/data/end + sid)
│   ├── receiver/
│   │   ├── capture.py              # Mac 抓采集卡帧
│   │   ├── decoder.py              # pyzbar 解码 + JSON
│   │   └── assembler.py            # 去重/校验/拼装(移植自 FileAssembler)
│   ├── fec/rs_codec.py             # Reed-Solomon 封装(可选)
│   ├── common/protocol.py          # 共享帧结构
│   └── tests/
│       ├── test_loopback.py        # 本机自环(不接采集卡)
│       └── test_fec.py
├── file_to_qr.py                   # 现有,微调输出 v2 元数据
├── docs/capture-card-bridge-design.md  # 本文
```

---

## 9. 接口定义

### 9.1 发送端

```python
player.play(
    png_dir: str = "./qr_out",   # file_to_qr.py 产物
    fps: int = 12,
    loops: int = 3,
    screen: int = 0,             # 多显示器时选 HDMI 输出屏
)
```

### 9.2 接收端

```python
capture.receive(
    device: int = 1,             # 采集卡在 Mac 上的摄像头 index
    out_dir: str = "./recv",
    sid_filter: str | None = None,
)
```

---

## 10. 部署与依赖

### 10.1 Windows 发送端

```powershell
pip install pygame qrcode pillow reedsolo
python file_to_qr.py .\files .\qr_out      # 生成二维码
python -m bridge.sender.player .\qr_out    # 全屏播放
```

### 10.2 Mac 接收端

```bash
pip install opencv-python pyzbar reedsolo
# pyzbar 需 zbar 库: brew install zbar
python -m bridge.receiver.capture --device 1 --out-dir ./recv
```

### 10.3 打包

- Windows 端 PyInstaller `--onefile`,内含 pygame。
- Mac 端 PyInstaller `--onefile`,注意 pyzbar 需带 `libzbar.dylib`(`brew install zbar` 后 `--add-data`)。

---

## 11. 调参清单

| 参数 | 默认 | 调节 |
|------|------|------|
| 发送 fps | 12 | 解码失败→降到 8;采集卡 60fps→可升 15 |
| 单块字节 | 384 | 码太密解不出→200;太稀→500 |
| 循环轮数 | 3 | 总丢块>0→加到 5;启用 FEC 后可降到 2 |
| 输出分辨率 | 1920×1080 | 采集卡支持 1080p 最佳 |

---

## 12. 测试计划

| 用例 | 方法 | 预期 |
|------|------|------|
| 本机自环 | 不接采集卡,player→assembler 直接内存流转 | 100% 还原 |
| 协议自检 | encode→封装→解封装→拼装 | 校验通过 |
| FEC 单元 | 随机丢 ≤R 块,验证恢复 | 文件一致 |
| 真机小文件 | 1KB 文本,Windows 全屏→采集卡→Mac | 收齐落盘 |
| 真机中文件 | 10MB zip,启用 FEC | 完整解压校验 |

---

## 13. 实施里程碑

1. **M1 协议与公共层**(`common/protocol.py` + `file_to_qr.py` v2 元数据)— 0.5 天,纯逻辑带单测
2. **M2 接收端**(`capture.py`+`decoder.py`+`assembler.py`)— 1.5 天,可先用录屏代替采集卡开发
3. **M3 发送端**(`player.py` pygame 全屏)— 1 天,Windows 上调试
4. **M4 FEC**(可选)— 1 天
5. **M5 真机联调 + 打包** — 1 天

---

## 14. 与现有项目的关系

| 现有组件 | 新方案角色 |
|----------|-----------|
| `file_to_qr.py` | **直接复用**,Windows 上跑,微调 v2 元数据 |
| 前端 `FileAssembler.tsx` | **移植为 Python** `assembler.py`,算法不变 |
| 前端 `html5-qrcode` | 不再用,改为 OpenCV+pyzbar |
| `terminal_qr_browser.py` | 保留作无 GUI 兜底(Windows 用不到) |
| `build_*_linux_x86_64.sh` | 新增 Windows/Mac 打包脚本 |

---

## 附录:吞吐量估算

- 384B/帧,12fps × ~288B 有效 ≈ **3.4 KB/s ≈ 200 KB/分钟**。
- 10MB ≈ 50 分钟(3 轮);启用 FEC + chunk-size=500 可降到 ~30 分钟。
- 50MB 建议挂机或分批。这是 QR 视频流方案的天花板。
