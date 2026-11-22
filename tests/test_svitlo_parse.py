import sys, unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "svitlo"))
import parse

TZ = ZoneInfo("Europe/Kyiv")
P = datetime(2026, 11, 22, 19, 30, tzinfo=TZ)


class T(unittest.TestCase):
    def d(self, text, trusted=False, when=P):
        return parse.parse_text(text, when, "2.2", trusted)["dates"]

    def test_table(self):
        t = ("Графіки відключень у Київській області на 23 листопада:\n"
             "| 1.1 і 1.2 | 10:30–14:30; 21:00–24:00 | 7 годин |\n"
             "| 2.1 і 2.2 | 10:30–14:30; 21:00–22:00 | 5 годин |")
        self.assertEqual(self.d(t), {"2026-11-23": [[630, 870], [1260, 1320]]})

    def test_block_lines(self):
        t = ("⚡️Київщина, графік на завтра\n2.1\n08:00-12:00\n2.2\n🔴 09:00 - 13:00\n🔴 19:00 - 23:00\n3.1\n10:00-14:00")
        self.assertEqual(self.d(t), {"2026-11-23": [[540, 780], [1140, 1380]]})

    def test_times_first(self):
        t = "Графік на 22.11 (Київщина):\n00:00-04:00 — 1.1, 2.2\n12:00-16:00 — 2.2, 3.1\n16:00-20:00 — 4.1"
        self.assertEqual(self.d(t), {"2026-11-22": [[0, 240], [720, 960]]})

    def test_untrusted_local_chat_without_region(self):
        t = "Графік на завтра:\n2.2 — 10:00-14:00"
        self.assertEqual(self.d(t), {})
        self.assertEqual(self.d(t, trusted=True), {"2026-11-23": [[600, 840]]})

    def test_other_region_skipped(self):
        t = "Графіки для Києва: у Києві 2.2 — 10:00-14:00"
        self.assertEqual(self.d(t, trusted=True), {})

    def test_two_regions(self):
        t = ("Графіки на 23 листопада\nКиївщина:\n2.2: 10:00-12:00\nДніпропетровщина:\n2.2: 14:00-18:00")
        self.assertEqual(self.d(t), {"2026-11-23": [[600, 720]]})

    def test_no_outage(self):
        t = "На Київщині 23 листопада графіки відключень не застосовуватимуться"
        self.assertEqual(self.d(t), {"2026-11-23": []})
        t2 = "Київщина, завтра:\n1.1: 10:00-12:00\n2.2: без відключень"
        self.assertEqual(self.d(t2), {"2026-11-23": []})

    def test_question_ignored(self):
        self.assertEqual(self.d("а в 2.2 коли світло буде?", trusted=True), {})

    def test_date_not_queue(self):
        t = "Київщина 22.11:\nчерга 2.2 з 10:00 до 14:00"
        self.assertEqual(self.d(t), {"2026-11-22": [[600, 840]]})

    def test_whole_queue(self):
        t = "Київщина сьогодні:\n2 черга — 18:00-22:00"
        self.assertEqual(self.d(t), {"2026-11-22": [[1080, 1320]]})

    def test_two_dates(self):
        t = ("Київщина\nСьогодні, 22 листопада:\n2.2: 10:00-12:00\nЗавтра, 23 листопада:\n2.2: 14:00-16:00")
        self.assertEqual(self.d(t), {"2026-11-22": [[600, 720]], "2026-11-23": [[840, 960]]})

    def test_official(self):
        hours = {str(h): "yes" for h in range(1, 25)}
        hours.update({"11": "no", "12": "no", "13": "first", "20": "second"})
        data = {"fact": {"data": {"1795298400": {"GPV2.2": hours}}, "update": "21.11.2026 22:28"}}
        out, upd = parse.parse_official(data, TZ)
        self.assertEqual(out, {"2026-11-22": [[600, 720 + 30], [1170, 1200]]})
        self.assertEqual(upd.hour, 22)


if __name__ == "__main__":
    unittest.main()
