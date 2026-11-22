"""Розбір графіків відключень (Київщина, підчерги 1.1–6.2).

Два джерела:
  * текст постів у Telegram (офіційні канали, новини, місцеві чати);
  * машиночитні дані сайту ДТЕК КРЕМ (через дзеркало outage-data-ua).

Графік на день = список інтервалів БЕЗ світла у хвилинах від початку доби: [[630, 870], ...].
Порожній список = відключень немає.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta

MONTHS = {
    "січня": 1, "лютого": 2, "березня": 3, "квітня": 4, "травня": 5, "червня": 6,
    "липня": 7, "серпня": 8, "вересня": 9, "жовтня": 10, "листопада": 11, "грудня": 12,
}

# Київщина (не Київ-місто)
REGION_OK = re.compile(
    r"київщин|київськ\w*\s+обл|київська\s+область|київській\s+області|"
    r"регіональн\w*\s+електромереж|\bкрем\b|dtek[\s_-]*krem|kyiv\s*region",
    re.I)
# Інші регіони або місто Київ — такі блоки пропускаємо
REGION_OTHER = re.compile(
    r"\bу\s+києві\b|\bв\s+києві\b|\bм\.\s*київ|\bміст[оі]\s+київ|столиц|\bкиян|"
    r"київські\s+електромережі|"
    r"одес|дніпр|львів|харків|житомир|черніг|полтав|сумщин|сумськ|черкас|вінниц|запоріж|"
    r"кропивн|кіровоград|миколаїв|херсон|рівн[еі]|рівненськ|луцьк|волин|тернопіл|франків|"
    r"ужгород|закарпат|чернів|хмельниц|донеч|донеччин",
    re.I)

DASH = re.compile(r"[‐-―−–—]")
TIME_RANGE = re.compile(
    r"(?<![\d:.])(\d{1,2})[:.](\d{2})\s*(?:-|до|по)\s*(\d{1,2})[:.](\d{2})(?![\d])", re.I)
QUEUE = re.compile(r"(?<![\d.,:])([1-6])[.,]([12])(?![\d]|[.,]\d)")
WHOLE_QUEUE = re.compile(
    r"(?<![\d.,:])([1-6])\s*(?:-?\s*(?:а|га|я))?\s*(?:під)?черг|черг[аиі]?\s*№?\s*([1-6])(?![.,]?\d)", re.I)
DATE_WORDS = re.compile(r"(?<!\d)(\d{1,2})\s+(" + "|".join(MONTHS) + r")", re.I)
DATE_NUM = re.compile(r"(?<![\d.,:])(\d{1,2})\.(\d{2})(?:\.(\d{2,4}))?(?![\d:])")
NO_OUTAGE = re.compile(
    r"без\s+відключ|відключень\s+не\s+(?:буде|передбач|прогноз|заплан)|не\s+застосов|"
    r"не\s+вимика|світло\s+буде\s+(?:весь|цілий|всю)|"
    r"(?:світло|електроенергія)\s+(?:є|буде)\s+(?:весь\s+день|цілу\s+добу|всю\s+добу)",
    re.I)
ON_MODE = re.compile(r"графік\w*\s+(?:включ|увімкн|подачі)", re.I)
SCHEDULE_WORDS = re.compile(r"графік|черг|відключ|світл|знеструм", re.I)


def _norm(text: str) -> str:
    text = DASH.sub("-", text or "")
    return text.replace(" ", " ").replace(" ", " ")


def _queues(line: str) -> set[str]:
    out = {f"{a}.{b}" for a, b in QUEUE.findall(line)}
    if not out:
        for m in WHOLE_QUEUE.finditer(line):
            n = m.group(1) or m.group(2)
            out |= {f"{n}.1", f"{n}.2"}
    return out


def _ranges(line: str) -> list[list[int]]:
    res = []
    for h1, m1, h2, m2 in TIME_RANGE.findall(line):
        h1, m1, h2, m2 = int(h1), int(m1), int(h2), int(m2)
        if h1 > 24 or h2 > 24 or m1 > 59 or m2 > 59:
            continue
        a, b = h1 * 60 + m1, h2 * 60 + m2
        if b == 0:
            b = 1440
        if a >= 1440 or b > 1440:
            continue
        if b <= a:                      # 22:00-02:00 → до кінця доби
            b = 1440
        res.append([a, b])
    return res


def merge(intervals: list[list[int]]) -> list[list[int]]:
    out: list[list[int]] = []
    for a, b in sorted(intervals):
        if out and a <= out[-1][1]:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return out


def invert(intervals: list[list[int]]) -> list[list[int]]:
    out, cur = [], 0
    for a, b in merge(intervals):
        if a > cur:
            out.append([cur, a])
        cur = max(cur, b)
    if cur < 1440:
        out.append([cur, 1440])
    return out


def _line_date(line: str, base: date) -> date | None:
    m = DATE_WORDS.search(line)
    if m:
        d, mon = int(m.group(1)), MONTHS[m.group(2).lower()]
        return _closest(base, mon, d)
    for m in DATE_NUM.finditer(TIME_RANGE.sub(" ", line)):
        d, mon = int(m.group(1)), int(m.group(2))
        if 1 <= d <= 31 and 1 <= mon <= 12:
            return _closest(base, mon, d)
    low = line.lower()
    if "післязавтра" in low:
        return base + timedelta(days=2)
    if "завтра" in low:
        return base + timedelta(days=1)
    if "сьогодні" in low:
        return base
    return None


def _closest(base: date, month: int, day: int) -> date | None:
    best = None
    for y in (base.year - 1, base.year, base.year + 1):
        try:
            cand = date(y, month, day)
        except ValueError:
            continue
        if best is None or abs((cand - base).days) < abs((best - base).days):
            best = cand
    return best


def parse_text(text: str, posted: datetime, queue: str = "2.2", trusted: bool = False) -> dict:
    """Повертає {"dates": {"YYYY-MM-DD": [[a,b],...]}, "region": "kr"|"other"|None}.

    trusted=True — чат підтверджено як місцевий (Київщина), тож пости без згадки регіону теж беремо.
    """
    result = {"dates": {}, "region": None}
    text = _norm(text)
    if not text or not (SCHEDULE_WORDS.search(text) or (QUEUE.search(text) and TIME_RANGE.search(text))):
        return result
    base = posted.date()
    on_mode = bool(ON_MODE.search(text))
    explicit_kr = bool(REGION_OK.search(text))
    if explicit_kr:
        result["region"] = "kr"
    elif REGION_OTHER.search(text):
        result["region"] = "other"

    region = None                      # регіон поточного блоку
    cur_date = None                    # дата поточного блоку
    header_date = _line_date(text.split("\n", 1)[0], base)
    found: dict[date, list[list[int]]] = {}
    marked_none: set[date] = set()
    cur_queues: set[str] = set()

    for raw in text.split("\n"):
        line = raw.strip()
        if not line:
            continue
        if REGION_OK.search(line):
            region = "kr"
        elif REGION_OTHER.search(line):
            region = "other"
            cur_queues = set()
        d = _line_date(line, base)
        rngs = _ranges(line)
        if d and not rngs:
            cur_date = d
            cur_queues = set()
        qs = _queues(line)
        if "?" in line and not rngs:
            continue
        block_ok = region == "kr" or (region is None and (explicit_kr or trusted))
        if not block_ok:
            continue
        day = d if (d and rngs) else (cur_date or header_date or base)
        if qs:
            cur_queues = qs
            if queue in qs:
                if rngs:
                    found.setdefault(day, []).extend(rngs)
                elif NO_OUTAGE.search(line):
                    marked_none.add(day)
        elif rngs and queue in cur_queues:
            found.setdefault(day, []).extend(rngs)

    # «Відключень не буде» для всієї області (без згадки черг)
    if not found and not marked_none and NO_OUTAGE.search(text) and not QUEUE.search(text):
        if explicit_kr or (trusted and result["region"] != "other"):
            marked_none.add(cur_date or header_date or base)

    for day, rngs in found.items():
        iv = merge(rngs)
        if on_mode:
            iv = invert(iv)
        if sum(b - a for a, b in iv) >= 1440 and not on_mode:
            continue                   # «без світла всю добу» з тексту — швидше за все помилка розбору
        result["dates"][day.isoformat()] = iv
    for day in marked_none:
        result["dates"].setdefault(day.isoformat(), [])
    return result


# ---------- офіційні дані (формат DisconSchedule.fact сайту ДТЕК) ----------

def parse_official(data: dict, tz, queue: str = "2.2") -> tuple[dict, datetime | None]:
    """{"YYYY-MM-DD": інтервали} і час оновлення графіка на сайті."""
    fact = (data or {}).get("fact") or {}
    out = {}
    key = "GPV" + queue
    for ts, groups in (fact.get("data") or {}).items():
        try:
            day = datetime.fromtimestamp(int(ts), tz).date()
        except (TypeError, ValueError):
            continue
        hours = (groups or {}).get(key)
        if not isinstance(hours, dict):
            continue
        slots = []
        for h in range(1, 25):
            v = str(hours.get(str(h), "yes")).lower()
            start = (h - 1) * 60
            if v in ("no", "mno"):
                slots.append([start, start + 60])
            elif v in ("first", "mfirst"):
                slots.append([start, start + 30])
            elif v in ("second", "msecond"):
                slots.append([start + 30, start + 60])
        out[day.isoformat()] = merge(slots)
    updated = None
    raw = fact.get("update") or data.get("updateFact")
    if raw:
        try:
            updated = datetime.strptime(raw, "%d.%m.%Y %H:%M").replace(tzinfo=tz)
        except ValueError:
            updated = None
    return out, updated


# ---------- форматування ----------

def hm(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def fmt_intervals(iv: list[list[int]]) -> str:
    if not iv:
        return "🟢 Відключень немає"
    return "\n".join(f"🔴 {hm(a)}–{hm(b)}" for a, b in iv)


def total_off(iv: list[list[int]]) -> str:
    m = sum(b - a for a, b in iv)
    h, mm = divmod(m, 60)
    return f"{h} год" + (f" {mm} хв" if mm else "")
