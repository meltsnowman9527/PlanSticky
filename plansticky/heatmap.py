"""完成图视图 —— 单月月历（v5）。

一页只显示**一个月份**的完成日历（大格子月历，每格带日期数字）：

    ‹ 上月 | 2026年9月 | 下月 ›        [回本月]
    一   二   三   四   五   六   日
    31   1    2    3    4    5    6
    7    8    9   [10]  11   12   13
    ...（格子颜色 = 当天完成度，全部完成 = 满格实色）

- 前后箭头 = 上/下月跳转；显示非本月时「回本月」高亮可点；
- 每个格子显示日期数字，颜色按当天完成度分级；
  无计划=近隐形、有计划没做完=浅、完成率递进、全部完成=满格；今天带描边；
- 格子大小随窗口宽/高自适应（更大窗口 => 更大格子），铺满页面；
- 悬停 = 日期+完成 x/y；点击格子 -> dayPicked，跳到单日编辑；
- 底部统计：本月共完成 x 项 · 全勤 y 天（本月时另附 今日 z/w 与连续达标）。
"""
from __future__ import annotations

from PySide6.QtCore import QDate, QPoint, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QPushButton, QVBoxLayout,
                               QWidget)

from plansticky.database import Database
from plansticky.theme import color

MIN_CELL = 14            # 格子最小边长（含日期数字的底线）
MAX_CELL = 46            # 格子最大边长
GRID_GAP = 4             # 格子间距
HEAD_H = 20              # 一~日 表头行高
PAD = 10                 # 画布内边距
BACK_MONTHS = 60         # 最远可回看几个月
WEEKDAYS = ["一", "二", "三", "四", "五", "六", "日"]


def _level_colors() -> list[QColor]:
    faint = QColor(color("faint"))
    faint.setAlpha(26)
    base = QColor(color("accent"))
    return [
        faint,
        QColor(base.red(), base.green(), base.blue(), 60),
        QColor(base.red(), base.green(), base.blue(), 120),
        QColor(base.red(), base.green(), base.blue(), 190),
        QColor(base.red(), base.green(), base.blue(), 255),
    ]


def _level_for(done: int, total: int) -> int:
    if not total:
        return 0
    if done >= total:
        return 4
    if done == 0:
        return 1
    return 2 if done / total < 0.5 else 3


