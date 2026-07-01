#!/usr/bin/env python3
"""
bridge.sender.player — pygame 全屏循环播放引擎

职责:
    接收 BuildResult 的帧 JSON 列表,生成 QR Surface,
    全屏循环播放 N 轮,固定 FPS 翻页。

设计:
    - 与 GUI 解耦:本模块只管"渲染+节拍",不管文件选择/参数输入。
    - GUI 在后台线程调用 play();CLI 直接调用。
    - 支持 stop_event 提前中断、on_progress 回调报进度。
    - 参考现有 verify_sender.py 的全屏渲染方式 + terminal_qr_browser 的循环节拍。
"""

from __future__ import annotations

import io
import os
import sys
import threading
import time
from typing import Callable, List, Optional

# 让作为模块或脚本运行都能 import
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

import qrcode

from bridge.sender.builder import BuildResult

# pygame 延迟导入:headless 测试和接收端环境无需安装 pygame。
# 只有真正调用 play(headless=False) 开窗口渲染时才需要。
pygame = None


def _ensure_pygame():
    """按需导入 pygame。headless 模式下不会调用本函数。"""
    global pygame
    if pygame is None:
        import pygame as _pg  # noqa: F401
        pygame = _pg
    return pygame


def _payload_to_surface(payload: str, box: int = 10, border: int = 4):
    """帧 JSON → QR pygame Surface(需要 pygame,非 headless 用)。"""
    _ensure_pygame()
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_H,
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


def play(
    result: BuildResult,
    fps: int = 12,
    loops: int = 3,
    display: int = 0,
    box: int = 10,
    border: int = 4,
    on_progress: Optional[Callable[[int, int, int], None]] = None,
    stop_event: Optional[threading.Event] = None,
    headless: bool = False,
) -> bool:
    """
    全屏循环播放 QR 帧序列。

    Args:
        result: builder.build() 的产物
        fps: 帧率(每秒翻页数)
        loops: 循环轮数
        display: 渲染到第几块显示器(双屏扩展时)
        box/border: QR 渲染参数
        on_progress: 回调(round, frame_idx, total_frames)
        stop_event: 外部可设置以提前停止
        headless: True 时不真正开窗口(测试/无显示环境用)

    Returns:
        是否完整播完(True)或被中断(False)
    """
    frame_ms = int(1000 / fps) if fps > 0 else 100

    if headless:
        # 无显示模式:不初始化 pygame 显示,只按节拍空转(测试用)
        total = len(result.frames)
        for rnd in range(loops):
            for i in range(total):
                if stop_event and stop_event.is_set():
                    return False
                if on_progress:
                    on_progress(rnd, i, total)
                time.sleep(frame_ms / 1000.0)
        return True

    pygame.init()
    try:
        info = pygame.display.Info()
        print(f"[发送端] 显示器分辨率: {info.current_w}x{info.current_h}")
    except pygame.error:
        pass
    print(f"[发送端] 会话 sid={result.sid}  文件 {result.file_count} 个  "
          f"数据块 {result.total_data_chunks}  帧 {len(result.frames)}  "
          f"fps={fps} 轮={loops}")

    # 预渲染所有 QR Surface(避免播放时卡顿)
    print("[发送端] 预渲染二维码…")
    surfaces: List[pygame.Surface] = []
    for idx, payload in enumerate(result.frames):
        if stop_event and stop_event.is_set():
            pygame.quit()
            return False
        surfaces.append(_payload_to_surface(payload, box, border))
        if (idx + 1) % 20 == 0:
            print(f"  渲染 {idx + 1}/{len(result.frames)}")

    flags = pygame.FULLSCREEN
    try:
        screen = pygame.display.set_mode((0, 0), flags, display=display)
    except TypeError:
        screen = pygame.display.set_mode((0, 0), flags)

    sw, sh = screen.get_size()

    def blit_centered(surf: pygame.Surface) -> None:
        screen.fill((255, 255, 255))
        qw, qh = surf.get_size()
        scale = min(sw / qw, sh / qh)
        if scale < 1.0:
            surf = pygame.transform.smoothscale(
                surf, (int(qw * scale), int(qh * scale))
            )
            qw, qh = surf.get_size()
        rect = surf.get_rect(center=(sw // 2, sh // 2))
        screen.blit(surf, rect)
        pygame.display.flip()

    clock = pygame.time.Clock()
    total = len(surfaces)
    running = True
    completed = False

    for rnd in range(loops):
        if not running:
            break
        for i in range(total):
            if stop_event and stop_event.is_set():
                running = False
                break
            # 处理退出事件
            for ev in pygame.event.get():
                if ev.type == pygame.QUIT:
                    running = False
                elif ev.type == pygame.KEYDOWN:
                    if ev.key in (pygame.K_ESCAPE, pygame.K_q):
                        running = False
            if not running:
                break

            blit_centered(surfaces[i])
            if on_progress:
                on_progress(rnd, i, total)
            clock.tick(fps)

        if running:
            print(f"[发送端] 第 {rnd + 1}/{loops} 轮完成")
    else:
        completed = True

    # 播完显示完成画面
    if completed:
        screen.fill((0, 80, 0))
        font = pygame.font.SysFont(None, 64)
        msg = font.render("Transmission Complete", True, (255, 255, 255))
        screen.blit(msg, msg.get_rect(center=(sw // 2, sh // 2)))
        pygame.display.flip()
        # 等几秒让用户看到,或按键退出
        for _ in range(300):
            for ev in pygame.event.get():
                if ev.type in (pygame.QUIT, pygame.KEYDOWN):
                    break
            clock.tick(30)

    pygame.quit()
    return completed
