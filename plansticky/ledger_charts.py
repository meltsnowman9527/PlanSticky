"""记账页自绘图表：近 N 天支出柱状图 + 分类占比环形图。

为什么自绘而不用 QtCharts：
- 现有 `heatmap.py` / `icons.py` / `ui_common.py` 全是 QPainter 自绘，风格统一；
- 零新增打包依赖，也不会给 exe 再塞一个 Qt 模块；
- 深浅色主题切换时 `theme.color()` 实时读取，无需同步 QChart 的画笔/画刷；
- 需求本身很简单（柱状 + 环形），QtCharts 的能力用不上。

绘制口径严格对齐旧版 `drawTrend()` / `drawDonut()`（见 docs/融合规格.md 第 2 节）。
"""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from plansticky.theme import color

BAR_MAX_HEIGHT = 150     # 柱体最大高度（与旧版一致，基线以上 150px）
PAD_X = 30               # 左右留白（旧版 pad=30）
GRID_LINES = 4           # 水平网格线条数


class TrendChart(QWidget):
    """近 N 天每日支出柱状图（只算支出，收入不计不抵消）。"""

    def __init__(self, db, parent: QWidget | None = None):
        super().__init__(parent)
        self._db = db
        self._days = 7
        self._data: list[tuple[str, float]] = []
        self.setMinimumHeight(170)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_days(self, days: int) -> None:
        if days != self._days:
            self._days = days
            self.refresh()

    @property
    def days(self) -> int:
        return self._days

    def refresh(self) -> None:
        self._data = self._db.trend(self._days)
        self.update()

    def paintEvent(self, event) -> None:      # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()

        data = self._data
        n = max(1, len(data))
        self._draw_grid(p, w, h)

        if not data:
            p.end()
            return

        values = [v for _d, v in data]
        peak = max(max(values), 1.0)

        # 几何：柱宽与间隙（旧版算法）
        usable = max(1, w - PAD_X * 2)
        bar_w = min(34.0, max(3.0, usable / (n * 2)))
        gap = max(0.0, (usable - bar_w * n) / (n + 1))
        baseline = h - 44.0
        max_bar = min(float(BAR_MAX_HEIGHT), max(20.0, baseline - 26.0))

        label_step = 1 if n <= 7 else (5 if n <= 30 else 14)
        font = QFont(self.font())
        font.setPointSizeF(max(7.0, font.pointSizeF() - 2))
        p.setFont(font)
        fm = QFontMetrics(font)

        for i, (key, value) in enumerate(data):
            left = PAD_X + gap + i * (bar_w + gap)
            bar_h = max(3.0, (value / peak) * max_bar)
            rect = QRectF(left, baseline - bar_h, bar_w, bar_h)

            # 柱体（顶部圆角 5、底部 2 —— 用统一小圆角即可，视觉一致）
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(color("chartBar")))
            p.drawRoundedRect(rect, min(4.0, bar_w / 2), min(4.0, bar_w / 2))

            # 数值标注：只标较高的柱（> 20% 峰值），与旧版一致
            if value > 0 and value / peak > 0.2:
                p.setPen(QColor(color("faint")))
                text = str(round(value))
                p.drawText(QRectF(left - 12, rect.top() - 16, bar_w + 24, 14),
                           Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom,
                           text)

            # 横轴标签（抽稀）
            if i % label_step == 0 or n <= 7:
                month = int(key[5:7])
                day = int(key[8:10])
                p.setPen(QColor(color("faint")))
                p.drawText(QRectF(left - 16, baseline + 4, bar_w + 32, 16),
                           Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
                           f"{month}/{day}")

        p.end()

    def _draw_grid(self, p: QPainter, w: int, h: int) -> None:
        baseline = h - 44.0
        max_bar = min(float(BAR_MAX_HEIGHT), max(20.0, baseline - 26.0))
        p.setPen(QPen(QColor(color("chartGrid")), 1))
        for i in range(GRID_LINES):
            y = baseline - max_bar + (max_bar / max(1, GRID_LINES - 1)) * i
            if y < 2:
                continue
            p.drawLine(QPointF(PAD_X, y), QPointF(max(PAD_X + 1, w - PAD_X), y))

    def sizeHint(self):                        # noqa: N802
        from PySide6.QtCore import QSize
        return QSize(300, 170)


class DonutChart(QWidget):
    """分类占比环形图（仅当月支出，中心显示分类个数）。"""

    def __init__(self, db, parent: QWidget | None = None):
        super().__init__(parent)
        self._db = db
        self._slices: list[tuple[str, float, str]] = []   # (名称, 金额, 颜色)
        self._total = 0.0
        self.setMinimumSize(130, 130)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setFixedSize(130, 130)

    def set_slices(self, month: str) -> None:
        """按月份重算各分类合计（只含支出，按金额降序）。"""
        from plansticky.ledger_db import category_color
        totals = self._db.category_totals(month)
        self._slices = [(name, amount, category_color(name)) for name, amount in totals]
        self._total = sum(amount for _n, amount, _c in self._slices)
        self.update()

    def paintEvent(self, event) -> None:      # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        side = min(self.width(), self.height())
        thickness = max(10.0, side * 0.22)
        radius = side / 2 - thickness / 2 - 2
        center = QPointF(self.width() / 2, self.height() / 2)
        box = QRectF(center.x() - radius, center.y() - radius, radius * 2, radius * 2)

        if not self._slices or self._total <= 0:
            # 无数据：整圈灰环，中心无文字（与旧版一致）
            p.setPen(QPen(QColor(color("line")), thickness,
                          Qt.PenStyle.SolidLine, Qt.PenCapStyle.FlatCap))
            p.drawArc(box, 0, 360 * 16)
            p.end()
            return

        # 从 12 点方向顺时针（Qt 角度单位 1/16 度，正值逆时针；start 用 90 度）
        start = 90 * 16
        p.setPen(Qt.PenStyle.NoPen)
        for _name, amount, hex_color in self._slices:
            span = -int(round(amount / self._total * 360 * 16))
            pen = QPen(QColor(hex_color), thickness,
                       Qt.PenStyle.SolidLine, Qt.PenCapStyle.FlatCap)
            p.setPen(pen)
            p.drawArc(box, start, span)
            start += span

        # 中心：分类个数
        p.setPen(QColor(color("text")))
        font = QFont(self.font())
        font.setPointSizeF(max(11.0, font.pointSizeF() + 5))
        font.setBold(True)
        p.setFont(font)
        p.drawText(QRectF(0, center.y() - 20, self.width(), 22),
                   Qt.AlignmentFlag.AlignCenter, str(len(self._slices)))
        font.setPointSizeF(max(7.0, self.font().pointSizeF() - 1))
        font.setBold(False)
        p.setFont(font)
        p.setPen(QColor(color("faint")))
        p.drawText(QRectF(0, center.y() + 1, self.width(), 16),
                   Qt.AlignmentFlag.AlignCenter, "个分类")
        p.end()
