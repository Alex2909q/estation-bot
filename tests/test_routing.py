"""Відкриті канали читаються через веб, акаунт — лише для каналів без веб-версії.
Запуск: python tests/test_routing.py"""
import asyncio
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.update(BOT_TOKEN="1:test", GROUPS="@ZBSkitai,@dealradarua,@technorika",
                  TG_SESSION="1:hash:session", NOTIFY_CHAT_ID="777")

import github_run  # noqa: E402
import web_source  # noqa: E402

web_calls, account_calls, logins = [], [], []


def fake_fetch_new(channel, last_id, max_pages=5):
    web_calls.append(channel)
    if channel == "dealradarua":
        raise web_source.NoWebPreview(channel)
    return f"Канал {channel}", [web_source.WebPost(100, "Продам EcoFlow River 2", "2026-10-25T06:00:00+00:00")]


class FakeReader:
    def __init__(self, creds, peers):
        logins.append(1)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        pass

    async def fetch_new(self, ref, last_id):
        account_calls.append(ref)
        return "DealRadarUA", "dealradarua", None, [web_source.WebPost(50, "Bluetti AC180", "2026-10-25T06:00:00+00:00")]


web_source.fetch_new = fake_fetch_new
github_run.AccountReader = FakeReader
tmp = Path(tempfile.mkdtemp())
github_run.STATE_DIR = tmp
github_run.STATE_FILE = tmp / "state.json"

# Запуск 1: dealradarua виявляється без веб-версії → читається через акаунт
r = github_run.Run()
asyncio.run(r.check_all())
assert web_calls == ["ZBSkitai", "dealradarua", "technorika"], web_calls
assert account_calls == ["@dealradarua"] and len(logins) == 1, (account_calls, logins)
assert r.state["web_blocked"] == ["dealradarua"] and not r.problems, (r.state["web_blocked"], r.problems)
github_run.save_state(r.state, "")

# Запуск 2: веб для dealradarua вже не пробуємо, акаунт — лише для нього
web_calls.clear(); account_calls.clear(); logins.clear()
r = github_run.Run()
asyncio.run(r.check_all())
assert web_calls == ["ZBSkitai", "technorika"], web_calls
assert account_calls == ["@dealradarua"] and len(logins) == 1, (account_calls, logins)
print("routing OK: веб — ZBSkitai, technorika; акаунт — лише dealradarua (1 вхід за перевірку)")
