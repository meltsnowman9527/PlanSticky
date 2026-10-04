"""日记 HTML 归一化层：Qt 文本文档 ←→ 旧版「清楚账本」的 HTML 格式。

## 为什么需要这一层

Qt 的 `QTextEdit.toHtml()` 输出的是完整 HTML 文档：

    <!DOCTYPE HTML PUBLIC ...>
    <html><head><style>p, li { white-space: pre-wrap; }</style></head>
    <body style=" font-family:'Sans Serif'; font-size:9pt; ...">
    <p style=" margin-top:0px; ... -qt-block-indent:0;">今天天气不错</p>

而旧版格式化只认 `<div>`/`<br>`/`<img>`/`<video>`，`<p style=...>` 会被整体丢弃。
两者直接互换会**丢内容**，且旧网页打开新日记会变成一坨无格式文本。

所以：Database 里存的**永远是旧版格式**，进出编辑器时经本模块转换。

## 格式定义（与旧版 app.py 的 JournalSanitizer 一致）

- 段落：`<div>文字</div>`，空行是 `<div><br></div>`
- 换行：`<br>`
- 图片：`<img src="/journal-images/{32位hex}.{png|jpg|webp|gif}" alt="...">`
- 视频：`<video src="/journal-videos/{32位hex}.{mp4|webm|mov}" controls></video>`
- 行内强调：`<b>` `<i>` `<u>`（旧版没有，是本项目新增的能力）

## 视频在 Qt 里的表示

Qt 文档不支持 `<video>` 元素，插入时会变成一个「未知对象」。因此编辑器里
把视频表示为一张**占位图片**，资源名用 `video:{uuid}.mp4` 前缀标记；
渲染时由 `journal_page.VideoBlock` 画成「▶ 文件名」的方块，点击弹窗播放。
本模块负责在 `video:` 标记与真 `<video>` 标签之间来回转换。
"""
from __future__ import annotations

import html as _html
import re

from PySide6.QtGui import QTextBlock, QTextCharFormat, QTextDocument, QTextImageFormat

from plansticky.ledger_db import IMAGE_EXTS, VIDEO_EXTS

VIDEO_PREFIX = "video:"
PLACEHOLDER = "\ufffc"           # Qt 的对象替换符（图片/视频占位）
# Qt 把 <br> 变成行分隔符 U+2028、段落分隔符 U+2029；保存时要还原成 <br>。
# 不处理会丢段内换行（例如 "行一<br>行二" 会被压成 "行一 行二"）。
QT_BREAKS = "\u2028\u2029"
ALT_SEP = "|"                    # 图片资源名里用 | 分隔 文件名 与 alt 文本
ALT_SEP_ESCAPED = "%7C"          # Qt 解析 src 会百分号编码，读取时需还原

