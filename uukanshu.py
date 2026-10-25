#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""uukanshu.py —— UU看書（uukanshu.cc，**繁體**站）站专用爬虫源：站内搜索 → 目录 → 正文（TXT）。

★ 为什么这个源必须走**浏览器**（2026-10-09/10 实测，别再来回试纯 HTTP）：
    · python（novelmeta.fetch：裸头 / 完整浏览器头 / 带 Referer / 带站点 cookie）→ **全部 HTTP 403**
      （CF 拦截页 4833B，Server: cloudflare，CF-RAY 落 FRA 节点）
    · `curl.exe`（浏览器 UA + 站点自己的 TREK_SESSION）→ **403**
    · headless Edge（默认 UA，UA 里带 `HeadlessChrome`）→ **403**
    · **headless Edge + `--user-agent=<正常 Edge UA>`** → ✅ 通过（19452 字符真站页面）
    · 真窗口 Edge → ✅ 也通过
  ⇒ 卡的是 **TLS/无头指纹**，不是 IP（同一 IP 的浏览器能过）。
  ⇒ 所以本模块用 Selenium + Edge(headless) 取页面，并且**必须覆盖 UA**（见 `_UA`）。
    开窗口也能过，但长跑要人盯着；默认无窗口。

★ 站点结构（实测，都是原始响应）：
  · 搜索 `GET /search?searchkey=<书名>`：
      - **精确命中时直接 302 到书页** `https://uukanshu.cc/book/<id>/`（没有结果列表）★
      - 否则跳到 `/search/<书名>/1.html`，结果块 `<div class="bookbox">`；
        书名在 `<div class="bookname"><a href="https://uukanshu.cc/book/N/">`（**绝对链接**）；
        页面自报结果数：`共有<b class="hottext"> N </b>條結果`
  · 书页 `/book/<id>/`：目录在 **`<dl class="book chapterlist">`** 的 `<dd><a href="…">名称</a>`
      （实测《贅婿》1307 章、《冬日重現》505 章，**一页出全**，无分页）
      ⚠️ 页首另有一块"最新章節"，所以**只取 dl 内部**再按 URL 去重
  · 正文页 `/book/<id>/<chapid>.html`：正文容器 **`div.readcotent`**（站方把 content 拼错了）
      ⚠️ **取值方式差 2.6 倍**（实测 12 章）：`d.page_source` 整页 DOM 序列化 0.29s/章；
        `execute_script → div.readcotent.innerText` **0.11s/章** → 本模块用后者
      ⚠️ `d.get()` 之后**必须等元素出现再取**：全本实测 505 章里有 **3 章**出现过"空载 0 字"，
        单独重试即成功（6591/5753/5530 字）→ 现在实现成「等元素 → 失败就**重新导航**重试」

★ 站方插的广告（实测：全本 502 章里 **7 处**；比便宜站少两个数量级，但不是 0）：
    · `正在閱讀：第310章 「陷阱」（下），最新章節盡在。`   ← **会插在段落中间**
    · `冬日重現來自「人人書庫」免費看書APP，百度搜索「人人書庫」下載安裝安卓APP…`
    · `《冬日重現》最新章節已發布！`
    · `下一章更精彩：第330章 …，期待您的光臨。`
  ★ 一个好认的特征：**广告句是简体**，而正文是繁體（这站是繁體站）。
  处理原则：**只删广告行/广告句，绝不改写正文**；删了什么**逐条记进日志**
  （默认 `%TEMP%\\uukanshu_removed_ads.log`，可用 `UUKANSHU_AD_LOG` 改），
  而且可以整体关掉：`UUKANSHU_KEEP_ADS=1`。
  （本机没有 opencc/zhconv，暂不做简繁转换；正文按原样繁體。）

★ 会话池：默认 **3 个 headless Edge**（`UUKANSHU_SESSIONS` 可调）。
  实测 3 并发 30 章：0 失败、0 被拦；整本 505 章 502 成功。
  被 CF 拦（识别到拦截页）时会**重开该会话**再试。

