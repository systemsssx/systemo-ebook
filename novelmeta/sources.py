# -*- coding: utf-8 -*-
"""Source adapters. Each returns normalized Candidates for a book title.

Registry design notes
---------------------
Every adapter answers the same question ("given an accurate title, who wrote it
and what is the cover?"), so callers can fan out to all of them concurrently and
rank whatever comes back. Sources are declared with a priority; lower wins.

Network reality check (measured from this machine; see README "数据源实测"):
  * weread (微信读书)   : works — JSON, title + author + cover + intro. Primary.
  * jjwxc (晋江文学城)  : works — server-rendered HTML, GB18030 query encoding.
  * fanqie (番茄小说)   : detail pages are SSR and parse fine, but the search
                          endpoint is signature-gated (ByteDance `a_bogus`) and
                          answers 200 with a zero-byte body, so title -> id is
                          not resolvable over plain HTTP.
  * qidian (起点中文网) : NOT implemented as a source on purpose. www.qidian.com
                          is behind Tencent's WAF (202 + probe.js challenge) and
                          m.qidian.com/soushu/<kw>.html renders its result list
                          client-side, so a plain GET yields no book records.
                          Qidian/Yuewen titles are covered through 微信读书.
  * webnovel / syosetu  : international; timed out from this network.
Adapters that cannot reach or parse their site raise SourceError, which the
caller reports per-source instead of failing the whole lookup.
"""

from __future__ import annotations

import html
import json
import os
import re
import socket
import threading
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable, Iterable, Sequence

from .catalog import (
    BILINOVEL_BASE,
    BILINOVEL_DETAIL,
    BILINOVEL_ID_CACHE,
    BILINOVEL_INDEX,
    FANQIE_ID_CACHE,
    get_index,
)
from .model import Candidate, title_similarity, norm_title, search_title
from .net import FetchError, Response, fetch

__all__ = [
    "SourceError",
    "REGISTRY",
    "get_source",
    "source_names",
    "search_sources",
    "Diagnostic",
    "cover_variants",
]

DESKTOP_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
MOBILE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1"
)


def _content_hit_count(page_text: str, query: str) -> int:
    """查询词在页面原文里的命中次数（译名容错用）。

    先按整串比（去掉空白，容忍排版差异）；整串不中就退到查询的**前 4 个字 / 前 3 个字**
    再比一次 —— 因为用户可能只搜作品名的一部分（「败犬女主」），而本站正名是另一个译名
    （《败北女角太多了！》）。实测：那一页里「败犬女主」出现 4 次，而整串
    「败犬女主太多了」也可能出现 —— 两种写法都要能命中。
    """
    if not page_text or not query:
        return 0
    needle = re.sub(r"\s+", "", query)
    hay = re.sub(r"\s+", "", page_text)
    if not needle:
        return 0
    n = hay.count(needle)
    if n:
        return n
    for cut in (4, 3):
        if len(needle) > cut:
            n = hay.count(needle[:cut])
            if n:
                return n
    return 0


class SourceError(RuntimeError):
    """A source could not produce data (network, WAF, missing field...)."""

    def __init__(self, source: str, message: str, *, blocked: bool = False):
        self.source = source
        self.blocked = blocked
        super().__init__(message)


@dataclass
class Diagnostic:
    source: str
    ok: bool
    count: int = 0
    error: str = ""
    blocked: bool = False
    ms: int = 0

    def as_dict(self) -> dict:
        d = {"source": self.source, "ok": self.ok, "count": self.count, "ms": self.ms}
        if self.error:
            d["error"] = self.error
        if self.blocked:
            d["blocked"] = True
        return d


def _dig(obj, *path, default=None):
    """Safe nested lookup: _dig(data, 'a', 'b', 0, 'c')."""
    cur = obj
    for key in path:
        if isinstance(cur, dict):
            cur = cur.get(key)
        elif isinstance(cur, list) and isinstance(key, int):
            cur = cur[key] if 0 <= key < len(cur) else None
        else:
            return default
        if cur is None:
            return default
    return cur


def _extract_json_object(text: str, start: int) -> str | None:
    """Return the balanced `{...}` substring beginning at text[start] == '{'.

    Needed because the interesting data sits inside an inline JS state blob where a
    plain regex cannot cope with nested braces (categoryV2 holds escaped JSON).
    """
    if start >= len(text) or text[start] != "{":
        return None
    depth = 0
    in_string = False
    escaped = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return None


def _extract_state(html_text: str, key: str) -> dict | None:
    """Pull one JSON object out of an inline page-state blob, e.g. "page":{...}.

    The blob is a JS object literal, so JS-only values are sanitized first; without
    that, one `undefined` field (present on plenty of books) rejects the whole
    object and the caller silently falls back to SEO meta tags.
    """
    match = re.search(rf'"{re.escape(key)}"\s*:\s*\{{', html_text)
    if not match:
        return None
    start = html_text.index("{", match.start())
    raw = _extract_json_object(html_text, start)
    if not raw:
        return None
    for candidate in (raw, _sanitize_js_literals(raw)):
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        return value if isinstance(value, dict) else None
    return None


_JSON_U_ESCAPE = re.compile(r"\\u([0-9a-fA-F]{4})")
# The inline state is a JS object literal, not JSON: some books carry
# `"description":undefined`, which makes json.loads() reject the whole blob.
_JS_LITERAL_RE = re.compile(r"(?:undefined|NaN|-?Infinity)\b")


def _sanitize_js_literals(text: str) -> str:
    """Turn JS-only literals into `null`, leaving string contents untouched."""
    out: list[str] = []
    i, n = 0, len(text)
    in_string = False
    escaped = False
    while i < n:
        ch = text[i]
        if in_string:
            out.append(ch)
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            i += 1
            continue
        if ch == '"':
            in_string = True
            out.append(ch)
            i += 1
            continue
        match = _JS_LITERAL_RE.match(text, i)
        if match:
            out.append("null")
            i = match.end()
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _unescape_json_url(value: str) -> str:
    """Inline state stores URLs as `https:\\u002F\\u002F...`; decode only the escapes."""
    if not value:
        return ""
    value = value.replace("\\/", "/")
    return _JSON_U_ESCAPE.sub(lambda m: chr(int(m.group(1), 16)), value)


def _abs_url(url: str, base: str = "") -> str:
    if not url:
        return ""
    url = _unescape_json_url(html.unescape(url.strip()))
    if url.startswith("//"):
        return "https:" + url
    if url.startswith("http://"):
        return "https://" + url[len("http://"):]
    if url.startswith("/") and base:
        return base.rstrip("/") + url
    return url