_BLOCK_TAG_RE = re.compile(r"<(div|p)\b[^>]*>(.*?)</\1>", re.IGNORECASE | re.DOTALL)
_ANY_BLOCK_RE = re.compile(r"<(div|p)\b[^>]*>", re.IGNORECASE)
_IMG_RE = re.compile(r"<img\b[^>]*>", re.IGNORECASE)
_SRC_RE = re.compile(r'src\s*=\s*"([^"]*)"', re.IGNORECASE)
_ALT_RE = re.compile(r'alt\s*=\s*"([^"]*)"', re.IGNORECASE)
_VIDEO_RE = re.compile(r"<video\b[^>]*>\s*</video>|<video\b[^>]*/>", re.IGNORECASE)
_MEDIA_REF_RE = re.compile(
    r"/(?:journal-images|journal-videos)/([0-9a-z]+\.(?:png|jpg|webp|gif|mp4|webm|mov))",
    re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")


# ------------------------------------------------------------------ 工具
def _escape(text: str) -> str:
    return _html.escape(str(text), quote=False)


def _escape_attr(text: str) -> str:
    return _html.escape(str(text), quote=True)


def _basename(url: str) -> str:
    """从 /journal-images/xxx.jpg 或完整路径里取出文件名。"""
    text = str(url or "").strip()
    text = text.split("?", 1)[0].split("#", 1)[0]
    return text.replace("\\", "/").rsplit("/", 1)[-1]


def plain_text(html_text: str) -> str:
    """把日记 HTML 转成纯文本（归档摘要、字数统计、"内容是否为空"判定用）。

    末尾用 `" ".join(text.split())` 而不是 `re.sub(r"\\s+", ...)`：
    Python 的 `str.strip()`/`\\s` **不包含不换行空格 U+00A0**，
    而网页粘贴的内容经常带它；不处理的话「只粘贴了一串空格」会被判定为有内容。
    """
    text = _VIDEO_RE.sub(" ", str(html_text or ""))
    text = _IMG_RE.sub(" ", text)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = _ANY_BLOCK_RE.sub("\n", text)
    text = re.sub(r"</(?:div|p)>", "\n", text, flags=re.IGNORECASE)
    text = _TAG_RE.sub("", text)
    text = _html.unescape(text)
    # split() 按 Unicode 空白切分，能正确处理 U+00A0 / U+3000 等
    return " ".join(text.split())


def is_blank(html_text: str) -> bool:
    """内容是否为空（只有空白、没有图片/视频）。"""
    if referenced_media(html_text):
        return False
    return not plain_text(html_text)


def first_image_url(html_text: str) -> str:
    """取第一篇图片的 URL（归档封面用）；没有返回空串。"""
    for match in _IMG_RE.finditer(str(html_text or "")):
        src = _SRC_RE.search(match.group(0))
        if src:
            return src.group(1)
    return ""


def referenced_media(html_text: str) -> set[str]:
    """HTML 里引用到的媒体文件名集合。"""
    return {m.group(1) for m in _MEDIA_REF_RE.finditer(str(html_text or ""))}


def is_valid_media_ref(url: str) -> bool:
    """是否是合法的媒体引用（旧版规则：32 位 hex + 白名单扩展名）。"""
    name = _basename(url)
    if "." not in name:
        return False
    stem, ext = name.rsplit(".", 1)
    ext = "." + ext.lower()
    if len(stem) != 32 or not all(c in "0123456789abcdefABCDEF" for c in stem):
        return False
    return ext in IMAGE_EXTS or ext in VIDEO_EXTS


def _is_video_name(name: str) -> bool:
    return str(name).lower().startswith(VIDEO_PREFIX)


def _video_source(name: str) -> str:
    """`video:xxx.mp4` -> `/journal-videos/xxx.mp4`。"""
    base = str(name)[len(VIDEO_PREFIX):]
    if "/" in base:
        return base
    return f"/journal-videos/{base}"


# ------------------------------------------------------- 文档 -> 旧版 HTML
def doc_to_html(doc: QTextDocument) -> str:
    """遍历 QTextDocument 生成旧版格式 HTML（**不使用 toHtml()**）。"""
    parts: list[str] = []
    block: QTextBlock = doc.begin()
    while block.isValid():
        parts.append(_block_to_html(block))
        block = block.next()
    # 去掉末尾的纯空行，避免每篇日记都拖一串 <div><br></div>
    while parts and parts[-1] == "<div><br></div>":
        parts.pop()
    return "".join(parts)


def _block_to_html(block: QTextBlock) -> str:
    parts: list[str] = []
    iterator = block.begin()
    while not iterator.atEnd():
        fragment = iterator.fragment()
        if fragment.isValid():
            fmt = fragment.charFormat()
            text = fragment.text()
            if fmt.isImageFormat():
                image_fmt = fmt.toImageFormat()
                parts.append(_image_to_html(image_fmt))
            elif text:
                # 段内换行符还原成 <br>，其余文本转义后按格式包标签
                chunks = re.split(f"[{QT_BREAKS}]", text)
                for index, chunk in enumerate(chunks):
                    if index:
                        parts.append("<br>")
                    if chunk:
                        parts.append(_apply_inline(_escape(chunk), fmt))
        iterator += 1
    # 去掉对象替换符；若整块只剩空白，则视为空行
    content = "".join(parts).replace(PLACEHOLDER, "")
    if not _has_content(content):
        return "<div><br></div>"
    return f"<div>{content}</div>"


def _has_content(content: str) -> bool:
    """块内是否有实际内容（图片/视频/非空白文字）。"""
    if "<img" in content or "<video" in content:
        return True
    return bool(content.strip())


def _image_to_html(fmt: QTextImageFormat) -> str:
    raw_name, alt = _split_alt(fmt.name() or "")
    if _is_video_name(raw_name):
        source = _video_source(raw_name)
        return f'<video src="{_escape_attr(source)}" controls></video>'
    if not raw_name:
        return ""
    source = raw_name if "/" in raw_name else f"/journal-images/{raw_name}"
    return f'<img src="{_escape_attr(source)}" alt="{_escape_attr(alt)}">'


def _split_alt(name: str) -> tuple[str, str]:
    """把资源名拆成 (文件名, alt)。没有 alt 时给默认文案。"""
    text = str(name or "").replace(ALT_SEP_ESCAPED, ALT_SEP)
    if ALT_SEP in text:
        head, _sep, tail = text.partition(ALT_SEP)
        return head, (tail or "日记图片")
    return text, "日记图片"


def image_name(filename: str, alt: str = "日记图片") -> str:
    """构造图片资源名：文件名 + | + alt（alt 借此在 Qt 文档里存活）。"""
    return f"{filename}{ALT_SEP}{alt or '日记图片'}"


def _apply_inline(text: str, fmt: QTextCharFormat) -> str:
    """按字符格式包行内标签，顺序固定 b > i > u，保证转换结果稳定。"""
    if fmt.fontWeight() >= 600:
        text = f"<b>{text}</b>"
    if fmt.fontItalic():
        text = f"<i>{text}</i>"
    if fmt.fontUnderline():
        text = f"<u>{text}</u>"
    return text


# ------------------------------------------------------- 旧版 HTML -> 文档
def prepare_for_editor(content: str) -> str:
    """把旧版 HTML 转成 Qt 能正确渲染的 HTML（供 `setHtml` 使用）。

    两处改动，都是为了「Qt 不会丢信息」：

    1. `<video src="...">` -> 占位 `<img src="video:...">`（Qt 不支持 video）；
    2. 图片的 `alt` 折进 src 的路径里（`xxx.jpg%7C我的照片`）——
       `QTextImageFormat` 不保留 HTML 的 alt 属性，不折进去打开一次就丢了。
    """
    text = str(content or "")
    if not text.strip():
        return ""

    def replace_video(match: re.Match) -> str:
        src = _SRC_RE.search(match.group(0))
        if not src:
            return ""
        name = _basename(src.group(1))
        return f'<img src="{VIDEO_PREFIX}{_escape_attr(name)}" alt="视频">'

    def replace_image(match: re.Match) -> str:
        raw = match.group(0)
        src = _SRC_RE.search(raw)
        if not src:
            return ""
        url = src.group(1)
        if url.lower().startswith(VIDEO_PREFIX):
            return raw
        alt = _ALT_RE.search(raw)
        alt_text = _html.unescape(alt.group(1)) if alt else "日记图片"
        name = _basename(url)
        if not name:
            return ""
        return (f'<img src="/journal-images/{_escape_attr(name)}'
                f'{ALT_SEP_ESCAPED}{_escape_attr(alt_text)}">')

    text = _VIDEO_RE.sub(replace_video, text)
    text = _IMG_RE.sub(replace_image, text)
    return text


def editor_to_html(content: str) -> str:
    """编辑器里的 HTML（可能是旧格式或 Qt 半成品）规整成标准旧版格式。

    主要用于：粘贴进来的富文本、或从 `prepare_for_editor` 之外来源的内容。
    正常保存走 `doc_to_html()`（更精确，能拿到每个字符的格式）。
    """
    text = str(content or "")
    if not text.strip():
        return ""

    def convert_block(match: re.Match) -> str:
        inner = match.group(2)
        parts: list[str] = []
        position = 0
        # 逐个处理块内的 img / video / br / 文本
        for token in re.finditer(
                r"<img\b[^>]*>|<video\b[^>]*>\s*</video>|<video\b[^>]*/>|<br\s*/?>",
                inner, re.IGNORECASE):
            raw = inner[position:token.start()]
            if raw.strip():
                parts.append(_strip_tags(raw))
            position = token.end()
            tag = token.group(0)
            low = tag.lower()
            if low.startswith("<img"):
                src = _SRC_RE.search(tag)
                alt = _ALT_RE.search(tag)
                name = _basename(src.group(1)) if src else ""
                alt_text = _escape_attr(alt.group(1) if alt else "日记图片")
                if name.lower().startswith(VIDEO_PREFIX):
                    parts.append(f'<video src="{_escape_attr(_video_source(name))}" '
                                 f'controls></video>')
                elif name:
                    parts.append(f'<img src="/journal-images/{_escape_attr(name)}" '
                                 f'alt="{alt_text}">')
            elif low.startswith("<video"):
                src = _SRC_RE.search(tag)
                if src:
                    parts.append(f'<video src="{_escape_attr(src.group(1))}" '
                                 f'controls></video>')
            else:
                parts.append("<br>")
        tail = inner[position:]
        if tail.strip():
            parts.append(_strip_tags(tail))
        content_html = "".join(parts)
        return f"<div>{content_html}</div>" if content_html else "<div><br></div>"

    normalized = re.sub(r"<(div|p)\b[^>]*>(.*?)</\1>", convert_block, text,
                        flags=re.IGNORECASE | re.DOTALL)
    if not normalized.strip():
        normalized = convert_block(re.match(r"(?s)(.*)", text))
    # 兜底：清理不认识的标签（保留 b/i/u/br/img/video）
    normalized = re.sub(r"<(?![/]?(?:b|i|u|br|img|video|div)\b)[^>]+>", "",
                        normalized, flags=re.IGNORECASE)
    while "</div><div>" in normalized:
        normalized = normalized.replace("</div><div>", "")
    return normalized


def _strip_tags(raw: str) -> str:
    """保留行内强调标签，剥掉其余标签并转义文本。"""
    pieces: list[str] = []
    position = 0
    for token in re.finditer(r"</?(?:b|i|u)\b[^>]*>", raw, re.IGNORECASE):
        pieces.append(_escape(_html.unescape(_TAG_RE.sub("", raw[position:token.start()]))))
        pieces.append(token.group(0).lower())
        position = token.end()
    pieces.append(_escape(_html.unescape(_TAG_RE.sub("", raw[position:]))))
    return "".join(pieces)
