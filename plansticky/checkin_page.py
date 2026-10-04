"""签到页：月历签到 + 标签选择 + 今日心情。

与旧版「每日生活 · 每日签到」对齐（见 docs/融合规格.md 3.1/3.2）：

- 月历：**周一起始**，每格显示日期数字，已签到的格子用该日标签色标记并打勾；
- 今日：日期数字带圆形高亮；选中：描边；未来日期同样可点可签（旧版行为）；
- 编辑器：7 个默认标签 + 自定义标签（颜色按调色板轮转）、今日心情、完成/删除签到。

与「完成图」的分工：完成图看**计划完成度**，本页看**生活签到**，数据源不同，故并存。
"""
from __future__ import annotations

import calendar
from datetime import date as _date

from PySide6.QtCore import QDate, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QAction, QColor, QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import (QDateEdit, QFrame, QGridLayout, QHBoxLayout,
                               QInputDialog, QLabel, QMenu, QMessageBox,
                               QPlainTextEdit, QPushButton, QSizePolicy,
                               QVBoxLayout, QWidget)

from plansticky.ledger_db import (DEFAULT_CHECKIN_TAGS, MAX_MOOD, MAX_TAG_NAME,
                                  Checkin, LedgerDatabase, ValidationError,
                                  normalize_space)
from plansticky.theme import color
from plansticky.ui_common import FlowLayout

WEEKDAYS = ["一", "二", "三", "四", "五", "六", "日"]
WEEKDAY_CN = ["一", "二", "三", "四", "五", "六", "日"]
DEFAULT_TAG_FALLBACK = "#176b4d"      # 未签到/未知标签的回退色（与旧版一致）


def today_key() -> str:
    return _date.today().strftime("%Y-%m-%d")


def month_key(day: str) -> str:
    return str(day)[:7]


def weekday_cn(day: str) -> str:
    try:
        parsed = _date.fromisoformat(day)
    except ValueError:
        return ""
    return WEEKDAY_CN[parsed.weekday()]