def _strip_tags(text: str) -> str:
    text = re.sub(r"<script.*?</script>", " ", text, flags=re.S | re.I)
    text = re.sub(r"<style.*?</style>", " ", text, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


_COVER_SIZE_PREFIXES = ("s_", "m_", "b_", "t_")


def cover_variants(url: str) -> list[str]:
    """Return the same cover at descending sizes, best first.

    WeRead's image CDN encodes the size in the file name prefix; measured on
    `.../cover/68/480068/s_480068.jpg`: `s_`=70x100, `m_`=84x120, `b_`=140x200.
    Unknown/other URLs are returned untouched so the caller can still try them.
    """
    if not url:
        return []
    head, sep, name = url.rpartition("/")
    if not sep:
        return [url]
    out: list[str] = []
    for prefix in ("b_", "m_"):
        if name[:2] in _COVER_SIZE_PREFIXES:
            out.append(f"{head}/{prefix}{name[2:]}")
    out.append(url)
    deduped: list[str] = []
    for item in out:
        if item not in deduped:
            deduped.append(item)
    return deduped


def _looks_blocked(resp: Response) -> bool:
    """Detect anti-bot interstitials that answer 200 but carry no data."""
    if resp.status in (202, 403, 412, 429):
        return True
    head = resp.body[:4000].decode("utf-8", "ignore").lower()
    markers = ("probe.js", "captcha", "cf-challenge", "geetest",
               "访问过于频繁", "安全验证", "验证码", "请开启javascript")
    return any(m in head for m in markers)


# --------------------------------------------------------------------------- #
# 微信读书 / WeRead (Tencent)  — primary source
# --------------------------------------------------------------------------- #
class WeReadSource:
    name = "weread"
    label = "微信读书"
    priority = 10
    SEARCH = "https://weread.qq.com/web/search/global"
    REFERER = "https://weread.qq.com/"

    def search(self, title: str, *, limit: int = 20, timeout: float = 12.0) -> list[Candidate]:
        params = {
            "keyword": title,
            "maxIdx": "0",
            "fragmentSize": "120",
            "count": str(max(1, min(limit, 20))),
        }
        url = self.SEARCH + "?" + urllib.parse.urlencode(params)
        try:
            resp = fetch(url, headers={"Referer": self.REFERER, "User-Agent": DESKTOP_UA},
                         timeout=timeout, tries=3)
        except FetchError as exc:
            raise SourceError(self.name, str(exc)) from exc
        if resp.status != 200:
            raise SourceError(self.name, f"HTTP {resp.status}", blocked=_looks_blocked(resp))
        try:
            data = json.loads(resp.text)
        except json.JSONDecodeError as exc:
            raise SourceError(self.name, f"响应不是 JSON: {exc}") from exc

        books = data.get("books") or []
        out: list[Candidate] = []
        for pos, item in enumerate(books):
            info = item.get("bookInfo") if isinstance(item, dict) else None
            if not isinstance(info, dict):
                continue
            title_text = (info.get("title") or "").strip()
            if not title_text:
                continue
            book_id = str(info.get("bookId") or "").strip()
            cover = _abs_url(info.get("cover") or "")
            out.append(
                Candidate(
                    title=title_text,
                    author=(info.get("author") or "").strip(),
                    cover=cover,
                    source=self.name,
                    url=f"https://weread.qq.com/web/bookDetail/{book_id}" if book_id else "",
                    intro=(info.get("intro") or "").strip(),
                    position=pos,
                    extra={
                        "bookId": book_id,
                        "publisher": info.get("publisher") or "",
                        "newRating": info.get("newRating"),
                        "readingCount": item.get("readingCount"),
                        # WeRead serves the same artwork at several sizes:
                        # s_ = 70x100, m_ = 84x120, b_ = 140x200 (measured).
                        "coverVariants": cover_variants(cover),
                    },
                )
            )
        if not out:
            raise SourceError(self.name, "搜索接口返回 0 条结果")
        return out


# --------------------------------------------------------------------------- #
# 番茄小说 / Fanqie (ByteDance)
# --------------------------------------------------------------------------- #
class FanqieSource:
    """Fanqie search + detail.

    The community-known search endpoint
    `/api/author/search/search_book/v1` is signature-gated: without ByteDance's
    `a_bogus`/`msToken` parameters it answers `200` with a zero-byte body.
    Detail pages (`/page/<bookId>`) are server-rendered and parse fine, so the
    adapter uses SSR detail pages for anything it can resolve, and reports the
    search endpoint as blocked when it returns nothing.
    """

    name = "fanqie"
    label = "番茄小说"
    priority = 30
    SEARCH = "https://fanqienovel.com/api/author/search/search_book/v1"
    DETAIL = "https://fanqienovel.com/page/{book_id}"
    REFERER = "https://fanqienovel.com/"

    def search(self, title: str, *, limit: int = 20, timeout: float = 12.0) -> list[Candidate]:
        params = {
            "filter": "127,127,127,127",
            "page_count": str(min(max(limit, 1), 10)),
            "page_index": "0",
            "query_type": "0",
            "query_word": title,
        }
        url = self.SEARCH + "?" + urllib.parse.urlencode(params, safe=",")
        try:
            resp = fetch(url, headers={"Referer": self.REFERER, "User-Agent": DESKTOP_UA},
                         timeout=timeout, tries=2)
        except FetchError as exc:
            # ★ 2026-09-29：官方搜索都连不上时，同样退到必应拿 id
            resolved = self._search_via_bing(title, timeout=timeout)
            if resolved:
                return resolved
            raise SourceError(self.name, str(exc)) from exc
        if not resp.body.strip():
            # ★ 2026-09-29：官方搜索被 Bdturing 滑块拦住 → 先试"必应拿 id + 官方详情页"，
            #   命中就正常返回；仍然拿不到才抛 blocked（诊断里能看出是哪一步失败）。
            resolved = self._search_via_bing(title, timeout=timeout)
            if resolved:
                return resolved
            raise SourceError(
                self.name,
                "搜索接口要求通过字节 Bdturing 滑块验证：HTTP 200 但 Content-Length 为 0，"
                "响应头 Bdturing-Verify 指示 subtype=slide（不是签名参数问题）。"
                "备用的必应 site: 解析也未命中；可改用直接粘贴番茄书籍链接取详情",
                blocked=True,
            )
        try:
            data = json.loads(resp.text)
        except json.JSONDecodeError as exc:
            raise SourceError(self.name, f"响应不是 JSON: {exc}", blocked=_looks_blocked(resp)) from exc

        rows = _dig(data, "data", "book_data", default=None)
        if rows is None:
            rows = _dig(data, "data", "search_book_data", default=None)
        if not rows:
            raise SourceError(self.name, "搜索接口无结果")

        out: list[Candidate] = []
        for row in rows:
            book_id = str(row.get("book_id") or row.get("bookId") or "").strip()
            thumb = row.get("thumb_url") or row.get("thumb_uri") or row.get("cover") or ""
            out.append(
                Candidate(
                    title=(row.get("book_name") or row.get("title") or "").strip(),
                    author=(row.get("author") or "").strip(),
                    cover=_abs_url(thumb),
                    source=self.name,
                    url=self.DETAIL.format(book_id=book_id) if book_id else "",
                    intro=(row.get("abstract") or "").strip(),
                    position=len(out),
                    extra={"bookId": book_id, "wordNumber": row.get("word_number")},
                )
            )
        out = [c for c in out if c.title]
        if not out:
            raise SourceError(self.name, "搜索结果缺少书名字段")
        return out

    # ------------------------------------------------------------------ #
    # 备用的「书名 → book_id」解析（2026-09-29 新增）
    #
    # 番茄官方搜索接口被字节 Bdturing 滑块拦死（HTTP 200 + 空 body），
    # 但**详情页是 SSR 的**：只要有 book_id 就能拿到全部元数据。
    # 缺的只是这一跳，用必应 `site:fanqienovel.com <书名>` 补上：
    #   · 只读公开搜索结果页，完全不碰番茄的风控；纯 HTTP、秒级（se 方案要 10~20 秒）
    #   · `&format=rss` 只有约 5KB（HTML 版约 97KB），更好解析
    #   · 实测：十日终焉 / 我不是戏神 / 我在精神病院学斩神 全部命中；
    #           起点书（大奉打更人 / 诡秘之主）0 误报
    #   · 必应有速率限制 → 单次查询最多 1 个请求 + 7 天磁盘缓存
    #   · ★ 只取元数据，绝不抓正文
    # ------------------------------------------------------------------ #
    BING = "https://cn.bing.com/search"
    _BING_ID_RE = re.compile(r"/page/(\d{15,25})")

    @classmethod
    def _bing_url(cls, title: str, *, rss: bool = True) -> str:
        q = urllib.parse.quote(f"site:fanqienovel.com {title}")
        url = f"{cls.BING}?q={q}&count=20"
        return url + "&format=rss" if rss else url

    def _resolve_book_id(self, title: str, *, timeout: float = 12.0) -> str:
        """书名 → book_id（必应 site:，带 7 天缓存）。拿不到返回空串。"""
        cached = FANQIE_ID_CACHE.get(title)
        if cached:
            return cached
        headers = {"User-Agent": DESKTOP_UA, "Accept-Language": "zh-CN,zh;q=0.9"}
        for rss in (True, False):          # RSS 优先（约 5KB），失败再退 HTML
            try:
                resp = fetch(self._bing_url(title, rss=rss), headers=headers,
                             timeout=timeout, tries=1)
            except FetchError:
                continue
            if resp.status != 200:
                continue
            match = self._BING_ID_RE.search(resp.text)
            if match:
                FANQIE_ID_CACHE.put(title, match.group(1))
                return match.group(1)
        return ""

    def _search_via_bing(self, title: str, *, timeout: float = 12.0) -> list[Candidate]:
        """必应拿 id → 官方 SSR 详情页 → 一致性校验通过才采纳。"""
        book_id = self._resolve_book_id(title, timeout=timeout)
        if not book_id:
            return []
        try:
            cand = self.detail(book_id, timeout=max(timeout, 15.0))
        except SourceError:
            return []
        # ★ 必应只保证"该域名下有这个页面"，不保证是同一本书 → 必须校验书名
        score = title_similarity(title, cand.title)
        if score < 0.85:
            return []
        cand.title_score = score
        cand.position = 0
        cand.extra["bookId"] = book_id
        cand.extra["resolvedBy"] = "bing"
        return [cand]

    def detail(self, book_id: str, *, timeout: float = 15.0) -> Candidate:
        """Parse a Fanqie SSR detail page (title / author / cover / abstract)."""
        url = self.DETAIL.format(book_id=book_id)
        try:
            resp = fetch(url, headers={"Referer": self.REFERER, "User-Agent": DESKTOP_UA},
                         timeout=timeout, tries=3)
        except FetchError as exc:
            raise SourceError(self.name, str(exc)) from exc
        if resp.status != 200:
            raise SourceError(self.name, f"HTTP {resp.status}", blocked=_looks_blocked(resp))
        page = resp.text

        def meta(prop: str) -> str:
            m = re.search(
                rf'<meta[^>]+(?:property|name)="{re.escape(prop)}"[^>]+content="(?P<c>[^"]*)"',
                page, re.I,
            )
            if not m:
                m = re.search(
                    rf'<meta[^>]+content="(?P<c>[^"]*)"[^>]+(?:property|name)="{re.escape(prop)}"',
                    page, re.I,
                )
            return html.unescape(m.group("c")).strip() if m else ""

        state = _extract_state(page, "page") or {}
        title = (state.get("bookName") or "").strip()
        author = (state.get("author") or "").strip()
        cover = _abs_url(str(state.get("thumbUrl") or state.get("thumbUri") or ""))
        intro = (state.get("abstract") or "").strip()

        if not title:
            # "书名完整版在线免费阅读_书名小说_番茄小说官网" -> "书名"
            tm = re.search(r"<title>(?P<t>.*?)</title>", page, re.S)
            if tm:
                parts = [p for p in _strip_tags(tm.group("t")).split("_") if p]
                if len(parts) >= 2:
                    title = parts[1][:-len("小说")] if parts[1].endswith("小说") else parts[1]
                elif parts:
                    title = re.sub(r"(完整版)?(在线)?免费阅读$", "", parts[0]).strip()
        if not author:
            author = meta("og:novel:author") or meta("author")
        if not cover:
            cm = re.search(r'"(?:thumbUrl|thumb_uri|thumbUri|coverUrl)"\s*:\s*"([^"]+)"', page)
            cover = _abs_url(cm.group(1) if cm else meta("og:image"))
        if not intro:
            intro = meta("description")

        categories: list[str] = []
        raw_categories = state.get("categoryV2")
        if isinstance(raw_categories, str) and raw_categories.startswith("["):
            try:
                categories = [c.get("Name") for c in json.loads(raw_categories)
                              if isinstance(c, dict) and c.get("Name")]
            except json.JSONDecodeError:
                categories = []
        return Candidate(
            title=title,
            author=author,
            cover=cover,
            source=self.name,
            url=url,
            intro=intro,
            extra={
                "bookId": str(state.get("bookId") or book_id),
                "authorId": state.get("authorId"),
                "wordNumber": state.get("wordNumber"),
                "categories": categories or None,
                "lastChapter": state.get("lastChapterTitle"),
                "stateParsed": bool(state),
                # The image CDN is hot-link protected; tell the downloader which
                # Referer to send instead of letting it guess from the host.
                "coverReferer": self.REFERER,
            },
        )


# --------------------------------------------------------------------------- #
# 晋江文学城 / JJWXC
# --------------------------------------------------------------------------- #
class JjwxcSource:
    """JJWXC (晋江文学城) search — server-rendered HTML, GB18030.

    Two quirks are easy to get wrong and were both verified against the live
    site: the query string must be percent-encoded in **GB18030** (UTF-8 encoding
    silently returns the generic page with zero hits), and the response body is
    GB18030 too. Result rows look like:

        <h3 class="title"><a href="...onebook.php?novelid=2368172">魔道祖师</a>
          <font size='-2' color='gray'>(2015-10-31)</font></h3>
        <div class="intro"> 简介 </div>
        <div class="info"> 作者： <a href="...oneauthor.php?authorid=...">墨香铜臭</a> ┃ 进度：完结 ┃ ... </div>

    JJWXC publishes no cover image in search results, so `cover` stays empty and
    the resolver prefers another source for artwork.
    """

    name = "jjwxc"
    label = "晋江文学城"
    priority = 25
    SEARCH = "https://www.jjwxc.net/search.php?kw={kw}&t=1&p={page}"
    REFERER = "https://www.jjwxc.net/"

    _ITEM_RE = re.compile(
        r'<h3[^>]*class="[^"]*title[^"]*"[^>]*>(?P<head>.*?)</h3>(?P<tail>.*?)(?=<h3[^>]*class="[^"]*title|</form>|\Z)',
        re.S,
    )
    _LINK_RE = re.compile(
        r'<a[^>]+href="(?P<href>[^"]*onebook\.php\?novelid=\d+[^"]*)"[^>]*>(?P<title>.*?)</a>', re.S
    )
    _AUTHOR_RE = re.compile(
        r'作者\s*[:：]?\s*(?:<[^>]+>\s*)*(?:<a[^>]*authorid=(?P<aid>\d+)[^>]*>)?(?P<author>.*?)(?:</a>)?\s*(?:┃|</div>)',
        re.S,
    )
    _INTRO_RE = re.compile(r'<div[^>]*class="[^"]*intro[^"]*"[^>]*>(?P<intro>.*?)</div>', re.S)

    @staticmethod
    def _quote_gb18030(text: str) -> str:
        return urllib.parse.quote(text.encode("gb18030", "ignore"))

    def _decode(self, resp: Response) -> str:
        """JJWXC serves GB18030 regardless of what the charset header claims."""
        try:
            return resp.body.decode("gb18030")
        except UnicodeDecodeError:
            return resp.body.decode("utf-8", "replace")

    def search(self, title: str, *, limit: int = 20, timeout: float = 12.0) -> list[Candidate]:
        url = self.SEARCH.format(kw=self._quote_gb18030(title), page=1)
        try:
            resp = fetch(url, headers={"Referer": self.REFERER, "User-Agent": DESKTOP_UA},
                         timeout=timeout, tries=3)
        except FetchError as exc:
            raise SourceError(self.name, str(exc)) from exc
        if _looks_blocked(resp) or resp.status != 200:
            raise SourceError(self.name, f"HTTP {resp.status}", blocked=_looks_blocked(resp))
        page = self._decode(resp)

        out: list[Candidate] = []
        seen: set[str] = set()
        for item in self._ITEM_RE.finditer(page):
            head, tail = item.group("head"), item.group("tail")
            link = self._LINK_RE.search(head)
            if not link:
                continue
            book_title = _strip_tags(link.group("title"))
            if not book_title or book_title in seen:
                continue
            seen.add(book_title)
            am = self._AUTHOR_RE.search(tail)
            author = _strip_tags(am.group("author")) if am else ""
            author = author.replace("┃", "").strip()
            im = self._INTRO_RE.search(tail)
            out.append(
                Candidate(
                    title=book_title,
                    author=author,
                    cover="",  # 晋江搜索结果不含封面图
                    source=self.name,
                    url=_abs_url(link.group("href"), "https://www.jjwxc.net"),
                    intro=_strip_tags(im.group("intro"))[:200] if im else "",
                    position=len(out),
                    extra={"coverAvailable": False},
                )
            )
            if len(out) >= limit:
                break
        if not out:
            raise SourceError(self.name, "未匹配到搜索结果（该站可能无此书）")
        return out



# --------------------------------------------------------------------------- #
# 哔哩轻小说 / bilinovel.com  (light novels)
# --------------------------------------------------------------------------- #
# Set by the CLI so a first-run index build can report progress to stderr.
BILINOVEL_PROGRESS: Callable[[str], None] | None = None
_BILINOVEL_SINGLETON = None      # resolve_bilinovel() 用的单例（在类定义后赋值）


# --------------------------------------------------------------------------- #
# 哔哩轻小说「搜索守卫」——站内搜索（★ 2026-09-30 实测摸清，取代"搜索不可用"的旧结论）
# --------------------------------------------------------------------------- #
# 旧结论（README §哔哩轻小说 / BilinovelSource 旧注释）说：`POST` 搜索接口
# **返回 200 但 body 为 0 字节**，带不带 Cookie、换不换 UA 都一样 → 当成 Cloudflare
# 验证 / CSR 空壳，于是改走「本地目录索引 + 必应反查」。
#
# 真正的原因是站点给搜索加了一道**三步守卫**，少任何一步都只给空 body：
#     GET  /S6/?search_guard=css             → 下发 cookie  jieqiSearchCss
#     GET  /S6/?search_guard=js              → 响应体里抠 jieqiSearchJs=<token> → 记成 cookie
#     GET  /S6/?search_guard=redeem&r=<毫秒>  → 下发 cookie  jieqiSearchTicket
#     POST /S6/   body: searchkey=<关键词>     → 带上这三个 cookie 才给结果页
#
# 三个实测坑（都踩过）：
#   ① **jieqiSearchCss 也必须带上** —— 只带 js+ticket 一样是空 body；
#   ② ticket **一次性**，搜完即废，下次搜索必须重新握手；
#   ③ 偶发仍返回**残缺页**（实测「魔法」第一次只给了 3025 字节、既无命中数也无结果列表）
#      → 判据用「页面里有 `search-tips` 或 `book-html-box`」，不满足就重握手重试。
#
# ★ 域名：站内搜索只在 **www.linovelib.com** 上通；同站的 www.bilinovel.com 请求 /S6/
#   会被本机解析到 127.0.0.1 直接拒连（实测），但它的 `/novel/<id>.html` 详情页正常且更快，
#   所以：**搜索走 linovelib，详情/封面继续走 BILINOVEL_BASE**（novel_id 两边通用）。
BILINOVEL_SEARCH_BASE = "https://www.linovelib.com"
BILINOVEL_SEARCH_URL = BILINOVEL_SEARCH_BASE + "/S6/"

_BL_JS_TOKEN_RE = re.compile(r"jieqiSearchJs=([^;\"'\s]+)")
_BL_TIPS_RE = re.compile(r'class="search-tips"')
_BL_ALIAS_RE = re.compile(r'class="bkname-body"[^>]*>(.*?)<', re.S)


def _parse_bilinovel_search(page: str) -> list[Candidate]:
    """搜索结果页 → Candidate 列表（保持站点自身的相关度顺序）。两种形态（2026-09-30 实测）：

    ① **单本精确命中**：`div.book-html-box`
       + `h1.book-name`（本站正名）+ `meta[name=url]`（书号）+ `img`（封面）
       + `span.bkname-body`（**别名**）
       站点自己判定"就是这一本"时才给这种页。实测搜「败犬女主太多了」直接给这个页，
       页面上明写「别名：败犬女主太多了！」—— 这是**站点自己给出的同书异名证据**，
       比"查询词在页面里出现过"更硬，所以标 `siteSingle`，由 rank_candidates 抬分。
    ② **候选列表**：`div.search-result-list` × N（`h2.tit > a[href=/novel/<id>.html]` 书名+书号，
       `authorarticle.php?author=` 作者，`img` 封面）—— 正常打分，不加成。
    """
    out: list[Candidate] = []

    if "book-html-box" in page:
        nid = re.search(r'name="url"[^>]*content="[^"]*/novel/(\d+)', page)
        name = re.search(r'class="book-name"[^>]*>(.*?)</h1>', page, re.S)
        cover = re.search(r'class="book-img[^"]*"[^>]*>\s*<img[^>]*src="([^"]+)"', page, re.S)
        intro = re.search(r'class="book-dec[^"]*"[^>]*>(.*?)</div>', page, re.S)
        aliases = [_strip_tags(a).strip() for a in _BL_ALIAS_RE.findall(page)]
        if nid:
            out.append(Candidate(
                title=_strip_tags(name.group(1)).strip() if name else "",
                author="",          # 单本页没有作者字段（作者在详情页 og:novel:author 里）
                cover=_abs_url((cover.group(1) if cover else "").split("?")[0]),
                source="bilinovel",
                url=BILINOVEL_DETAIL.format(novel_id=nid.group(1)),
                intro=_strip_tags(intro.group(1))[:400] if intro else "",
                position=0,
                extra={"novelId": nid.group(1), "resolvedBy": "search",
                       "siteSingle": True, "aliases": aliases},
            ))
        return out

    for pos, blk in enumerate(re.split(r'<div class="search-result-list', page)[1:]):
        m = re.search(r'<h2[^>]*>\s*<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', blk, re.S)
        if not m:
            continue
        nid = re.search(r"/novel/(\d+)", m.group(1))
        if not nid:
            continue
        au = re.search(r'authorarticle\.php\?author=[^"]*"[^>]*>([^<]+)<', blk)
        cv = re.search(r'<img[^>]*src="([^"]+)"', blk)
        out.append(Candidate(
            title=_strip_tags(m.group(2)).strip(),
            author=_strip_tags(au.group(1)).strip() if au else "",
            cover=_abs_url((cv.group(1) if cv else "").split("?")[0]),
            source="bilinovel",
            url=BILINOVEL_DETAIL.format(novel_id=nid.group(1)),
            position=pos,
            extra={"novelId": nid.group(1), "resolvedBy": "search"},
        ))
    return out


class _BilinovelSearch:
    """站内搜索：三步守卫握手 + POST 搜索 + 解析（纯标准库 + novelmeta.net）。"""

    def __init__(self) -> None:
        self.cookies: dict[str, str] = {}

    # -- 守卫 --------------------------------------------------------------- #
    def _headers(self, referer: str, ctype: str = "") -> dict[str, str]:
        h = {
            "User-Agent": DESKTOP_UA,
            "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Origin": BILINOVEL_SEARCH_BASE,
            "Referer": referer,
            "Sec-Fetch-Site": "same-origin",
        }
        if ctype:
            h["Content-Type"] = ctype
        if self.cookies:
            h["Cookie"] = "; ".join(f"{k}={v}" for k, v in self.cookies.items())
        return h

    def _absorb(self, resp: Response) -> None:
        raw = resp.headers.get("Set-Cookie")
        if not raw:
            return
        for part in re.split(r",(?=[^;=]+=)", raw):
            m = re.match(r"\s*([^=;]+)=([^;]*)", part)
            if m:
                self.cookies[m.group(1).strip()] = m.group(2).strip()

    def _get(self, url: str, *, timeout: float, referer: str) -> Response:
        # tries=2：这台机器出口抖（README「工程细节」）——同域名多个 A 记录，
        # 单地址偶发失败，tries=1 会让整套握手白跑（实测 refresh 9.7s 还是失败）。
        resp = fetch(url, headers=self._headers(referer), timeout=timeout, tries=2)
        self._absorb(resp)
        return resp

    def refresh(self, *, timeout: float) -> bool:
        """走完三步守卫，拿到一次性 ticket。"""
        self.cookies.pop("jieqiSearchTicket", None)
        base_ref = BILINOVEL_SEARCH_BASE + "/"
        try:
            self._get(BILINOVEL_SEARCH_URL + "?search_guard=css", timeout=timeout, referer=base_ref)
            js = self._get(BILINOVEL_SEARCH_URL + "?search_guard=js", timeout=timeout, referer=base_ref)
        except FetchError:
            return False
        token = _BL_JS_TOKEN_RE.search(js.text)
        if not token:
            return False
        self.cookies["jieqiSearchJs"] = token.group(1)
        for delay in (0.12, 0.8, 2.0):
            time.sleep(delay)
            try:
                self._get(BILINOVEL_SEARCH_URL + "?search_guard=redeem&r=%d" % int(time.time() * 1000),
                          timeout=timeout, referer=BILINOVEL_SEARCH_URL)
            except FetchError:
                continue
            if "jieqiSearchTicket" in self.cookies:
                return True
        return "jieqiSearchTicket" in self.cookies

    # -- 搜索 --------------------------------------------------------------- #
    def search(self, keyword: str, *, timeout: float, attempts: int = 3) -> list[Candidate]:
        """站内搜索。空 body / 残缺页 / 守卫失败一律重握手重试；全失败返回空列表。"""
        for _ in range(max(1, attempts)):
            if not self.refresh(timeout=timeout):
                continue
            try:
                resp = fetch(BILINOVEL_SEARCH_URL,
                             headers=self._headers(BILINOVEL_SEARCH_BASE + "/",
                                                   "application/x-www-form-urlencoded"),
                             timeout=timeout, tries=2, method="POST",
                             form={"searchkey": keyword})
            except FetchError:
                continue
            self._absorb(resp)
            page = resp.text
            # ★ 判据：正常结果页一定有 search-tips（「共搜索到 N 部」）或单本命中块
            if _BL_TIPS_RE.search(page) or "book-html-box" in page:
                return _parse_bilinovel_search(page)
        return []


def bilinovel_source() -> "BilinovelSource":
    """进程内单例（索引与缓存复用）。"""
    global _BILINOVEL_SINGLETON
    if _BILINOVEL_SINGLETON is None:
        _BILINOVEL_SINGLETON = BilinovelSource()
    return _BILINOVEL_SINGLETON


def bilinovel_search_candidates(title: str, *, timeout: float = 15.0) -> list:
    """站内搜索的候选列表（**保持站点自己的相关度顺序**），每项带 novelId/title/author/cover。

    ★ 2026-09-30：给界面"多结果让用户选"用的 —— 书名可能对应好几部
      （实测「无职转生」→ 正传 + 外传《蛇足篇》；中文网文源那边还有一堆同人），
      光靠打分排序会选错（外传标题更短，前缀分反而更高），所以把选择权交回给人。
    """
    from .model import title_similarity

    key = (title or "").strip()
    if not key:
        return []
    src = bilinovel_source()
    out = []
    try:
        for c in src._search_site(key, timeout=max(timeout, 15.0)):
            if (c.extra.get("matchedBy") in ("content", "site")
                    or title_similarity(key, c.title) >= 0.85):
                c.extra.setdefault("resolvedBy", "search")
                out.append(c)
    except Exception:  # noqa: BLE001
        return []
    return out


def resolve_bilinovel(title: str, *, timeout: float = 15.0, exact_only: bool = False,
                      allow_bing: bool = False, allow_index_build: bool = True):
    """★ 2026-09-30：**书名 → Candidate 的统一解析**（linovelib.py / novel_dl.py 共用）。

    顺序：
      ① **精确命中**本地目录（norm 后完全相同）→ 直接返回（0.1s、零网络）；
      ② 否则走**站内搜索**，按**站点自己的相关度顺序**取第一个够像的；
      ③ 站内搜索没结果 → 回到 `BilinovelSource.search()`（索引打分 + 必应反查）。

    ★ `allow_index_build=False`（2026-09-30 新增，**交互式调用方用**）：
      ① 只吃**已缓存**的本地目录；没缓存就**跳过**、直接进②站内搜索。
      为什么：冷缓存时①会现爬 178 页（站点限流下数分钟）—— 用户实测「按书名查分卷」
      三次都卡在这一步、界面像卡死。预热索引请走 CLI（`meta_lookup.py --allow-index-build`）。

    ★★ 为什么②必须听站点的顺序，而不是自己按相似度排：
      实测搜「**无职转生**」，本地目录里有两部 ——
      正传《无职转生 ～到了异世界就拿出真本事～》和外传《无职转生 ～蛇足篇～》。
      我们的 `title_similarity` 对"前缀匹配"按**长度比**给分，外传标题更短 → 分数反而更高
      → **索引打分把外传排到了正传前面（选错书，用户实测报的正是这个）**。
      索引是从 `/wenku/` 列表建的，**没有任何相关度/热度信息**；
      而站内搜索结果的第一条就是正传 —— 这种"只写了正传名"的情况必须听站点的。

    `exact_only=True` 时只做①（给"先快速判一下是不是轻小说"的预检用，避免拖慢普通网文）。
    """
    from .model import Candidate, title_similarity

    key = (title or "").strip()
    if not key:
        return None
    src = bilinovel_source()

    # ① 精确命中（正名，含《》！等装饰差异）
    best = None
    for e in src.load_index(timeout=max(timeout, 20.0), build_if_missing=allow_index_build):
        s = title_similarity(key, e.title)
        if s >= 0.995 and (best is None or s > best[0]):
            best = (s, e)
    if best is not None:
        e = best[1]
        return Candidate(title=e.title, author=e.author, cover=e.cover, source=src.name,
                         url=e.url, intro=e.description, position=0,
                         extra={"novelId": e.novel_id, "resolvedBy": "index-exact",
                                "matchedBy": "title"})
    if exact_only:
        return None

    # ② 站内搜索：**保持站点返回的顺序**（那个顺序就是它的相关度排序）
    try:
        for c in src._search_site(key, timeout=max(timeout, 15.0)):
            if (c.extra.get("matchedBy") in ("content", "site")
                    or title_similarity(key, c.title) >= 0.85):
                c.extra.setdefault("resolvedBy", "search")
                return c
    except Exception:  # noqa: BLE001 —— 站内搜索只是中间一层，失败不该让整件事失败
        pass

    # ③ 兜底：索引打分 + 必应反查（★ 默认**不做**：必应那步要 5~10s，而②的站内搜索
    #    已经能处理译名（实测「败犬女主太多了」靠站点别名栏就命中了）。
    #    只有调用方明确说"这是最后一道防线"时才开（novel_dl 的第二次尝试）。
    if not allow_bing:
        return None
    try:
        for c in src.search(key, timeout=timeout):
            if (c.extra.get("matchedBy") in ("content", "site")
                    or title_similarity(key, c.title) >= 0.85):
                return c
    except Exception:  # noqa: BLE001
        return None
    return None


class BilinovelSource:
    """哔哩轻小说 (bilinovel.com / linovelib.com) — light novels.

    三层取数（2026-09-30 定稿；层号与 `search()` 里的注释一致）：

      ① **本地目录索引**（5327+ 部，见 `novelmeta.catalog`）：`/wenku/` 公开列表镜像，
         命中 `title_similarity >= 0.60` 就直接答，**零网络**（实测 0.1s）。
      ② **站内搜索**（`_BilinovelSearch`，2026-09-30 新增）：三步搜索守卫 + POST /S6/。
         覆盖全站（含目录里没有的新书/冷门书）。站点给"单本精确命中"页时标 `matchedBy='site'`。
      ③ **必应 site: 反查**（2026-09-29）：译名容错的最后兜底，见下面的小结。

    历史更正：旧注释写「搜索接口返回 200 + 零字节，所以不可用」—— 那只是因为没走
    **三步搜索守卫**（`search_guard=css/js/redeem` 三个 cookie），不是被 Cloudflare 挡。
    详见文件上方 `_BilinovelSearch` 的实测说明。

    作者/封面通常索引里就有；单本命中页没有作者字段，会补一次详情页。
    """

    name = "bilinovel"
    label = "哔哩轻小说"
    # Priority 5: ahead of WeRead on purpose. An exact title hit inside this
    # ~5.3k-entry *curated light-novel* catalog is a much higher-precision signal
    # than an exact hit on a platform that hosts millions of self-published
    # titles. Measured example: 《魔法禁书目录》 exists on WeRead as a fan work
    # (作者 新御坂美琴) while the official novel (镰池和马) is the catalog entry —
    # the catalog must win. Titles absent from the catalog simply fall through to
    # the other sources.
    priority = 5
    BASE = BILINOVEL_BASE
    index_ttl_days = 7.0
    _lock = threading.Lock()
    _entries: list | None = None
    _normalized: list | None = None

    # -- index -------------------------------------------------------------- #
    def load_index(self, *, refresh: bool = False, timeout: float = 20.0,
                   build_if_missing: bool = True) -> list:
        """★ `build_if_missing=False`：只读缓存，缺失就返回 []（不现爬 178 页）。
        给**交互式**调用方用（按书名查分卷），避免冷缓存时界面像卡死；建索引走 CLI 预热。"""
        with self._lock:
            if self._entries is not None and not refresh:
                return self._entries
            entries = get_index(refresh=refresh, max_age_days=self.index_ttl_days,
                                timeout=max(timeout, 20.0), progress=BILINOVEL_PROGRESS,
                                build_if_missing=build_if_missing)
            # ★ 故意跳过构建时不要把 [] 记进实例缓存（否则后续带 build 的调用也被空索引毒住）
            if entries or build_if_missing:
                self._entries = entries
                self._normalized = [(norm_title(e.title), e) for e in entries]
            return entries

    def refresh_index(self, *, timeout: float = 20.0) -> int:
        return len(self.load_index(refresh=True, timeout=timeout))

    @property
    def index_size(self) -> int:
        return len(self._entries or [])

    # -- lookup ------------------------------------------------------------- #
    def search(self, title: str, *, limit: int = 20, timeout: float = 12.0) -> list[Candidate]:
        # ★★★ 2026-09-30：**冷索引绝不现爬**（与 `linovelib.py` 的按书名路径、`meta_lookup.py`
        #   的口径统一）。原来这里 `load_index()` 默认会现建索引（178 页 / 3~4 分钟），
        #   所以 meta_lookup 只能整个跳过哔哩轻小说源 → 轻小说的**作者/封面**就查不到了
        #   （用户实测《弹珠汽水瓶里的千岁同学》：微信读书 0 条、番茄要过滑块、晋江只有同人，
        #    唯一有的就是哔哩轻小说，却被跳过 → 界面上"找不到作者"）。
        #   现在：没缓存就直接走②站内搜索（秒级）+③必应，不再卡在索引上。
        entries = self.load_index(timeout=timeout, build_if_missing=False)
        if not entries:
            try:
                via_site = self._search_site(title, timeout=max(timeout, 15.0))
                if via_site:
                    return via_site
            except Exception:  # noqa: BLE001 —— 站内搜索失败不该让整个源失败
                pass
            try:
                via_bing = self._search_via_bing(title, timeout=timeout)
                if via_bing:
                    return via_bing
            except Exception:  # noqa: BLE001
                pass
            raise SourceError(self.name, "目录索引未缓存且站内搜索/必应都没命中")

        scored: list[tuple[float, object]] = []
        pairs = self._normalized or [(norm_title(e.title), e) for e in entries]
        for _norm, entry in pairs:
            score = title_similarity(title, entry.title)
            if score >= 0.60:
                scored.append((score, entry))
        scored.sort(key=lambda item: -item[0])

        if not scored:
            # ② ★ 2026-09-30 新增：**真·站内搜索**（覆盖全站，不再只认这 5327 部目录）
            via_site = self._search_site(title, timeout=timeout)
            if via_site:
                return via_site
            # ③ 必应 site: 反查（译名容错的最后兜底，2026-09-29 加的）
            via_bing = self._search_via_bing(title, timeout=timeout)
            if via_bing:
                return via_bing
            raise SourceError(
                self.name,
                f"本地目录（{len(entries)} 部）无相似书名，站内搜索与必应 site: 反查也未命中"
            )

        out: list[Candidate] = []
        for pos, (_score, entry) in enumerate(scored[:limit]):
            author, cover = entry.author, entry.cover
            if (not author or not cover) and pos == 0:
                try:  # one extra request, only to fill a gap on the winner
                    detail = self.detail(entry.novel_id, timeout=timeout)
                    author = author or detail.author
                    cover = cover or detail.cover
                except SourceError:
                    pass
            out.append(
                Candidate(
                    title=entry.title,
                    author=author,
                    cover=cover,
                    source=self.name,
                    url=entry.url,
                    intro=entry.description,
                    position=pos,
                    extra={"novelId": entry.novel_id,
                           "catalogSize": len(entries),
                           "index": BILINOVEL_INDEX.path},
                )
            )
        return out

    # ---- 站内搜索（2026-09-30 新增） ------------------------------------- #
    _search_engine: "_BilinovelSearch | None" = None
    _search_lock = threading.Lock()

    def _search_site(self, title: str, *, timeout: float) -> list[Candidate]:
        """走站内搜索；失败/无结果返回空列表（由调用方继续下一层兜底）。

        · 关键词用 `search_title()`（只剥装饰：《书名（电视剧原著）》、卷次后缀），
          保留可读形式 —— 站内搜索框吃的就是这个。
        · **单本精确命中**（站点自己判定"就是这一本"）标 `matchedBy='site'`，
          由 `rank_candidates` 抬到 0.99：实测搜「败犬女主太多了」→ 站点直接给
          《败北女角太多了！》的单本页，且页面「别名」栏就是这个名字 ——
          与必应反查的"内容命中"同级证据，但**少两跳网络**（不用必应、不用详情页）。
        · **候选列表**照原样返回、正常打分 —— 只有书名真的像（≥0.85）才会被最终采纳。
        """
        keyword = search_title(title)
        if not keyword:
            return []

        # ⓪ 先看 7 天缓存里有没有这个书名对应的 novel_id（站内搜索与必应反查共用这张表）：
        #    第二次查同一本书只花**一次详情页**，不用再走握手。
        cached_id = BILINOVEL_ID_CACHE.get(title)
        if cached_id:
            try:
                full = self.detail(cached_id, timeout=max(timeout, 15.0), probe=title)
            except SourceError:
                full = None
            if full is not None:
                score = title_similarity(title, full.title)
                hit = int(full.extra.get("contentHit") or 0)
                if score >= 0.85 or hit > 0:      # 与必应反查同一套校验
                    full.extra["matchedBy"] = "title" if score >= 0.85 else "site"
                    full.extra["resolvedBy"] = "search-cache"
                    return [full]

        with self._search_lock:
            if self._search_engine is None:
                self._search_engine = _BilinovelSearch()
            engine = self._search_engine
            try:
                raw = engine.search(keyword, timeout=max(timeout, 15.0))
            except Exception:  # noqa: BLE001 - 站内搜索只是"多一层"，绝不能拖垮整个源
                return []
        out: list[Candidate] = []
        for cand in raw:
            if cand.extra.get("siteSingle"):
                score = title_similarity(title, cand.title)
                cand.extra["matchedBy"] = "title" if score >= 0.85 else "site"
                # ★ 单本命中页**没有作者字段**（实测：book-name / labels / nums / dec / 别名，
                #   就是没有作者），不补的话 rank_candidates 少 0.02 作者加成 ——
                #   实测「败犬女主太多了」会因此与番茄那本同名同人打成平手（1.03 vs 1.03），
                #   只能靠源优先级拆平。补一次详情页（顺带拿到更大的封面和简介）更稳。
                if not cand.author:
                    try:
                        full = self.detail(cand.extra["novelId"], timeout=max(timeout, 15.0))
                        cand.author = cand.author or full.author
                        cand.cover = full.cover or cand.cover
                        cand.intro = cand.intro or full.intro
                    except SourceError:
                        pass
                # 顺手把「书名 → 书号」写进 7 天缓存（与必应反查共用），下次直接命中
                BILINOVEL_ID_CACHE.put(title, cand.extra["novelId"])
            out.append(cand)
        return out

    # ---- 必应反查（译名容错，2026-09-29；★ 2026-09-30 起降为第 ③ 层兜底） ----- #
    # 当初加它的原因：那时以为站内搜索不可用（POST 返回 200 + 空 body），
    # 于是用必应 site: 反查 id，再用「书名相似度 ≥0.85 或 查询词真的出现在详情页原文里」校验
    # （实测《败犬女主太多了》→ 站内正名《败北女角太多了！》，相似度仅 0.34，
    # 但「败犬女主」在那一页里出现 4 次，足以证明是同一部作品的不同中文译名）。
    # ★ 2026-09-30：空 body 的真正原因查明 = **三步搜索守卫**（见文件上方 `_BilinovelSearch`），
    #   站内搜索已可用并升为第 ② 层；这条**保留**：万一守卫再变或被限流，
    #   它仍是最后一道能兜住"译名不同"的网。（两条路共用同一张 title→id 缓存。）
    BING = "https://cn.bing.com/search"
    _BING_ID_RE = re.compile(r"/novel/(\d+)\.html")

    @classmethod
    def _bing_url(cls, title: str, *, rss: bool = True) -> str:
        q = urllib.parse.quote(f"site:bilinovel.com {title}")
        url = f"{cls.BING}?q={q}&count=20"
        return url + "&format=rss" if rss else url

    def _resolve_novel_id_bing(self, title: str, *, timeout: float = 12.0) -> str:
        """书名 → 本站 novel_id（必应 site:，带 7 天缓存）。拿不到返回空串。"""
        cached = BILINOVEL_ID_CACHE.get(title)
        if cached:
            return cached
        headers = {"User-Agent": DESKTOP_UA, "Accept-Language": "zh-CN,zh;q=0.9"}
        for rss in (True, False):          # RSS 优先（约 5KB），失败再退 HTML
            try:
                resp = fetch(self._bing_url(title, rss=rss), headers=headers,
                             timeout=timeout, tries=1)
            except FetchError:
                continue
            if resp.status != 200:
                continue
            match = self._BING_ID_RE.search(resp.text)
            if match:
                BILINOVEL_ID_CACHE.put(title, match.group(1))
                return match.group(1)
        return ""

    def _search_via_bing(self, title: str, *, timeout: float = 12.0) -> list[Candidate]:
        """必应反查 id → 详情页 → 译名容错校验通过才采纳。"""
        novel_id = self._resolve_novel_id_bing(title, timeout=timeout)
        if not novel_id:
            return []
        try:
            cand = self.detail(novel_id, timeout=max(timeout, 15.0), probe=title)
        except SourceError:
            return []
        score = title_similarity(title, cand.title)
        content_hit = int(cand.extra.get("contentHit") or 0)
        if score < 0.85 and content_hit <= 0:
            return []                    # 既不像、页面里又没出现 → 宁可返回空
        # ★ 内容命中的权重定成 0.99（而不是 0.90），是为了让**官方版**赢过**同名同人**：
        #   实测《败犬女主太多了》在番茄上有一本**照抄官方书名**的同人（title_score 1.00），
        #   而哔哩轻小说的官方版正名是《败北女角太多了！》（内容命中，原只能给 0.90）。
        #   番外加分后：同人 1.00+0.04+0.02−0.01×3 = 1.03；
        #              官方版 0.90+0.06 = 0.96 → 同人胜 → **书选错**。
        #   改成 0.99 后：官方版 0.99+0.06 = 1.05 > 1.03 ✅，
        #   而"正名精确命中"仍是 1.00+0.06 = 1.06，层级依旧：正名 > 译名命中 > 同名同人。
        cand.title_score = score if score >= 0.85 else 0.99
        cand.position = 0
        cand.extra["resolvedBy"] = "bing"
        cand.extra["matchedBy"] = "title" if score >= 0.85 else "content"
        return [cand]

    def detail(self, novel_id: str, *, timeout: float = 15.0, probe: str = "") -> Candidate:
        """Parse a novel page's OpenGraph metadata (authoritative single-book data).

        `probe`（可选）：传查询词进来时，会在 `extra['contentHit']` 里给出它在整页
        原文里出现的次数 —— 供译名不同的场景当证据用（见 `_search_via_bing`）。
        """
        url = BILINOVEL_DETAIL.format(novel_id=novel_id)
        try:
            resp = fetch(url, headers={"Referer": self.BASE + "/", "User-Agent": DESKTOP_UA},
                         timeout=timeout, tries=3)
        except FetchError as exc:
            raise SourceError(self.name, str(exc)) from exc
        if _looks_blocked(resp) or resp.status != 200:
            raise SourceError(self.name, f"HTTP {resp.status}", blocked=_looks_blocked(resp))
        page = resp.text
        content_hit = _content_hit_count(page, probe) if probe else 0

        def meta(prop: str) -> str:
            m = re.search(
                rf'<meta[^>]+property="{re.escape(prop)}"[^>]+content="(?P<c>[^"]*)"', page, re.I)
            return html.unescape(m.group("c")).strip() if m else ""

        title = meta("og:novel:book_name")
        if not title:
            tm = re.search(r'<h1[^>]*class="[^"]*book-title[^"]*"[^>]*>(?P<t>.*?)</h1>', page, re.S)
            title = _strip_tags(tm.group("t")) if tm else ""
        author = meta("og:novel:author")
        if not author:
            am = re.search(r'<span[^>]*class="authorname"[^>]*>(?P<a>.*?)</span>', page, re.S)
            author = _strip_tags(am.group("a")) if am else ""
        cover = _abs_url(meta("og:image") or "")
        if not cover:
            cm = re.search(r'<img[^>]+class="book-cover"[^>]+src="(?P<c>[^"]+)"', page)
            cover = _abs_url(cm.group("c").split("?")[0]) if cm else ""
        return Candidate(
            title=title,
            author=author,
            cover=cover,
            source=self.name,
            url=url,
            intro=meta("og:description"),
            extra={"novelId": novel_id,
                   "category": meta("og:novel:category"),
                   "status": meta("og:novel:status"),
                   "contentHit": content_hit},
        )


# --------------------------------------------------------------------------- #
# International sources (kept for completeness; may be unreachable in CN)
# --------------------------------------------------------------------------- #
class WebnovelSource:
    """Webnovel.com (Yuewen's overseas platform). Requires unrestricted egress."""

    name = "webnovel"
    label = "Webnovel (海外)"
    priority = 60
    SEARCH = "https://www.webnovel.com/go/pcm/search/result"
    REFERER = "https://www.webnovel.com/"

    def search(self, title: str, *, limit: int = 20, timeout: float = 12.0) -> list[Candidate]:
        url = self.SEARCH + "?" + urllib.parse.urlencode({"keywords": title, "pageIndex": 1})
        try:
            resp = fetch(url, headers={"Referer": self.REFERER, "User-Agent": DESKTOP_UA},
                         timeout=timeout, tries=2)
        except FetchError as exc:
            raise SourceError(self.name, str(exc)) from exc
        try:
            data = json.loads(resp.text)
        except json.JSONDecodeError as exc:
            raise SourceError(self.name, f"响应不是 JSON: {exc}", blocked=_looks_blocked(resp)) from exc

        rows = _dig(data, "data", "bookInfo", default=None) or _dig(data, "data", "books", default=None) or []
        if isinstance(rows, dict):
            rows = [rows]
        out: list[Candidate] = []
        for row in rows:
            out.append(
                Candidate(
                    title=_strip_tags(str(row.get("bookName") or row.get("title") or "")),
                    author=_strip_tags(str(row.get("authorName") or row.get("author") or "")),
                    cover=_abs_url(row.get("coverUrl") or row.get("cover") or ""),
                    source=self.name,
                    url=_abs_url(row.get("url") or "", self.REFERER),
                    intro=_strip_tags(str(row.get("description") or "")),
                    position=len(out),
                )
            )
        out = [c for c in out if c.title]
        if not out:
            raise SourceError(self.name, "搜索结果为空")
        return out


class SyosetuSource:
    """なろう小説API (Japan). Free official API; unreachable from some networks."""

    name = "syosetu"
    label = "なろう小説API (日本)"
    priority = 61
    API = "https://api.syosetu.com/novelapi/api/"

    def search(self, title: str, *, limit: int = 20, timeout: float = 12.0) -> list[Candidate]:
        params = {
            "out": "json",
            "lim": str(max(1, min(limit, 20))),
            "wname": title,
            "of": "t-n-w-s-ga-gf",
        }
        url = self.API + "?" + urllib.parse.urlencode(params, encoding="utf-8")
        try:
            resp = fetch(url, headers={"User-Agent": DESKTOP_UA}, timeout=timeout, tries=2)
        except FetchError as exc:
            raise SourceError(self.name, str(exc)) from exc
        if _looks_blocked(resp) or resp.status != 200:
            raise SourceError(self.name, f"HTTP {resp.status}", blocked=_looks_blocked(resp))
        try:
            data = json.loads(resp.text)
        except json.JSONDecodeError as exc:
            raise SourceError(self.name, f"响应不是 JSON: {exc}") from exc
        rows = data[1:] if isinstance(data, list) and data else []
        out: list[Candidate] = []
        for row in rows:
            ncode = row.get("ncode") or ""
            out.append(
                Candidate(
                    title=str(row.get("title") or "").strip(),
                    author=str(row.get("writer") or "").strip(),
                    cover="",  # the API exposes no cover image
                    source=self.name,
                    url=f"https://ncode.syosetu.com/{ncode.lower()}/" if ncode else "",
                    intro=str(row.get("story") or "").strip(),
                    position=len(out),
                    extra={"ncode": ncode, "globalPoint": row.get("global_point")},
                )
            )
        out = [c for c in out if c.title]
        if not out:
            raise SourceError(self.name, "搜索结果为空")
        return out


# --------------------------------------------------------------------------- #
# ★★★ 2026-10-01：**榜单快照源**（起点 / 晋江 / 番茄）★★★
# --------------------------------------------------------------------------- #
# 用户原话：「还是找不到作者，把 novelmeta 的源再多加一个 QiDianRankTracker」
#           「番茄和晋江的推荐池来源是啥，一并加上吧」
#
# 起因：精选推荐里的《我的超能道具随机刷新》（起点·签约作者新书榜，作者「一片雪饼」），
# 在书籍信息页点「AI 搜索」查作者，**四个源全军覆没**：
#     微信读书 9 条不够像 / 晋江 20 条最像的只有 0.752 / 番茄被字节 Bdturing 滑块挡 / 哔哩没命中。
#
# 为什么加这三个能解决：**推荐板块（rank_pool.py）本来就有这三个源**，而且它们都是
# **公开的榜单快照 API**，不走各站那套被 WAF/滑块守着的搜索接口：
#   · 起点  https://siweimidu.github.io/QiDianRankTracker/          （MIT，Actions 每日抓）
#   · 晋江  https://sherrysouthxs.github.io/novel-tracker/data/jjwxc/latest.json
#   · 番茄  https://fanqienovel.com/api/author/library/book_list/v0/  ← **不是**搜索接口
#           （番茄的搜索接口要 a_bogus 签名 + Bdturing 滑块，见本文件头；这条书库列表不用）
#
# ⚠️ 定位要清楚：快照**只覆盖上榜的书**（每榜几十本，合计几百本），
#    所以它们是**补充源**，不是通用搜索源 —— 查上榜书/热门书命中率高，查冷门书照样查不到。
#    这也是为什么三条都带 TTL 缓存：冷缓存才去拉，之后 6 小时内直接读本地。
_SNAP_TTL_HOURS = 6.0
_SNAP_MIN_SCORE = 0.60          # 书名相似度门槛（快照小，宁可多给几条让上层打分）


def _snap_path(name: str) -> str:
    from .catalog import cache_dir
    return os.path.join(cache_dir(), f"src_{name}.json")


def _snap_load(name: str, ttl_hours: float = _SNAP_TTL_HOURS):
    """读快照缓存；超龄/损坏返回 None（调用方会去重新拉）。"""
    p = _snap_path(name)
    try:
        if time.time() - os.path.getmtime(p) > ttl_hours * 3600:
            return None
        with open(p, encoding="utf-8") as fh:
            rows = json.load(fh)
        return rows if isinstance(rows, list) and rows else None
    except (OSError, ValueError):
        return None


def _snap_save(name: str, rows: list) -> None:
    p = _snap_path(name)
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(rows, fh, ensure_ascii=False)
        os.replace(tmp, p)
    except OSError:
        pass


def _snap_pick(rows: list, title: str, source_name: str, limit: int) -> list:
    """在快照里按书名相似度挑候选（只有几百条，线性扫足够）。"""
    scored = []
    for r in rows:
        t = (r.get("title") or "").strip()
        if not t:
            continue
        s = title_similarity(title, t)
        if s >= _SNAP_MIN_SCORE:
            scored.append((s, r))
    scored.sort(key=lambda x: -x[0])
    out = []
    for i, (_s, r) in enumerate(scored[:max(1, limit)]):
        out.append(Candidate(
            title=r["title"], author=r.get("author") or "", cover=r.get("cover") or "",
            source=source_name, url=r.get("url") or "", intro=r.get("intro") or "",
            position=i, extra={"snapshot": True},
        ))
    return out


class QiDianTrackerSource:
    """起点榜快照（QiDianRankTracker，MIT）。≈ 4 个榜 × 30 本。"""

    name = "qidiantracker"
    label = "起点榜快照 (QiDianRankTracker)"
    priority = 30                    # 排在 weread(默认) 之后、overseas 之前
    BASE = "https://siweimidu.github.io/QiDianRankTracker/"
    BOARDS = ("yuepiao", "hotsales", "newsign", "signnewbook")

    def _rows(self, timeout: float) -> list:
        cached = _snap_load(self.name)
        if cached is not None:
            return cached
        rows, seen = [], set()
        for slug in self.BOARDS:
            url = (self.BASE + "api/" + slug + "/latest/"
                   + urllib.parse.quote("全部") + ".json")
            try:
                resp = fetch(url, headers={"User-Agent": DESKTOP_UA, "Referer": self.BASE},
                             timeout=timeout, tries=2)
                data = json.loads(resp.text)
            except Exception:  # noqa: BLE001 - 单个榜坏了不该拖垮整源
                continue
            for b in (data.get("books") or []):
                bid = str(b.get("bid") or "")
                if not bid or bid in seen:
                    continue
                seen.add(bid)
                rows.append({
                    "title": (b.get("title") or "").strip(),
                    "author": (b.get("author") or "").strip(),
                    "cover": (b.get("cover") or "").strip(),
                    "intro": (b.get("intro") or "").strip(),
                    "url": (b.get("bookUrl") or "").strip(),
                })
        if rows:
            _snap_save(self.name, rows)
        return rows

    def search(self, title: str, *, limit: int = 20, timeout: float = 12.0) -> list[Candidate]:
        rows = self._rows(timeout)
        if not rows:
            raise SourceError(self.name, "起点榜快照拉不到（GitHub Pages 不通或上游异常）")
        out = _snap_pick(rows, title, self.name, limit)
        if not out:
            raise SourceError(self.name, f"快照 {len(rows)} 本里没有相似书名")
        return out


class JjwxcTrackerSource:
    """晋江榜快照（novel-tracker）。★ 与 JjwxcSource 的区别：这条是**快照**，
    不走晋江站那套服务端搜索，所以更稳；但只覆盖上榜的书。"""

    name = "jjwxc_tracker"
    label = "晋江榜快照 (novel-tracker)"
    priority = 31
    URL = "https://sherrysouthxs.github.io/novel-tracker/data/jjwxc/latest.json"

    def _rows(self, timeout: float) -> list:
        cached = _snap_load(self.name)
        if cached is not None:
            return cached
        try:
            resp = fetch(self.URL,
                         headers={"User-Agent": DESKTOP_UA,
                                  "Referer": "https://sherrysouthxs.github.io/novel-tracker/"},
                         timeout=timeout, tries=2)
            data = json.loads(resp.text)
        except Exception as exc:  # noqa: BLE001
            raise SourceError(self.name, f"快照拉取失败: {type(exc).__name__}") from exc
        rows, seen = [], set()
        for b in (data.get("books") or []):
            bid = str(b.get("book_id") or "")
            if not bid or bid in seen:
                continue
            seen.add(bid)
            rows.append({
                "title": (b.get("book_name") or "").strip(),
                "author": (b.get("author") or "").strip(),
                "cover": (b.get("thumb_url") or "").strip(),
                "intro": (b.get("abstract") or "").strip(),
                "url": (b.get("book_url") or "").strip(),
            })
        if rows:
            _snap_save(self.name, rows)
        return rows

    def search(self, title: str, *, limit: int = 20, timeout: float = 12.0) -> list[Candidate]:
        rows = self._rows(timeout)
        if not rows:
            raise SourceError(self.name, "晋江榜快照为空")
        out = _snap_pick(rows, title, self.name, limit)
        if not out:
            raise SourceError(self.name, f"快照 {len(rows)} 本里没有相似书名")
        return out


class FanqieLibrarySource:
    """番茄**书库列表**快照（`api/author/library/book_list`）。

    ★ 与 FanqieSource 的区别很关键：FanqieSource 走的是**搜索**接口，
      那条被字节 Bdturing 滑块挡死（HTTP 200 但 body 为 0）；这一条是**书库列表**，
      推荐板块（rank_pool.py）一直用得好好的。
    ⚠️ 覆盖更小（每页 20 本，这里男女频各拉几页），只当补充。
    ⚠️ 番茄的 `author` 字段常混入 **PUA 私用区字符**（字体混淆），要剔掉 ——
       否则作者名会缺字（rank_pool.py 里已经踩过这个坑）。
    """

    name = "fanqie_library"
    label = "番茄书库快照 (author/library)"
    priority = 32
    PAGES = 3
    PUA_RE = re.compile(r"[\ue000-\uf8ff]")

    def _rows(self, timeout: float) -> list:
        cached = _snap_load(self.name)
        if cached is not None:
            return cached
        rows, seen = [], set()
        jobs = [(g, p) for g in (0, 1) for p in range(self.PAGES)]
        for gender, page in jobs:
            url = ("https://fanqienovel.com/api/author/library/book_list/v0/?"
                   f"page_count=20&page_index={page}&gender={gender}&category_id=-1"
                   "&creation_status=-1&word_count=-1&book_type=-1&sort=0")
            try:
                resp = fetch(url, headers={"User-Agent": DESKTOP_UA,
                                           "Accept": "application/json",
                                           "Referer": "https://fanqienovel.com/"},
                             timeout=timeout, tries=2)
                data = json.loads(resp.text)
            except Exception:  # noqa: BLE001 - 单页失败跳过
                continue
            for b in ((data.get("data") or {}).get("book_list") or []):
                bid = str(b.get("book_id") or "")
                if not bid or bid in seen:
                    continue
                seen.add(bid)
                rows.append({
                    "title": (b.get("book_name") or "").strip(),
                    "author": self.PUA_RE.sub("", (b.get("author") or "")).strip(),
                    "cover": (b.get("thumb_url") or b.get("thumb_uri") or "").strip(),
                    "intro": (b.get("abstract") or "").strip(),
                    "url": f"https://fanqienovel.com/page/{bid}",
                })
        if rows:
            _snap_save(self.name, rows)
        return rows

    def search(self, title: str, *, limit: int = 20, timeout: float = 12.0) -> list[Candidate]:
        rows = self._rows(timeout)
        if not rows:
            raise SourceError(self.name, "番茄书库列表拉不到")
        out = _snap_pick(rows, title, self.name, limit)
        if not out:
            raise SourceError(self.name, f"快照 {len(rows)} 本里没有相似书名")
        return out


# --------------------------------------------------------------------------- #
# Registry + concurrent fan-out
# --------------------------------------------------------------------------- #
REGISTRY: dict[str, object] = {}


def _register(source) -> None:
    REGISTRY[source.name] = source


for _src in (WeReadSource(), BilinovelSource(), JjwxcSource(), FanqieSource(),
             QiDianTrackerSource(), JjwxcTrackerSource(), FanqieLibrarySource(),
             WebnovelSource(), SyosetuSource()):
    _register(_src)

# Sources marked `default = False` are only queried when explicitly requested.
# The overseas endpoints are unreachable from mainland networks and each one
# costs a full timeout (~17 s), which would dominate the latency of every run.
_DEFAULT_OFF = {"webnovel", "syosetu"}


def source_names(include_overseas: bool = False) -> list[str]:
    """Source names in priority order; overseas sources are opt-in."""
    names = sorted(REGISTRY, key=lambda n: REGISTRY[n].priority)
    if not include_overseas:
        names = [n for n in names if n not in _DEFAULT_OFF]
    return names


def get_source(name: str):
    key = (name or "").strip().lower()
    if key not in REGISTRY:
        raise KeyError(f"未知数据源 {name!r}，可用: {', '.join(source_names())}")
    return REGISTRY[key]


def search_sources(
    title: str,
    *,
    sources: Sequence[str] | None = None,
    workers: int = 6,
    timeout: float = 12.0,
    on_result: Callable[[str, list[Candidate], Diagnostic], None] | None = None,
) -> tuple[list[Candidate], list[Diagnostic]]:
    """Query every selected source in parallel; never raises for source errors."""
    names = list(sources) if sources else source_names()
    selected = [(n, get_source(n)) for n in names]

    def run(item):
        name, src = item
        started = time.perf_counter()
        try:
            found = src.search(title, timeout=timeout)
            diag = Diagnostic(source=src.label, ok=True, count=len(found),
                              ms=int((time.perf_counter() - started) * 1000))
            return found, diag
        except SourceError as exc:
            diag = Diagnostic(source=src.label, ok=False, error=str(exc), blocked=exc.blocked,
                              ms=int((time.perf_counter() - started) * 1000))
            return [], diag
        except (socket.timeout, TimeoutError) as exc:
            diag = Diagnostic(source=src.label, ok=False, error=f"超时: {exc}",
                              ms=int((time.perf_counter() - started) * 1000))
            return [], diag
        except Exception as exc:  # noqa: BLE001 - one bad source must not kill the run
            diag = Diagnostic(source=src.label, ok=False,
                              error=f"{type(exc).__name__}: {exc}",
                              ms=int((time.perf_counter() - started) * 1000))
            return [], diag

    candidates: list[Candidate] = []
    diagnostics: list[Diagnostic] = []
    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(selected) or 1))) as pool:
        for (name, _src), (found, diag) in zip(selected, pool.map(run, selected)):
            candidates.extend(found)
            diagnostics.append(diag)
            if on_result:
                on_result(diag.source, found, diag)
    diagnostics.sort(key=lambda d: (not d.ok, d.ms))
    return candidates, diagnostics
