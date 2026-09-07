"""主悬浮窗口。

- 无边框自绘窗口：顶部条可拖动（Qt 原生 startSystemMove，支持 Win11 贴靠），
  窗口四边/四角 7px 热区原生缩放（startSystemResize）；
- 记住窗口位置/大小/置顶/当前 Tab；
- 顶部：分段 Tab（长期/短期）+ 主题、置顶、最小化、关闭按钮；
- 长期页：隐藏已完成开关 + 列表 + 添加条；
- 短期页：← 日期 → | 今天 | 今日 3/7 + 列表 + 添加条；
- 关闭按钮 = 隐藏到托盘（托盘不可用时直接退出）。
"""
from __future__ import annotations

from PySide6.QtCore import QDate, QEvent, QPoint, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QMenu, QPushButton,
                               QStackedWidget, QVBoxLayout, QWidget)

from plansticky import config, icons
from plansticky.add_bar import AddBar
from plansticky.calendar_popup import CalendarPopup
from plansticky.database import Database
from plansticky.day_list import DayListView
from plansticky.heatmap import HeatmapView
from plansticky.task_list import TaskListView
from plansticky.ui_common import Segmented, SwitchButton

EDGE = 7            # 四边缩放热区宽度
DEFAULT_W, DEFAULT_H = 320, 500


