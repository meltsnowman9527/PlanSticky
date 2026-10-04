"""日记页：图文日记编辑 + 往日日记归档。

与旧版「每日生活 · 每日日记」对齐（见 docs/融合规格.md 3.3），并修掉旧版缺陷：

- **未保存提醒**：切换日期/切走页面时若有未保存改动，先确认再丢弃（旧版静默丢）；
- **媒体不再孤立**：图片/视频在**保存日记时**才转正，不保存就不留垃圾
  （旧版先上传后保存，未保存就永久留下孤立文件）；
- **失败不再静默**：字数/格式/媒体校验失败都明确提示。

正文存储格式见 `journal_html`：数据库里永远是旧版 ``div/br/img/video`` 格式，
所以旧网页也能打开这里写的日记。
"""
from __future__ import annotations

import os
import shutil
import uuid
from datetime import date as _date

from PySide6.QtCore import (QDate, QEvent, QObject, QPointF, QRectF, QSize,
                            QTimer, QUrl, Qt, Signal)
from PySide6.QtGui import (QColor, QFont, QImage, QPainter, QPen, QPixmap,
                           QPolygonF, QTextCharFormat, QTextCursor,
                           QTextDocument, QTextFormat, QTextImageFormat,
                           QTextObjectInterface)
from PySide6.QtWidgets import (QCalendarWidget, QDialog, QFileDialog, QFrame,
                               QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
                               QMessageBox, QPushButton, QTextEdit, QVBoxLayout,
                               QWidget)

from plansticky import config, journal_html
from plansticky.ledger_db import (IMAGE_EXTS, MAX_IMAGE_SIZE, MAX_JOURNAL_TEXT,
                                  MAX_VIDEO_SIZE, VIDEO_EXTS, Journal,
                                  LedgerDatabase, ValidationError)
from plansticky.theme import color

# 自定义文本对象类型：视频占位块（QTextObjectInterface 用）
VIDEO_OBJECT_TYPE = QTextFormat.ObjectTypes.UserObject + 1
IMAGE_FILTER = "图片 (*.png *.jpg *.jpeg *.webp *.gif)"
VIDEO_FILTER = "视频 (*.mp4 *.webm *.mov)"
PENDING_PREFIX = ".pending-"
# 图片资源的「最长边」上限。
#
# 取值权衡（用户原图是 4096x3072 那种高清照）：
# - 太小（如 320）会导致图片在编辑器里被放大显示而发糊；
# - 太大（原尺寸）会让一篇 26 张图的日记吃掉 1 GB 以上内存
#   （4096*3072*4B ≈ 50MB/张）。
# 900 是折中：窗口放大到 900px 宽仍然清晰，26 张图约 65MB，可接受。
MAX_IMAGE_RESOURCE_SIDE = 900
# 显示宽度上限，避免过宽的图撑破窄窗口
MAX_IMAGE_DISPLAY_WIDTH = 640


def today_key() -> str:
    return _date.today().strftime("%Y-%m-%d")


def human_date(day: str) -> str:
    try:
        parsed = _date.fromisoformat(str(day))
    except ValueError:
        return str(day)
    return f"{parsed.year} 年 {parsed.month} 月 {parsed.day} 日"


def _sniff_image_ext(data: bytes) -> str:
    """按魔数判断图片格式，返回扩展名；无法识别返回空串。"""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return ".gif"
    if len(data) >= 12 and data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return ".webp"
    return ""


def _sniff_video_ext(data: bytes) -> str:
    """按魔数判断视频格式；无法识别返回空串。"""
    if data.startswith(b"\x1aE\xdf\xa3"):
        return ".webm"
    if len(data) >= 12 and data[4:8] == b"ftyp":
        brands = {data[8:12]}
        for index in range(16, min(len(data), 64) - 3, 4):
            brands.add(data[index:index + 4])
        return ".mov" if b"qt  " in brands else ".mp4"
    return ""


# ======================================================== 视频占位绘制器
class VideoObjectHandler(QObject, QTextObjectInterface):
    """把 `video:` 占位对象画成「▶ 视频 · 文件名」小块。

    Qt 文档不支持 `<video>`，所以编辑器里用占位对象表示，点击由本页弹窗播放；
    保存时 `journal_html.doc_to_html` 再把它转回 `<video>` 标签。
    """

    def intrinsicSize(self, doc, pos_in_document, fmt) -> QSize:    # noqa: N802
        return QSize(180, 38)

    def drawObject(self, painter, rect, doc, pos_in_document, fmt) -> None:  # noqa: N802
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        box = QRectF(rect).adjusted(1, 1, -1, -1)
        painter.setPen(QPen(QColor(color("line")), 1))
        painter.setBrush(QColor(color("panel")))
        painter.drawRoundedRect(box, 6, 6)

        # 播放三角
        side = max(10.0, min(20.0, box.height() - 10))
        center = box.center()
        triangle_x = box.left() + 11
        from PySide6.QtGui import QPolygonF as _Poly
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(color("income")))
        painter.drawPolygon(_Poly([
            QPointF(triangle_x, center.y() - side / 2),
            QPointF(triangle_x, center.y() + side / 2),
            QPointF(triangle_x + side * 0.8, center.y()),
        ]))

        raw = str(fmt.name() or "")
        filename = raw[len(journal_html.VIDEO_PREFIX):] if raw.startswith(
            journal_html.VIDEO_PREFIX) else raw
        filename = journal_html._split_alt(filename)[0]
        painter.setPen(QColor(color("text")))
        font = QFont(painter.font())
        font.setPointSizeF(max(7.5, font.pointSizeF() - 1.5))
        painter.setFont(font)
        painter.drawText(
            QRectF(triangle_x + side + 4, box.top(), box.width() - side - 16,
                   box.height()),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            f"视频 · {filename[:18]}")
        painter.restore()