class MonthCalendar(QWidget):
    """自绘月历（周一起始）。日期格里可显示标签色标记与勾。"""

    dateClicked = Signal(str)          # 'YYYY-MM-DD'

    HEADER_H = 18
    GAP = 2

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._year = _date.today().year
        self._month = _date.today().month
        self._selected = today_key()
        self._marks: dict[str, str] = {}     # 日期 -> 标签色
        self._cells: dict[str, QRectF] = {}
        self._header_rects: list[QRectF] = []
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumHeight(180)
        self.setMouseTracking(True)
        self._hover = ""
        self._cell = 0.0

    # ---- 数据 ----
    def set_month(self, year: int, month: int) -> None:
        self._year, self._month = int(year), int(month)
        self.update()

    def month(self) -> tuple[int, int]:
        return self._year, self._month

    def set_marks(self, marks: dict[str, str]) -> None:
        """日期 -> 标签色（已签到的日子）。"""
        self._marks = dict(marks)
        self.update()

    def set_selected(self, day: str) -> None:
        self._selected = str(day)
        self.update()

    def selected(self) -> str:
        return self._selected

    def refresh(self) -> None:
        self.update()

    # ---- 几何 ----
    def _layout_cells(self) -> None:
        self._cells.clear()
        self._header_rects = []
        days = calendar.monthrange(self._year, self._month)[1]
        # 周一起始：weekday() 里周一=0
        first_weekday = _date(self._year, self._month, 1).weekday()
        rows = (first_weekday + days + 6) // 7
        cols = 7

        width = max(1.0, float(self.width()))
        height = max(1.0, float(self.height()))
        cell_w = (width - self.GAP * (cols - 1)) / cols
        avail_h = height - self.HEADER_H - self.GAP
        cell_h = (avail_h - self.GAP * (rows - 1)) / rows
        self._cell = max(8.0, min(cell_w, cell_h))

        # 表头
        for col in range(cols):
            x = col * (cell_w + self.GAP)
            self._header_rects.append(
                QRectF(x, 0, cell_w, self.HEADER_H - 2))

        # 日期格
        for day in range(1, days + 1):
            index = first_weekday + day - 1
            row, col = divmod(index, 7)
            x = col * (cell_w + self.GAP)
            y = self.HEADER_H + row * (cell_h + self.GAP)
            key = f"{self._year:04d}-{self._month:02d}-{day:02d}"
            self._cells[key] = QRectF(x, y, cell_w, cell_h)

    def cell_rect(self, day: str) -> QRectF | None:
        if not self._cells:
            self._layout_cells()
        rect = self._cells.get(str(day))
        return QRectF(rect) if rect is not None else None

    # ---- 绘制 ----
    def paintEvent(self, event) -> None:      # noqa: N802
        self._layout_cells()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        font = QFont(self.font())
        font.setPointSizeF(max(7.0, font.pointSizeF() - 2))
        p.setFont(font)
        fm = QFontMetrics(font)

        # 星期表头
        p.setPen(QColor(color("faint")))
        for col, rect in enumerate(self._header_rects):
            p.drawText(rect, Qt.AlignmentFlag.AlignCenter, WEEKDAYS[col])

        today = today_key()
        radius = min(5.0, self._cell / 5)

        for key, rect in self._cells.items():
            tag_color = self._marks.get(key, "")
            is_today = key == today
            is_selected = key == self._selected
            is_hover = key == self._hover
            # 每格正方形居中（格子可能是长方形）
            side = min(rect.width(), rect.height())
            box = QRectF(rect.center().x() - side / 2, rect.center().y() - side / 2,
                         side, side)

            if tag_color:
                fill = QColor(tag_color)
                fill.setAlpha(38)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(fill)
                p.drawRoundedRect(box, radius, radius)
                p.setBrush(Qt.BrushStyle.NoBrush)
                border = QColor(tag_color)
                border.setAlpha(170)
                p.setPen(QPen(border, 1))
                p.drawRoundedRect(box.adjusted(0.5, 0.5, -0.5, -0.5), radius, radius)

            if is_selected:
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.setPen(QPen(QColor(tag_color or color("accent")), 2))
                p.drawRoundedRect(box.adjusted(1, 1, -1, -1), radius, radius)
            elif is_hover and not tag_color:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(color("hover")))
                p.drawRoundedRect(box, radius, radius)

            # 日期数字
            day_number = int(key[8:10])
            if is_today:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(tag_color or color("todayRing")))
                dot = min(22.0, side - 4)
                p.drawEllipse(QRectF(box.center().x() - dot / 2,
                                     box.center().y() - dot / 2 - 3, dot, dot))
                p.setPen(QColor("#FFFFFF"))
            else:
                p.setPen(QColor(color("text") if tag_color else color("sub")))
            p.drawText(QRectF(box.x(), box.y() - 3, box.width(), box.height()),
                       Qt.AlignmentFlag.AlignCenter, str(day_number))

            # 已签到勾
            if tag_color:
                p.setPen(QColor(tag_color))
                small = QFont(font)
                small.setPointSizeF(max(6.0, font.pointSizeF() - 1))
                small.setBold(True)
                p.setFont(small)
                p.drawText(QRectF(rect.right() - 14, rect.bottom() - 13, 12, 12),
                           Qt.AlignmentFlag.AlignCenter, "✓")
                p.setFont(font)

        p.end()

    # ---- 交互 ----
    def _cell_at(self, pos) -> str:
        self._layout_cells()
        x, y = float(pos.x()), float(pos.y())
        for key, rect in self._cells.items():
            if rect.contains(x, y):
                return key
        return ""

    def mouseReleaseEvent(self, event) -> None:   # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            key = self._cell_at(event.position())
            if key:
                self.set_selected(key)
                self.dateClicked.emit(key)
                event.accept()
                return
        super().mouseReleaseEvent(event)

    def mouseMoveEvent(self, event) -> None:      # noqa: N802
        key = self._cell_at(event.position())
        if key != self._hover:
            self._hover = key
            self.update()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:          # noqa: N802
        if self._hover:
            self._hover = ""
            self.update()
        super().leaveEvent(event)

    def sizeHint(self) -> QSize:                  # noqa: N802
        return QSize(300, 200)


