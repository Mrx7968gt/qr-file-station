#!/usr/bin/env python3
"""
采集卡链路 · 最小验证脚本(发送端 / Windows 端)

用途:全屏显示一张固定二维码,用来验证「Windows 屏幕 → 采集卡 → Mac」这条
     单向视频链路能不能被接收端稳定读出。

使用(Windows 上):
    pip install pygame qrcode pillow
    python verify_sender.py
    # 按 ESC / Q 退出,按 F 切换窗口/全屏

注意:
    - 双屏扩展时,可加 --display 1 把画面送到接采集卡的那块屏。
    - 本脚本只为验证链路;真正传输用 bridge/sender/player.py。
"""

import argparse
import io
import sys

import pygame
import qrcode

# 固定测试载荷 —— 接收端读到这串就说明链路通。
# 刻意做长一点、带中文和特殊字符,贴近真实数据。
TEST_PAYLOAD = (
    "CAPTURE_LINK_PROBE::sid=verify-0001::"
    "filename=hello.txt::size=42::index=0::total=1::"
    "data=SGVsbG8sIOS4lueVjOe8kOe7n+eUqCBjYXB0dXJlLWNhcmQgdHJhbnNmZXIu::"
    "checksum=probe"
)


def make_qr_surface(payload: str, box: int = 12, border: int = 6) -> pygame.Surface:
    """生成一张高容错 QR 码,返回带充足白边的 pygame Surface。"""
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_H,  # 最高容错 30%
        box_size=box,
        border=border,
    )
    qr.add_data(payload)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white").convert("RGB")

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return pygame.image.load(buf).convert()


def main() -> None:
    ap = argparse.ArgumentParser(description="采集卡链路验证 · 发送端")
    ap.add_argument("--display", type=int, default=0,
                    help="渲染到第几块显示器(双屏扩展时把接采集卡的那块设为 1)")
    ap.add_argument("--box", type=int, default=12, help="QR 模块像素大小")
    args = ap.parse_args()

    pygame.init()
    # 先获取显示器信息,便于排错
    display_count = pygame.display.get_init()
    try:
        info = pygame.display.Info()
        print(f"[发送端] 显示器当前分辨率: {info.current_w}x{info.current_h}")
    except pygame.error as e:
        print(f"[发送端] 无法读取分辨率: {e}", file=sys.stderr)
    print(f"[发送端] 目标 display={args.display};"
          f" 多屏时若画面没到采集卡,请改 --display")
    print(f"[发送端] 载荷长度 {len(TEST_PAYLOAD)} 字节,容错率 H(30%)")
    print(f"[发送端] 按 ESC/Q 退出, F 切换全屏")

    flags = pygame.FULLSCREEN
    try:
        screen = pygame.display.set_mode(
            (0, 0), flags, display=args.display
        )
    except TypeError:
        # 旧版 pygame 的 set_mode 不支持 display 关键字
        screen = pygame.display.set_mode((0, 0), flags)

    screen.fill((255, 255, 255))
    qr = make_qr_surface(TEST_PAYLOAD, box=args.box)

    # 等比放大,尽量铺满屏幕(高度优先,保证不超出)
    sw, sh = screen.get_size()
    qw, qh = qr.get_size()
    scale = min(sw / qw, sh / qh)
    qr_scaled = pygame.transform.smoothscale(
        qr, (int(qw * scale), int(qh * scale))
    )

    rect = qr_scaled.get_rect(center=(sw // 2, sh // 2))
    screen.blit(qr_scaled, rect)
    pygame.display.flip()

    clock = pygame.time.Clock()
    running = True
    while running:
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                running = False
            elif ev.type == pygame.KEYDOWN:
                if ev.key in (pygame.K_ESCAPE, pygame.K_q):
                    running = False
                elif ev.key == pygame.K_f:
                    flags ^= pygame.FULLSCREEN
                    screen = pygame.display.set_mode((0, 0), flags,
                                                     display=args.display)
                    screen.fill((255, 255, 255))
                    screen.blit(qr_scaled, rect)
                    pygame.display.flip()
        clock.tick(30)

    pygame.quit()


if __name__ == "__main__":
    main()