命令（独立调试；App 里由 novel_dl.py 按 `kind="crawler"` 调 search/toc/chapter）：
  py uukanshu.py --search 冬日重现
  py uukanshu.py --toc http://uukanshu.cc/book/26187/
  py uukanshu.py --chapter http://uukanshu.cc/book/26187/16983613.html
  py uukanshu.py --get 冬日重现 --limit 5 --out C:\\work\\download
"""

from __future__ import annotations

import argparse
import atexit
import json
import os
import re
import sys
import tempfile
import threading
import time
import urllib.parse

NAME = "UU看書"
BASE = (os.environ.get("UUKANSHU_BASE") or "https://uukanshu.cc").rstrip("/")
#: ★ 必须是真的 Edge/Chrome UA —— 默认无头 UA 里带 HeadlessChrome，会被 CF 403
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/154.0.0.0 Safari/537.36 Edg/154.0.0.0")
#: 会话数：★★ 2026-10-10 实测教训 —— 3 并发 × 505 章 / 8 分钟，把这个 IP 送进了
#:   CF 硬封（"Sorry, you have been blocked"，连有窗口 Edge 都进不去、25 秒不解）。
#:   所以默认降到 **2**，并且下面加了**全局最小请求间隔**（默认 0.8s）。
_SESSIONS = max(1, int(os.environ.get("UUKANSHU_SESSIONS") or 2))
#: 全局最小请求间隔（秒/请求，所有会话共用；0 = 不限）
_MIN_GAP = float(os.environ.get("UUKANSHU_GAP") or 0.8)
_HEADLESS = os.environ.get("UUKANSHU_HEADLESS", "1") != "0"
#: 繁體 → 简体（默认开）。关掉：UUKANSHU_SIMP=0 → 正文保持繁體原样。
#: ★ 用**随包内置**的 OpenCC（Apache-2.0，68KB，见同目录 `opencc/`），不依赖 pip。
#:   配置实测对比（2.19M 字真实语料，探针字命中残留）：
#:     t2s / hk2s → 13,742 处（5.55%，`著急` 都不会转）；**tw2s → 168 处（0.07%）** ✓
#:   刁难样例：`著急→着急`、`幹活→干活`、`乾脆→干脆`、`一隻→一只`、`麵館→面馆`、`藉故→借故`、
#:             `乾坤`/`著作` 不被误改 ✓  速度 4s/219万字（≈0.1ms/章）
_SIMP = os.environ.get("UUKANSHU_SIMP", "1") != "0"
_KEEP_ADS = os.environ.get("UUKANSHU_KEEP_ADS") == "1"
_AD_LOG = os.environ.get("UUKANSHU_AD_LOG") or os.path.join(
    tempfile.gettempdir(), "uukanshu_removed_ads.log")

#: 连续被拦多少次就判定"本机被封"（够判一次偶发，不会因为一次抽风就整体放弃）
_BLOCK_LIMIT = int(os.environ.get("UUKANSHU_BLOCK_LIMIT") or 3)


class SiteBlocked(RuntimeError):
    """站点把本机判定为机器人/封锁了 —— 调用方看到这个应**停止抓取**。

    ★ 为什么专门定义：CF 的硬封（错误页 "Sorry, you have been blocked"）不会自己好，
      继续硬抓只会让封锁更久。novel_dl 收到这个异常会**整体停下**并留下缓存续传点。
    """
    site_blocked = True


def _throttle() -> None:
    """全局最小请求间隔（所有会话共用）—— 实测教训：爬太快会被 CF 硬封。"""
    global _next_at
    if _MIN_GAP <= 0:
        return
    with _gap_lock:
        now = time.monotonic()
        wait = max(0.0, _next_at - now)
        _next_at = max(now, _next_at) + _MIN_GAP
    if wait > 0:
        time.sleep(wait)


#: 正文容器（站方拼写就是 readcotent）
CONTENT_SEL = "div.readcotent"
#: 目录选择器：★ 2026-10-10 实测 —— 站方的 class 写法会变（见过 `class="book chapterlist"`，
#:   也见过别的位置），所以这里写成"多选择器 + 宽容匹配"，别死抠某一个字符串。
TOC_SEL = "dl.chapterlist dd a, dl.book.chapterlist dd a, #list-chapterAll dd a"
#: 判断"这是书页而不是搜索结果页"：用 JS 直接问 DOM（比字符串匹配稳）
_BOOK_JS = ("return !!(document.querySelector('dl.chapterlist') "
            "|| document.querySelector('#list-chapterAll'));")
_BOOK_HINTS = ("全部章節", "全部章节", "chapterlist")
#: CF / 拦截页指纹
_BLOCK_MARKS = ("attention required", "just a moment", "cf-chl", "challenge-platform",
                "請開啟javascript", "访问过于频繁")

#: 站方广告（逐条实测得来；**句式会变**，所以按"特征词 + 促销尾巴"写宽一点）。
#: 删的是**广告句本身**，正文一个字不动；删了什么会进审计日志。
_AD_PATTERNS = [
    # 「正在閱讀第329章 …，沉浸其中無法自拔。」/「正在閱讀：第310章 …，最新章節盡在。」
    #   ★ 必须要求"后面跟着章节引用或促销尾巴"，否则会误伤正文里正常出现的"正在閱讀"四个字
    #     （离线样例实测：`「正在閱讀」這四個字，是他最討厭的詞。` 曾被整句删掉 → 见 filter_check）。
    r"正在閱讀(?:第[^。\n]{0,50}章|[：:]\s*第?[^。\n]{0,50}章)[^。\n]{0,50}[。！!]?",
    r"正在閱讀[^。\n]{0,60}(?:沉浸其中無法自拔|最新章節盡在)[^。\n]{0,24}[。！!]?",
    # 「…，最新章節《》已更新，速來追更！」/「最新章節已就位！書迷速歸。」
    r"最新章節[^。\n]{0,40}[。！!]?",
    r"[^\n。！!]{0,16}(?:速來追更|書迷速歸|沉浸其中無法自拔|最新章節隨便看|最新章節盡在)[^。\n]{0,24}[。！!]?",
    r"《?[^》\n]{0,20}》?最新章節已發布！?",
    r"下一章更精彩[:：][^。\n]{0,90}期待您的光臨。?",
    # 换源/拉 APP 的整句
    r"[^\n]{0,24}來自「[^」\n]{1,12}」[^\n]{0,80}(?:APP|app)[^\n]{0,60}[！!]?",
    r"百度搜索「[^」\n]{1,12}」[^\n]{0,60}",
    r"[^\n]{0,40}uukanshu\.cc[^\n]{0,40}",
    r"[^\n]{0,30}UU看書[^\n]{0,10}(?:免費|免费)[^\n]{0,30}",
]

_stats_lock = threading.Lock()
_stats = {"chapters": 0, "ads": 0, "retries": 0, "blocks": 0, "sessions": 0}


def log(msg: str) -> None:
    print(msg, flush=True)


def _stat(key: str, n: int = 1) -> None:
    with _stats_lock:
        _stats[key] = _stats.get(key, 0) + n


def stats() -> dict:
    with _stats_lock:
        return dict(_stats)


# ══════════════════════════════════════════════════════════════════════════
# 浏览器会话池（默认 2 个 headless Edge 轮转；线程安全）+ 全局节流 + 断路
# ══════════════════════════════════════════════════════════════════════════
_pool_lock = threading.Lock()
_idle: list = []
_sem = threading.Semaphore(_SESSIONS)
_alive: list = []
#: 全局限速状态（所有会话共用）
_gap_lock = threading.Lock()
_next_at = 0.0
#: 连续被拦计数 → 到 _BLOCK_LIMIT 就抛 SiteBlocked（让调用方整体停手）
_blocks_lock = threading.Lock()
_consecutive_blocks = 0
_blocked_hard = False


def _note_block() -> None:
    """记一次"被 CF 拦"。连续够次数 → 判定本机被封，抛 SiteBlocked。"""
    global _consecutive_blocks, _blocked_hard
    with _blocks_lock:
        _consecutive_blocks += 1
        n = _consecutive_blocks
        if n >= _BLOCK_LIMIT:
            _blocked_hard = True
    if _blocked_hard:
        raise SiteBlocked(
            f"{NAME} 已连续 {n} 次返回 Cloudflare 封锁页 → 判定本机被封，"
            f"停止抓取（继续只会加重封锁）。")
    log(f"  🛡️ [{NAME}] 命中 CF 拦截页（第 {n}/{_BLOCK_LIMIT} 次）")


def _note_ok() -> None:
    global _consecutive_blocks
    with _blocks_lock:
        _consecutive_blocks = 0


def is_blocked_hard() -> bool:
    return _blocked_hard


def _new_driver():
    try:
        from selenium import webdriver
        from selenium.webdriver.edge.options import Options
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"需要 selenium 才能爬这个站：{exc}") from exc
    opt = Options()
    if _HEADLESS:
        opt.add_argument("--headless=new")
    opt.add_argument(f"--user-agent={_UA}")          # ★ 不覆盖 UA = 必被 CF 403
    opt.add_argument("--window-size=1400,1000")
    opt.add_argument("--disable-blink-features=AutomationControlled")
    opt.add_argument("--blink-settings=imagesEnabled=false")   # 不加载图片，省时间
    opt.add_experimental_option("excludeSwitches", ["enable-automation"])
    opt.add_experimental_option("useAutomationExtension", False)
    d = webdriver.Edge(options=opt)
    d.set_page_load_timeout(70)
    _stat("sessions")
    with _pool_lock:
        _alive.append(d)
    return d


def _acquire():
    _sem.acquire()
    with _pool_lock:
        if _idle:
            return _idle.pop()
    try:
        return _new_driver()
    except Exception:
        _sem.release()
        raise


def _release(d) -> None:
    with _pool_lock:
        if d is not None and d in _alive:
            _idle.append(d)
    _sem.release()


def _kill(d) -> None:
    """会话坏了/被拦 → 关掉并从存活表里摘掉（下次 acquire 会新建）。"""
    try:
        d.quit()
    except Exception:
        pass
    with _pool_lock:
        if d in _alive:
            _alive.remove(d)


def close() -> None:
    """关掉所有会话（novel_dl 收尾 / 独立跑完时调用；atexit 也挂了）。"""
    with _pool_lock:
        for d in list(_alive):
            try:
                d.quit()
            except Exception:
                pass
        _alive.clear()
        _idle.clear()


atexit.register(close)


def _is_blocked(html: str, title: str) -> bool:
    """只认**硬封页**（CF 的 "Sorry, you have been blocked" / Attention Required）。

    ★ 2026-10-10 踩过：早先版本把"页面里含 challenge-platform / cf-chl"也当拦截，
      而 CF 的普通脚本在**每个**正常页面上都有 → 把正常页全判成拦截页。
      现在只认硬封页的特征串。
    """
    t = (title or "").lower()
    low = (html or "").lower()
    return ("attention required" in t
            or "you have been blocked" in low
            or "cf.errors.css" in low)


def _goto(d, url: str, *, tries: int = 2):
    """导航到一个页面，返回 (html, title, status)。status ∈ ok / blocked / blank。

    · blocked：CF 硬封 → 记账（连续够次数会抛 SiteBlocked）
    · blank  ：页面没加载出来（偶发空载）→ 上层重试，**不**算封禁
    """
    html, title = "", ""
    for _ in range(tries):
        _throttle()
        try:
            d.get(url)
        except Exception:
            pass                                   # 超时也让下面去读
        html = ""
        for _ in range(24):                        # 最多 ~3.6s 等 DOM
            try:
                html = d.execute_script("return document.documentElement.outerHTML") or ""
            except Exception:
                html = ""
            if html and len(html) > 2000:
                break
            time.sleep(0.15)
        try:
            title = d.title or ""
        except Exception:
            title = ""
        if _is_blocked(html, title):
            _stat("blocks")
            _note_block()                          # 够次数会抛 SiteBlocked
            return html, title, "blocked"
        if len(html) < 2000:
            time.sleep(0.4)
            continue                               # 空载 → 再试一次
        _note_ok()
        return html, title, "ok"
    return html, title, "blank"


def _chapter_text(d, url: str, *, tries: int = 3) -> str:
    """取一章正文（innerText，实测 0.11s/章）。空载 → **重新导航**再试。"""
    if _blocked_hard:
        raise SiteBlocked(f"{NAME} 本机已被 CF 封锁，停止抓取")
    for attempt in range(1, tries + 1):
        _throttle()
        try:
            d.get(url)
        except Exception:
            pass
        txt = ""
        for _ in range(40):                        # 最多等 ~6s 让正文出现
            try:
                txt = d.execute_script(
                    "const e=document.querySelector(arguments[0]);"
                    "return e ? (e.innerText||e.textContent||'') : '';", CONTENT_SEL) or ""
            except Exception:
                txt = ""
            if len(txt) > 150:
                break
            time.sleep(0.15)
        if len(txt) > 150:
            _note_ok()
            return txt
        # 没拿到 → 看看是不是被封页
        try:
            html = d.execute_script("return document.documentElement.outerHTML") or ""
            title = d.title or ""
        except Exception:
            html, title = "", ""
        if _is_blocked(html, title):
            _stat("blocks")
            _note_block()                          # 够次数会抛 SiteBlocked
            return ""
        if attempt < tries:
            _stat("retries")
            time.sleep(0.5)
    return ""


# ══════════════════════════════════════════════════════════════════════════
# 文本清洗：标题复读 / 站方广告（**只删广告，不改写正文**）
# ══════════════════════════════════════════════════════════════════════════
_WS_RE = re.compile(r"[ \t\u3000]+")


def _log_removed(kind: str, text: str) -> None:
    try:
        with open(_AD_LOG, "a", encoding="utf-8") as fh:
            fh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}\t{kind}\t{text}\n")
    except OSError:
        pass


def strip_ads(text: str) -> tuple[str, int]:
    """删掉站方广告句；返回 (清洗后文本, 删掉的处数)。删了什么会记日志。"""
    if _KEEP_ADS or not text:
        return text, 0
    removed = 0
    out_lines = []
    for line in text.split("\n"):
        s = _WS_RE.sub(" ", line).strip()
        if not s:
            continue
        new = s
        hit_here = 0
        for pat in _AD_PATTERNS:
            for m in list(re.finditer(pat, new)):
                frag = m.group(0)
                if len(frag.strip()) < 6:           # 太短的别动，防误伤
                    continue
                _log_removed("inline", frag)
                new = new.replace(frag, "")
                removed += 1
                hit_here += 1
        new = _WS_RE.sub(" ", new).strip(" 　")
        if not new or re.fullmatch(r"[（(]?\s*[)）]?", new):
            # ★ 只有"没做过 inline 删除却整行没了"才记一次 line（否则审计日志会重复两条）
            if not hit_here:
                _log_removed("line", s)
            continue
        out_lines.append(new)
    return "\n".join(out_lines), removed


_simp_lock = threading.Lock()
_simp_cc = None
_simp_tried = False


def _to_simp(text: str) -> str:
    """繁體 → 简体（随包内置的 OpenCC `tw2s`）。关掉：`UUKANSHU_SIMP=0`。

    ★ 为什么用 tw2s 而不是 t2s：真实语料实测（2.19M 字，探针字残留）
        t2s/hk2s 13,742 处（5.55%）｜**tw2s 168 处（0.07%）** —— 这站是台版繁體用字
        （`著急`/`隻`/`麵`/`裡`），t2s 根本不认这些。
    ★ 转换器**只建一次**（词典加载有开销），并用锁护住（会话池是 2 个线程）。
    ★ 没装/没随包 opencc 时**静默降级**为繁體 —— 转换是加分项，不该让下载失败。
    """
    global _simp_cc, _simp_tried
    if not _SIMP or not text:
        return text
    with _simp_lock:
        if _simp_cc is None and not _simp_tried:
            _simp_tried = True
            try:
                from opencc import OpenCC              # 优先随包那份（同目录 opencc/）
                _simp_cc = OpenCC("tw2s")
                log(f"  🔤 [{NAME}] 正文转简体：OpenCC tw2s（随包，Apache-2.0）")
            except Exception as exc:  # noqa: BLE001
                _simp_cc = False
                log(f"  ⚠️ [{NAME}] 没有可用的 opencc → 正文保持繁體"
                    f"（{type(exc).__name__}: {str(exc)[:50]}）")
        cc = _simp_cc
    if not cc:
        return text
    try:
        with _simp_lock:
            return cc.convert(text)
    except Exception:  # noqa: BLE001
        return text


def _drop_title_echo(text: str, chapter_name: str) -> str:
    """删掉正文首行的**标题复读**（站方每章都把标题抄在正文第一行）。

    ★ 与 xbiquzw.py 同一道闸门：删完剩余正文必须 **≥30 字**，否则保留原样
      （宁可留一行重复，也不能把整章删没）。
    """
    if not text or not chapter_name:
        return text
    norm = lambda s: re.sub(r"[^\w\u4e00-\u9fff]+", "", s or "")  # noqa: E731
    n_title = norm(chapter_name)
    if len(n_title) < 4:
        return text
    lines = text.split("\n")
    first = norm(lines[0])
    if not (first and first.startswith(n_title) and len(first) <= len(n_title) + 12):
        return text
    rest = "\n".join(lines[1:]).strip()
    if len(re.sub(r"\s", "", rest)) < 30:
        return text
    return rest


# ══════════════════════════════════════════════════════════════════════════
# 三件套：search / toc / chapter（novel_dl.py 按这三个函数调）
# ══════════════════════════════════════════════════════════════════════════
_SEARCH_JS = """
const out = [];
document.querySelectorAll('div.bookbox').forEach(b => {
  const a = b.querySelector('.bookname a');
  if (!a) return;
  const au = b.querySelector('.author a');
  out.push({title: (a.textContent||'').trim(), url: a.getAttribute('href')||'',
            author: au ? (au.textContent||'').trim() : ''});
});
return out;
"""

_TOC_JS = """
const out = [];
document.querySelectorAll(arguments[0]).forEach(a => {
  out.push({name: (a.textContent||'').trim(), url: a.getAttribute('href')||''});
});
return out;
"""

_INFO_JS = """
const g = k => { const m = document.querySelector('meta[property="og:'+k+'"]');
                 return m ? (m.getAttribute('content')||'') : ''; };
