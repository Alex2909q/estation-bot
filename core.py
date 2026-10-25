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
                        matched: list[str] | None = None, author_html: str | None = None, when: str = "",
                        text_limit: int = 700, header: str | None = None) -> str:
    """HTML-текст сповіщення (parse_mode=HTML). Перший рядок — назва станції, другий — ціна:
    саме їх видно у спливаючому сповіщенні, ще до відкриття Telegram."""
    title, price = product_summary(text)
    lines = [f"🔋 <b>{html.escape(title)}</b>",
             f"💶 <b>{html.escape(price)}</b>" if price else "💶 ціну не вказано"]
    meta = f"👥 {html.escape(group_title)}"
    if when:
        meta += f" · 🕒 {html.escape(when)}"
    lines.append(meta)
    if author_html:
        lines.append(f"👤 {author_html}")
    if header:
        lines.append(header)
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


# ---------------------------------------------------------------- дублікати ---
# «Однакове оголошення» = та сама назва товару (бренд + модель) і та сама ціна.
# Назви в каналах пишуть по-різному: «OSCAL Max 2400SE» / «OSCAL PowerMax2400SE»,
# «IEETek P3200» / «IEE P3200 Portable Power Station», «ІЕЕTek» кирилицею — зводимо до одного.

_BRANDS = [   # (канонічна назва, регулярний вираз по тексту латиницею)
    ("ecoflow", r"eco\s*flow"), ("bluetti", r"bluetti"), ("jackery", r"jackery"),
    ("anker", r"anker"), ("allpowers", r"allpowers"), ("fossibot", r"fossibot"),
    ("tallpower", r"tallpower"), ("vtoman", r"vtoman"), ("necespow", r"necespow"),
    ("abok", r"abok"), ("aferiy", r"aferiy"), ("solarplay", r"solar\s*play"),
    ("oscal", r"oscal"), ("oukitel", r"oukitel"), ("newsmy", r"newsmy"), ("iee", r"iee(?:tek)?"),
]
_BRAND_RX = re.compile(r"(?<![a-z0-9])(?:" + "|".join(f"(?P<b{i}>{rx})" for i, (_, rx) in enumerate(_BRANDS))
                       + r")(?![a-z])")
_MODEL_WORDS = {"plus", "pro", "max", "mini", "ultra", "se", "pe", "lite", "air", "gen", "power",
                "powermax", "house", "powerhouse", "river", "delta", "stream", "ac", "eb", "elite",
                "core", "smart", "home", "v", "e", "s"}
_FILLER_WORDS = {"explorer", "solix"}            # «Jackery Explorer 1000» = «Jackery 1000»
_UNITS = {"w", "wh", "kw", "kwh", "mah", "ah", "v", "hz", "вт", "втч", "квт", "квтч", "ватт",
          "мaч", "мач", "aч", "ач", "в"}
_SPEC_RX = re.compile(r"^\d+(?:w|wh|kw|kwh|mah|ah|v|hz)$")
_CUR_TOKENS = {"€", "$", "₴", "eur", "euro", "евро", "євро", "usd", "грн", "uah"}
_TOKEN_RX = re.compile(r"[a-zа-яіїєґё0-9]+|\S")

_AMOUNT_CORE = r"(?:\d{1,3}(?:[   ]\d{3})+(?!\d)|\d+)"
_AMOUNT = r"(?<![\w.,])" + _AMOUNT_CORE
_PRICE_RX = re.compile(
    r"(?P<c1>€|\$|₴|usd|eur)\s?(?P<a1>" + _AMOUNT_CORE + r")(?:[.,](?P<f1>\d{1,2}))?"
    r"|(?P<a2>" + _AMOUNT + r")(?:[.,](?P<f2>\d{1,2}))?\s?(?P<c2>€|\$|₴|eur\b|euro\b|евро|євро|usd\b|грн|uah\b)")
_CUR_CANON = {"€": "eur", "eur": "eur", "euro": "eur", "евро": "eur", "євро": "eur",
              "$": "usd", "usd": "usd", "₴": "uah", "грн": "uah", "uah": "uah"}


def _latin_lines(text: str) -> str:
    text = (text or "").translate(_INVISIBLE).lower().replace("ё", "е")
    return text.translate(_LOOKALIKE)


_INVISIBLE_CHARS = {chr(c) for c in _INVISIBLE}


def _latin_aligned(text: str) -> str:
    """Те саме, що _latin_lines, але тієї ж довжини, що й оригінал (щоб вирізати назву з оригіналу)."""
    out = []
    for ch in text:
        if ch in _INVISIBLE_CHARS:
            out.append(" ")
            continue
        lo = ch.lower()
        out.append(lo if len(lo) == 1 else ch)
    return "".join(out).replace("ё", "е").translate(_LOOKALIKE)


