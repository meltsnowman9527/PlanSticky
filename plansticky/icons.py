"""图标：程序图标 / 托盘图标全部用 QPainter 代码绘制。

好处：
- 零资源文件，源码和 exe 都不需要附带 .ico/.png；
- 打包更干净；需要自定义图标时替换这里即可。
"""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (QColor, QFont, QIcon, QPainter, QPainterPath,
                           QPixmap, QPolygonF)

from plansticky.theme import color

_FONT_CHECK = None  # 惰性缓存“图标字体是否可用”


def has_mdl2_font() -> bool:
    """Windows 10/11 自带 Segoe MDL2 Assets（清爽的矢量符号字体）。"""
    global _FONT_CHECK
    if _FONT_CHECK is None:
        from PySide6.QtGui import QFontDatabase
        fams = QFontDatabase.families()
        _FONT_CHECK = ("Segoe MDL2 Assets" in fams) or ("Segoe Fluent Icons" in fams)
    return _FONT_CHECK


def glyph(char_mdl2: str, char_fallback: str) -> str:
    """返回要在按钮上显示的字符（不负责设置字体）。"""
    return char_mdl2 if has_mdl2_font() else char_fallback


def mdl2_font(pixel: int = 13) -> QFont:
    f = QFont("Segoe MDL2 Assets")
    f.setPixelSize(pixel)
    return f


def make_app_icon(size: int = 64) -> QIcon:
    """圆角方块 + 白勾，类似“完成清单”的意象。"""
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    r = size * 0.08
    rect = QRectF(0, 0, size, size)
    path = QPainterPath()
    path.addRoundedRect(rect, r, r)
    p.fillPath(path, QColor(color("accent")))
    # 白色对勾
    p.setPen(QColor("#FFFFFF"))
    pen = p.pen()
    pen.setWidthF(size * 0.09)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    pts = QPolygonF([
        QPointF(size * 0.26, size * 0.52),
        QPointF(size * 0.44, size * 0.69),
        QPointF(size * 0.75, size * 0.33),
    ])
    p.drawPolyline(pts)
    p.end()
    return QIcon(pm)


def make_tray_pixmap(size: int = 22) -> QPixmap:
    """托盘小图标：简化版（圆角底 + 细勾）。"""
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    rect = QRectF(size * 0.02, size * 0.02, size * 0.96, size * 0.96)
    path = QPainterPath()
    path.addRoundedRect(rect, size * 0.24, size * 0.24)
    p.fillPath(path, QColor(color("accent")))
    p.setPen(QColor("#FFFFFF"))
    pen = p.pen()
    pen.setWidthF(size * 0.10)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    pts = QPolygonF([
        QPointF(size * 0.24, size * 0.52),
        QPointF(size * 0.44, size * 0.70),
        QPointF(size * 0.77, size * 0.32),
    ])
    p.drawPolyline(pts)
    p.end()
    return pm


def make_app_icon_png_bytes(size: int = 256) -> bytes:
    """生成 PNG 字节（供打包脚本制作 .ico 用）。"""
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    rect = QRectF(0, 0, size, size)
    path = QPainterPath()
    path.addRoundedRect(rect, size * 0.08, size * 0.08)
    p.fillPath(path, QColor(color("accent")))
    p.setPen(QColor("#FFFFFF"))
    pen = p.pen()
    pen.setWidthF(size * 0.09)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    pts = QPolygonF([
        QPointF(size * 0.26, size * 0.52),
        QPointF(size * 0.44, size * 0.69),
        QPointF(size * 0.75, size * 0.33),
    ])
    p.drawPolyline(pts)
    p.end()
    from PySide6.QtCore import QBuffer, QIODevice
    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    pm.save(buf, "PNG")
    return bytes(buf.data())
