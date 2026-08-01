#!/usr/bin/env python3
"""
bridge.sender.player - pygame fullscreen playback engine (v3)

v3 changes:
  - Default FPS: 12 -> 30
  - QR error correction: H (30%) -> M (15%)
  - Grid layout: display NxN QR codes simultaneously
  - Binary frame payloads (bytes)
"""

from __future__ import annotations

import io
import os
import sys
import threading
import time
from typing import Callable, List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

import qrcode
from bridge.sender.builder import BuildResult

QR_EC = qrcode.constants.ERROR_CORRECT_M

pygame = None


def _ensure_pygame():
    global pygame
    if pygame is None:
        import pygame as _pg
        pygame = _pg
    return pygame


def _payload_to_surface(payload, box=10, border=4):
    _ensure_pygame()
    qr = qrcode.QRCode(
        version=None, error_correction=QR_EC,
        box_size=box, border=border,
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
    fps: int = 30,
    loops: int = 1,
    display: int = 0,
    box: int = 10,
    border: int = 4,
    grid_size: int = 2,
    on_progress: Optional[Callable] = None,
    stop_event: Optional[threading.Event] = None,
    headless: bool = False,
) -> bool:
    """
    Fullscreen playback of QR frame sequence.

    grid_size: NxN grid of QR codes per screen (1=single, 2=2x2, 3=3x3).
    With fountain codes, loops can be 1 (each frame is unique).
    """
    frames_per_screen = grid_size * grid_size
    frame_ms = int(1000 / fps) if fps > 0 else 100
    total = len(result.frames)

    if headless:
        for rnd in range(loops):
            for i in range(total):
                if stop_event and stop_event.is_set():
                    return False
                if on_progress:
                    on_progress(rnd, i, total)
                time.sleep(frame_ms / 1000.0)
        return True

    _ensure_pygame()
    pygame.init()
    try:
        info = pygame.display.Info()
        print(f"[sender] display: {info.current_w}x{info.current_h}")
    except pygame.error:
        pass
    print(f"[sender] sid={result.sid} files={result.file_count} "
          f"blocks={result.total_data_chunks} frames={total} "
          f"fps={fps} grid={grid_size}x{grid_size} loops={loops}")

    # Pre-render QR surfaces
    print("[sender] pre-rendering QR codes...")
    surfaces: List = []
    for idx, payload in enumerate(result.frames):
        if stop_event and stop_event.is_set():
            pygame.quit()
            return False
        surfaces.append(_payload_to_surface(payload, box, border))
        if (idx + 1) % 20 == 0:
            print(f"  rendered {idx + 1}/{total}")

    flags = pygame.FULLSCREEN
    try:
        screen = pygame.display.set_mode((0, 0), flags, display=display)
    except TypeError:
        screen = pygame.display.set_mode((0, 0), flags)

    sw, sh = screen.get_size()
    clock = pygame.time.Clock()
    running = True
    completed = False

    def blit_grid(group):
        screen.fill((255, 255, 255))
        n = grid_size
        gap = 10
        cell_w = (sw - gap * (n + 1)) // n
        cell_h = (sh - gap * (n + 1)) // n
        for idx, surf in enumerate(group):
            if surf is None:
                continue
            row = idx // n
            col = idx % n
            qw, qh = surf.get_size()
            scale = min(cell_w / qw, cell_h / qh)
            if scale < 1.0:
                surf = pygame.transform.smoothscale(
                    surf, (int(qw * scale), int(qh * scale))
                )
                qw, qh = surf.get_size()
            cx = gap + col * (cell_w + gap) + (cell_w - qw) // 2
            cy = gap + row * (cell_h + gap) + (cell_h - qh) // 2
            screen.blit(surf, (cx, cy))
        pygame.display.flip()

    for rnd in range(loops):
        if not running:
            break
        i = 0
        while i < total:
            if stop_event and stop_event.is_set():
                running = False
                break
            for ev in pygame.event.get():
                if ev.type == pygame.QUIT:
                    running = False
                elif ev.type == pygame.KEYDOWN:
                    if ev.key in (pygame.K_ESCAPE, pygame.K_q):
                        running = False
            if not running:
                break

            group = surfaces[i:i + frames_per_screen]
            while len(group) < frames_per_screen:
                group.append(None)
            blit_grid(group)

            if on_progress:
                on_progress(rnd, i, total)
            clock.tick(fps)
            i += frames_per_screen

        if running:
            print(f"[sender] round {rnd + 1}/{loops} done")
    else:
        completed = True

    if completed:
        screen.fill((0, 80, 0))
        font = pygame.font.SysFont(None, 64)
        msg = font.render("Transmission Complete", True, (255, 255, 255))
        screen.blit(msg, msg.get_rect(center=(sw // 2, sh // 2)))
        pygame.display.flip()
        for _ in range(300):
            for ev in pygame.event.get():
                if ev.type in (pygame.QUIT, pygame.KEYDOWN):
                    break
            clock.tick(30)

    pygame.quit()
    return completed