class HeatmapGrid(QWidget):
    """单月月历画布：7 天列 × 若干周行，格子带日期数字。"""

    dayPicked = Signal(str)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._year = QDate.currentDate().year()
        self._month = QDate.currentDate().month()
        self._stats: dict[str, tuple[int, int]] = {}
        self._today_key = ""
        self._cell = MIN_CELL
        self._weeks = 5
        self._offset = 0
        self._dim = 31
        self._geom_ready = False     # apply_layout 后为 True
        self.setMouseTracking(True)

    # ------------------------------------------------------------ 数据
    def set_month(self, year: int, month: int,
                  stats: dict[str, tuple[int, int]], today_key: str) -> None:
        self._year = year
        self._month = month
        first = QDate(year, month, 1)
        self._offset = first.dayOfWeek() - 1      # 周一=0
        self._dim = first.daysInMonth()
        self._weeks = max(1, (self._offset + self._dim + 6) // 7)
        self._stats = stats
        self._today_key = today_key
        self._geom_ready = False
        self.update()

    # ------------------------------------------------------ 布局（铺满）
    def apply_layout(self, avail_w: int, avail_h: int) -> None:
        w = max(avail_w, 120)
        h = max(avail_h, 80)
        inner_w = w - 2 * PAD
        inner_h = h - 2 * PAD - HEAD_H
        cell_w = (inner_w - 6 * GRID_GAP) // 7
        cell_h = (inner_h - (self._weeks - 1) * GRID_GAP) // self._weeks
        cell = max(MIN_CELL, min(MAX_CELL, min(cell_w, cell_h)))
        self._cell = cell
        # 内容整体居中
        grid_w = 7 * cell + 6 * GRID_GAP
        grid_h = self._weeks * cell + (self._weeks - 1) * GRID_GAP
        self._ox = max(PAD, (w - grid_w) // 2)
        self._oy = PAD + HEAD_H + max(0, (inner_h - grid_h) // 2)
        self._geom_ready = True
        self.setFixedSize(w, h)
        self.update()

    # ------------------------------------------------------------ 几何
    def _cell_rect(self, date: QDate) -> QRectF | None:
        if not self._geom_ready:
            return None
        if (date.year(), date.month()) != (self._year, self._month):
            return None
        day = date.day()
        col = (self._offset + day - 1) % 7
        row = (self._offset + day - 1) // 7
        x = self._ox + col * (self._cell + GRID_GAP)
        y = self._oy + row * (self._cell + GRID_GAP)
        c = self._cell
        return QRectF(x, y, c, c)

    def _date_at(self, pos: QPoint) -> QDate | None:
        if not self._geom_ready:
            return None
        p = self._cell + GRID_GAP
        col = (pos.x() - self._ox) // p
        row = (pos.y() - self._oy) // p
        if not (0 <= col <= 6):
            return None
        if (pos.x() - self._ox) % p >= self._cell:
            return None
        if row < 0 or row >= self._weeks:
            return None
        if (pos.y() - self._oy) % p >= self._cell:
            return None
        day = row * 7 + col - self._offset + 1
        if 1 <= day <= self._dim:
            return QDate(self._year, self._month, day)
        return None

    def date_at(self, pos: QPoint):
        return self._date_at(pos)

    # ------------------------------------------------------------ 事件
    def mouseMoveEvent(self, e) -> None:      # noqa: N802
        date = self._date_at(e.position().toPoint())
        if date is None:
            self.setToolTip("")
        else:
            key = date.toString("yyyy-MM-dd")
            done, total = self._stats.get(key, (0, 0))
            wd = "周" + "一二三四五六日"[date.dayOfWeek() - 1]
            if total:
                tip = (f"{date.year()}年{date.month()}月{date.day()}日 {wd}："
                       f"已完成 {done}/{total}")
                if done >= total:
                    tip += "，全部完成"
            else:
                tip = (f"{date.year()}年{date.month()}月{date.day()}日 {wd}"
                       "：无计划")
            self.setToolTip(tip)
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e) -> None:   # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton:
            date = self._date_at(e.position().toPoint())
            if date is not None and date <= QDate.currentDate():
                self.dayPicked.emit(date.toString("yyyy-MM-dd"))
                return
        super().mouseReleaseEvent(e)

    # ------------------------------------------------------------ 绘制
    def paintEvent(self, e) -> None:          # noqa: N802
        if not self._geom_ready:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        levels = _level_colors()
        today = QDate.currentDate()
        base_font = QFont(self.font())
        small = QFont(self.font())
        small.setPointSizeF(max(7.0, self.font().pointSizeF() - 2.0))

        # 一~日 表头
        fm = QFontMetrics(small)
        p.setFont(small)
        p.setPen(QColor(color("faint")))
        for col, ch in enumerate(WEEKDAYS):
            cx = self._ox + col * (self._cell + GRID_GAP)
            tw = fm.horizontalAdvance(ch)
            p.drawText(int(cx + (self._cell - tw) / 2),
                       int(PAD + fm.ascent() + 1), ch)

        c = self._cell
        num_font = QFont(base_font)
        # 日期数字字号随格子大小
        num_font.setPixelSize(max(9, min(18, c - 8)))
        fmn = QFontMetrics(num_font)
        text_col = QColor(color("text"))
        for day in range(1, self._dim + 1):
            date = QDate(self._year, self._month, day)
            rect = self._cell_rect(date)
            if rect is None:
                continue
            key = date.toString("yyyy-MM-dd")
            done, total = self._stats.get(key, (0, 0))
            level = _level_for(done, total)
            x, y = rect.x(), rect.y()
            # 空白日期底色（极淡）
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(levels[0])
            p.drawRoundedRect(QRectF(x, y, c, c), 4, 4)
            if level > 0:
                p.setBrush(levels[level])
                p.drawRoundedRect(QRectF(x, y, c, c), 4, 4)
            # 日期数字
            label = str(day)
            tw = fmn.horizontalAdvance(label)
            ncol = QColor(color("accentText")) if level == 4 else text_col
            ncol.setAlpha(200 if level in (0, 1) else 255)
            p.setFont(num_font)
            p.setPen(ncol)
            p.drawText(int(x + (c - tw) / 2),
                       int(y + (c + fmn.ascent() - fmn.descent()) / 2), label)
            # 今天描边
            if key == self._today_key:
                pen = QPen(QColor(color("accent")))
                pen.setWidthF(1.6)
                p.setPen(pen)
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRoundedRect(QRectF(x - 1, y - 1, c + 2, c + 2), 5, 5)
        p.end()


class HeatmapView(QWidget):
    """完成图整页：月份导航 + 单月月历 + 统计/图例。"""

    dayPicked = Signal(str)

    def __init__(self, db: Database, parent: QWidget | None = None):
        super().__init__(parent)
        self._db = db
        today = QDate.currentDate()
        self._view = QDate(today.year(), today.month(), 1)
        self._relayout_pending = False

        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(4)

        nav = QWidget(self)
        nh = QHBoxLayout(nav)
        nh.setContentsMargins(0, 0, 0, 0)
        nh.setSpacing(4)
        self._btn_older = QPushButton("‹ 上月", nav)
        self._btn_older.setProperty("cls", "nav")
        self._btn_older.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_older.clicked.connect(lambda: self._shift_month(-1))
        nh.addWidget(self._btn_older)
        self._range_label = QLabel("", nav)
        self._range_label.setObjectName("mutedLabel")
        self._range_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        nh.addWidget(self._range_label, 1)
        self._btn_newer = QPushButton("下月 ›", nav)
        self._btn_newer.setProperty("cls", "nav")
        self._btn_newer.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_newer.clicked.connect(lambda: self._shift_month(1))
        nh.addWidget(self._btn_newer)
        self._btn_today_month = QPushButton("回本月", nav)
        self._btn_today_month.setProperty("cls", "today")
        self._btn_today_month.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_today_month.clicked.connect(self.refresh)
        nh.addWidget(self._btn_today_month)
        v.addWidget(nav)

        self._grid = HeatmapGrid(self)
        self._grid.dayPicked.connect(self.dayPicked)
        v.addWidget(self._grid, 1)

        self._stats_label = QLabel("", self)
        self._stats_label.setObjectName("mutedLabel")
        self._stats_label.setWordWrap(True)
        self._stats_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(self._stats_label)
        v.addWidget(Legend(self), 0, Qt.AlignmentFlag.AlignCenter)

        self.refresh()

    # ------------------------------------------------------------ 数据
    def refresh(self) -> None:
        """回到当前月份并重建。"""
        today = QDate.currentDate()
        self._view = QDate(today.year(), today.month(), 1)
        self._rebuild()

    def _shift_month(self, delta: int) -> None:
        cur = QDate.currentDate()
        new = self._view.addMonths(delta)
        earliest = QDate(cur.year(), cur.month(), 1).addMonths(-BACK_MONTHS)
        new = min(new, cur)          # 不许翻到未来
        new = max(new, earliest)
        if new != self._view:
            self._view = new
            self._rebuild()

    def _rebuild(self) -> None:
        today = QDate.currentDate()
        y, m = self._view.year(), self._view.month()
        first = QDate(y, m, 1)
        end = first.addDays(first.daysInMonth() - 1)
        if self._view == QDate(today.year(), today.month(), 1):
            end = today
        stats = self._db.day_stats_range(
            first.toString("yyyy-MM-dd"), end.toString("yyyy-MM-dd"))
        self._grid.set_month(y, m, stats, today.toString("yyyy-MM-dd"))

        is_cur = (y, m) == (today.year(), today.month())
        self._range_label.setText(f"{y}年 {m}月")
        self._btn_newer.setEnabled(not is_cur)
        self._btn_today_month.setEnabled(not is_cur)
        self._update_stats(stats)
        self._schedule_relayout()

    def _update_stats(self, stats: dict[str, tuple[int, int]]) -> None:
        today = QDate.currentDate()
        done_sum = sum(d for d, _ in stats.values())
        all_done = sum(1 for d, t in stats.values() if t and d >= t)
        cur = (today.year(), today.month()) == (self._view.year(),
                                                self._view.month())
        parts = [f"{self._view.year()}年{self._view.month()}月"
                 f"共完成 {done_sum} 项", f"全部完成 {all_done} 天"]
        if cur:
            t_key = today.toString("yyyy-MM-dd")
            d, t = stats.get(t_key, (0, 0))
            parts.append(f"今日 {d}/{t}")
            streak = 0
            cursor = today
            while True:
                dd, tt = stats.get(cursor.toString("yyyy-MM-dd"), (0, 0))
                if tt and dd >= tt:
                    streak += 1
                    cursor = cursor.addDays(-1)
                else:
                    break
            parts.append(f"连续达标 {streak} 天" if streak else "连续达标 —")
        self._stats_label.setText(" · ".join(parts))

    # ------------------------------------------------------------ 自适应
    def resizeEvent(self, e) -> None:         # noqa: N802
        super().resizeEvent(e)
        self._schedule_relayout()

    def _schedule_relayout(self) -> None:
        if not self._relayout_pending:
            self._relayout_pending = True
            QTimer.singleShot(0, self._relayout)

    def _relayout(self) -> None:
        self._relayout_pending = False
        self._grid.apply_layout(max(self.width(), 120),
                                max(self.height() - 74, 100))


class Legend(QWidget):
    """“无计划 → 全部完成”颜色图例。"""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setFixedSize(5 * 16 + 72, 16)

    def paintEvent(self, e) -> None:          # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        levels = _level_colors()
        for i, lvl in enumerate(levels):
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(lvl)
            p.drawRoundedRect(QRectF(i * 16, 2, 12, 12), 3, 3)
        p.setPen(QColor(color("faint")))
        p.drawText(5 * 16 + 4, 12, "无计划")
        p.drawText(5 * 16 + 60, 12, "全部完成")
        p.end()
