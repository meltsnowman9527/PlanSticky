"""生成 assets/icon.ico（256px PNG 内嵌 ICO）。

PyInstaller --icon 需要一个 .ico 文件；用应用代码里同款绘制逻辑离屏渲染，
不依赖任何图片资源。运行：.venv\\Scripts\\python.exe scripts\\make_icon.py
"""
from __future__ import annotations

import os
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QGuiApplication  # noqa: E402

app = QGuiApplication([])  # 只需 GUI 基础，无需 QApplication 事件循环

from plansticky import icons  # noqa: E402

png = icons.make_app_icon_png_bytes(256)

# ICO 容器：1 张 256x256 PNG（Vista+ 支持内嵌 PNG）
header = struct.pack("<HHH", 0, 1, 1)                    # reserved, type, count
entry = struct.pack("<BBBBHHII", 0, 0, 0, 0, 1, 32, len(png), 22)  # w,h,0,0,planes,bpp,size,offset
ico = header + entry + png

out_dir = os.path.join(ROOT, "assets")
os.makedirs(out_dir, exist_ok=True)
out = os.path.join(out_dir, "icon.ico")
with open(out, "wb") as f:
    f.write(ico)
print(f"written {out} ({len(ico)} bytes)")
