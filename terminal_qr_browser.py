#!/usr/bin/env python3
"""
终端二维码浏览器

直接在 Linux 终端中浏览文件转换后的二维码，支持：
  1. 从输入文件夹实时生成二维码内容并展示，不落地 PNG。
  2. 浏览 file_to_qr.py 已经生成的 PNG 二维码。
  3. 后台增量加载：界面立即打开，条目边扫描边出现，二维码显示时才渲染。
  4. 自动翻页：空格/a 开关，+/- 调节间隔。

兼容 Python 3.7+，TUI 使用 Linux 标准库 curses，不依赖 textual。
"""

import argparse
import curses
import json
import os
import threading
import time
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

DEFAULT_TERMINAL_CHUNK_SIZE = 160
DEFAULT_AUTO_INTERVAL = 3.0
MIN_AUTO_INTERVAL = 0.5
AUTO_INTERVAL_STEP = 0.5


class QrEntry:
    """二维码条目，矩阵在首次显示时才生成并缓存。"""

    def __init__(
        self,
        title: str,
        source: str,
        kind: str,
        module_width: int,
        payload: Optional[str] = None,
        image_path: Optional[Path] = None,
        max_modules: Optional[int] = None,
    ) -> None:
        self.title = title
        self.source = source
        self.kind = kind
        self.module_width = module_width
        self._payload = payload
        self._image_path = image_path
        self._max_modules = max_modules
        self._matrix: Optional[List[List[bool]]] = None

    @property
    def matrix(self) -> List[List[bool]]:
        if self._matrix is None:
            if self._payload is not None:
                self._matrix = qr_payload_to_matrix(self._payload)
            else:
                self._matrix = image_to_matrix(self._image_path, self._max_modules)
        return self._matrix


def missing_dependency() -> SystemExit:
    return SystemExit("缺少依赖，请先运行: pip install -r requirements.txt")


def qr_payload_to_matrix(payload: str) -> List[List[bool]]:
    try:
        import qrcode
    except ModuleNotFoundError as exc:
        raise missing_dependency() from exc

    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_H,
        box_size=10,
        border=4,
    )
    qr.add_data(payload)
    qr.make(fit=True)
    return qr.get_matrix()


def image_to_matrix(image_path: Path, max_modules: int) -> List[List[bool]]:
    try:
        from PIL import Image
    except ModuleNotFoundError as exc:
        raise missing_dependency() from exc

    with Image.open(str(image_path)) as image:
        if image.mode == "RGBA":
            background = Image.new("RGBA", image.size, "WHITE")
            background.alpha_composite(image)
            image = background.convert("L")
        else:
            image = image.convert("L")

        if image.width > max_modules:
            height = max(1, round(image.height * (max_modules / image.width)))
            image = image.resize((max_modules, height), Image.NEAREST)

        pixels = image.load()
        return [[pixels[x, y] < 128 for x in range(image.width)] for y in range(image.height)]


def iter_input_files(input_dir: Path) -> Iterable[Path]:
    for root, _, files in os.walk(str(input_dir)):
        for filename in sorted(files):
            if filename.startswith(".") or filename.endswith(".png"):
                continue
            yield Path(root) / filename


