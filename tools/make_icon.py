"""由 island/branding.py 的同一套绘制生成 assets/xiaoer.ico 和尺寸预览。

改图标只改 branding.py，然后重跑本脚本；打包时 --icon assets/xiaoer.ico。
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QRectF
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from island.branding import render_icon  # noqa: E402

ASSETS = ROOT / "assets"
ICO_SIZES = (16, 20, 24, 32, 48, 64, 128, 256)


def _png_bytes(size: int) -> bytes:
    buf = QByteArray()
    dev = QBuffer(buf)
    dev.open(QIODevice.WriteOnly)
    render_icon(size).save(dev, "PNG")
    dev.close()
    return bytes(buf)


def save_ico(path: Path) -> None:
    """PNG 负载的 ICO（Windows Vista+ 与 PyInstaller 均可读）。"""
    payloads = [(s, _png_bytes(s)) for s in ICO_SIZES]
    header = struct.pack("<HHH", 0, 1, len(payloads))
    offset = 6 + 16 * len(payloads)
    entries = b""
    for s, data in payloads:
        dim = 0 if s >= 256 else s
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(header + entries + b"".join(d for _s, d in payloads))


def save_preview(path: Path) -> None:
    """上排浅底、下排深底的多尺寸对照图，方便肉眼确认托盘效果。"""
    sizes = (128, 64, 48, 32, 24, 16)
    width = 20 + sum(s + 16 for s in sizes)
    img = QImage(width, 300, QImage.Format_ARGB32)
    img.fill(QColor("#f2f2f7"))
    p = QPainter(img)
    x = 20
    for s in sizes:
        p.drawImage(QRectF(x, 20, s, s), render_icon(s))
        x += s + 16
    p.fillRect(0, 200, width, 100, QColor("#202020"))
    x = 20
    for s in sizes:
        p.drawImage(QRectF(x, 250 - s / 2, s, s), render_icon(s))
        x += s + 16
    p.end()
    img.save(str(path))


def main() -> int:
    app = QApplication(sys.argv)
    save_ico(ASSETS / "xiaoer.ico")
    save_preview(ASSETS / "icon-sizes.png")
    print("ico:", (ASSETS / "xiaoer.ico").stat().st_size, "bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