const h1 = document.querySelector('h1');
return {book_id: g('book_id'), type: g('type'), title: g('title'),
        cover: g('image'), intro: g('description'),
        h1: h1 ? (h1.textContent||'').trim() : '',
        desc: (document.querySelector('meta[name=description]')||{}).content || ''};
"""


def search(title: str, *, timeout: float = 0.0) -> list:
    """书名 → [{'title','author','url','ctx'(,'site_exact')}]。

    ★ 实测：**精确命中时站点直接 302 到书页**（没有结果列表）——
      这种情况下把书页当成**唯一一条**命中，并打上 `site_exact=True`
      （给 novel_dl 的"首条完美匹配"闸门用：书名是繁體、用户输的是简体，
      归一化比较必然不相等，只有站点自己给的"唯一命中"才算完美）。
    """
    d = _acquire()
    try:
        url = f"{BASE}/search?searchkey={urllib.parse.quote(title)}"
        log(f"  [{NAME}] 搜索: {url}")
        html, page_title, status = _goto(d, url)
        if status != "ok":                     # blocked → 换会话再来一次；blank → 同会话重试
            if status == "blocked":
                _kill(d)
                d = _acquire()
            html, page_title, status = _goto(d, url)
        if status != "ok":
            log(f"  [{NAME}] 搜索页没拿到（{status}），放弃本源")
            return []
        final = ""
        try:
            final = d.current_url or ""
        except Exception:
            pass
        # ★ 精确命中时站点直接跳到书页 → 认出来，当成"唯一一条命中"
        is_book = False
        try:
            is_book = bool(d.execute_script(_BOOK_JS))
        except Exception:
            is_book = False
        if not is_book:
            is_book = sum(1 for h in _BOOK_HINTS if h in html) >= 2
        if is_book:
            info = _info(d) or {}
            name = (info.get("title") or info.get("h1") or "").strip()
            if not name:
                m = re.search(r"<title>(.*?)(?:最新章節|最新章节|最新章节列表)", html or "")
                name = re.sub(r"<[^>]+>", "", m.group(1)).strip() if m else title
            au = ""
            md = info.get("desc") or ""
            m2 = re.search(r"由作者\(([^)]*)\)", md) or re.search(r"作者[：:]\s*([^,，\s]{1,20})", md)
            au = m2.group(1).strip() if m2 else ""
            log(f"  [{NAME}] 站点直接跳到书页：《{name}》{(' / ' + au) if au else ''}")
            # ★ 书名也转简体：这样 a) 成品书名/文件名统一是简体；
            #   b) 用户输简体时 `_score` 直接 ==1.0（连 site_exact 都不用兜）。
            #   原始繁體名留在 raw_title 里，排查时不丢线索。
            return [{"title": _to_simp(name), "raw_title": name,
                     "author": au, "url": final or f"{BASE}/",
                     "ctx": md[:200], "site_exact": True}]
        hits = []
        try:
            for it in (d.execute_script(_SEARCH_JS) or []):
                t = (it.get("title") or "").strip()
                u = (it.get("url") or "").strip()
                if not t or not u:
                    continue
                if u.startswith("/"):
                    u = BASE + u
                hits.append({"title": _to_simp(t), "raw_title": t,
                             "author": (it.get("author") or "").strip(),
                             "url": u, "ctx": f"{it.get('author') or ''}"})
        except Exception as exc:  # noqa: BLE001
            log(f"  [{NAME}] 结果解析失败：{type(exc).__name__}: {str(exc)[:60]}")
        log(f"  [{NAME}] 命中 {len(hits)} 条")
        return hits
    finally:
        _release(d)


def _info(d) -> dict:
    try:
        return d.execute_script(_INFO_JS) or {}
    except Exception:
        return {}


def info(book_url: str, *, timeout: float = 0.0) -> dict:
    """书页元数据（og: 标签实测齐全：book_id / type / title / image / description）。"""
    d = _acquire()
    try:
        html, _t, status = _goto(d, book_url)
        if status != "ok":
            _kill(d)
            return {}
        o = _info(d)
        o["url"] = book_url
        # 对外给的元数据也统一成简体（原始值留在 raw_* 里）
        for k in ("title", "h1"):
            if o.get(k):
                o["raw_" + k] = o[k]
                o[k] = _to_simp(o[k])
        return o
    finally:
        _release(d)


def toc(book_url: str, *, timeout: float = 0.0) -> list:
    """书页 → [{'name','url'}]，按目录页出现顺序。

    ★ 只取 `dl.book.chapterlist` 里的 `<dd><a>`（页首那块"最新章節"不在里面），
      再按 URL 去重；实测《贅婿》1307 章 / 《冬日重現》505 章一页出全。
    """
    d = _acquire()
    try:
        html, page_title, status = _goto(d, book_url)
        if status != "ok":
            if status == "blocked":
                _kill(d)
                d = _acquire()
            html, page_title, status = _goto(d, book_url)
        if status != "ok":
            log(f"  [{NAME}] 书页没拿到（{status}），目录失败")
            return []
        items = []
        try:
            items = d.execute_script(_TOC_JS, TOC_SEL) or []
        except Exception as exc:  # noqa: BLE001
            log(f"  [{NAME}] 目录解析失败：{type(exc).__name__}: {str(exc)[:60]}")
        out, seen = [], set()
        for it in items:
            u = (it.get("url") or "").strip()
            n = (it.get("name") or "").strip()
            if not u or not n:
                continue
            if u.startswith("/"):
                u = BASE + u
            if u in seen:
                continue
            seen.add(u)
            # ★★ 2026-10-10：章名也要转简体（原来只转正文 → 出现"简体正文 + 繁體章名"的
            #   不一致，用户当场就看出来了）。章名的繁体版本没丢，见 hint 字段。
            out.append({"name": _to_simp(n), "url": u,
                        **({"hint": n} if _SIMP and n != _to_simp(n) else {})})
        log(f"  [{NAME}] 目录 {len(out)} 章")
        return out
    finally:
        _release(d)


def chapter(url: str, *, timeout: float = 0.0) -> str:
    """正文页 → 纯文本（innerText；删首行标题复读与站方广告）。"""
    d = _acquire()
    try:
        raw = _chapter_text(d, url)
        if not raw:
            _stat("blocks")
            log(f"  ⚠️ [{NAME}] 正文没取到（等元素超时）→ 重开会话再试一次 {url}")
            _kill(d)
            d = _acquire()
            raw = _chapter_text(d, url)
        if not raw:
            return ""
        txt, n_ads = strip_ads(raw)
        _stat("ads", n_ads)
        _stat("chapters")
        # 章名：正文首行就是（也是标题复读的判据）
        first = (txt.split("\n")[0] if txt else "").strip()
        # 顺序：去广告 → 去首行标题复读 → 转简体（转换放最后，避免广告正则被简体化后不匹配）
        return _to_simp(_drop_title_echo(txt, first))
    finally:
        _release(d)


# ══════════════════════════════════════════════════════════════════════════
# CLI（独立调试 / 出 TXT）
# ══════════════════════════════════════════════════════════════════════════
def _download(target: str, *, limit: int = 0, out_dir: str = "") -> dict:
    hits = search(target) if not target.startswith("http") else []
    book_url = target if target.startswith("http") else (hits[0]["url"] if hits else "")
    if not book_url:
        return {"ok": False, "error": f"搜索不到《{target}》"}
    meta = info(book_url)
    title = (meta.get("title") or meta.get("h1")
             or (hits[0]["title"] if hits else target)).strip() or target
    chapters = toc(book_url)
    if len(chapters) < 2:
        return {"ok": False, "error": f"目录只有 {len(chapters)} 章"}
    if limit:
        chapters = chapters[:limit]
    out_dir = out_dir or os.path.join(os.getcwd(), "download")
    os.makedirs(out_dir, exist_ok=True)
    parts, failed, t0 = [], [], time.time()
    for i, ch in enumerate(chapters, 1):
        txt = chapter(ch["url"])
        if txt:
            parts.append((ch["name"], txt))
        else:
            failed.append(ch["name"])
            log(f"    ❌ 第 {i} 章失败：{ch['name'][:24]}")
        if i % 10 == 0 or i == len(chapters):
            log(f"    进度 {i}/{len(chapters)}  {time.time()-t0:.0f}s  ❌{len(failed)}")
    if not parts:
        return {"ok": False, "error": "所有章节都抓取失败"}
    safe = re.sub(r'[\\/:*?"<>|]', "_", title).strip() or "book"
    path = os.path.join(out_dir, f"{safe}.txt")
    with open(path, "w", encoding="utf-8") as fh:
        # ★ 2026-10-10：TXT 头不再写 `来源：…`（用户要求，全源统一）
        fh.write(f"{title}\n\n")
        for n, t in parts:
            fh.write(f"\n\n{n}\n\n{t}\n")
    return {"ok": True, "file": path, "book": title, "chapters": len(parts),
            "failed": failed, "elapsed": round(time.time() - t0, 1), "stats": stats()}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=f"{NAME} 站专用爬虫（Selenium + Edge，出 TXT）")
    ap.add_argument("target", nargs="?", help="书名")
    ap.add_argument("--search", default="")
    ap.add_argument("--info", default="")
    ap.add_argument("--toc", default="")
    ap.add_argument("--chapter", default="")
    ap.add_argument("--get", default="")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--out", default="")
    ap.add_argument("--sessions", type=int, default=0, help="并发浏览器数（默认 2，★ 别调大）")
    a = ap.parse_args(argv)
    global _SESSIONS
    if a.sessions:
        _SESSIONS = max(1, min(4, a.sessions))
    try:
        if a.search:
            for h in search(a.search)[:10]:
                print(f"  《{h['title']}》 {h.get('author','')}  {h['url']}"
                      + ("   [站点唯一命中]" if h.get("site_exact") else ""))
            return 0
        if a.info:
            print(json.dumps(info(a.info), ensure_ascii=False, indent=1))
            return 0
        if a.toc:
            chs = toc(a.toc)
            print(f"共 {len(chs)} 章；前 3：{[c['name'] for c in chs[:3]]}")
            return 0 if chs else 1
        if a.chapter:
            t = chapter(a.chapter)
            print(f"{len(t)} 字\n{t[:300]}")
            return 0 if t else 1
        if a.get or a.target:
            r = _download(a.get or a.target, limit=a.limit, out_dir=a.out)
            print(json.dumps(r, ensure_ascii=False))
            return 0 if r.get("ok") else 1
    except SiteBlocked as exc:
        # ★ 被封就干净退出（退出码 3），别丢一堆 traceback 让人以为是代码坏了
        print(json.dumps({"ok": False, "blocked": True, "error": str(exc)},
                         ensure_ascii=False))
        return 3
    finally:
        close()
    ap.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
