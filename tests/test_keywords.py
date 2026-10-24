"""Перевірка keywords.txt: погоджені бренди ловляться за будь-якої згадки, стороннє — ні.
Запуск: python tests/test_keywords.py"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import core  # noqa: E402

KW = core.load_keywords(ROOT / "keywords.txt")

SHOULD_MATCH = [
    # погоджені бренди — будь-яка згадка
    "EcoFlow River 2 Pro", "Eco Flow Delta 3", "EcoFlow RAPID Pro павербанк", "BLUETTI AC180",
    "Jackery Explorer 1000 V2", "Anker SOLIX C1000", "Anker Nano 30W зарядка", "Allpowers R1500",
    "FOSSIBOT F2400", "Fossibot F101 смартфон", "TALLPOWER V2400", "Vtoman FlashSpeed 1500",
    "Necespow N1200", "ABOK Ark2800", "AFERIY P1800 Pro", "SolarPlay Q2501",
    "OSCAL PowerMax 3600", "Смартфон OSCAL C80", "OUKITEL P1500E PLUS", "Oukitel WP32 Pro",
    "Newsmy павербанк 10000mAh", "IEETek P3200", "IEE P1800", "ЕсоFlow Delta 2 (кирилиця)",
    # загальні правила
    "Зарядна станція 1000Вт", "Продам зарядную станцию", "Portable Power Station",
    "Невідомий бренд 1800W / 1024Wh", "Станція на 2048 Вт·год",
]
SHOULD_NOT_MATCH = [
    "Кабель USB-C PD 240W", "Павербанк UGREEN 55W 10000mAh", "Ноутбук з батареєю 99Wh",
    "Обігрівач 2000W", "Ремонт телефонів біля станції метро", "Стандарт IEEE 802.11ax",
    "Zendure SuperTank", "Xiaomi Redmi Note 13",
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
