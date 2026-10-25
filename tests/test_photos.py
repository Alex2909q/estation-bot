"""Фото в сповіщеннях: парсер (фото поста, запасне прев'ю), відправка файлом і запасні варіанти,
повторне надсилання поста. Запуск: python tests/test_photos.py"""
import asyncio
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.update(BOT_TOKEN="1:test", GROUPS="@egogorushka88", TG_SESSION="", NOTIFY_CHAT_ID="777")

import github_run  # noqa: E402
import web_source  # noqa: E402

# --- 1. Парсер: альбом (фото поста) і пост лише з прев'ю посилання (як 65840 і 65850 у EGOGORUSHKA)
HTML = """<html><head><meta property="og:title" content="EGOGORUSHKA"></head><body>
<div class="tgme_widget_message js-widget_message" data-post="egogorushka88/65848">
 <a class="tgme_widget_message_photo_wrap grouped_media_wrap blured js-message_photo"
    style="left:0px;background-image:url('https://cdn4.telesco.pe/file/album1.jpg')" href="#"></a>
 <a class="tgme_widget_message_photo_wrap grouped_media_wrap blured js-message_photo"
    style="background-image:url('https://cdn4.telesco.pe/file/album2.jpg')" href="#"></a>
 <div class="tgme_widget_message_text js-message_text">Портативная электростанция OUKITEL P1000E PLUS 1800 Вт, 1024 Вт·ч</div>
 <a class="tgme_widget_message_link_preview" href="#"><i class="link_preview_image"
    style="background-image:url('https://cdn4.telesco.pe/file/preview_should_not_win.jpg')"></i></a>
 <time datetime="2026-10-25T07:49:00+00:00"></time>
</div>
<div class="tgme_widget_message js-widget_message" data-post="egogorushka88/65850">
 <div class="tgme_widget_message_text js-message_text">🔋 Кто ждал купонов? Сейчас хорошие цены на зарядные станции</div>
 <a class="tgme_widget_message_link_preview" href="#"><i class="link_preview_image"
    style="background-image:url('https://cdn4.telesco.pe/file/preview.jpg')"></i></a>
 <time datetime="2026-10-25T07:50:00+00:00"></time>
</div>
<div class="tgme_widget_message js-widget_message" data-post="egogorushka88/65851">
 <div class="tgme_widget_message_text js-message_text">Просто текст без картинок</div>
</div></body></html>"""
title, posts = web_source.parse_page(HTML, "egogorushka88")
by_id = {p.id: p for p in posts}
assert by_id[65848].photo == "https://cdn4.telesco.pe/file/album1.jpg", by_id[65848].photo   # фото поста важливіше
assert by_id[65850].photo == "https://cdn4.telesco.pe/file/preview.jpg", by_id[65850].photo   # прев'ю як запасне
assert by_id[65851].photo is None
print("parser OK: фото альбому, запасне прев'ю, пост без картинки")

# --- 2. Відправка: файл → посилання → текст
log = []


class FakeBot:
    def __init__(self, token):
        pass

    async def call(self, method, params=None, http_timeout=30):
        return {"ok": True, "result": {"username": "EstationC_bot"} if method == "getMe" else []}

    async def send_photo(self, chat_id, caption, photo_url=None, photo_bytes=None, button_url=None):
        kind = "file" if photo_bytes else "url"
        log.append(kind)
        if kind == "url":                     # як у реальності: Telegram не бере фото з CDN за посиланням
            return False, {"ok": False, "description": "Bad Request: failed to get HTTP URL content"}
        return True, {"ok": True}

    async def send(self, chat_id, text, button_url=None):
        log.append("text")
        return True, {"ok": True}


github_run.BotAPI = FakeBot
tmp = Path(tempfile.mkdtemp())
github_run.STATE_DIR = tmp
github_run.STATE_FILE = tmp / "state.json"


def run_flush(fetch):
    web_source.fetch_bytes = fetch
    log.clear()
    r = github_run.Run()
    r.state["announced"] = True
    r.state["pending"] = [["txt", "https://t.me/egogorushka88/65848", "https://cdn4.telesco.pe/file/album1.jpg",
                           "egogorushka88/65848"]]
    asyncio.run(r.flush())
    return r


r = run_flush(lambda url, **kw: b"\xff\xd8jpeg")
assert log == ["file"] and r.sent == 1 and r.sent_with_photo == 1, (log, r.sent, r.sent_with_photo)
print("send OK: фото відправлено файлом")


def broken(url, **kw):
    raise TimeoutError("timed out")


r = run_flush(broken)
assert log == ["url", "text"] and r.sent == 1 and r.sent_with_photo == 0, log
assert any("завантаження" in e for e in r.photo_errors) and any("посилання" in e for e in r.photo_errors)
print("fallback OK: якщо фото не вдалося — оголошення все одно приходить текстом")

# --- 3. Повторне надсилання поста (RESEND)
web_source.fetch_page = lambda channel, before=None, timeout=20: web_source.parse_page(HTML, channel)
web_source.fetch_bytes = lambda url, **kw: b"\xff\xd8jpeg"
log.clear()
r = github_run.Run()
r.state["announced"] = True
asyncio.run(r.resend("https://t.me/egogorushka88/65848"))
assert len(r.state["pending"]) == 1 and "Повторне" in r.state["pending"][0][0], r.state["pending"]
assert r.state["pending"][0][2] == "https://cdn4.telesco.pe/file/album1.jpg"
asyncio.run(r.flush())
assert log == ["file"], log
print("resend OK: пост 65848 повторно надіслано з фото")
