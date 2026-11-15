"""Якщо стан загубився (немає owner_chat_id), чат власника відновлюється з акаунта TG_SESSION,
і накопичена черга сповіщень надсилається. Запуск: python tests/test_owner.py"""
import asyncio
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("HOT_MODE", "off")
os.environ.update(BOT_TOKEN="1:test", GROUPS="@ZBSkitai,@dealradarua", TG_SESSION="1:hash:session", NOTIFY_CHAT_ID="")

import github_run  # noqa: E402
import web_source  # noqa: E402


class Me:
    id = 123456789


class FakeReader:
    def __init__(self, creds, peers):
        self.client = self

    async def get_me(self):
        return Me()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        pass

    async def fetch_new(self, ref, last_id):
        return "DealRadarUA", "dealradarua", None, []


sent = []


class FakeBot:
    def __init__(self, token):
        pass

    async def call(self, method, params=None, http_timeout=30):
        return {"ok": True, "result": {"username": "EstationC_bot"} if method == "getMe" else []}

    async def send(self, chat_id, text, button_url=None):
        sent.append((chat_id, button_url))
        return True, {"ok": True}

    async def send_photo(self, *a, **kw):
        return False, {"description": "-"}


def fetch_new(channel, last_id, max_pages=5):
    return channel, []          # усі канали відкриті: акаунт потрібен лише для відновлення чату


web_source.fetch_new = fetch_new
github_run.AccountReader = FakeReader
github_run.BotAPI = FakeBot
tmp = Path(tempfile.mkdtemp())
github_run.STATE_DIR = tmp
github_run.STATE_FILE = tmp / "state.json"

r = github_run.Run()                      # «чистий» стан, як після збою кешу, але з чергою
r.state["pending"] = [["🔋 <b>OSCAL</b>", "https://t.me/ZBSkitai/1", None, "ZBSkitai/1"],
                      ["💬 <b>Розетка</b>", "https://t.me/ZBSkitai/2", None, "ZBSkitai/2"]]
r.state["announced"] = True
github_run.save_state(r.state, "")
r = github_run.Run()
asyncio.run(r.main())
assert r.state["owner_chat_id"] == 123456789, r.state["owner_chat_id"]
assert [c for c, _ in sent] == [123456789, 123456789] and r.state["pending"] == [], (sent, r.state["pending"])
print("owner OK: чат власника відновлено з акаунта, черга з 2 сповіщень надіслана")
