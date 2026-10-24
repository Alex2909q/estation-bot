"""Перевірка keywords.txt: погоджені бренди ловляться, павербанки/смартфони/зарядки — ні.
Запуск: python tests/test_keywords.py"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import core  # noqa: E402

KW = core.load_keywords(ROOT / "keywords.txt")

SHOULD_MATCH = [
    "EcoFlow River 2 Pro", "Eco Flow Delta 3", "BLUETTI AC180", "Jackery Explorer 1000 V2",
    "Allpowers R1500", "TALLPOWER V2400", "Vtoman FlashSpeed 1500", "Necespow N1200",
    "ABOK Ark2800", "AFERIY P1800 Pro", "SolarPlay Q2501", "FOSSIBOT F2400",
    "TALLPOWER (Fossibot) - €515", "Anker SOLIX C1000", "Anker 757 PowerHouse",
    "OSCAL PowerMax 3600", "OSCAL Max 1800SE від €336", "OUKITEL BP2000", "OUKITEL P1500E PLUS",
    "IEE P1800", "IEETek P3200", "Newsmy N1000 — 1000W / 1008Wh",
    "Зарядна станція 1000Вт", "Продам зарядную станцию", "Portable Power Station",
    "ЕсоFlow Delta 2 (кирилиця)", "Невідомий бренд 1800W / 1024Wh", "Станція на 2048 Вт·год",
]
SHOULD_NOT_MATCH = [
    "Anker Nano 30W зарядка", "Anker 535 Power Bank 20000mAh", "EcoFlow RAPID Pro павербанк 27650mAh",
    "Fossibot F101 захищений смартфон", "Смартфон OSCAL C80 8/256GB", "Oukitel WP32 Pro",
    "Newsmy павербанк 10000mAh", "Кабель USB-C PD 240W", "Павербанк UGREEN 55W 10000mAh",
    "Ноутбук з батареєю 99Wh", "Обігрівач 2000W", "Ремонт телефонів біля станції метро",
]


def main() -> int:
    problems = 0
    for text in SHOULD_MATCH:
        if not core.find_matches(text, KW):
            print("НЕ ЗНАЙДЕНО:", text)
            problems += 1
    for text in SHOULD_NOT_MATCH:
        hit = core.find_matches(text, KW)
        if hit:
            print("ЗАЙВЕ СПРАЦЮВАННЯ:", text, hit)
            problems += 1
    print(f"keywords: {len(SHOULD_MATCH)} мають спрацювати, {len(SHOULD_NOT_MATCH)} — ні; проблем: {problems}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
