"""Чиста логіка без мережі: ключові слова, посилання, формат сповіщень.

Винесено окремо, щоб її можна було легко перевіряти й змінювати.
"""
from __future__ import annotations

import hashlib
import html
import re
from pathlib import Path

# Невидимі символи, якими іноді «ламають» пошук у оголошеннях
_INVISIBLE = dict.fromkeys(map(ord, "​‌‍⁠﻿­"), None)

# Кириличні літери, схожі на латинські (ЕсоFlow -> ecoflow)
_LOOKALIKE = str.maketrans("аеорсухікї", "aeopcyxiki")

_SEPARATOR = r"[\s\-–—_/.]*"   # між словами ключа: пробіл, дефіс, крапка або нічого


def normalize(text: str) -> str:
    text = (text or "").translate(_INVISIBLE).lower()
    text = text.replace("ё", "е").replace("’", "'").replace("ʼ", "'").replace("`", "'")
    return re.sub(r"\s+", " ", text).strip()


def compile_keyword(line: str) -> re.Pattern:
    """Перетворює рядок із keywords.txt на регулярний вираз.

    * `*` у кінці слова — будь-яке закінчення: `зарядн* станц*`
      знайде «зарядна станція», «зарядну станцію», «зарядная станция».
    * Слова можна розділяти пробілом, дефісом або писати разом.
    * Рядок, що починається з `re:`, — це готовий регулярний вираз.
    """
    line = line.strip()
    if line.lower().startswith("re:"):
        return re.compile(line[3:].strip(), re.IGNORECASE)
    words = normalize(line).split(" ")
    parts = [re.escape(w).replace(r"\*", r"\w*") for w in words]
    return re.compile(r"(?<!\w)" + _SEPARATOR.join(parts) + r"(?!\w)", re.IGNORECASE)


def load_keywords(path: Path) -> list[tuple[str, re.Pattern]]:
    if not path.exists():
        return []
    result = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        label = line
        if "  #" in line:                     # "правило  # підпис у сповіщенні"
            line, label = (p.strip() for p in line.split("  #", 1))
        result.append((label, compile_keyword(line)))
    return result


def find_matches(text: str, keywords, excludes=()) -> list[str]:
    """Повертає список ключів, що спрацювали (порожній — якщо збігу немає
    або спрацювало слово-виключення)."""
    if not text:
        return []
    plain = normalize(text)
    latin = plain.translate(_LOOKALIKE)
    variants = (plain, latin)
    if any(rx.search(v) for _, rx in excludes for v in variants):
        return []
    return [label for label, rx in keywords if any(rx.search(v) for v in variants)]


def text_fingerprint(text: str) -> str:
    """Відбиток тексту, щоб не надсилати один і той самий репост із різних груп."""
    plain = re.sub(r"[^\w]+", "", normalize(text))
    return hashlib.sha1(plain.encode("utf-8")).hexdigest()[:16]


def parse_group_ref(ref: str):
    """Посилання/юзернейм/ID групи -> те, що розуміє Telethon get_entity."""
    ref = ref.strip()
    if re.fullmatch(r"-?\d+", ref):
        return int(ref)
    m = re.match(r"^(?:https?://)?(?:www\.)?(?:t|telegram)\.(?:me|dog)/(.+)$", ref, re.I)
    if not m:
        return ref.lstrip("@")
    path = m.group(1).split("?")[0].strip("/")
    if path.startswith("+") or path.lower().startswith("joinchat/"):
        return "https://t.me/" + path            # приватне посилання-запрошення
    segments = path.split("/")
    if segments[0] == "c" and len(segments) > 1 and segments[1].isdigit():
        return int("-100" + segments[1])         # t.me/c/123456/78
    if segments[0] == "s" and len(segments) > 1:  # t.me/s/name (веб-перегляд)
        return segments[1]
    return segments[0]


def primary_username(entity) -> str | None:
    """Юзернейм групи/людини (враховує й нові «колекційні» юзернейми)."""
    name = getattr(entity, "username", None)
    if name:
        return name
    for item in getattr(entity, "usernames", None) or []:
        if getattr(item, "active", False) and getattr(item, "username", None):
            return item.username
    return None


def message_link(msg_id: int, username: str | None, chat_id: int, is_channel: bool) -> str | None:
    """Пряме посилання на повідомлення.

    Публічна група -> https://t.me/<username>/<id>
    Приватна супергрупа -> https://t.me/c/<id>/<msg> (відкривається в учасників групи)
    Стара «звичайна» група -> посилань у Telegram немає.
    """
    if username:
        return f"https://t.me/{username}/{msg_id}"
    if is_channel:
        return f"https://t.me/c/{abs(chat_id)}/{msg_id}"
    return None


def shorten(text: str, limit: int = 700) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def format_notification(*, group_title: str, text: str, link: str | None,
                        matched: list[str], author_html: str | None, when: str,
                        text_limit: int = 700, header: str | None = None) -> str:
    """HTML-текст сповіщення для Bot API (parse_mode=HTML)."""
    lines = [header or "🔋 <b>Нове оголошення: зарядна станція</b>",
             f"👥 {html.escape(group_title)}"]
    if author_html:
        lines.append(f"👤 {author_html}")
    lines.append(f"🕒 {html.escape(when)}")
    lines.append(f"🔑 <i>{html.escape(', '.join(matched[:5]))}</i>")
    lines.append("")
    lines.append(html.escape(shorten(text, text_limit)))
    lines.append("")
    if link:
        lines.append(f'🔗 <a href="{html.escape(link, quote=True)}">{html.escape(link)}</a>')
    else:
        lines.append("🔗 Посилання недоступне: це стара «звичайна» група без посилань на повідомлення.")
    return "\n".join(lines)


def format_author(first: str | None, last: str | None, username: str | None,
                  user_id: int | None, is_user: bool) -> str | None:
    name = " ".join(p for p in (first, last) if p).strip()
    if username:
        shown = html.escape(name) + " " if name else ""
        return f"{shown}@{html.escape(username)}"
    if is_user and user_id:
        return f'<a href="tg://user?id={user_id}">{html.escape(name or "Профіль автора")}</a>'
    return html.escape(name) if name else None
