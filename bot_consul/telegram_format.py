"""
Ответ LLM для Telegram в режиме HTML: <b>, <a>, <br/>, списки с «•».

Длинное тире в начале строки (списки) приводим к «• »; сущности раскрываем;
остальной HTML чистим до подмножества Telegram HTML.
"""

from __future__ import annotations

import html as html_module
import re
from html import escape as html_escape

from bs4 import BeautifulSoup, Comment, NavigableString

from typing import Union

# Типичные заголовки в КАПС (из старых промптов / копипаст модели) → нормальный регистр + <b>
_CAPS_SECTION_HEADERS: tuple[tuple[str, str], ...] = (
    ("ОФИЦИАЛЬНЫЕ ИСТОЧНИКИ:", "<b>Официальные источники:</b>"),
    ("ПРЕДУПРЕЖДЕНИЯ:", "<b>Предупреждения:</b>"),
    ("НЕПОДТВЕРЖДЁННАЯ ИНФОРМАЦИЯ:", "<b>Неподтверждённая информация:</b>"),
    ("НЕПОДТВЕРЖДЕННАЯ ИНФОРМАЦИЯ:", "<b>Неподтверждённая информация:</b>"),
    ("РЕКОМЕНДАЦИИ ПУТЕШЕСТВЕННИКАМ:", "<b>Рекомендации путешественникам:</b>"),
    ("ПОЛЕЗНЫЕ СОВЕТЫ ПО СБОРАМ:", "<b>Полезные советы по сборам:</b>"),
    ("ОФИЦИАЛЬНЫЕ ТРЕБОВАНИЯ:", "<b>Официальные требования:</b>"),
    ("ИСТОЧНИКИ:", "<b>Источники:</b>"),
    ("ССЫЛКИ:", "<b>Ссылки:</b>"),
    ("КРАТКОЕ РЕЗЮМЕ:", "<b>Краткое резюме:</b>"),
)


def _caps_section_headers_to_bold(s: str) -> str:
    """Подмена устоявшихся заголовков в КАПС на <b>…</b> в обычном регистре."""
    for old, new in _CAPS_SECTION_HEADERS:
        s = s.replace(old, new)
    return s


# Подмножество https://core.telegram.org/bots/api#html-style
_ALLOWED_TAGS = frozenset({"b", "strong", "i", "em", "u", "s", "code", "pre", "a", "br"})


def _bullet_lines_to_bullet_char(s: str) -> str:
    return re.sub(r"(?m)^(\s*)[\*\-]\s+", r"\1• ", s)


def _em_dash_lines_to_bullet(s: str) -> str:
    """Строки, начинающиеся с длинного тире «— », делаем маркером «• »."""
    return re.sub(r"(?m)^(\s*)—\s+", r"\1• ", s)


def _markdown_bold_to_b(s: str) -> str:
    """**текст** → <b>текст</b> (простой случай без вложенности)."""
    return re.sub(
        r"\*\*([^*]+)\*\*",
        lambda m: f"<b>{m.group(1)}</b>",
        s,
    )


def _markdown_link_to_html_a(s: str) -> str:
    """[текст](https://...) → <a href=\"...\">текст</a>."""

    def repl(m: re.Match[str]) -> str:
        label = m.group(1).strip()
        url = m.group(2).strip()
        if not url.startswith(("http://", "https://")):
            return m.group(0)
        le = html_escape(label, quote=False)
        ue = html_escape(url, quote=True)
        if label:
            return f'<a href="{ue}">{le}</a>'
        return f'<a href="{ue}">{ue}</a>'

    return re.sub(r"\[([^\]]+)\]\(([^)]+)\)", repl, s)


def _remove_markdown_double_stars(s: str) -> str:
    """Оставшиеся одиночные ** убираем (после конвертации в <b>)."""
    return re.sub(r"\*\*+", "", s)


