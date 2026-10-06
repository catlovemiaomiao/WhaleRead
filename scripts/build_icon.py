"""Render our original vector at each macOS icon size; no image dependencies."""
import os
import subprocess
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QGuiApplication, QImage, QPainter
from PyQt6.QtSvg import QSvgRenderer

root = Path(__file__).resolve().parents[1]
app = QGuiApplication([])
renderer = QSvgRenderer(str(root / "assets/HyTranslator.svg"))
if not renderer.isValid():
    raise SystemExit("Invalid icon SVG")
with tempfile.TemporaryDirectory(prefix="hy-icon-") as raw:
    iconset = Path(raw) / "HyTranslator.iconset"
    iconset.mkdir()
    for size in (16, 32, 128, 256, 512):
        for scale in (1, 2):
            canvas = QImage(size * scale, size * scale, QImage.Format.Format_ARGB32_Premultiplied)
            canvas.fill(Qt.GlobalColor.transparent)
            painter = QPainter(canvas)
            renderer.render(painter)
            painter.end()
            suffix = "@2x" if scale == 2 else ""
            assert canvas.save(str(iconset / f"icon_{size}x{size}{suffix}.png"))
            if size == 512 and scale == 2:
                assert canvas.save(str(root / "assets/HyTranslator.png"))
    subprocess.run(["/usr/bin/iconutil", "-c", "icns", str(iconset), "-o", str(root / "assets/HyTranslator.icns")], check=True)
