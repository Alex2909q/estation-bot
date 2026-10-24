"""Читання публічних Telegram-каналів через їхню веб-версію t.me/s/<канал>.

Не потребує акаунта Telegram. Працює лише для публічних каналів,
у яких власник не вимкнув веб-перегляд (інакше t.me/s/ перенаправляє на t.me/).
"""
from __future__ import annotations

import os
import re
import ssl
import urllib.request
from dataclasses import dataclass, field
from html.parser import HTMLParser

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")


@dataclass
class WebPost:
    id: int
    text: str = ""
    date: str | None = None          # ISO-8601 з атрибута <time datetime>
    author: str | None = None        # підпис автора поста (якщо є)
    parts: list = field(default_factory=list, repr=False)


class NoWebPreview(Exception):
    """Канал не має веб-версії (приватний або власник її вимкнув)."""


class _Parser(HTMLParser):
    def __init__(self, channel: str):
        super().__init__(convert_charrefs=True)
        self.channel = channel.lower()
        self.posts: list[WebPost] = []
        self.title: str | None = None
        self.cur: WebPost | None = None
        self.div_depth = 0
        self.text_depth: int | None = None      # глибина div-а з текстом поста
        self.in_author = False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        classes = (a.get("class") or "").split()
        if tag == "meta" and a.get("property") == "og:title":
            self.title = a.get("content")
        if tag == "div":
            self.div_depth += 1
            post = a.get("data-post")
            if post and "tgme_widget_message" in classes:
                name, _, num = post.rpartition("/")
                if num.isdigit():
                    self.cur = WebPost(int(num))
                    self.posts.append(self.cur)
                    self.text_depth = None
            elif (self.cur and self.text_depth is None and "tgme_widget_message_text" in classes
                  and "js-message_reply_text" not in classes):      # превʼю цитованого поста не беремо
                self.text_depth = self.div_depth
        elif self.cur is not None:
            if tag == "br" and self.text_depth is not None:
                self.cur.parts.append("\n")
            elif tag == "time" and self.cur.date is None and "datetime" in a:
                self.cur.date = a["datetime"]
            elif tag == "span" and "tgme_widget_message_from_author" in classes:
                self.in_author = True

    def handle_endtag(self, tag):
        if tag == "div":
            if self.text_depth is not None and self.div_depth == self.text_depth:
                self.text_depth = None
                if self.cur is not None:
                    self.cur.parts.append("\n")
            self.div_depth -= 1
        elif tag == "span" and self.in_author:
            self.in_author = False

    def handle_data(self, data):
        if self.cur is None:
            return
        if self.text_depth is not None:
            self.cur.parts.append(data)
        elif self.in_author:
            self.cur.author = (self.cur.author or "") + data


def parse_page(html_text: str, channel: str) -> tuple[str | None, list[WebPost]]:
    p = _Parser(channel)
    p.feed(html_text)
    p.close()
    posts = {}
    for post in p.posts:
        post.text = re.sub(r"[ \t]+\n", "\n", "".join(post.parts)).strip()
        post.text = re.sub(r"\n{3,}", "\n\n", post.text)
        posts[post.id] = post                     # на випадок дублів — беремо останній
    return p.title, sorted(posts.values(), key=lambda x: x.id)


def _ssl_context():
    ctx = ssl.create_default_context()
    for bundle in ("/etc/ssl/cert.pem", "/etc/ssl/certs/ca-certificates.crt"):
        if os.path.exists(bundle):
            try:
                ctx.load_verify_locations(bundle)
            except Exception:
                pass
    return ctx


_CTX = _ssl_context()


def fetch_page(channel: str, before: int | None = None, timeout: float = 20) -> tuple[str | None, list[WebPost]]:
    url = f"https://t.me/s/{channel}" + (f"?before={before}" if before else "")
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "uk,en;q=0.8"})
    with urllib.request.urlopen(req, timeout=timeout, context=_CTX) as resp:
        final_url = resp.geturl()
        body = resp.read().decode("utf-8", "replace")
    if "/s/" not in final_url and "tgme_widget_message" not in body:
        raise NoWebPreview(channel)
    return parse_page(body, channel)


def fetch_new(channel: str, last_id: int, max_pages: int = 5) -> tuple[str | None, list[WebPost]]:
    """Усі пости з id > last_id (до max_pages сторінок по ~20 постів), від старих до нових."""
    title, page = fetch_page(channel)
    collected = {p.id: p for p in page if p.id > last_id}
    pages = 1
    while page and last_id and min(p.id for p in page) > last_id + 1 and pages < max_pages:
        _, page = fetch_page(channel, before=min(p.id for p in page))
        pages += 1
        for p in page:
            if p.id > last_id:
                collected[p.id] = p
    return title, sorted(collected.values(), key=lambda x: x.id)
