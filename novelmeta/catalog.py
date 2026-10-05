# -*- coding: utf-8 -*-
"""A small on-disk catalog index for sites whose search endpoint is unusable.

Why this exists
---------------
哔哩轻小说 (bilinovel.com) blocks its search endpoint: `POST /search.html`
with `searchkey=<kw>` answers `200` with a **zero-byte body**, with or without
cookies, from every network we tried. Its public 文库 listing, however, is plain
server-rendered HTML — `/wenku/lastupdate_0_0_0_0_0_0_0_<page>_0.html`, 30 books
per page, 178 pages — and each row already carries the title, the author and the
cover image. `robots.txt` allows `/wenku/`.

So instead of searching we mirror that listing into a local index once, cache it
on disk, and answer title lookups from the cache. Measured: 178 pages, ~5.3k
books, one-time ≈15-25 s, afterwards every lookup is instant and offline.

The crawl is deliberately polite: a handful of workers, a small delay, and a
cache TTL so it does not re-crawl on every run.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from typing import Callable, Iterable, Sequence

from .model import norm_title
from .net import FetchError, fetch

__all__ = [
    "CatalogEntry",
    "CatalogCache",
    "cache_dir",
    "BILINOVEL_INDEX",
    "FANQIE_ID_CACHE",
    "BILINOVEL_ID_CACHE",
    "build_bilinovel_index",
]

BILINOVEL_BASE = "https://www.bilinovel.com"
BILINOVEL_LIST = BILINOVEL_BASE + "/wenku/lastupdate_0_0_0_0_0_0_0_{page}_0.html"
BILINOVEL_DETAIL = BILINOVEL_BASE + "/novel/{novel_id}.html"

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

_ITEM_RE = re.compile(r'<li class="book-li">(?P<body>.*?)</li>', re.S)
_ID_RE = re.compile(r'href="/novel/(?P<id>\d+)\.html"')
_TITLE_RE = re.compile(r'<h4 class="book-title">(?P<t>.*?)</h4>', re.S)
_AUTHOR_RE = re.compile(r'class="book-author">(?P<a>.*?)</span>', re.S)
_AUTHOR_TEXT_RE = re.compile(r"</svg>(?P<a>.*)$", re.S)
_COVER_RE = re.compile(r'data-src="(?P<c>[^"]+)"')
_LAST_PAGE_RE = re.compile(r'class="last">(?P<n>\d+)<')
_PAGE_LINK_RE = re.compile(r"lastupdate_0_0_0_0_0_0_0_(?P<n>\d+)_0\.html")


def cache_dir() -> str:
    """Per-user cache directory (no third-party dependency)."""
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"),
                                                             "AppData", "Local")
    else:
        base = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    return os.path.join(base, "novelmeta")


def _strip_tags(text: str) -> str:
    import html as _html

    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", _html.unescape(text)).strip()


@dataclass
class CatalogEntry:
    novel_id: str
    title: str
    author: str = ""
    cover: str = ""
    description: str = ""

    @property
    def url(self) -> str:
        return BILINOVEL_DETAIL.format(novel_id=self.novel_id)

    @property
    def norm(self) -> str:
        return norm_title(self.title)


class CatalogCache:
    """JSON-backed list of entries with a TTL."""

    def __init__(self, name: str, *, path: str | None = None, ttl_days: float = 7.0):
        self.name = name
        self.path = path or os.path.join(cache_dir(), f"{name}.json")
        self.ttl_seconds = ttl_days * 86400

    def load(self, *, max_age: float | None = None) -> list[CatalogEntry] | None:
        age_limit = self.ttl_seconds if max_age is None else max_age
        try:
            with open(self.path, encoding="utf-8") as fh:
                payload = json.load(fh)
        except (OSError, json.JSONDecodeError):
            return None
        if age_limit >= 0 and time.time() - float(payload.get("built_at", 0)) > age_limit:
            return None
        entries = []
        for row in payload.get("entries") or []:
            if isinstance(row, dict) and row.get("title"):
                entries.append(CatalogEntry(
                    novel_id=str(row.get("novel_id") or ""),
                    title=row.get("title") or "",
                    author=row.get("author") or "",
                    cover=row.get("cover") or "",
                ))
        return entries or None

    def save(self, entries: Sequence[CatalogEntry], *, meta: dict | None = None) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        payload = {
            "built_at": time.time(),
            "entries": [asdict(e) for e in entries],
        }
        if meta:
            payload.update(meta)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False)
        os.replace(tmp, self.path)

    def age(self) -> float | None:
        try:
            with open(self.path, encoding="utf-8") as fh:
                return time.time() - float(json.load(fh).get("built_at", 0))
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            return None


BILINOVEL_INDEX = CatalogCache("bilinovel_index")


class FanqieIdCache:
    """书名 → 番茄 book_id 的小字典缓存。

    为什么需要：番茄官方搜索接口被字节 Bdturing 滑块拦住（200 + 空 body），
    备用方案是走必应 `site:fanqienovel.com` 拿 id —— 而必应对自动化查询有速率限制，
    所以同一个书名 7 天内只查一次。结构：{归一化书名: {"id": ..., "ts": ...}}，
    写盘用 .tmp + os.replace（与 CatalogCache 一致，避免半个文件）。
    """

    def __init__(self, *, path: str | None = None, ttl_days: float = 7.0):
        self.path = path or os.path.join(cache_dir(), "fanqie_ids.json")
        self.ttl_seconds = ttl_days * 86400

    def _load(self) -> dict:
        try:
            with open(self.path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    def get(self, title: str) -> str:
        row = self._load().get(norm_title(title))
        if not isinstance(row, dict):
            return ""
        if time.time() - float(row.get("ts") or 0) > self.ttl_seconds:
            return ""
        return str(row.get("id") or "")

    def put(self, title: str, book_id: str) -> None:
        data = self._load()
        data[norm_title(title)] = {"id": str(book_id), "ts": time.time()}
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False)
            os.replace(tmp, self.path)
        except OSError:
            pass


FANQIE_ID_CACHE = FanqieIdCache()
# 哔哩轻小说也要一份：本站正名可能是**另一个中文译名**
# （实测：用户搜《败犬女主太多了》，站内正名是《败北女角太多了！》，书名相似度只有 0.34），
# 所以用必应 site: 反查 id，再把"查询词是否出现在详情页原文里"当证据。
BILINOVEL_ID_CACHE = FanqieIdCache(path=os.path.join(cache_dir(), "bilinovel_ids.json"))


def parse_listing_page(page_html: str) -> list[CatalogEntry]:
    """Extract every book row from one 文库 listing page."""
    out: list[CatalogEntry] = []
    for match in _ITEM_RE.finditer(page_html):
        body = match.group("body")
        id_match = _ID_RE.search(body)
        title_match = _TITLE_RE.search(body)
        if not (id_match and title_match):
            continue
        title = _strip_tags(title_match.group("t"))
        if not title:
            continue
        author = ""
        author_match = _AUTHOR_RE.search(body)
        if author_match:
            raw = author_match.group("a")
            inner = _AUTHOR_TEXT_RE.search(raw)
            author = _strip_tags(inner.group("a") if inner else raw)
            author = re.sub(r"^\s*作者\s*[:：]?\s*", "", author).strip()
        cover = ""
        cover_match = _COVER_RE.search(body)
        if cover_match:
            cover = cover_match.group("c").split("?")[0].strip()
        desc = ""
        desc_match = re.search(r'<p class="book-desc">(?P<d>.*?)</p>', body, re.S)
        if desc_match:
            desc = _strip_tags(desc_match.group("d"))
        out.append(CatalogEntry(novel_id=id_match.group("id"), title=title,
                                author=author, cover=cover, description=desc))
    return out


def last_page_number(page_html: str) -> int:
    """How many listing pages exist (from the pagination widget)."""
    match = _LAST_PAGE_RE.search(page_html)
    if match:
        return int(match.group("n"))
    pages = [int(n) for n in _PAGE_LINK_RE.findall(page_html)]
    return max(pages) if pages else 1


class _Throttle:
    """Spacing + AIMD backoff shared by all crawl workers.

    Cloudflare fronts this site and answers `429` as soon as requests come too
    fast — measured 9 of 30 pages rejected at 5 workers / 0.15 s spacing. Worse,
    the rejection is an ordinary `429` page that would parse to "zero books" and
    silently shrink the index. So: reserve a global slot per request, slow down
    hard on 429, and creep back up after successes.
    """

    def __init__(self, delay: float = 0.3, *, floor: float = 0.25, ceiling: float = 2.5):
        self.delay = max(floor, delay)
        self.floor = floor
        self.ceiling = ceiling
        self._lock = threading.Lock()
        self._next_at = 0.0

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            sleep_for = max(0.0, self._next_at - now)
            self._next_at = max(now, self._next_at) + self.delay
        if sleep_for:
            time.sleep(sleep_for)

    def penalise(self, factor: float = 1.5) -> None:
        with self._lock:
            self.delay = min(self.ceiling, self.delay * factor)

    def reward(self) -> None:
        # Recover quickly: the site's limit is bursty, so a long memory of one 429
        # wastes minutes over a 178-page crawl.
        with self._lock:
            self.delay = max(self.floor, self.delay * 0.92)


# Retryable statuses: Cloudflare rate limiting and transient edge errors.
_BUSY_STATUS = {429, 403, 502, 503, 520, 521, 522, 524}


def _fetch_listing(page: int, *, timeout: float, referer: str, throttle: _Throttle,
                   attempts: int = 5) -> tuple[int, str | None, str]:
    """Fetch one listing page under the shared throttle -> (page, html|None, note)."""
    url = BILINOVEL_LIST.format(page=page)
    note = ""
    for attempt in range(attempts):
        throttle.wait()
        try:
            # tries=1: retry policy lives here so we can back off globally.
            resp = fetch(url, headers={"Referer": referer, "User-Agent": _UA},
                         timeout=timeout, tries=1)
        except FetchError:
            note = "network"
            throttle.penalise(1.3)
            time.sleep(min(8.0, 1.0 * (attempt + 1)))
            continue
        if resp.status in _BUSY_STATUS:
            note = f"HTTP {resp.status}"
            throttle.penalise()
            time.sleep(min(10.0, 1.5 * (attempt + 1)))
            continue
        if resp.status != 200:
            return page, None, f"HTTP {resp.status}"
        throttle.reward()
        return page, resp.text, ""
    return page, None, note or "unknown"


def build_bilinovel_index(
    *,
    workers: int = 4,
    timeout: float = 20.0,
    max_pages: int | None = None,
    delay: float = 0.25,
    progress: Callable[[str], None] | None = None,
) -> list[CatalogEntry]:
    """Crawl the whole 文库 listing into a list of entries.

    Concurrency is deliberately modest: this is a one-off cached crawl and the
    site rate-limits aggressively (see `_Throttle`). Pages that stay rate-limited
    are retried in a second, slower pass before the result is accepted.
    """
    say = progress or (lambda _msg: None)
    base = BILINOVEL_BASE + "/"
    throttle = _Throttle(delay)

    # First page also tells us how many pages there are.
    _page, first_html, note = _fetch_listing(1, timeout=timeout, referer=base, throttle=throttle)
    if not first_html:
        raise FetchError(BILINOVEL_LIST.format(page=1), [f"page 1 failed: {note}"])
    entries = parse_listing_page(first_html)
    total_pages = last_page_number(first_html)
    if max_pages:
        total_pages = min(total_pages, max_pages)
    say(f"目录共 {total_pages} 页，已取第 1 页（{len(entries)} 部）")

    pages = list(range(2, total_pages + 1))
    failed: list[tuple[int, str]] = []
    done = 1
    if pages:
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            futures = [pool.submit(_fetch_listing, page, timeout=timeout, referer=base,
                                   throttle=throttle) for page in pages]
            for future in futures:
                page, html, note = future.result()
                if html:
                    entries.extend(parse_listing_page(html))
                else:
                    failed.append((page, note))
                done += 1
                if progress and done % 20 == 0:
                    say(f"  已抓取 {done}/{total_pages} 页"
                        f"（失败 {len(failed)}，当前间隔 {throttle.delay:.2f}s）")

    if failed:
        say(f"{len(failed)} 页被限流，冷却 5s 后重试…")
        time.sleep(5.0)
        still: list[tuple[int, str]] = []
        for page, _note in failed:
            _p, html, note = _fetch_listing(page, timeout=timeout, referer=base,
                                            throttle=throttle, attempts=6)
            if html:
                entries.extend(parse_listing_page(html))
            else:
                still.append((page, note))
        if still:
            say(f"仍有 {len(still)} 页未取到：{still[:5]}")
        failed = still

    # De-duplicate by novel id, keeping entries that carry a cover.
    best: dict[str, CatalogEntry] = {}
    for entry in entries:
        if not entry.novel_id:
            continue
        current = best.get(entry.novel_id)
        if current is None or (not current.cover and entry.cover):
            best[entry.novel_id] = entry
    say(f"抓取结束：{len(best)} 部作品，未取到的页 {len(failed)}")
    return list(best.values())


def get_index(
    *,
    refresh: bool = False,
    max_age_days: float = 7.0,
    workers: int = 3,
    timeout: float = 20.0,
    progress: Callable[[str], None] | None = None,
    build_if_missing: bool = True,
) -> list[CatalogEntry]:
    """Load the cached index, rebuilding it when missing, stale or forced.

    ★ 2026-09-30：`build_if_missing=False` = **只吃缓存，绝不现爬**；缓存缺失/过期返回 []。
      为什么需要：按书名查分卷是**交互式**操作，冷缓存下现爬 178 页（受站点限流要数分钟）
      会让界面看起来像卡死（用户实测：三次查询都停在同一行、日志再无输出）。
      这时应该让调用方直接走②站内搜索（秒级）；「建索引」留给 CLI 预热
      （`meta_lookup.py --allow-index-build`）、或缓存已经存在时的零网络快路。
    """
    say = progress or (lambda _msg: None)
    if not refresh:
        cached = BILINOVEL_INDEX.load(max_age=(-1 if refresh else max_age_days * 86400))
        if cached:
            return cached
    if not build_if_missing and not refresh:
        say("本地目录索引未缓存 → 跳过现爬，直接走站内搜索")
        return []
    say("首次使用需构建哔哩轻小说目录索引（约 5300 部作品 / 178 页，"
        "受站点限流影响需数分钟），仅首次需要，之后缓存 7 天…")
    started = time.perf_counter()
    entries = build_bilinovel_index(workers=workers, timeout=timeout, progress=say)
    if entries:
        BILINOVEL_INDEX.save(entries, meta={"source": BILINOVEL_BASE})
        say(f"索引已缓存 {len(entries)} 部作品，用时 {time.perf_counter() - started:.1f}s "
            f"-> {BILINOVEL_INDEX.path}")
    return entries