def _scan_products(text: str) -> list[tuple[str, str, bool]]:
    """[(ключ, назва як у пості, чи є модель)] для кожної згадки погодженого бренду."""
    text = text or ""
    latin = _latin_aligned(text)
    found = []
    for line in re.finditer(r"[^\n]+", latin):
        base = line.start()
        for m in _BRAND_RX.finditer(line.group()):
            brand = next(_BRANDS[int(k[1:])][0] for k, v in m.groupdict().items() if v)
            toks = list(_TOKEN_RX.finditer(line.group(), m.end()))
            model, end = [], m.end()
            for i, t in enumerate(toks):
                tok = t.group()
                if len(model) >= 5:
                    break
                nxt = toks[i + 1].group() if i + 1 < len(toks) else ""
                if not tok.isalnum():
                    break                                    # дефіс, кома, дужка, емодзі, ціна
                if tok.isdigit():
                    if nxt in _UNITS or nxt in _CUR_TOKENS:
                        break                                # «800 Вт», «386 €» — характеристики/ціна
                elif any(ch.isdigit() for ch in tok):
                    if _SPEC_RX.match(tok) or not tok.isascii():
                        break                                # «2400w», «1024wh»
                elif tok in _FILLER_WORDS:
                    continue
                elif tok not in _MODEL_WORDS:
                    break
                model.append(tok)
                end = t.end()
            key = brand + "".join(model).replace("powermax", "max")
            if not model and toks and _SPEC_RX.match(toks[0].group()):
                end = toks[0].end()          # «SolarPlay 2400W»: у назві показуємо, у ключ не беремо
            display = re.sub(r"\s+", " ", text[base + m.start():base + end]).strip()
            found.append((key, display, bool(model)))
    return found


def product_names(text: str) -> list[str]:
    """Назви товарів погоджених брендів у пості: ['oukitelp800e', 'oscalmax2400se', …] без повторів."""
    found = _scan_products(text)
    with_model = [k for k, _, has in found if has]
    keys = with_model or [k for k, _, _ in found]
    return list(dict.fromkeys(keys))


def _price_match(text: str):
    m = _PRICE_RX.search((text or "").lower())
    if not m:
        return None
    g = m.groupdict()
    if g["c1"]:
        whole, frac, cur = g["a1"], g["f1"], g["c1"]
    else:
        whole, frac, cur = g["a2"], g["f2"], g["c2"]
    value = float(re.sub(r"\D", "", whole) + "." + (frac or "0"))
    raw = re.sub(r"\s+", " ", (text or "")[m.start():m.end()]).strip()
    return _CUR_CANON.get(cur.strip(), cur.strip()), int(value + 0.5), raw


def first_price(text: str) -> tuple[str, int] | None:
    """Перша ціна в пості: ('eur', 575) з «€575», «575€», «Цена от: 575.40€», «32 500 грн»…"""
    pm = _price_match(text)
    return (pm[0], pm[1]) if pm else None


def product_summary(text: str) -> tuple[str, str | None]:
    """(назва станції, ціна як у пості) — для першого й другого рядка сповіщення."""
    found = _scan_products(text)
    names = list(dict.fromkeys(d for _, d, has in found if has)) or list(dict.fromkeys(d for _, d, _ in found))
    pm = _price_match(text)
    price = pm[2] if pm else None
    if names:
        title = ", ".join(names[:3]) + (f" і ще {len(names) - 3}" if len(names) > 3 else "")
    else:
        title = ""
        for line in (text or "").splitlines():
            clean = line
            if price and price in clean:
                clean = clean.replace(price, " ")
            clean = re.sub(r"[^\w\s()+/.,%'’\-]", " ", clean)      # емодзі й значки геть
            clean = re.sub(r"\s+", " ", clean).strip(" -–—~:.,")
            if sum(ch.isalpha() for ch in clean) >= 3:
                title = clean
                break
        title = title or "Зарядна станція"
    if len(title) > 70:
        title = title[:69].rstrip() + "…"
    return title, price


def announcement_key(text: str) -> str:
    """Ключ «того самого оголошення»: назва(и) товару + ціна. Якщо бренд не розпізнано —
    перший рядок поста + ціна; якщо й його немає — відбиток усього тексту."""
    price = first_price(text)
    price_part = f"{price[0]}{price[1]}" if price else "-"
    names = product_names(text)
    if names:
        return "p:" + "+".join(names) + "|" + price_part
    lines = [re.sub(r"[^\w]+", " ", _latin_lines(l)).strip() for l in (text or "").splitlines()]
    lines = [l for l in lines if len(l) >= 3][:2]
    if lines:
        return "l:" + " / ".join(lines)[:120] + "|" + price_part
    return "t:" + text_fingerprint(text)