# ============================================================== 播放弹窗
class VideoPlayerDialog(QDialog):
    """内嵌 QMediaPlayer 的视频播放窗口（关闭即停止）。"""

    def __init__(self, path: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle(f"播放视频 · {os.path.basename(path)}")
        self.setModal(True)
        self.resize(640, 440)
        self._player = None

        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(6)

        self._status = QLabel("正在准备播放…", self)
        self._status.setObjectName("cardHint")
        lay.addWidget(self._status)

        try:
            from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
            from PySide6.QtMultimediaWidgets import QVideoWidget
        except ImportError as exc:                       # pragma: no cover
            self._status.setText(f"当前环境不支持视频播放：{exc}")
            close = QPushButton("关闭", self)
            close.clicked.connect(self.accept)
            lay.addWidget(close)
            return

        video = QVideoWidget(self)
        video.setMinimumHeight(220)
        video.setStyleSheet("background:#000; border-radius:6px;")
        lay.addWidget(video, 1)

        self._player = QMediaPlayer(self)
        audio = QAudioOutput(self)
        audio.setVolume(0.8)
        self._player.setAudioOutput(audio)
        self._player.setVideoOutput(video)
        self._player.errorOccurred.connect(
            lambda _err, msg: self._status.setText(f"播放失败：{msg}"))
        self._player.setSource(QUrl.fromLocalFile(path))

        close = QPushButton("关闭", self)
        close.clicked.connect(self.accept)
        lay.addWidget(close)

    def showEvent(self, event) -> None:      # noqa: N802
        super().showEvent(event)
        if self._player is not None:
            self._player.play()
            self._status.setText("正在播放，点「关闭」停止")

    def closeEvent(self, event) -> None:     # noqa: N802
        if self._player is not None:
            self._player.stop()
        super().closeEvent(event)


# ================================================================= 日记页
class JournalPage(QWidget):
    """日记页：编辑器 + 往日日记归档。"""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._db = LedgerDatabase()
        self._day = today_key()
        self._dirty = False
        self._loading = False
        self._handler = VideoObjectHandler()
        self._pending: dict[str, object] = {}   # 文件名 -> 临时路径 / ('copy', 源路径)
        self._build_ui()
        self.refresh()
        self._load()
        self._editor.installEventFilter(self)
        # 编辑器宽度变化时重排图片（防止窄窗口溢出、拉宽后发糊）
        self._editor.viewport().installEventFilter(self)
        self._reflow_timer = QTimer(self)
        self._reflow_timer.setSingleShot(True)
        self._reflow_timer.setInterval(220)
        self._reflow_timer.timeout.connect(self._reflow_images)
        self._last_reflow_width = 0

    # ---------------------------------------------------------------- UI
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(6)
        root.addWidget(self._build_editor(), 3)
        root.addWidget(self._build_archive(), 2)

    def _build_editor(self) -> QWidget:
        card = QFrame(self)
        card.setObjectName("cardFlat")
        v = QVBoxLayout(card)
        v.setContentsMargins(10, 8, 10, 8)
        v.setSpacing(5)

        # ---- 日期导航
        nav = QWidget(card)
        nh = QHBoxLayout(nav)
        nh.setContentsMargins(0, 0, 0, 0)
        nh.setSpacing(2)
        self._btn_prev = self._nav_button(nav, "‹", "前一天", lambda: self.shift_day(-1))
        nh.addWidget(self._btn_prev)

        self._date_label = QLabel("", nav)
        self._date_label.setObjectName("cardTitle")
        self._date_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._date_label.setMinimumWidth(110)
        nh.addWidget(self._date_label)

        self._btn_next = self._nav_button(nav, "›", "后一天", lambda: self.shift_day(1))
        nh.addWidget(self._btn_next)
        nh.addStretch(1)

        self._btn_pick = QPushButton("选日期", nav)
        self._btn_pick.setProperty("cls", "today")
        self._btn_pick.setFixedHeight(24)
        self._btn_pick.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_pick.setToolTip("选择任意日期")
        self._btn_pick.clicked.connect(self._pick_date)
        nh.addWidget(self._btn_pick)

        self._btn_today = QPushButton("今天", nav)
        self._btn_today.setProperty("cls", "today")
        self._btn_today.setFixedHeight(24)
        self._btn_today.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_today.setToolTip("回到今天")
        self._btn_today.clicked.connect(self.goto_today)
        nh.addWidget(self._btn_today)
        v.addWidget(nav)

        # ---- 格式与媒体工具栏
        tools = QWidget(card)
        th = QHBoxLayout(tools)
        th.setContentsMargins(0, 0, 0, 0)
        th.setSpacing(3)
        self._fmt_buttons: dict[str, QPushButton] = {}
        for label, tip, key in (("B", "粗体 (Ctrl+B)", "bold"),
                                ("I", "斜体 (Ctrl+I)", "italic"),
                                ("U", "下划线 (Ctrl+U)", "underline")):
            btn = QPushButton(label, tools)
            btn.setCheckable(True)
            btn.setFixedSize(24, 22)
            btn.setToolTip(tip)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            font = btn.font()
            font.setBold(key == "bold")
            font.setItalic(key == "italic")
            font.setUnderline(key == "underline")
            btn.setFont(font)
            btn.clicked.connect(lambda _=False, k=key: self._toggle_format(k))
            th.addWidget(btn)
            self._fmt_buttons[key] = btn

        self._btn_image = QPushButton("＋ 图片", tools)
        self._btn_image.setProperty("cls", "nav")
        self._btn_image.setToolTip("插入图片（可多选）")
        self._btn_image.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_image.clicked.connect(self.insert_images)
        th.addWidget(self._btn_image)

        self._btn_video = QPushButton("＋ 视频", tools)
        self._btn_video.setProperty("cls", "nav")
        self._btn_video.setToolTip("插入视频")
        self._btn_video.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_video.clicked.connect(self.insert_video)
        th.addWidget(self._btn_video)
        th.addStretch(1)
        v.addWidget(tools)

        hint = QLabel("图片单张最大 20 MB · MP4、WebM、MOV 视频单个最大 300 MB", card)
        hint.setObjectName("cardHint")
        hint.setWordWrap(True)
        v.addWidget(hint)

        # ---- 编辑区
        self._editor = QTextEdit(card)
        self._editor.setPlaceholderText("记录今天发生的事、感受或想法……")
        self._editor.setAcceptRichText(True)
        self._editor.setMinimumHeight(150)
        self._editor.textChanged.connect(self._on_text_changed)
        self._editor.cursorPositionChanged.connect(self._sync_format_buttons)
        self._editor.mouseReleaseEvent = self._on_editor_click     # type: ignore[assignment]
        v.addWidget(self._editor, 1)

        # ---- 状态 + 操作
        actions = QWidget(card)
        ah = QHBoxLayout(actions)
        ah.setContentsMargins(0, 0, 0, 0)
        ah.setSpacing(6)
        self._status = QLabel("", actions)
        self._status.setObjectName("cardHint")
        ah.addWidget(self._status)
        ah.addStretch(1)
        self._btn_delete = QPushButton("删除", actions)
        self._btn_delete.setProperty("cls", "nav")
        self._btn_delete.setProperty("role", "danger")
        self._btn_delete.setMinimumHeight(26)
        self._btn_delete.clicked.connect(self.delete_journal)
        ah.addWidget(self._btn_delete)
        self._btn_save = QPushButton("保存日记", actions)
        self._btn_save.setProperty("cls", "today")
        self._btn_save.setMinimumHeight(26)
        self._btn_save.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_save.clicked.connect(lambda: self.save())
        ah.addWidget(self._btn_save)
        v.addWidget(actions)
        return card

    def _nav_button(self, parent: QWidget, text: str, tip: str, slot) -> QPushButton:
        btn = QPushButton(text, parent)
        btn.setProperty("cls", "nav")
        btn.setFixedSize(22, 24)
        btn.setToolTip(tip)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.clicked.connect(slot)
        return btn

    def _build_archive(self) -> QWidget:
        card = QFrame(self)
        card.setObjectName("cardFlat")
        v = QVBoxLayout(card)
        v.setContentsMargins(10, 8, 10, 8)
        v.setSpacing(5)

        head = QWidget(card)
        hh = QHBoxLayout(head)
        hh.setContentsMargins(0, 0, 0, 0)
        hh.setSpacing(4)
        title = QLabel("往日日记", head)
        title.setObjectName("cardTitle")
        hh.addWidget(title)
        hh.addStretch(1)
        self._count_label = QLabel("0 篇", head)
        self._count_label.setObjectName("cardHint")
        hh.addWidget(self._count_label)
        v.addWidget(head)

        self._archive = QListWidget(card)
        self._archive.setFrameShape(QFrame.Shape.NoFrame)
        self._archive.setStyleSheet(
            f"QListWidget {{ background:transparent; border:none; }}"
            f"QListWidget::item {{ border-bottom:1px solid {color('line')}; padding:3px; }}"
            f"QListWidget::item:selected {{ background:{color('accentSoft')}; }}")
        self._archive.itemActivated.connect(self._open_archive_item)
        self._archive.itemDoubleClicked.connect(self._open_archive_item)
        v.addWidget(self._archive, 1)

        self._archive_empty = QLabel("保存第一篇日记后，会显示在这里", card)
        self._archive_empty.setObjectName("emptyHint")
        self._archive_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(self._archive_empty)
        return card

    def _install_video_handler(self) -> None:
        layout = self._editor.document().documentLayout()
        layout.registerHandler(VIDEO_OBJECT_TYPE, self._handler)

    # ------------------------------------------------------------ 图片重排
    def eventFilter(self, obj, event) -> bool:      # noqa: N802
        """监听编辑区尺寸变化，稍后重排图片。"""
        if obj is self._editor.viewport() and event.type() == QEvent.Type.Resize:
            width = self._editor.viewport().width()
            if width > 120 and abs(width - self._last_reflow_width) > 12:
                self._last_reflow_width = width
                self._reflow_timer.start()
        return super().eventFilter(obj, event)

    def _reflow_images(self) -> None:
        """按当前可用宽度重新计算图片显示宽度。

        只改图片的显示宽度，不动正文文本，**也不能把日记标记成"有未保存改动"**：
        改宽度同样会触发 `textChanged`，若不屏蔽，用户只是拉宽窗口看一眼旧日记，
        切日期或退出时就会莫名弹出"日记还没保存"。
        """
        if not self.isVisible() and self._editor.viewport().width() <= 0:
            return
        available = self._editor.viewport().width() - 16
        if available < 120:
            return
        document = self._editor.document()
        cursor = self._editor.textCursor()
        saved = cursor.position()
        previous_loading = self._loading
        self._loading = True              # 屏蔽 textChanged 引发的 _dirty
        try:
            block = document.begin()
            while block.isValid():
                iterator = block.begin()
                while not iterator.atEnd():
                    fragment = iterator.fragment()
                    if fragment.isValid() and fragment.charFormat().isImageFormat():
                        image_fmt = fragment.charFormat().toImageFormat()
                        name = str(image_fmt.name() or "")
                        if name and not name.lower().startswith(journal_html.VIDEO_PREFIX):
                            resource = document.resource(
                                QTextDocument.ResourceType.ImageResource, QUrl(name))
                            width = getattr(resource, "width", None)
                            if width is not None and width() > 0:
                                target_width = float(max(48, min(
                                    width(), available, MAX_IMAGE_DISPLAY_WIDTH)))
                                if abs(image_fmt.width() - target_width) > 0.5:
                                    target = fragment.position()
                                    if target >= 0:
                                        cursor.setPosition(target)
                                        cursor.movePosition(
                                            QTextCursor.MoveOperation.Right,
                                            QTextCursor.MoveMode.KeepAnchor, 1)
                                        updated = cursor.charFormat().toImageFormat()
                                        updated.setWidth(target_width)
                                        cursor.setCharFormat(updated)
                                        cursor.clearSelection()
                    iterator += 1
                block = block.next()
            cursor.setPosition(min(saved, max(0, document.characterCount() - 1)))
            self._editor.setTextCursor(cursor)
        finally:
            self._loading = previous_loading
        # 重排不是用户编辑，状态显示不能变成"未保存"
        if not self._dirty:
            self._render_status()

    # ------------------------------------------------------------ 外部接口
    def has_unsaved(self) -> bool:
        return self._dirty

    def confirm_discard(self) -> bool:
        """有未保存改动时询问；返回 True 表示可以继续（已保存或明确丢弃）。

        测试用环境变量 `PLANSTICKY_UNSAVED_ANSWER` 控制自动应答
        （save / discard / cancel），避免离屏环境下模态框永久阻塞。
        """
        if not self._dirty:
            return True
        auto = os.environ.get("PLANSTICKY_UNSAVED_ANSWER", "").strip().lower()
        if auto == "save":
            return self.save(quiet=True)
        if auto == "discard":
            return True
        if auto == "cancel":
            return False
        answer = QMessageBox.question(
            self.window(), "日记还没保存",
            f"{human_date(self._day)} 的日记还没保存。\n\n先保存再离开吗？",
            QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save)
        if answer == QMessageBox.StandardButton.Save:
            return self.save(quiet=True)
        return answer == QMessageBox.StandardButton.Discard

    def refresh(self) -> None:
        """重载归档与状态；编辑器内容不动，避免打断正在写的内容。"""
        self._render_archive()
        self._render_status()

    def set_date(self, day: str) -> bool:
        """切换日期。有未保存内容时先询问；返回是否切换成功。"""
        day = str(day or "")
        if not day:
            return False
        try:
            _date.fromisoformat(day)
        except ValueError:
            return False
        if day == self._day:
            return True
        if not self.confirm_discard():
            return False
        self._day = day
        self._cleanup_pending()
        self._load()
        return True

    def shift_day(self, delta: int) -> None:
        parsed = _date.fromisoformat(self._day)
        self.set_date(parsed.fromordinal(parsed.toordinal() + delta).strftime("%Y-%m-%d"))

    def goto_today(self) -> None:
        self.set_date(today_key())

    # ------------------------------------------------------------ 载入/保存
    def _load(self) -> None:
        self._loading = True
        record = self._db.get_journal(self._day)
        if record is None:
            self._editor.clear()
        elif record.format == "html":
            self._editor.setHtml(journal_html.prepare_for_editor(record.content))
        else:
            # 旧版纯文本日记：转成 HTML 后统一格式
            self._editor.setHtml(f"<div>{journal_html._escape(record.content)}</div>")
        # setHtml 是新建文档，必须先设内容再注册资源与视频占位
        self._install_video_handler()
        # 重置「当前字符格式」并回到开头：否则上一天如果切成过粗体/斜体，
        # 这个格式会残留，新一天敲进去的字会莫名变粗（用户在 7-26 看到的现象）。
        self._reset_editor_cursor()
        # 用「按窗口宽度估算」的可用宽度，而不是此刻可能还没定型的 viewport 宽度
        estimated = max(160, self.width() - 60)
        self._register_document_images(estimated)
        self._loading = False
        self._dirty = False
        self._render_nav()
        self._render_status(record)
        # 重建归档列表：让「当前打开那天」的标记跟着走（否则会停在旧日期）
        self._render_archive()
        self._sync_format_buttons()

    def _reset_editor_cursor(self) -> None:
        """光标回到开头，并把当前字符格式清成「和开头一致」。

        清格式必须用「插入点处的实际格式」（`charFormat()`），
        不能自己造一个空 QTextCharFormat —— 那会把字体族/字号也一起抹掉，
        导致新输入的文字与正文不一致。
        """
        cursor = self._editor.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.Start)
        self._editor.setTextCursor(cursor)
        base = cursor.charFormat()
        base.setFontWeight(QFont.Weight.Normal)
        base.setFontItalic(False)
        base.setFontUnderline(False)
        self._editor.setCurrentCharFormat(base)

    def _register_document_images(self, available: int | None = None) -> None:
        """把正文里的图片注册为文档资源，并限制显示尺寸。

        **资源键必须用「文档里图片对象的 name」原样**：Qt 渲染时拿这个键去查
        资源表，键不一致就渲染成空白。另外正文用的是旧版 web 风格路径
        （`/journal-images/x.jpg`），若直接当文件路径，在 Windows 上会被解析成
        当前盘根目录（`C:\\journal-images\\...`）而找不到文件——所以统一转成
        绝对路径再加载。

        顺带按最长边缩放并写回显示宽度：旧日记里有单张 5~7 MB、几千像素宽的图，
        一篇 26 张，按原尺寸渲染会吃掉几百 MB 内存并撑破窗口。

        `available` 为编辑器可用宽度，由调用方按**最终布局**算出：
        载入过程中 `_editor.viewport().width()` 可能还是旧值，直接用会让图片
        比窗口还宽（出现横向滚动条）。
        """
        document = self._editor.document()
        cursor = self._editor.textCursor()
        previous_loading = self._loading
        self._loading = True          # 改图片宽度会触发 textChanged，不能算用户编辑
        try:
            block = document.begin()
            while block.isValid():
                iterator = block.begin()
                while not iterator.atEnd():
                    fragment = iterator.fragment()
                    if fragment.isValid() and fragment.charFormat().isImageFormat():
                        self._register_one_image(document, cursor, fragment, available)
                    iterator += 1
                block = block.next()
            self._editor.setTextCursor(cursor)
        finally:
            self._loading = previous_loading

    def _register_one_image(self, document, cursor, fragment,
                            available: int | None = None) -> None:
        """为一个图片对象加载文件、按需缩放、注册资源并写回显示宽度。"""
        image_fmt = fragment.charFormat().toImageFormat()
        name = str(image_fmt.name() or "")
        if not name:
            return
        if name.lower().startswith(journal_html.VIDEO_PREFIX):
            pixmap = QPixmap(1, 1)
            pixmap.fill(QColor(0, 0, 0, 0))
            document.addResource(QTextDocument.ResourceType.ImageResource,
                                 QUrl(name), pixmap)
            return
        path = journal_html.media_local_path(name)
        if not path or not os.path.isfile(path):
            return
        image = QImage(path)
        if image.isNull():
            return
        # 按最长边约束资源尺寸（900px），既保证清晰度又控制内存；
        # 与显示宽度解耦：显示宽度按视口给，Qt 会用高分辨率资源缩放出清晰的图。
        if max(image.width(), image.height()) > MAX_IMAGE_RESOURCE_SIDE:
            image = image.scaled(MAX_IMAGE_RESOURCE_SIDE, MAX_IMAGE_RESOURCE_SIDE,
                                 Qt.AspectRatioMode.KeepAspectRatio,
                                 Qt.TransformationMode.SmoothTransformation)
        document.addResource(QTextDocument.ResourceType.ImageResource,
                             QUrl(name), image)
        # 宽度必须写回文档：toImageFormat() 拿到的是副本，改副本不影响文档。
        # 步骤：选中该图片对象 -> 取 QTextImageFormat 副本改宽 ->
        # setCharFormat 写回（QTextImageFormat 继承自 QTextCharFormat）。
        target = fragment.position()
        if target < 0:
            return
        cursor.setPosition(target)
        cursor.movePosition(QTextCursor.MoveOperation.Right,
                            QTextCursor.MoveMode.KeepAnchor, 1)
        updated = cursor.charFormat().toImageFormat()
        updated.setWidth(float(self._display_width(image, available)))
        cursor.setCharFormat(updated)
        cursor.clearSelection()

    def _display_width(self, image: QImage, available: int | None = None) -> int:
        """图片在编辑器里的显示宽度。

        取「可用宽度」与「资源实际宽度」的较小值：
        可用宽度比图片大时按原尺寸显示（不放大、不糊），
        比图片小时按可用宽度缩（Qt 用高分辨率资源缩，清晰）。

        `available` 由调用方显式传入：载入过程中 `_editor.viewport().width()`
        可能还是旧布局值，直接用会导致图片比窗口宽（出现横向滚动条）。
        """
        if available is None:
            available = self._editor.viewport().width() - 16
        if available < 120:
            available = 320            # 布局尚未定型时的兜底
        return int(max(48, min(image.width(), available, MAX_IMAGE_DISPLAY_WIDTH)))

    def _render_nav(self) -> None:
        self._date_label.setText(human_date(self._day))
        self._btn_today.setEnabled(self._day != today_key())

    def _render_status(self, record: Journal | None = None) -> None:
        if record is None and not self._loading:
            record = self._db.get_journal(self._day)
        if self._dirty:
            self._status.setText("有未保存的改动")
            self._status.setStyleSheet(f"color:{color('warn')};")
        elif record is not None:
            self._status.setText("已保存，可继续编辑")
            self._status.setStyleSheet(f"color:{color('faint')};")
        else:
            self._status.setText("这一天还没有记录")
            self._status.setStyleSheet(f"color:{color('faint')};")
        self._btn_delete.setVisible(record is not None or self._dirty)

    def _render_archive(self) -> None:
        """重建往日日记列表。当前打开的那天用「粗体 + 主题色」标记。

        注意两个坑：
        1. 标记必须在这里重建 —— `set_date()` 换了日期后如果不重建，
           列表上的标记会停在旧日期，与正文对不上。
        2. 粗体走 `Qt.ItemDataRole.FontRole`。用 `item.setFont()` 时
           `item.font().bold()` 读回来不一定是真值（受 item 状态影响），
           FontRole 是委托取值时直接用的，最可靠。
        """
        journals = self._db.list_journals()
        self._count_label.setText(f"{len(journals)} 篇")
        self._archive.clear()
        self._archive_empty.setVisible(not journals)
        self._archive.setVisible(bool(journals))
        for record in journals:
            item = QListWidgetItem(self._archive)
            item.setData(Qt.ItemDataRole.UserRole, record.date)
            item.setSizeHint(QSize(0, 34))
            text = journal_html.plain_text(record.content)
            summary = text[:52] if text else "（只含图片或视频）"
            item.setText(f"{record.date}　{summary}")
            item.setToolTip(f"{record.date} · 双击打开"
                            + ("（当前打开）" if record.date == self._day else ""))
            font = item.font()
            if record.date == self._day:
                font.setBold(True)
            item.setData(Qt.ItemDataRole.FontRole, font)
            if record.date == self._day:
                # 当前这天的日期文字用主题色，和加粗一起构成"你在这里"的标记
                item.setForeground(QColor(color("accent")))

    def save(self, quiet: bool = False) -> bool:
        """保存到数据库；返回是否成功。"""
        html_text = journal_html.doc_to_html(self._editor.document())
        if journal_html.is_blank(html_text):
            self._notify("请先写下日记内容", "error")
            return False
        text = journal_html.plain_text(html_text)
        if len(text) > MAX_JOURNAL_TEXT:
            self._notify(f"日记文字最多 {MAX_JOURNAL_TEXT} 个字（当前 {len(text)}）", "error")
            return False
        if not self._commit_pending_media(html_text):
            return False
        # 媒体转正后重新序列化：资源名 → 真实 URL
        html_text = journal_html.doc_to_html(self._editor.document())
        try:
            self._db.save_journal(self._day, html_text, "html")
        except ValidationError as exc:
            self._notify(str(exc), "error")
            return False
        self._dirty = False
        self._render_archive()
        self._render_status()
        if not quiet:
            self._notify("日记已保存")
        return True

    def delete_journal(self) -> None:
        record = self._db.get_journal(self._day)
        if record is None and not self._dirty:
            self._notify("这一天还没有日记", "error")
            return
        answer = QMessageBox.question(
            self.window(), "删除日记",
            f"确定删除 {human_date(self._day)} 的日记吗？\n\n删除后不可恢复。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._db.delete_journal(self._day)
        self._cleanup_pending()
        self._load()
        self._render_archive()
        self._notify("日记已删除")

    # ------------------------------------------------------------ 编辑动作
    def _on_text_changed(self) -> None:
        if self._loading:
            return
        if not self._dirty:
            self._dirty = True
        self._render_status()
        self._sync_format_buttons()

    def _sync_format_buttons(self) -> None:
        fmt = self._editor.currentCharFormat()
        self._fmt_buttons["bold"].setChecked(fmt.fontWeight() >= 600)
        self._fmt_buttons["italic"].setChecked(fmt.fontItalic())
        self._fmt_buttons["underline"].setChecked(fmt.fontUnderline())

    def _toggle_format(self, key: str) -> None:
        fmt = self._editor.currentCharFormat()
        if key == "bold":
            fmt.setFontWeight(400 if fmt.fontWeight() >= 600 else 700)
        elif key == "italic":
            fmt.setFontItalic(not fmt.fontItalic())
        elif key == "underline":
            fmt.setFontUnderline(not fmt.fontUnderline())
        self._editor.mergeCurrentCharFormat(fmt)
        self._editor.setFocus()
        self._sync_format_buttons()

    def _on_editor_click(self, event) -> None:
        """点击视频占位块时弹窗播放。"""
        if event.button() == Qt.MouseButton.LeftButton:
            day = self._video_at(event.position().toPoint())
            if day:
                self._play_video(day)
                return
        QTextEdit.mouseReleaseEvent(self._editor, event)

    def _video_at(self, point) -> str:
        """返回点击位置下的视频文件名（无则空串）。"""
        cursor = self._editor.cursorForPosition(point)
        block = cursor.block()
        iterator = block.begin()
        position = block.position()
        while not iterator.atEnd():
            fragment = iterator.fragment()
            if fragment.isValid():
                fmt = fragment.charFormat()
                length = fragment.length()
                if (fmt.isImageFormat() and position <= cursor.position()
                        < position + max(1, length)):
                    image_fmt = fmt.toImageFormat()
                    raw = str(image_fmt.name() or "")
                    if raw.startswith(journal_html.VIDEO_PREFIX):
                        name = raw[len(journal_html.VIDEO_PREFIX):]
                        return journal_html._split_alt(name)[0]
                position += length
            iterator += 1
        return ""

    def _play_video(self, filename: str) -> None:
        path = os.path.join(config.journal_videos_dir(), filename)
        if not os.path.isfile(path):
            # 可能是尚未转正的待保存视频
            for name, source in self._pending.items():
                if name == filename and isinstance(source, tuple):
                    path = str(source[1])
                    break
            else:
                self._notify(f"视频文件不存在：{filename}", "error")
                return
        VideoPlayerDialog(path, self.window()).exec()

    def _pick_date(self) -> None:
        dialog = QDialog(self.window())
        dialog.setWindowTitle("选择日期")
        lay = QVBoxLayout(dialog)
        cal = QCalendarWidget(dialog)
        cal.setGridVisible(False)
        cal.setSelectedDate(QDate.fromString(self._day, "yyyy-MM-dd"))
        lay.addWidget(cal)
        cal.clicked.connect(lambda _d: dialog.accept())
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.set_date(cal.selectedDate().toString("yyyy-MM-dd"))

    # ------------------------------------------------------------ 媒体插入
    def insert_images(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self.window(), "插入图片（可多选）", "", IMAGE_FILTER)
        if not paths:
            return
        added = 0
        for path in paths:
            if self._insert_image_file(path):
                added += 1
        if added:
            self._notify(f"已插入 {added} 张图片，保存日记后生效")

    def _insert_image_file(self, path: str) -> bool:
        try:
            with open(path, "rb") as fh:
                data = fh.read()
        except OSError as exc:
            self._notify(f"读取失败：{os.path.basename(path)}（{exc}）", "error")
            return False
        if len(data) > MAX_IMAGE_SIZE:
            self._notify(f"{os.path.basename(path)} 超过 20 MB", "error")
            return False
        ext = _sniff_image_ext(data) or os.path.splitext(path)[1].lower()
        if ext not in IMAGE_EXTS:
            self._notify(f"{os.path.basename(path)} 不是支持的图片格式"
                         "（仅 PNG、JPEG、WebP、GIF）", "error")
            return False
        filename = f"{uuid.uuid4().hex}{ext}"
        # 修旧版缺陷：先落临时文件，保存日记时才转正；不保存就删掉
        temp = os.path.join(config.journal_images_dir(), f"{PENDING_PREFIX}{filename}")
        try:
            with open(temp, "wb") as fh:
                fh.write(data)
        except OSError as exc:
            self._notify(f"写入失败：{exc}", "error")
            return False
        image = QImage(temp)
        if image.isNull():
            self._notify(f"{os.path.basename(path)} 无法解码为图片", "error")
            try:
                os.remove(temp)
            except OSError:
                pass
            return False
        self._pending[filename] = temp
        self._insert_media(filename, "日记图片", image=image)
        return True

    def insert_video(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self.window(), "插入视频", "", VIDEO_FILTER)
        if not path:
            return
        try:
            size = os.path.getsize(path)
        except OSError as exc:
            self._notify(f"读取失败：{exc}", "error")
            return
        if size > MAX_VIDEO_SIZE:
            self._notify(f"{os.path.basename(path)} 超过 300 MB", "error")
            return
        try:
            with open(path, "rb") as fh:
                head = fh.read(64)
        except OSError as exc:
            self._notify(f"读取失败：{exc}", "error")
            return
        ext = _sniff_video_ext(head) or os.path.splitext(path)[1].lower()
        if ext not in VIDEO_EXTS:
            self._notify(f"{os.path.basename(path)} 不是支持的视频格式"
                         "（仅 MP4、WebM、MOV）", "error")
            return
        filename = f"{uuid.uuid4().hex}{ext}"
        # 视频可能很大：先记住源路径，保存时才拷贝
        self._pending[filename] = ("copy", path)
        self._insert_media(filename, "视频", video=True)
        self._notify("视频已插入，保存日记后生效")

    def _insert_media(self, filename: str, alt: str, image: QImage | None = None,
                      video: bool = False) -> None:
        cursor = self._editor.textCursor()
        cursor.beginEditBlock()
        if not cursor.atBlockStart():
            cursor.insertBlock()
        fmt = QTextImageFormat()
        full_name = journal_html.image_name(filename, alt)
        if video:
            fmt.setObjectType(VIDEO_OBJECT_TYPE)
            fmt.setName(journal_html.VIDEO_PREFIX + full_name)
        else:
            fmt.setName(full_name)
            if image is not None and not image.isNull():
                # 资源键必须与 fmt.name() 完全一致，否则 doc_to_html 取不到
                scaled = image
                if max(image.width(), image.height()) > MAX_IMAGE_RESOURCE_SIDE:
                    scaled = image.scaled(
                        MAX_IMAGE_RESOURCE_SIDE, MAX_IMAGE_RESOURCE_SIDE,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation)
                self._editor.document().addResource(
                    QTextDocument.ResourceType.ImageResource,
                    QUrl(full_name), scaled)
                fmt.setWidth(float(self._display_width(scaled)))
        cursor.insertImage(fmt)
        cursor.insertBlock()
        cursor.endEditBlock()
        self._editor.setTextCursor(cursor)
        self._editor.setFocus()

    # ------------------------------------------------------------ 待保存媒体
    def _commit_pending_media(self, html_text: str) -> bool:
        """把待落盘的媒体转正；本次未被引用的待保存媒体直接丢弃。"""
        referenced = journal_html.referenced_media(html_text)
        for filename, source in list(self._pending.items()):
            self._pending.pop(filename, None)
            if isinstance(source, str):
                target = os.path.join(config.journal_images_dir(), filename)
                if filename in referenced:
                    try:
                        os.replace(source, target)
                    except OSError as exc:
                        self._notify(f"图片保存失败：{exc}", "error")
                        return False
                else:
                    self._remove_quiet(source)
            elif isinstance(source, tuple) and source and source[0] == "copy":
                if filename in referenced:
                    target = os.path.join(config.journal_videos_dir(), filename)
                    try:
                        shutil.copy2(source[1], target)
                    except OSError as exc:
                        self._notify(f"视频保存失败：{exc}", "error")
                        return False
        self._cleanup_orphan_pending()
        return True

    def _cleanup_pending(self) -> None:
        """丢弃所有待保存媒体（取消编辑/切换日期时调用）。"""
        for filename, source in list(self._pending.items()):
            self._pending.pop(filename, None)
            if isinstance(source, str):
                self._remove_quiet(source)
        self._cleanup_orphan_pending()

    def _cleanup_orphan_pending(self) -> None:
        """清掉目录里遗留的 .pending- 临时文件（上次崩溃留下的）。"""
        directory = config.journal_images_dir()
        try:
            names = os.listdir(directory)
        except OSError:
            return
        for name in names:
            if name.startswith(PENDING_PREFIX):
                self._remove_quiet(os.path.join(directory, name))

    @staticmethod
    def _remove_quiet(path: str) -> None:
        try:
            os.remove(path)
        except OSError:
            pass

    # ------------------------------------------------------------ 归档
    def _open_archive_item(self, item: QListWidgetItem) -> None:
        day = item.data(Qt.ItemDataRole.UserRole)
        if not day:
            return
        if self.set_date(str(day)):
            self._editor.setFocus()

    def _notify(self, text: str, level: str = "ok") -> None:
        win = self.window()
        if hasattr(win, "toast"):
            win.toast(text, level)