def _flatten_ul_ol(s: str) -> str:
    """<ul>/<ol> → строки с «• » или «1. »."""
    if "<ul" not in s.lower() and "<ol" not in s.lower():
        return s

    soup = BeautifulSoup(f"<div>{s}</div>", "html.parser")
    root = soup.find("div")
    if not root:
        return s

    changed = True
    while changed:
        changed = False
        for lst in list(root.find_all(["ul", "ol"])):
            if lst.find(["ul", "ol"]):
                continue
            is_ol = lst.name.lower() == "ol"
            lines: list[str] = []
            for n, li in enumerate(lst.find_all("li", recursive=False), start=1):
                inner = (li.decode_contents() or "").strip()
                if not inner:
                    continue
                if is_ol:
                    lines.append(f"{n}. {inner}")
                else:
                    lines.append(f"• {inner}")
            if not lines:
                lst.decompose()
                changed = True
                continue
            replacement = "\n".join(lines)
            frag = BeautifulSoup(replacement, "html.parser")
            lst.replace_with(frag)
            changed = True

    return root.decode_contents()


def _node_to_telegram_html(node: Union[NavigableString, object]) -> str:
    if isinstance(node, NavigableString):
        if isinstance(node, Comment):
            return ""
        return html_escape(str(node), quote=False)

    name = getattr(node, "name", None)
    if name is None:
        return ""

    if name == "br":
        return "\n"

    if name not in _ALLOWED_TAGS:
        return "".join(_node_to_telegram_html(c) for c in node.children)

    if name == "a":
        href = (node.get("href") or "").strip()
        inner = "".join(_node_to_telegram_html(c) for c in node.children)
        if not href.startswith(("http://", "https://")):
            return inner
        he = html_escape(href, quote=True)
        return f'<a href="{he}">{inner}</a>'

    inner = "".join(_node_to_telegram_html(c) for c in node.children)
    tag = "b" if name in ("b", "strong") else name
    if tag not in ("b", "i", "em", "u", "s", "code", "pre"):
        return inner
    return f"<{tag}>{inner}</{tag}>"


def _fragment_to_telegram_html(html_str: str) -> str:
    """Разрешённые теги сохраняем, остальное разворачиваем; текст экранируем."""
    if not html_str or not html_str.strip():
        return ""
    soup = BeautifulSoup(f"<div>{html_str}</div>", "html.parser")
    root = soup.find("div")
    if not root:
        return html_escape(html_str, quote=False)
    return "".join(_node_to_telegram_html(c) for c in root.children)


def _normalize_blank_lines(s: str) -> str:
    s = re.sub(r"[ \t]+\n", "\n", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


def llm_reply_for_telegram_html(text: str) -> str:
    """
    Готовый текст для отправки с parse_mode=HTML.

    Раскрывает сущности; списки — «•»; заголовки разделов — через <b>...</b> в ответе модели.
    """
    if not text:
        return ""
    s = text.replace("\x00", "").replace("\r\n", "\n")
    s = html_module.unescape(s)
    s = _caps_section_headers_to_bold(s)

    s = _flatten_ul_ol(s)
    s = _markdown_link_to_html_a(s)
    s = _markdown_bold_to_b(s)
    s = _bullet_lines_to_bullet_char(s)
    s = _em_dash_lines_to_bullet(s)
    s = _remove_markdown_double_stars(s)
    s = _fragment_to_telegram_html(s)
    s = _normalize_blank_lines(s)

    return s


def llm_reply_for_telegram(text: str) -> str:
    """
    Плоский текст (без HTML): для тестов или fallback.
    Снимает теги через повторное использование пайплайна без финального HTML.
    """
    h = llm_reply_for_telegram_html(text)
    if "<" not in h:
        return h
    soup = BeautifulSoup(f"<div>{h}</div>", "html.parser")
    root = soup.find("div")
    if not root:
        return h
    out = root.get_text(separator="\n")
    return _normalize_blank_lines(out)


def llm_text_to_telegram_html(text: str) -> str:
    """Совместимость: то же, что llm_reply_for_telegram_html."""
    return llm_reply_for_telegram_html(text)