class MainWindow(QWidget):
    pinChanged = Signal(bool)

    def __init__(self, db: Database, theme_mgr, parent: QWidget | None = None):
        super().__init__(parent)
        self._db = db
        self._theme = theme_mgr
        self._tray = None          # 由 app 注入
        self._quitting = False
        self._pinned = False
        self._geo_timer = QTimer(self)
        self._geo_timer.setSingleShot(True)
        self._geo_timer.setInterval(500)
        self._geo_timer.timeout.connect(self._save_geometry_now)
        self._today_probe = QTimer(self)   # 每天检查一次“今天”是否已翻页
        self._today_probe.setInterval(60_000)
        self._today_probe.timeout.connect(self._maybe_advance_today)

        self.setObjectName("rootWin")
        self.setWindowTitle(config.APP_DISPLAY_NAME)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Window)
        self.setMinimumSize(300, 400)
        self.setWindowIcon(icons.make_app_icon())
        self.setMouseTracking(True)

        self._selected_date = QDate.currentDate()
        self._last_today = QDate.currentDate()
        self._cal_popup: CalendarPopup | None = None
        self._today_counts = (0, 0)
        self._list_anchored = False

        self._build_ui()
        self._restore_state()

    # ================================================================ UI
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 8, 10, 10)   # 外圈留出缩放热区
        root.setSpacing(6)

        # ---- 顶栏：Tab + 窗口按钮
        self._topbar = QWidget(self)
        self._topbar.setFixedHeight(34)
        self._topbar.installEventFilter(self)
        tb = QHBoxLayout(self._topbar)
        tb.setContentsMargins(0, 0, 0, 0)
        tb.setSpacing(2)
        self._seg = Segmented(["长期计划", "短期计划"], self._topbar)
        self._seg.indexChanged.connect(self._on_tab_changed)
        tb.addWidget(self._seg)
        tb.addStretch(1)

        self._btn_theme = self._make_icon_btn(self._topbar, icons.glyph("\ue790", "◑"),
                                              "主题：浅色/深色/跟随系统",
                                              pixel=14)
        self._btn_theme.clicked.connect(self._open_theme_menu)
        tb.addWidget(self._btn_theme)

        self._btn_pin = self._make_icon_btn(self._topbar, icons.glyph("\ue718", "📌"),
                                            "置顶窗口", pixel=14, checkable=True)
        self._btn_pin.toggled.connect(lambda on: self.set_pinned(on, persist=True))
        tb.addWidget(self._btn_pin)

        self._btn_min = self._make_icon_btn(self._topbar, icons.glyph("\ue921", "—"),
                                            "最小化", pixel=13)
        self._btn_min.clicked.connect(self.showMinimized)
        tb.addWidget(self._btn_min)

        self._btn_close = self._make_icon_btn(self._topbar, icons.glyph("\ue8bb", "✕"),
                                              "关闭（缩到托盘）", pixel=13)
        self._btn_close.setProperty("role", "danger")
        self._btn_close.clicked.connect(self.close)
        tb.addWidget(self._btn_close)
        root.addWidget(self._topbar)

        # ---- 两个 Tab 页
        self._stack = QStackedWidget(self)
        self._long_page = self._build_long_page()
        self._day_page = self._build_day_page()
        self._stack.addWidget(self._long_page)   # 0: 长期
        self._stack.addWidget(self._day_page)    # 1: 短期
        root.addWidget(self._stack, 1)

    def _make_icon_btn(self, parent, text: str, tip: str, *,
                       pixel: int = 13, checkable: bool = False) -> QPushButton:
        b = QPushButton(text, parent)
        b.setProperty("cls", "icon")
        b.setFixedSize(26, 26)
        b.setToolTip(tip)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        if icons.has_mdl2_font():
            b.setFont(icons.mdl2_font(pixel))
        if checkable:
            b.setCheckable(True)
        return b

    # -------------------------------------------------------- 长期计划页
    def _build_long_page(self) -> QWidget:
        page = QWidget(self)
        v = QVBoxLayout(page)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(4)

        head = QWidget(page)
        h = QHBoxLayout(head)
        h.setContentsMargins(2, 0, 2, 0)
        h.setSpacing(6)
        self._hide_done_sw = SwitchButton(head)
        self._hide_done_sw.setChecked(
            self._db.get_setting_bool(config.KEY_HIDE_DONE, False))
        self._hide_done_sw.toggled.connect(self._on_hide_done_toggled)
        h.addWidget(self._hide_done_sw)
        lbl = QLabel("隐藏已完成", head)
        lbl.setObjectName("mutedLabel")
        h.addWidget(lbl)
        h.addStretch(1)
        head.setFixedHeight(24)
        v.addWidget(head)

        self._long_view = TaskListView(self._db, "long", None, page)
        v.addWidget(self._long_view, 1)

        self._long_add = AddBar("输入长期计划，Enter 保存 · Esc 取消", page)
        self._long_add.addRequested.connect(self._add_long_task)
        v.addWidget(self._long_add)
        return page

    # -------------------------------------------------------- 短期计划页
    def _build_day_page(self) -> QWidget:
        """短期计划 = 视图模式条（单日 / 多日列表）+ 页面栈。"""
        page = QWidget(self)
        v = QVBoxLayout(page)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(2)

        strip = QWidget(page)
        sh = QHBoxLayout(strip)
        sh.setContentsMargins(0, 0, 2, 0)
        sh.setSpacing(2)
        self._day_mode_seg = Segmented(["单日", "多日列表", "完成图"], strip)
        self._day_mode_seg.setToolTip(
            "单日：一次看一天 · 多日列表：按日期浏览计划 · 完成图：一整年的完成热力图")
        self._day_mode_seg.indexChanged.connect(self._on_day_mode_changed)
        sh.addWidget(self._day_mode_seg)
        sh.addStretch(1)
        strip.setFixedHeight(30)
        v.addWidget(strip)

        self._day_stack = QStackedWidget(page)
        self._day_single = self._build_day_single_page()
        self._day_stack.addWidget(self._day_single)          # 0: 单日

        multi = QWidget(page)
        mv = QVBoxLayout(multi)
        mv.setContentsMargins(0, 0, 0, 0)
        mv.setSpacing(0)
        self._day_list = DayListView(self._db, multi)
        self._day_list.dateActivated.connect(self.show_day_detail)
        mv.addWidget(self._day_list)
        self._day_stack.addWidget(multi)                     # 1: 多日列表

        heat = QWidget(page)
        hv = QVBoxLayout(heat)
        hv.setContentsMargins(0, 0, 0, 0)
        hv.setSpacing(0)
        self._heat = HeatmapView(self._db, heat)
        self._heat.dayPicked.connect(self.show_day_detail)
        hv.addWidget(self._heat)
        self._day_stack.addWidget(heat)                      # 2: 贡献图

        v.addWidget(self._day_stack, 1)
        self._day_mode = "single"   # 稍后在 _restore_state 按设置恢复
        return page

    def _build_day_single_page(self) -> QWidget:
        page = QWidget(self)
        v = QVBoxLayout(page)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(4)

        nav = QWidget(page)
        nav.setFixedHeight(30)
        h = QHBoxLayout(nav)
        h.setContentsMargins(0, 0, 2, 0)
        h.setSpacing(2)

        self._btn_prev = self._make_nav_btn(nav, icons.glyph("\ue76b", "‹"), "前一天")
        self._btn_prev.clicked.connect(lambda: self._shift_day(-1))
        h.addWidget(self._btn_prev)

        self._btn_date = QPushButton(nav)
        self._btn_date.setProperty("cls", "date")
        self._btn_date.setFixedHeight(26)
        self._btn_date.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_date.setToolTip("选择日期")
        self._btn_date.clicked.connect(self._open_calendar)
        h.addWidget(self._btn_date)

        self._btn_next = self._make_nav_btn(nav, icons.glyph("\ue76c", "›"), "后一天")
        self._btn_next.clicked.connect(lambda: self._shift_day(1))
        h.addWidget(self._btn_next)

        h.addStretch(1)

        self._btn_today = QPushButton("今天", nav)
        self._btn_today.setProperty("cls", "today")
        self._btn_today.setFixedHeight(26)
        self._btn_today.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_today.clicked.connect(self.show_today)
        h.addWidget(self._btn_today)

        self._count_label = QLabel(nav)
        self._count_label.setObjectName("dayCount")
        self._count_label.setAlignment(Qt.AlignmentFlag.AlignVCenter
                                       | Qt.AlignmentFlag.AlignRight)
        self._count_label.hide()
        h.addWidget(self._count_label)
        v.addWidget(nav)

        self._day_view = TaskListView(
            self._db, "day", self._selected_date.toString("yyyy-MM-dd"), page)
        self._day_view.countChanged.connect(self._on_day_counts)
        v.addWidget(self._day_view, 1)

        self._day_add = AddBar("输入今日计划，Enter 保存 · Esc 取消", page)
        self._day_add.addRequested.connect(self._add_day_task)
        v.addWidget(self._day_add)
        return page

    # ------------------------------------------------- 短期页：模式切换
    def _on_day_mode_changed(self, idx: int) -> None:
        """单日 / 多日列表 / 贡献图 视图切换。"""
        mode = ("single" if idx == 0 else "list" if idx == 1 else "heat")
        if mode == self._day_mode and self._day_stack.currentIndex() == idx:
            return
        self._day_mode = mode
        self._db.set_setting(config.KEY_DAY_MODE, mode)
        self._day_stack.setCurrentIndex(idx)
        if mode == "list":
            # 每次进入列表视图都重建：把单日视图里新增/改过的计划同步进来，
            # 并定位到今天的分节
            self._day_list.reload(scroll_to_today=True)
        elif mode == "heat":
            self._heat.refresh()   # 每次进入贡献图都以“今天”为最新端重建

    def show_day_detail(self, day_key: str) -> None:
        """列表模式点击某天标题 -> 切回单日视图并打开这一天编辑。"""
        date = QDate.fromString(day_key, "yyyy-MM-dd")
        if not date.isValid():
            return
        if self._stack.currentIndex() != 1:
            self._seg.set_current_index(1)     # 触发 _on_tab_changed
        if self._day_mode != "single":
            self._day_mode_seg.set_current_index(0)
        self._day_add.flush()
        self._set_date(date)

    def _make_nav_btn(self, parent, text: str, tip: str) -> QPushButton:
        b = QPushButton(text, parent)
        b.setProperty("cls", "nav")
        b.setFixedSize(24, 26)
        b.setToolTip(tip)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        if icons.has_mdl2_font():
            b.setFont(icons.mdl2_font(12))
        return b

    # ============================================================ 状态
    def _restore_state(self) -> None:
        # 主题（按钮弹出菜单里同步勾选状态）
        # 置顶
        self._pinned = self._db.get_setting_bool(config.KEY_PINNED, False)
        if self._pinned:
            self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self._btn_pin.blockSignals(True)
        self._btn_pin.setChecked(self._pinned)
        self._btn_pin.blockSignals(False)

        # 几何
        geo = self._db.get_geometry()
        if geo and self._geo_is_usable(geo):
            self.setGeometry(geo["x"], geo["y"], geo["w"], geo["h"])
        else:
            sg = QGuiApplication.primaryScreen().availableGeometry()
            self.resize(DEFAULT_W, DEFAULT_H)
            self.move(sg.right() - DEFAULT_W - 48, sg.top() + 48)

        # Tab
        tab = self._db.get_setting(config.KEY_LAST_TAB, "day")
        idx = 1 if tab == "day" else 0
        self._seg.set_current_index(idx)
        self._stack.setCurrentIndex(idx)

        # 短期页视图模式：单日 / 多日列表 / 贡献图
        mode = self._db.get_setting(config.KEY_DAY_MODE, "single")
        mode_idx = {"single": 0, "list": 1, "heat": 2}.get(mode, 0)
        if mode_idx:
            self._day_mode_seg.set_current_index(mode_idx)   # 触发重建+持久化

        self._render_date()
        self._update_today_button()
        self._update_addbar_hint()
        self._today_probe.start()

    def _geo_is_usable(self, geo: dict) -> bool:
        r = QRect(geo["x"], geo["y"], geo["w"], geo["h"])
        for s in QGuiApplication.screens():
            inter = s.availableGeometry().intersected(r)
            if inter.width() >= 120 and inter.height() >= 120:
                return True
        return False

    # ============================================================ 顶栏交互
    def eventFilter(self, obj, ev) -> bool:   # noqa: N802
        # 顶栏空白处按住拖动窗口（子控件自己会处理点击）
        if (obj is self._topbar
                and ev.type() == QEvent.Type.MouseButtonPress
                and ev.button() == Qt.MouseButton.LeftButton):
            hw = self.windowHandle()
            if hw is not None:
                hw.startSystemMove()
                return True
        return super().eventFilter(obj, ev)

    def _edges_at(self, p: QPoint) -> Qt.Edge:
        edges = Qt.Edge(0)
        r = self.rect()
        if p.x() <= EDGE:
            edges |= Qt.Edge.LeftEdge
        elif p.x() >= r.width() - EDGE:
            edges |= Qt.Edge.RightEdge
        if p.y() <= EDGE:
            edges |= Qt.Edge.TopEdge
        elif p.y() >= r.height() - EDGE:
            edges |= Qt.Edge.BottomEdge
        return edges

    def _cursor_for_edges(self, edges: Qt.Edge):
        if edges in (Qt.Edge.LeftEdge | Qt.Edge.TopEdge,
                     Qt.Edge.RightEdge | Qt.Edge.BottomEdge):
            return Qt.CursorShape.SizeFDiagCursor
        if edges in (Qt.Edge.RightEdge | Qt.Edge.TopEdge,
                     Qt.Edge.LeftEdge | Qt.Edge.BottomEdge):
            return Qt.CursorShape.SizeBDiagCursor
        if edges & (Qt.Edge.LeftEdge | Qt.Edge.RightEdge):
            return Qt.CursorShape.SizeHorCursor
        if edges & (Qt.Edge.TopEdge | Qt.Edge.BottomEdge):
            return Qt.CursorShape.SizeVerCursor
        return Qt.CursorShape.ArrowCursor

    def mousePressEvent(self, e) -> None:     # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton:
            edges = self._edges_at(e.position().toPoint())
            hw = self.windowHandle()
            if hw is not None:
                if edges:
                    hw.startSystemResize(edges)
                else:
                    hw.startSystemMove()   # 空白背景拖动窗口
            e.accept()
            return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e) -> None:      # noqa: N802
        if e.buttons() == Qt.MouseButton.NoButton:
            edges = self._edges_at(e.position().toPoint())
            self.setCursor(self._cursor_for_edges(edges) if edges
                           else Qt.CursorShape.ArrowCursor)
        super().mouseMoveEvent(e)

    # ============================================================ Tab / 数据
    def _on_tab_changed(self, idx: int) -> None:
        self._stack.setCurrentIndex(idx)
        self._db.set_setting(config.KEY_LAST_TAB, "day" if idx == 1 else "long")
        # 切走之前把正在输入的内容提交
        for bar in (self._long_add, self._day_add):
            bar.flush()
        # 回到短期页时按当前视图刷新（跨零点日期窗口会移动）
        if idx == 1:
            if self._day_mode == "list":
                self._day_list.reload(scroll_to_today=False)
            elif self._day_mode == "heat":
                self._heat.refresh()

    def _on_hide_done_toggled(self, on: bool) -> None:
        self._db.set_setting_bool(config.KEY_HIDE_DONE, on)
        self._long_view.set_hide_done(on)

    def _add_long_task(self, text: str) -> None:
        self._db.add_task("long", text)
        self._long_view.reload(keep_scroll=False)

    def _add_day_task(self, text: str) -> None:
        self._db.add_task("day", text, self._selected_date.toString("yyyy-MM-dd"))
        self._day_view.reload(keep_scroll=False)

    # ============================================================ 日期导航
    def _date_key(self) -> str:
        return self._selected_date.toString("yyyy-MM-dd")

    def _render_date(self) -> None:
        d = self._selected_date
        wd = "周" + "一二三四五六日"[d.dayOfWeek() - 1]
        self._btn_date.setText(f"{d.year()}年{d.month()}月{d.day()}日 {wd}")

    def _shift_day(self, delta: int) -> None:
        self._day_add.flush()
        self._set_date(self._selected_date.addDays(delta))

    def _set_date(self, date: QDate) -> None:
        self._selected_date = date
        self._last_today = QDate.currentDate()
        self._day_view.set_day(self._date_key())
        self._render_date()
        self._update_today_button()
        self._update_addbar_hint()
        self._day_view.reload(keep_scroll=False)

    def _update_today_button(self) -> None:
        """“今天/回到今天”按钮随所看日期变化：

        - 查看今天：按钮显示「今天」并置灰（提示：已显示今天），避免被误读成
          其它日期的标签；
        - 查看其它日期：按钮显示「回到今天」，点击跳回今天，名副其实。
        """
        on_today = self._selected_date == QDate.currentDate()
        self._btn_today.setText("今天" if on_today else "回到今天")
        self._btn_today.setEnabled(not on_today)
        self._btn_today.setToolTip("已显示今天" if on_today else "回到今天")

    def _update_addbar_hint(self) -> None:
        """添加条占位符跟随所看日期：今天是“今日”，其它日期写明 M月D日。"""
        d = self._selected_date
        if d == QDate.currentDate():
            hint = "输入今日计划，Enter 保存 · Esc 取消"
        else:
            hint = f"输入{d.month()}月{d.day()}日计划，Enter 保存 · Esc 取消"
        self._day_add.set_placeholder(hint)

    def show_today(self) -> None:
        """切换/定位到今天（托盘“今日计划”也走这里）。"""
        if self._stack.currentIndex() != 1:
            self._seg.set_current_index(1)     # 触发 _on_tab_changed
        if self._day_mode != "single":
            self._day_mode_seg.set_current_index(0)
        self._day_add.flush()
        self._set_date(QDate.currentDate())

    def _open_calendar(self) -> None:
        if self._cal_popup is None:
            self._cal_popup = CalendarPopup(self, self._selected_date)
            self._cal_popup.datePicked.connect(self._set_date)
        self._cal_popup._cal.setSelectedDate(self._selected_date)
        self._cal_popup._cal.setCurrentPage(self._selected_date.year(),
                                            self._selected_date.month())
        self._cal_popup.show_below(self._btn_date)

    def _on_day_counts(self, done: int, total: int) -> None:
        self._today_counts = (done, total)
        if total <= 0:
            self._count_label.hide()
            return
        if self._selected_date == QDate.currentDate():
            text = f"今日 {done}/{total}"
        else:
            text = f"{done}/{total}"
        self._count_label.setText(text)
        self._count_label.setToolTip(f"已完成 {done} 项，共 {total} 项")
        self._count_label.show()

    def _maybe_advance_today(self) -> None:
        """跨零点后仍在“今天”视图时自动前进到新的一天（仅单日视图）。"""
        today = QDate.currentDate()
        if today != self._last_today:
            if self._day_mode == "single" and self._selected_date == self._last_today:
                self._set_date(today)
            self._last_today = today

    def _open_theme_menu(self) -> None:
        from PySide6.QtGui import QActionGroup
        menu = QMenu(self)
        group = QActionGroup(menu)
        group.setExclusive(True)
        for key, label in (("light", "浅色"), ("dark", "深色"), ("system", "跟随系统")):
            act = menu.addAction(label)
            act.setCheckable(True)
            act.setChecked(self._theme.key == key)
            act.triggered.connect(lambda _=False, k=key: self._theme.set_key(k))
            group.addAction(act)
        menu.exec(self._btn_theme.mapToGlobal(
            QPoint(0, self._btn_theme.height() + 2)))

    # ============================================================ 置顶/几何
    @property
    def pinned(self) -> bool:
        return self._pinned

    def set_pinned(self, on: bool, persist: bool = True) -> None:
        on = bool(on)
        flag_on = bool(self.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)
        if on == self._pinned and on == flag_on:
            return
        geo = self.geometry()
        self._pinned = on
        # Qt 规则：对可见窗口直接 setWindowFlag 会把窗口隐藏且新 flag 不生效。
        # 正确顺序：先主动 hide -> 修改 flag -> 再 show() 重建窗口（此时才带新 flag）。
        was_visible = self.isVisible()
        if was_visible:
            self.hide()
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, on)
        if was_visible:
            self.show()
            self.setGeometry(geo)
        self._btn_pin.blockSignals(True)
        self._btn_pin.setChecked(on)
        self._btn_pin.blockSignals(False)
        if persist:
            self._db.set_setting_bool(config.KEY_PINNED, on)
        self.pinChanged.emit(on)

    def _schedule_geo_save(self) -> None:
        self._geo_timer.start()

    def _save_geometry_now(self) -> None:
        r = self.geometry()
        self._db.save_geometry(r.x(), r.y(), r.width(), r.height())

    def moveEvent(self, e) -> None:           # noqa: N802
        super().moveEvent(e)
        self._schedule_geo_save()

    def resizeEvent(self, e) -> None:         # noqa: N802
        super().resizeEvent(e)
        self._schedule_geo_save()

    # ============================================================ 显隐/退出
    def attach_tray(self, tray) -> None:
        """由 app 注入托盘对象；关闭按钮据此选择 缩到托盘 or 退出。"""
        self._tray = tray

    def showEvent(self, e) -> None:           # noqa: N802
        super().showEvent(e)
        self._maybe_advance_today()
        # 启动时若停在“多日列表”视图，布局就绪后定位到今天分节（只锚定一次，
        # 之后保持用户滚动位置，跨零点由 _on_tab_changed 重建窗口日期）
        if (self._day_mode == "list" and not self._list_anchored
                and self._stack.currentIndex() == 1):
            self._list_anchored = True
            self._day_list.reload(scroll_to_today=True)

    def hideEvent(self, e) -> None:           # noqa: N802
        self._flush_all_edits()
        self._save_geometry_now()
        super().hideEvent(e)

    def closeEvent(self, e) -> None:          # noqa: N802
        self._flush_all_edits()
        self._save_geometry_now()
        if self._tray is not None and not self._quitting:
            # 缩到托盘继续运行
            e.ignore()
            self.hide()
            self._tray.notify_hidden_once()
            return
        e.accept()
        if not self._quitting:
            from PySide6.QtWidgets import QApplication
            QApplication.quit()

    def _flush_all_edits(self) -> None:
        for bar in (self._long_add, self._day_add):
            bar.flush()
        for view in (self._long_view, self._day_view):
            view.finish_editing_rows()
        self._day_list.finish_editing_rows()

    def quit_app(self) -> None:
        """托盘“退出程序”。"""
        self._quitting = True
        self._flush_all_edits()
        self._save_geometry_now()
        from PySide6.QtWidgets import QApplication
        QApplication.quit()

    def show_and_raise(self) -> None:
        """托盘/二次启动唤起：显示并到前台。"""
        if self.isMinimized():
            self.showNormal()
        self.show()
        self.raise_()
        self.activateWindow()