class CheckinPage(QWidget):
    """签到页。"""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._db = LedgerDatabase()
        self._selected = today_key()
        self._year, self._month = _date.today().year, _date.today().month
        self._activity = ""
        self._chips: dict[str, QPushButton] = {}
        self._build_ui()
        self.refresh()

    # ---------------------------------------------------------------- UI
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(6)

        nav = QWidget(self)
        nav.setFixedHeight(26)
        nh = QHBoxLayout(nav)
        nh.setContentsMargins(0, 0, 2, 0)
        nh.setSpacing(2)
        self._btn_prev = QPushButton("‹", nav)
        self._btn_prev.setProperty("cls", "nav")
        self._btn_prev.setFixedSize(22, 24)
        self._btn_prev.setToolTip("上个月")
        self._btn_prev.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_prev.clicked.connect(lambda: self.shift_month(-1))
        nh.addWidget(self._btn_prev)

        self._month_label = QLabel("", nav)
        self._month_label.setObjectName("cardTitle")
        self._month_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._month_label.setMinimumWidth(110)
        nh.addWidget(self._month_label)

        self._btn_next = QPushButton("›", nav)
        self._btn_next.setProperty("cls", "nav")
        self._btn_next.setFixedSize(22, 24)
        self._btn_next.setToolTip("下个月")
        self._btn_next.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_next.clicked.connect(lambda: self.shift_month(1))
        nh.addWidget(self._btn_next)
        nh.addStretch(1)

        self._btn_today = QPushButton("今天", nav)
        self._btn_today.setProperty("cls", "today")
        self._btn_today.setFixedHeight(24)
        self._btn_today.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_today.setToolTip("回到今天")
        self._btn_today.clicked.connect(self.goto_today)
        nh.addWidget(self._btn_today)
        root.addWidget(nav)

        self._calendar = MonthCalendar(self)
        self._calendar.dateClicked.connect(self.set_date)
        root.addWidget(self._calendar, 1)

        editor = QFrame(self)
        editor.setObjectName("cardFlat")
        ev = QVBoxLayout(editor)
        ev.setContentsMargins(10, 8, 10, 8)
        ev.setSpacing(5)

        head = QWidget(editor)
        hh = QHBoxLayout(head)
        hh.setContentsMargins(0, 0, 0, 0)
        hh.setSpacing(4)
        self._date_label = QLabel("", head)
        self._date_label.setObjectName("cardTitle")
        hh.addWidget(self._date_label)
        hh.addStretch(1)
        self._status_label = QLabel("未签到", head)
        self._status_label.setObjectName("cardHint")
        hh.addWidget(self._status_label)
        ev.addWidget(head)

        tags_label = QLabel("今日标签（点已选中的可取消）", editor)
        tags_label.setObjectName("cardHint")
        ev.addWidget(tags_label)

        self._tag_host = QWidget(editor)
        self._tag_flow = FlowLayout(self._tag_host, margin=0, spacing=4)
        self._tag_host.setLayout(self._tag_flow)
        self._tag_host.setMinimumHeight(26)
        ev.addWidget(self._tag_host)

        mood_label = QLabel("今日心情", editor)
        mood_label.setObjectName("cardHint")
        ev.addWidget(mood_label)
        self._mood = QPlainTextEdit(editor)
        self._mood.setPlaceholderText("写下今天的心情、感受或状态……")
        self._mood.setFixedHeight(58)
        self._mood.setTabChangesFocus(True)
        ev.addWidget(self._mood)

        actions = QWidget(editor)
        ah = QHBoxLayout(actions)
        ah.setContentsMargins(0, 0, 0, 0)
        ah.setSpacing(6)
        self._btn_save = QPushButton("完成签到", actions)
        self._btn_save.setProperty("cls", "today")
        self._btn_save.setMinimumHeight(26)
        self._btn_save.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_save.clicked.connect(self.save)
        ah.addWidget(self._btn_save)
        self._btn_delete = QPushButton("删除签到", actions)
        self._btn_delete.setProperty("cls", "nav")
        self._btn_delete.setProperty("role", "danger")
        self._btn_delete.setMinimumHeight(26)
        self._btn_delete.clicked.connect(self.delete)
        ah.addWidget(self._btn_delete)
        ah.addStretch(1)
        ev.addWidget(actions)
        root.addWidget(editor)

    # ------------------------------------------------------------ 外部接口
    def refresh(self) -> None:
        """重载标签与月历标记，并按选中日期回填编辑器。"""
        self._render_tags()
        self._render_calendar()
        self._render_editor()

    def set_date(self, day: str) -> None:
        if not day:
            return
        try:
            parsed = _date.fromisoformat(str(day))
        except ValueError:
            return
        self._selected = parsed.strftime("%Y-%m-%d")
        self._year, self._month = parsed.year, parsed.month
        self._render_calendar()
        self._render_editor()

    def gotoToday(self) -> None:              # 兼容驼峰习惯调用
        self.goto_today()

    def goto_today(self) -> None:
        today = _date.today()
        self._year, self._month = today.year, today.month
        self.set_date(today_key())

    def shift_month(self, delta: int) -> None:
        total = self._year * 12 + (self._month - 1) + delta
        year, month = total // 12, total % 12 + 1
        if not (1900 <= year <= 2200):
            return
        self._year, self._month = year, month
        # 与旧版一致：目标月是当前月则选今天，否则选该月 1 日
        today = _date.today()
        if (year, month) == (today.year, today.month):
            self._selected = today_key()
        else:
            self._selected = f"{year:04d}-{month:02d}-01"
        self._render_calendar()
        self._render_editor()

    # ------------------------------------------------------------ 渲染
    def _render_calendar(self) -> None:
        month = f"{self._year:04d}-{self._month:02d}"
        checkins = self._db.checkins_in_month(month)
        marks = {day: self._db.tag_color(item.activity)
                 for day, item in checkins.items()}
        self._calendar.set_month(self._year, self._month)
        self._calendar.set_marks(marks)
        self._calendar.set_selected(self._selected)
        count = self._db.checkin_count_in_month(month)
        self._month_label.setText(f"{self._year} 年 {self._month} 月 · {count} 天")
        self._btn_next.setEnabled(month < today_key()[:7])

    def _render_tags(self) -> None:
        while self._tag_flow.count():
            item = self._tag_flow.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.hide()
                widget.deleteLater()
        self._chips = {}

        for tag in self._db.list_tags():
            chip = QPushButton(tag.name, self._tag_host)
            chip.setCheckable(True)
            chip.setCursor(Qt.CursorShape.PointingHandCursor)
            chip.setMinimumHeight(22)
            chip.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            chip.customContextMenuRequested.connect(
                lambda pos, name=tag.name, w=chip: self._chip_menu(name, w, pos))
            chip.clicked.connect(lambda _=False, name=tag.name: self._toggle_tag(name))
            self._apply_chip_style(chip, tag.color, False)
            self._tag_flow.addWidget(chip)
            self._chips[tag.name] = chip

        add_btn = QPushButton("＋", self._tag_host)
        add_btn.setFixedSize(26, 22)
        add_btn.setToolTip("添加标签")
        add_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        add_btn.setStyleSheet(
            f"QPushButton {{ border:1px dashed {color('lineStrong')}; border-radius:11px;"
            f" color:{color('sub')}; }}"
            f"QPushButton:hover {{ border-color:{color('accent')}; color:{color('accent')}; }}")
        add_btn.clicked.connect(self.add_tag)
        self._tag_flow.addWidget(add_btn)
        self._tag_host.updateGeometry()

    def _apply_chip_style(self, chip: QPushButton, hex_color: str, active: bool) -> None:
        if active:
            chip.setStyleSheet(
                f"QPushButton {{ border:1px solid {hex_color}; border-radius:11px;"
                f" padding:0 9px; font-size:11px; font-weight:600;"
                f" color:{hex_color}; background:{hex_color}22; }}")
            chip.setChecked(True)
        else:
            chip.setStyleSheet(
                f"QPushButton {{ border:1px solid {color('line')}; border-radius:11px;"
                f" padding:0 9px; font-size:11px; color:{color('sub')}; }}"
                f"QPushButton:hover {{ border-color:{hex_color}; color:{hex_color}; }}")
            chip.setChecked(False)

    def _render_editor(self) -> None:
        parsed = _date.fromisoformat(self._selected)
        self._date_label.setText(
            f"{parsed.month} 月 {parsed.day} 日 · 星期{weekday_cn(self._selected)}")

        item = self._db.get_checkin(self._selected)
        self._activity = item.activity if item else ""
        self._mood.blockSignals(True)
        self._mood.setPlainText(item.detail if item else "")
        self._mood.blockSignals(False)

        # 标签选中态（颜色从库里取，避免主题切换后芯片变色不一致）
        colors = {tag.name: tag.color for tag in self._db.list_tags()}
        for name, chip in self._chips.items():
            self._apply_chip_style(chip, colors.get(name, DEFAULT_TAG_FALLBACK),
                                   name == self._activity)

        if item:
            self._status_label.setText("已签到")
            self._status_label.setStyleSheet(f"color:{color('ok')};font-weight:600;")
        elif self._selected == today_key():
            self._status_label.setText("今天未签到")
            self._status_label.setStyleSheet(f"color:{color('faint')};")
        else:
            self._status_label.setText("未签到")
            self._status_label.setStyleSheet(f"color:{color('faint')};")
        self._btn_delete.setVisible(item is not None)

    # ------------------------------------------------------------ 交互
    def _toggle_tag(self, name: str) -> None:
        self._activity = "" if self._activity == name else name
        colors = {tag.name: tag.color for tag in self._db.list_tags()}
        for tag_name, chip in self._chips.items():
            self._apply_chip_style(chip, colors.get(tag_name, DEFAULT_TAG_FALLBACK),
                                   tag_name == self._activity)

    def _chip_menu(self, name: str, chip: QPushButton, pos) -> None:
        if name in {n for n, _c in DEFAULT_CHECKIN_TAGS}:
            return          # 默认标签不可删
        menu = QMenu(self)
        act = QAction(f"删除标签「{name}」", menu)
        act.triggered.connect(lambda: self.delete_tag(name))
        menu.addAction(act)
        menu.exec(chip.mapToGlobal(pos))

    def add_tag(self) -> None:
        name, ok = QInputDialog.getText(self.window(), "添加标签",
                                        f"标签名称（最多 {MAX_TAG_NAME} 个字）")
        if not ok:
            return
        name = (name or "").strip()
        if not name:
            return
        try:
            self._db.add_tag(name)
        except ValidationError as exc:
            self._notify(str(exc), "error")
            return
        self.refresh()
        self._notify(f"已添加标签「{name}」")

    def delete_tag(self, name: str) -> None:
        answer = QMessageBox.question(
            self.window(), "删除标签",
            f"确定删除标签「{name}」吗？\n\n已用该标签的签到会保留心情文字，但标签会被清空。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        if self._db.delete_tag(name):
            self.refresh()
            self._notify(f"已删除标签「{name}」")
        else:
            self._notify("默认标签不能删除", "error")

    def save(self) -> bool:
        """保存签到；返回是否成功（校验失败返回 False 并提示原因）。"""
        detail = self._mood.toPlainText()
        # normalize_space 而不是 strip()：不换行空格/全角空格也要算空
        if not self._activity and not normalize_space(detail):
            # 与旧版文案一致（旧版这里会静默失败，现在明确提示）
            self._notify("请选择标签或写下今日心情", "error")
            return False
        try:
            self._db.save_checkin(self._selected, self._activity, detail)
        except ValidationError as exc:
            self._notify(str(exc), "error")
            return False
        self.refresh()
        self._notify("签到已保存")
        return True

    def delete(self) -> None:
        item = self._db.get_checkin(self._selected)
        if item is None:
            self._notify("这一天还没有签到", "error")
            return
        answer = QMessageBox.question(
            self.window(), "删除签到",
            f"确定删除 {self._selected} 的签到吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        if self._db.delete_checkin(self._selected):
            self.refresh()
            self._notify("签到已删除")

    def _notify(self, text: str, level: str = "ok") -> None:
        win = self.window()
        if hasattr(win, "toast"):
            win.toast(text, level)
