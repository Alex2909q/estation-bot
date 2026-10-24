"""Перевірка github_run.py без мережі: три запуски поспіль на справжній розмітці t.me/s."""
import asyncio
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.update(BOT_TOKEN="123:test", GROUPS="", NOTIFY_CHAT_ID="", TG_SESSION="")

import web_source  # noqa: E402
import github_run  # noqa: E402

FIXTURE = (ROOT / "tests" / "fixture_zbskitai.html").read_text(encoding="utf-8")
_, ALL_POSTS = web_source.parse_page(FIXTURE, "ZBSkitai")
visible = {"n": 1}      # скільки постів «видно» на сторінці каналу в поточному запуску


def fake_fetch_page(channel, before=None, timeout=20):
    if channel == "dealradarua":
        raise web_source.NoWebPreview(channel)
    if channel == "ZBSkitai":
        posts = ALL_POSTS[:visible["n"]]
        if before:
            posts = [p for p in posts if p.id < before]
        return "ZBS Kitai", posts
    return f"Канал {channel}", []


sent, updates = [], []


class FakeBot:
    def __init__(self, token): pass

    async def call(self, method, params=None, http_timeout=30):
        if method == "getMe":
            return {"ok": True, "result": {"username": "EStation_bot"}}
        if method == "getUpdates":
            off = (params or {}).get("offset", 0)
            return {"ok": True, "result": [u for u in updates if u["update_id"] >= off]}
        return {"ok": True}

    async def send(self, chat_id, text, button_url=None):
        sent.append((chat_id, text, button_url))
        return True, {"ok": True}


def run_once():
    r = github_run.Run()
    code = asyncio.run(r.main())
    assert code == 0
    return r


tmp = Path(tempfile.mkdtemp())
github_run.STATE_DIR = tmp / "state"
github_run.STATE_FILE = github_run.STATE_DIR / "state.json"
github_run.BotAPI = FakeBot
web_source.fetch_page = fake_fetch_page

# Запуск 1: нікого немає, канал має 1 пост → лише запам'ятовуємо позиції
run_once()
st = json.loads(github_run.STATE_FILE.read_text())
assert st["last_ids"]["ZBSkitai"] == 2427 and not sent, (st["last_ids"], sent)
assert (github_run.STATE_DIR / "changed").exists()
print("run1 OK: positions", st["last_ids"])

# Запуск 2: користувач натиснув Start; з'явились нові пости 2435–2443
updates.append({"update_id": 10, "message": {"chat": {"id": 777}, "text": "/start"}})
visible["n"] = len(ALL_POSTS)
run_once()
st = json.loads(github_run.STATE_FILE.read_text())
links = [s[2] for s in sent if s[2]]
texts = [s[1] for s in sent]
assert st["owner_chat_id"] == 777 and st["update_offset"] == 11
assert links == ["https://t.me/ZBSkitai/2439", "https://t.me/ZBSkitai/2443"], links
assert any("Моніторинг на GitHub запущено" in t for t in texts)
assert any("dealradarua" in t and "TG_SESSION" in t for t in texts)
assert any("Підключено" in t for t in texts)
assert all(s[0] == 777 for s in sent)
print("run2 OK:", len(sent), "messages ->", links)

# Запуск 3: нічого нового → нічого не шлемо, кеш не перезаписуємо
n = len(sent)
run_once()
assert len(sent) == n, sent[n:]
assert not (github_run.STATE_DIR / "changed").exists()
print("run3 OK: nothing new, state unchanged")

print("\n--- приклад сповіщення ---\n" + [t for _, t, l in sent if l][0])
shutil.rmtree(tmp)