class EntryLoader:
    """后台线程扫描目录并生成条目，UI 可立即启动并增量读取。"""

    def __init__(self, args: argparse.Namespace) -> None:
        self._args = args
        self._entries: List[QrEntry] = []
        self._lock = threading.Lock()
        self._done = False
        self._error: Optional[str] = None
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def snapshot(self) -> Tuple[List[QrEntry], bool, Optional[str]]:
        with self._lock:
            return list(self._entries), self._done, self._error

    def _append(self, entry: QrEntry) -> None:
        with self._lock:
            self._entries.append(entry)

    def _run(self) -> None:
        try:
            for input_dir in self._args.input_dir:
                self._load_input_dir(input_dir)
            for image_dir in self._args.image_dir:
                self._load_image_dir(image_dir)
        except BaseException as exc:  # noqa: BLE001 - 错误信息透传给 UI
            with self._lock:
                self._error = str(exc) or repr(exc)
        finally:
            with self._lock:
                self._done = True

    def _load_input_dir(self, input_dir: Path) -> None:
        try:
            import file_to_qr
        except ModuleNotFoundError as exc:
            if exc.name == "file_to_qr":
                raise SystemExit("未找到 file_to_qr.py，请在项目根目录运行此脚本") from exc
            raise missing_dependency() from exc

        original_chunk_size = file_to_qr.MAX_CHUNK_SIZE
        try:
            file_to_qr.MAX_CHUNK_SIZE = self._args.chunk_size
            for file_path in iter_input_files(input_dir):
                chunks = file_to_qr.encode_file_to_chunks(str(file_path))
                for chunk in chunks:
                    payload = json.dumps(chunk, ensure_ascii=False)
                    title = "{} [{}/{}]".format(
                        chunk["filename"], chunk["index"] + 1, chunk["total"]
                    )
                    self._append(
                        QrEntry(
                            title=title,
                            source=str(file_path),
                            kind="generated",
                            module_width=self._args.module_width,
                            payload=payload,
                        )
                    )
        finally:
            file_to_qr.MAX_CHUNK_SIZE = original_chunk_size

    def _load_image_dir(self, image_dir: Path) -> None:
        max_modules = max(1, self._args.image_max_width // self._args.module_width)
        for image_path in sorted(image_dir.glob("*.png")):
            self._append(
                QrEntry(
                    title=image_path.name,
                    source=str(image_path),
                    kind="png",
                    module_width=self._args.module_width,
                    image_path=image_path,
                    max_modules=max_modules,
                )
            )


def matrix_to_cells(matrix: List[List[bool]], module_width: int) -> List[List[Tuple[str, int]]]:
    cells = []
    block = " " * module_width

    for source_row in matrix:
        line = []
        for black in source_row:
            line.append((block, 1 if black else 3))
        cells.append(line)

    return cells


def add_text(window, y: int, x: int, text: str, attr: int = 0) -> None:
    height, width = window.getmaxyx()
    if y < 0 or y >= height or x >= width:
        return
    text = text[: max(0, width - x - 1)]
    if text:
        window.addstr(y, x, text, attr)


def draw_entry_list(window, entries: List[QrEntry], selected: int, list_width: int, loading: bool) -> None:
    height, _ = window.getmaxyx()
    header = "二维码列表 ({})".format(len(entries))
    if loading:
        header += " 加载中..."
    add_text(window, 0, 0, header, curses.A_BOLD)

    visible_rows = max(1, height - 4)
    start = max(0, selected - visible_rows + 1)
    if selected < start:
        start = selected

    for row, entry_index in enumerate(range(start, min(len(entries), start + visible_rows)), start=2):
        entry = entries[entry_index]
        label = "{}. {}".format(entry_index + 1, entry.title)
        attr = curses.A_REVERSE if entry_index == selected else 0
        add_text(window, row, 0, label[: list_width - 2], attr)


def draw_help_line(window, auto_play: bool, auto_interval: float) -> None:
    height, _ = window.getmaxyx()
    auto_state = "开 {:.1f}s".format(auto_interval) if auto_play else "关"
    help_text = "↑/↓/j/k 选择 | n/p 翻页 | 空格/a 自动翻页[{}] | +/- 调速 | q 退出".format(auto_state)
    add_text(window, height - 1, 0, help_text, curses.A_BOLD)


def draw_qr(window, entry: QrEntry, selected: int, total: int, list_width: int) -> None:
    height, width = window.getmaxyx()
    qr_x = list_width + 2
    qr_y = 3
    right_width = max(1, width - qr_x - 1)

    add_text(window, 0, qr_x, entry.title, curses.A_BOLD)
    add_text(window, 1, qr_x, "{}/{} | {} | {}".format(selected + 1, total, entry.kind, entry.source))

    cells = matrix_to_cells(entry.matrix, entry.module_width)
    max_rows = max(0, height - qr_y - 2)
    max_cells = max(1, right_width // entry.module_width)

    if len(cells) > max_rows or (cells and len(cells[0]) > max_cells):
        add_text(window, 2, qr_x, "提示: 二维码较大，可调小 --chunk-size 或 --module-width，或放大终端。", curses.A_BOLD)

    for row_index, line in enumerate(cells[:max_rows]):
        x = qr_x
        for text, color_pair in line[:max_cells]:
            window.addstr(qr_y + row_index, x, text, curses.color_pair(color_pair))
            x += len(text)


def run_browser(loader: EntryLoader, auto_interval: float) -> None:
    def app(stdscr) -> None:
        curses.curs_set(0)
        curses.start_color()
        curses.use_default_colors()
        curses.init_pair(1, curses.COLOR_BLACK, curses.COLOR_BLACK)
        curses.init_pair(2, curses.COLOR_BLACK, curses.COLOR_WHITE)
        curses.init_pair(3, curses.COLOR_WHITE, curses.COLOR_WHITE)
        stdscr.timeout(100)

        selected = 0
        auto_play = False
        interval = auto_interval
        last_flip = time.monotonic()

        while True:
            entries, done, error = loader.snapshot()

            if error:
                raise SystemExit(error)
            if done and not entries:
                raise SystemExit("没有找到可展示的二维码")

            stdscr.erase()
            height, width = stdscr.getmaxyx()
            list_width = min(40, max(20, width // 3))

            for y in range(height - 1):
                add_text(stdscr, y, list_width, "|")

            if entries:
                selected = min(selected, len(entries) - 1)
                draw_entry_list(stdscr, entries, selected, list_width, not done)
                draw_qr(stdscr, entries[selected], selected, len(entries), list_width)
            else:
                add_text(stdscr, 0, 0, "二维码列表 加载中...", curses.A_BOLD)
                add_text(stdscr, 2, list_width + 2, "正在扫描并编码文件，请稍候...", curses.A_BOLD)
            draw_help_line(stdscr, auto_play, interval)
            stdscr.refresh()

            key = stdscr.getch()
            if key in (ord("q"), ord("Q")):
                break
            if not entries:
                continue

            if key in (curses.KEY_DOWN, ord("j"), ord("n")):
                selected = (selected + 1) % len(entries)
                last_flip = time.monotonic()
            elif key in (curses.KEY_UP, ord("k"), ord("p")):
                selected = (selected - 1) % len(entries)
                last_flip = time.monotonic()
            elif key in (ord(" "), ord("a"), ord("A")):
                auto_play = not auto_play
                last_flip = time.monotonic()
            elif key in (ord("+"), ord("=")):
                interval = max(MIN_AUTO_INTERVAL, interval - AUTO_INTERVAL_STEP)
            elif key in (ord("-"), ord("_")):
                interval += AUTO_INTERVAL_STEP

            if auto_play and time.monotonic() - last_flip >= interval:
                selected = (selected + 1) % len(entries)
                last_flip = time.monotonic()

    curses.wrapper(app)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="在终端中浏览文件转换二维码")
    parser.add_argument("--input-dir", type=Path, action="append", default=[], help="输入文件夹，直接生成并展示二维码")
    parser.add_argument("--image-dir", type=Path, action="append", default=[], help="已生成 PNG 二维码的目录")
    parser.add_argument(
        "--image-max-width",
        type=int,
        default=120,
        help="PNG 展示时占用的最大终端列数，默认 120",
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=DEFAULT_TERMINAL_CHUNK_SIZE,
        help="--input-dir 每个二维码承载的 Base64 字符数，越小二维码越窄，默认 160",
    )
    parser.add_argument(
        "--module-width",
        type=int,
        choices=[1, 2],
        default=2,
        help="每个二维码模块占用的终端列数，2 更接近方形，1 更窄，默认 2",
    )
    parser.add_argument(
        "--auto-interval",
        type=float,
        default=DEFAULT_AUTO_INTERVAL,
        help="自动翻页间隔秒数（运行中可用 +/- 调节），默认 3.0",
    )
    return parser.parse_args()


def validate_args(args: argparse.Namespace) -> None:
    if not args.input_dir and not args.image_dir:
        raise SystemExit("请提供 --input-dir 或 --image-dir")

    for directory in args.input_dir + args.image_dir:
        if not directory.is_dir():
            raise SystemExit("目录不存在: {}".format(directory))
    if args.chunk_size <= 0:
        raise SystemExit("--chunk-size 必须大于 0")
    if args.image_max_width <= 0:
        raise SystemExit("--image-max-width 必须大于 0")
    if args.auto_interval < MIN_AUTO_INTERVAL:
        raise SystemExit("--auto-interval 不能小于 {}".format(MIN_AUTO_INTERVAL))


def main() -> None:
    args = parse_args()
    validate_args(args)

    loader = EntryLoader(args)
    loader.start()
    run_browser(loader, args.auto_interval)


if __name__ == "__main__":
    main()
