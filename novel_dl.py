#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""novel_dl.py —— EasyPub 的下载引擎：**多书源聚合，纯 HTTP**。

替换掉旧的「Selenium 浏览器自动化 + pyautogui 固定坐标点击 + DeepSeek 判来源」实现
（旧路径又慢又脆，且需要 API key）。新实现只要一个 HTTP 栈。

■ 书源与顺序（★★ 2026-10-09 用户拍板：顺序即优先级，命中即停、不并发）★★
  1. 笔趣阁·爬虫源 http://www.xbiquzw.net/  ★ **站专用爬虫**（xbiquzw.py）：
       站内搜索 / 无 Cookie / 出 TXT；自带 `pop_enabled_t` 反爬处理 + xbiquwk.com 域名兜底。
       ★ **只认「搜索首条 + 完美匹配」**（`strict_first=True`）：首条归一化后与书名不完全
         相同就整源跳过，宁可放过、不抓错书。
  2. 久久小说网    女频 TXT 直链型（kind="txt"，整本一次拿；搜索只有 POST 通）
  3. 轻小说源      哔哩轻小说（linovelib.py）—— 不在本表里，是**网文源全灭之后的兜底**，
       命中后直接产带封面 EPUB（见 download() 末尾那段）。
  ★ **SoNovel 那两个源（笔尖中文 / 悠久小说网）已于 2026-10-09 整块删除**（用户：「完全舍弃
     sonovel」）。早年那套「通用正则表」的取源思路也随之作废 —— 现在要么是站专用爬虫，
     要么是 TXT 直链站；`kind="chapter"` 的通用正则分支仍保留在代码里，但没有源在用了。
  ★ 源的三种形态（Source.kind）：
     · "crawler"：**站专用爬虫模块**驱动（`crawler="xbiquzw"` → 同目录 xbiquzw.py 的
                  search()/toc()/chapter()）。站点有反爬门/特殊参数时走这条。
     · "txt"    ：整本 TXT 直链（久久小说网）
     · "chapter"：通用正则表驱动（本项目已无源使用；留给以后接新站）

■ 三件套：search(title) -> [候选] ；toc(book_url) -> [(章节名, url)] ；chapter(url) -> 正文

■ 实测证据（★ 2026-10-09 重写：SoNovel 那两个源已删，这里只留**现在还在用的**）
  · 笔趣阁·爬虫源（xbiquzw.net → 301 → xbiquwk.com）：见 **xbiquzw.py 文件头**的完整实测留档
    （搜索表格 / `id="list"` 目录 / `id="content"` 正文 / `pop_enabled_t` 反爬门 / 域名抖动数据）。
  · 久久小说网（TXT 直链）：搜索只有 POST 通、下载页 download_1 直链、GB18030 —— 见本文件
    download_via_txt() 上方那一段注释（同样是原始响应实测）。
  · 轻小说源（哔哩轻小说）：见 linovelib.py 文件头。
  （历史：2026-09-29 那批「笔尖中文 / 悠久小说网 / 阅读库 / 笔趣阁365」的探站结论已随源删除而作废，
    保留在 `_backup\20261009-*` 的存档里，需要时去翻。）

■ 设计要点
  · HTTP 复用 `novelmeta/net.py` 的 fetch()：已有 IPv4-only 修正（本机 IPv6 无出口，
    否则每个请求白等 8 秒）、超时、重试、FetchError 语义。**不要再引 requests。**
  · 目录/正文选择器：先按各源的精确规则；不中时退到**通用启发式**（同路径下所有章节链接 /
    页面上文本最长的 div），这样模板小改也不会立刻失效。
  · 章序按目录页出现顺序（**不按 URL 数字排序** —— 有的站 ID 无序）。
  · 断点续传：章节缓存在 `download/<书名>/.cache/<序号>.txt`，中断重跑只补缺的。
    ⚠️ 副作用（2026-10-09 实测踩到）：**同一个书名重下时，缓存会让它"待抓 0"、直接复用旧正文** ——
       App 的「下载前清理」只删 download/ 的顶层文件、不删这个子目录，于是"重新下载"看着成功、
       其实一个字都没重抓（TXT 与旧版逐字节相同 → 界面还会显示「TXT内容未变，跳过」）。
       要真重抓就先删 download/<书名>/ 整个目录（或给本脚本加 --no-resume）。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.parse
import threading
from concurrent.futures import ThreadPoolExecutor


class RateLimiter:
    """全局最小请求间隔。

    ★ 为什么不用"每章 sleep(gap)"：串行时那是唯一能控速的办法，但一上多线程，
      每个线程各自 sleep 就等于把并发优势全抵消掉（实测 3 线程 + 每章 sleep 0.4s
      和串行差不多慢）。改成**全局**节流：所有线程共用一个"下次允许发请求的时刻"，
      既保证对站点礼貌（默认 20 请求/秒上限），又不浪费并发。
    """

    def __init__(self, min_interval: float):
        self.min_interval = max(0.0, float(min_interval))
        self._lock = threading.Lock()
        self._next = 0.0

    def wait(self) -> None:
        if not self.min_interval:
            return
        with self._lock:
            now = time.monotonic()
            sleep_for = max(0.0, self._next - now)
            self._next = max(now, self._next) + self.min_interval
        if sleep_for > 0:
            time.sleep(sleep_for)

# ── 复用 novelmeta 的 HTTP 栈 ────────────────────────────────────────────────
_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (os.environ.get("NOVELMETA_HOME") or "", _HERE):
    if _p and _p not in sys.path:
        sys.path.insert(0, _p)
# 本机 IPv6 无出口 → 强制 IPv4（实测 weread 8.4s → 0.24s；这里对 4 个源同样有效）
os.environ.setdefault("NOVELMETA_IPV4_ONLY", "1")
try:
    from novelmeta.net import FetchError, fetch
except ImportError as exc:  # 打包/路径异常时给一句人话
    print(json.dumps({"ok": False, "error": f"无法导入 novelmeta.net.fetch：{exc}"}, ensure_ascii=False))
    raise SystemExit(1)

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
AD_PATTERNS = [
    r"请记住本站域名", r"笔趣阁", r"最新章节", r"手机版阅读网址", r"天才一秒记住",
    r"txt下载", r"无弹窗", r"全文免费阅读", r"最快更新", r"加入书签", r"投推荐票",
    r"求月票", r"求收藏", r"本站网址", r"www\.[a-z0-9\-]+\.(?:com|net|org|cc|la|info)",
    r"一秒记住", r"免费阅读", r"更新最快", r"第\s*\(\d+/\d+\)\s*页",
]
_SCRIPT_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.S | re.I)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t\u3000]+")


# ══════════════════════════════════════════════════════════════════════════
# 书源定义
# ══════════════════════════════════════════════════════════════════════════
class Source:
    __slots__ = ("name", "base", "search_url", "result_re", "toc_url", "toc_container",
                 "toc_re", "content_res", "search_gap", "note", "kind", "crawler",
                 "strict_first", "max_workers")

    def __init__(self, name, base, *, search_url=None, result_re=None, toc_url=None,
                 toc_container=None, toc_re=None, content_res=(), search_gap=0.0, note="",
                 kind="chapter", crawler="", strict_first=False, max_workers=0):
        self.name = name
        self.base = base.rstrip("/")
        self.search_url = search_url
        self.result_re = re.compile(result_re) if result_re else None
        self.toc_url = toc_url              # 形如 "/read/{id}/"，None=目录就在详情页
        # ★ 目录容器作用域：不限定的话会把页首"最新章节"块也抓进来（实测笔尖：1474 条里
        #   混入了 第一二六四章 等最新章节，导致章序错乱）。
        self.toc_container = re.compile(toc_container, re.S | re.I) if toc_container else None
        self.toc_re = re.compile(toc_re) if toc_re else None
        self.content_res = [re.compile(p, re.S) for p in content_res]
        self.search_gap = search_gap
        self.note = note
        # ★ "chapter"=逐章三件套；"txt"=整本一次（久久小说网）；"crawler"=站专用爬虫模块
        self.kind = kind
        # ★★ 2026-10-09（用户：「改成类轻小说源（linovelib.py）的直接爬网页的爬虫型源」）：
        #   `crawler` 填**同目录的模块名**（如 "xbiquzw"）—— 这类源的 search/toc/chapter
        #   不再由本文件的通用正则表驱动，而是交给那个模块里同名的三个函数去爬。
        #   为什么要开这条路：目标站（xbiquzw.net，301 到 xbiquwk.com）有一层
        #   **`pop_enabled_t` 反爬门**，通用正则源没法给每个请求补参数，反爬窗口一开就
        #   整源静默失效（实测 20 章里 18 章拿到 1133B 挑战页）。站专用爬虫能自己处理。
        self.crawler = crawler
        # ★★ 2026-10-09（用户：「新源要完美匹配的第一条才可以」）★★
        #   True = 这个源**只认「站内搜索结果的第一条 + 完美匹配」**：
        #     · 第一条（站点给的相关度首条，**不是**本文件按作者/相似度重排后的那条）
        #       归一化后必须与查询书名**完全相同**（_score == 1.0，即只差标点/空格/大小写）；
        #     · 不满足就**直接跳过本源**，交给下一个源 —— 宁可放过，不可抓错书。
        self.strict_first = strict_first
        # ★★ 2026-10-10（uukanshu 源）★★
        #   浏览器型源的**并发上限**：一个会话就是一个真浏览器，开 16 个会瞬间被封。
        #   0 = 不限制（纯 HTTP 源）。_download_chapters() 会用它夹住 --workers。
        self.max_workers = max_workers

    def abs(self, url: str) -> str:
        return urllib.parse.urljoin(self.base + "/", url)


# ═════════════════════════════════════════════════════════════════════════
# 久久小说网（女频 TXT 直链型源）—— 2026-09-29 追加。底座：帝国CMS 下载系统。
# 以下全部是**实测证据**：
#   · 搜索**只有 POST 通**（GET 三种参数组合均 0 结果）：
#       POST /e/search/index.php
#       form: keyboard=<书名>&show=title,writer&tbname=download&tempid=2&orderby=newstime&myorder=0
#     结果页正则 /txt/dl-(\d+)-(\d+)\.html → classid=第1组, id=第2组
#   · 站内唯一的 AJAX 是评分接口 getfen.php（不是搜索）；必应 site: 该站 0 命中
#     → **这站没有任何搜索兜底**，所以另留了 --txt-url 直通入口
#   · 下载页 GET /txt/dl-{classid}-{id}.html（UTF-8）内含：
#       <a class="download_1" href="https://down.ijjjjxsw.com/e/DownSys/doaction.php?enews=DownSoft
#           &classid=14&id=36568&pathid=0&pass=<md5>&p=∶∶∶">TXT…【无需解压缩】扣除会员积分0</a>
#     即 TXT 免费直链；download_2 是 RAR（不用）；pass 令牌**静态**（两次请求一致）
#   · 直链 GET（带 Referer=下载页）→ application/octet-stream +
#     Content-Disposition: attachment → 实测《渭北春天树》709,678 B、
#     GB18030、第1章→第78章、广告 0 命中
# ═════════════════════════════════════════════════════════════════════════
IJJX_BASE = "https://www.ijjxsxzw.com"
IJJX_SEARCH = IJJX_BASE + "/e/search/index.php"
IJJX_TXT_PAGE = IJJX_BASE + "/txt/dl-{classid}-{id}.html"
_IJJX_IDS_RE = re.compile(r"/txt/dl-(\d+)-(\d+)\.html")
_IJJX_BOOK_DL_RE = re.compile(r'href="(/txt/dl-(\d+)-(\d+)\.html)"')
_IJJX_DL1_RE = re.compile(r'class="download_1"\s+href="([^"]+)"')
_IJJX_PAGE_META = {
    "title": re.compile(r"<h3>([^<]+?)txt电子书下载信息</h3>"),
    "author": re.compile(r"<strong>\s*小说作者：\s*</strong>\s*([^<]+)"),
    "status": re.compile(r"<strong>\s*写作进度：\s*</strong>\s*([^<]+)"),
    "size": re.compile(r"<strong>\s*文件大小：\s*</strong>\s*([^<]+)"),
    "updated": re.compile(r"<strong>\s*最后更新：\s*</strong>\s*([^<]+)"),
    "cover": re.compile(r'<img\s+src="(//image\.jjjjxsw\.com/[^"]+)"'),
}

# 章节标题（站点可能带前缀，统一去掉）
_CHAPTER_RE = re.compile(
    r"^(?:正文|VIP章节|收费章节|vip章节)?\s*[:：]?\s*"  # ★ 前缀是**可选**的（实测踩过：必选导致 0 章节）
    r"(第[一二三四五六七八九十百千零〇0-9]+[章回节卷][^\n]{0,40}"
    r"|楔子[^\n]{0,40}|序章[^\n]{0,40}|序[^\n]{0,20}|前言[^\n]{0,20}"
    r"|后记[^\n]{0,20}|尾声[^\n]{0,20}|番外[^\n]{0,40})$"
)
# 头部元信息整段：========== 《书名》 作者：X 一句话简介：… 立意：… ==========
_IJJX_HDR_RE = re.compile(r"=+\s*《(?P<title>[^》]+)》\s*(?:作者[：:]\s*(?P<author>[^\s=]+))?[^=]*=+")
# 站标 / 广告 / 分隔线（逐行命中即删）
_IJJX_AD_LINE = [
    r"请记住本站", r"手机版阅读", r"最新章节", r"笔趣阁", r"久久小说网", r"ijjxsxzw",
    r"^\s*www\.[a-z0-9\-]+\.(?:com|net|org|cc|la|info)\s*$",
    r"^【?本章完】?\s*$", r"^\s*[-=*—＿_·]{6,}\s*$",
]

SOURCES = [
    # ★★★ 2026-10-10（用户：「测冬日重现，全本」「开工」）★★★
    #   新增 **UU看書（uukanshu.cc）** 站专用爬虫源，置顶。实测对比（《冬日重現》全本）：
    #     · 质量：站方样板/广告 **7 处**（xbiquzw 版 250+ 处）；「别的书片段」凑字 **0 处**（xbiquzw 版有 14 段）
    #     · 代价：必须走浏览器（Selenium+Edge，python/curl 全 403），单章 0.11s，默认 2 会话
    #     · 是**繁體**站（正文繁體，广告句是简体 —— 也正因此好认）
    Source(
        "UU看書(uukanshu.cc)", "https://uukanshu.cc",
        kind="crawler", crawler="uukanshu", strict_first=True, max_workers=2,
        note="站专用爬虫（uukanshu.py / Selenium+Edge）：**只认首条完美匹配**（精确命中时站点会直接跳书页"
             "→ 记为 site_exact）；为防 CF 封 IP，默认 2 会话 + 0.8s 全局间隔；被封会整体停手",
    ),
    Source(
        "笔趣阁·爬虫源(xbiquzw.net)", "http://www.xbiquzw.net",
        kind="crawler", crawler="xbiquzw", strict_first=True,
        note="站专用爬虫（xbiquzw.py）：站内搜索 / 无 Cookie / 出 TXT；"
             "自动补 pop_enabled_t 反爬参数，域名失败自动切 xbiquwk.com；"
             "**只认搜索首条完美匹配**",
    ),
    Source(
        "久久小说网", IJJX_BASE,
        kind="txt",
        note="女频 TXT 直链站（帝国CMS 下载系统）；搜索**只有 POST 通**",
    ),
]


# ══════════════════════════════════════════════════════════════════════════
# 基础工具
# ══════════════════════════════════════════════════════════════════════════
def log(msg: str) -> None:
    """进度走 **stdout**（不是 stderr）。

    ★ 2026-09-29 踩的坑：一开始把进度打到 stderr，结果 main.js 的 UI 只订阅 stdout 的流，
      界面在整本书下载的十几分钟里毫无动静 → 用户以为卡死，直接把窗口关了
      （实测缓存里其实已经下了 40 章）。进度行不带 `{`，不会干扰 main.js 的 JSON 契约扫描。
    """
    print(msg, flush=True)


def fetch_text(url: str, *, timeout: float = 20.0, tries: int = 3, referer: str = "") -> str:
    """取页面并按「Content-Type charset → GB18030 → UTF-8」解码（实测这 4 站都是 UTF-8）。"""
    headers = {"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9"}
    if referer:
        headers["Referer"] = referer
    resp = fetch(url, headers=headers, timeout=timeout, tries=tries)
    if resp.status != 200:
        raise FetchError(f"HTTP {resp.status}", [url])
    ctype = (resp.headers.get("Content-Type") or resp.headers.get("content-type") or "")
    m = re.search(r"charset=([\w\-]+)", ctype, re.I)
    order = []
    if m:
        order.append(m.group(1).lower())
    order += ["utf-8", "gb18030"]
    for enc in order:
        try:
            text = resp.body.decode(enc)
            if text.count("\ufffd") == 0:
                return text
        except (UnicodeDecodeError, LookupError):
            continue
    log(f"⚠️ 编码没洗干净，按 gb18030 容错解码: {url}")
    return resp.body.decode("gb18030", "replace")


def strip_html(html: str, *, keep_breaks: bool = True) -> str:
    html = _SCRIPT_RE.sub("", html)
    if keep_breaks:
        html = re.sub(r"<br\s*/?>|</p>|</div>", "\n", html, flags=re.I)
    text = _TAG_RE.sub("", html)
    text = (text.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<")
                .replace("&gt;", ">").replace("&quot;", '"').replace("&#39;", "'"))
    lines = []
    for raw in text.split("\n"):
        line = _WS_RE.sub(" ", raw).strip()
        if not line:
            continue
        if any(re.search(p, line, re.I) for p in AD_PATTERNS):
            continue                    # ★ 只删广告行，绝不改写正文
        lines.append(line)
    # 去掉相邻重复段（有些站同一段重复输出）
    out = []
    for line in lines:
        if out and out[-1] == line:
            continue
        out.append(line)
    return "\n".join(out)


def _extract_div(html: str, start_idx: int) -> str:
    """从 `<div ...>` 开始，**按 div 深度**找到配对的 `</div>`。

    ★ 不能用非贪婪 `.*?</div>`：它会在第一个内层 `</div>` 就截断，
      实测把悠久的正文容器截成 64 个字的设置面板（本项目已因同类写法出过 4 次事故）。
    """
    i = html.find(">", start_idx)
    if i < 0:
        return ""
    i += 1
    depth, pos = 1, i
    tag_re = re.compile(r"<(/?)div\b[^>]*>", re.I)
    while depth > 0:
        m = tag_re.search(html, pos)
        if not m:
            return html[i:]
        depth += -1 if m.group(1) else 1
        pos = m.end()
    return html[i:m.start()]


def _longest_text_block(html: str) -> str:
    """通用兜底：扫描 div，取纯文本最长的那块（各站模板不同时的保险）。"""
    body = _SCRIPT_RE.sub("", html)
    best = ""
    for m in re.finditer(r"<div\b[^>]*>", body):
        txt = strip_html(_extract_div(body, m.start()))
        if len(txt) > len(best):
            best = txt
    return best


def _pick_content(html: str, src: "Source") -> str:
    for cre in src.content_res:                 # ★ 这些正则只匹配**开标签**
        m = cre.search(html)
        if m:
            txt = strip_html(_extract_div(html, m.start()))
            if len(txt) >= 80:
                return txt
    return _longest_text_block(html)


# ══════════════════════════════════════════════════════════════════════════
# 三件套
# ══════════════════════════════════════════════════════════════════════════
def _crawler_module(src: "Source"):
    """`kind="crawler"` 的源 → 取到它的模块对象；取不到返回 None（**不影响其它源**）。

    ★ 懒导入：模块缺失/语法错只让这一个源失效，不能连累整条下载链。
    """
    if not src.crawler:
        return None
    try:
        import importlib
        return importlib.import_module(src.crawler)
    except Exception as exc:  # noqa: BLE001
        log(f"  ⚠️ [{src.name}] 爬虫模块 {src.crawler}.py 不可用："
            f"{type(exc).__name__}: {str(exc)[:80]}")
        return None


def _crawler_call(src: "Source", fn: str, *a, **kw):
    """把三件套转给站专用爬虫模块。返回 None = 这个源现在不可用（上层按"没命中"处理）。"""
    mod = _crawler_module(src)
    if mod is None:
        return None
    return getattr(mod, fn)(*a, **kw)


def _crawler_close(src: "Source") -> None:
    """收摊：浏览器型源（如 uukanshu）用完把 headless Edge 会话全关掉。

    ★ 没有这一步的后果：一次下载结束后还留着 2 个无头 Edge 进程，
      用户看不见窗口，但它们一直占内存/句柄（实测要人工去任务管理器清）。
    """
    mod = _crawler_module(src)
    if mod is not None and hasattr(mod, "close"):
        try:
            mod.close()
        except Exception as exc:  # noqa: BLE001
            log(f"  ⚠️ [{src.name}] 关闭会话失败（不影响结果）：{type(exc).__name__}")


def search(src: "Source", title: str, *, timeout: float = 20.0) -> list:
    """书名 → [{'title','author','url'}]。不支持搜索的源返回空表。"""
    if src.kind == "crawler":
        hits = _crawler_call(src, "search", title, timeout=timeout)
        return hits or []
    if not src.search_url:
        log(f"  [{src.name}] 不支持搜索（{src.note}）→ 跳过")
        return []
    url = src.base + src.search_url.format(kw=urllib.parse.quote(title))
    log(f"  [{src.name}] 搜索: {url}")
    html = fetch_text(url, timeout=timeout, referer=src.base + "/")
    out, seen = [], set()
    for m in src.result_re.finditer(html):
        href, name = m.group(1), strip_html(m.group(2)).strip()
        if not name or name in seen:
            continue
        seen.add(name)
        # ★ 顺手抓链接后面的原始文字当上下文：杰奇系的搜索结果行里带作者名，
        #   用户传了 --author 时靠它消歧（同名书极多，实测"赘婿"有 45 条）。
        ctx = strip_html(html[m.end():m.end() + 300])
        out.append({"title": name, "author": "", "url": src.abs(href), "ctx": ctx})
    log(f"  [{src.name}] 命中 {len(out)} 条")
    if src.search_gap:
        time.sleep(src.search_gap)      # 站点强制的最小搜索间隔（历史：笔趣阁365 要 ≥15s）
    return out


def toc(src: "Source", book_url: str, *, timeout: float = 20.0) -> list:
    """详情页 → [(章节名, 绝对URL)]，**按目录页出现顺序**。

    ★ 两个实测坑：
      1. 有的站详情页只有"最新章节"，完整目录在另一个 URL（悠久：/book/{id}/ 只有 12 条，
         完整目录在 /read/{id}/ 有 1284 条）→ 用 Source.toc_url 指定。
      2. 有的站章节 href 是**相对详情页**的（笔尖：`1.html`），用 site base 拼会 404
         → 必须 urljoin 到 book_url。
    """
    if src.kind == "crawler":
        return _crawler_call(src, "toc", book_url, timeout=timeout) or []
    page_url = book_url
    if src.toc_url:
        m = re.search(r"/(\d+)/?", urllib.parse.urlparse(book_url).path)
        if m:
            page_url = src.abs(src.toc_url.format(id=m.group(1)))
    html = fetch_text(page_url, timeout=timeout, referer=src.base + "/")
    scope = html
    if src.toc_container:               # ★ 只在目录容器内取章节，避免页首"最新章节"块
        m = src.toc_container.search(html)
        if m:
            scope = _extract_div(html, m.start())
    pairs = []
    if src.toc_re:
        # ★ 章节 href 相对 **book_url** 解析，不是相对站点根
        pairs = [(urllib.parse.urljoin(page_url, h), strip_html(n).strip())
                 for h, n in src.toc_re.findall(scope) if n.strip()]
    if not pairs:
        # 通用兜底：同一路径下的所有 .html 链接
        prefix = urllib.parse.urlparse(page_url).path
        prefix = prefix if prefix.endswith("/") else prefix.rsplit("/", 1)[0] + "/"
        for h, n in re.findall(r'<a[^>]+href="([^"]+\.html?)"[^>]*>([^<]{1,60})</a>', scope):
            name = strip_html(n).strip()
            if name and urllib.parse.urlparse(urllib.parse.urljoin(page_url, h)).path.startswith(prefix):
                pairs.append((urllib.parse.urljoin(page_url, h), name))
    # 去重保序
    seen, out = set(), []
    for url, name in pairs:
        if url in seen:
            continue
        seen.add(url)
        out.append({"name": name, "url": url})
    return out


def chapter(src: "Source", url: str, *, timeout: float = 20.0) -> str:
    if src.kind == "crawler":
        return _crawler_call(src, "chapter", url, timeout=timeout) or ""
    return _pick_content(fetch_text(url, timeout=timeout, referer=src.base + "/"), src)


# ══════════════════════════════════════════════════════════════════════════
def ijjx_search(title: str, *, timeout: float = 20.0):
    """书名 → [{'id','title','author','page'}]。

    ★ 实测：POST /e/search/index.php 会 **302 到 /search/result-{searchid}-0.html**
      （fetch() 会自动跟随），结果页结构：
        <div class="searchTopic">1 、<a href="/txt/36568.html" class="searchtitle">
          <mark class="search-hit">渭北春天树</mark></a>&nbsp;&nbsp;作者：休屠城</div>
      → 书码就是 **/txt/{id}.html** 里的 id；classid 还要再走一步（见 _book_to_dl_page）。
      ★ GET 搜索实测 0 结果 → **只能 POST**。
    """
    form = {"keyboard": title, "show": "title,writer", "tbname": "download",
            "tempid": "2", "orderby": "newstime", "myorder": "0"}
    log(f"  [久久小说网] POST 搜索: {IJJX_SEARCH}")
    try:
        resp = fetch(IJJX_SEARCH, method="POST", form=form,
                     headers={"User-Agent": UA, "Referer": IJJX_BASE + "/",
                              "Accept-Language": "zh-CN,zh;q=0.9"},
                     timeout=timeout, tries=2)
    except FetchError as exc:
        log(f"  [久久小说网] 搜索失败: {exc}")
        return []
    if resp.status != 200:
        log(f"  [久久小说网] 搜索 HTTP {resp.status}")
        return []
    html = resp.body.decode("utf-8", "replace")
    hits = []
    for block in html.split('class="searchTopic"')[1:]:
        m = re.search(r'href="/txt/(\d+)\.html"', block)
        if not m:
            continue
        tm = re.search(r"<mark[^>]*>([^<]+)</mark>", block) or re.search(r">([^<]{1,60})</a>", block)
        am = re.search(r"作者：\s*([^<\s&]+)", block)
        hits.append({"id": m.group(1),
                     "title": (tm.group(1).strip() if tm else ""),
                     "author": (am.group(1).strip() if am else ""),
                     "page": IJJX_BASE + "/txt/" + m.group(1) + ".html"})
    log(f"  [久久小说网] 结果页命中 {len(hits)} 条")
    if not hits:
        log("  [久久小说网] 没解析到 searchTopic 结果块；HTML 片段: "
            + repr(re.sub(r"\s+", " ", html[:200])))
    return hits


def _book_to_dl_page(book_url: str, *, timeout: float = 20.0) -> str:
    """书籍页 /txt/{id}.html → 下载页 /txt/dl-{classid}-{id}.html。

    ★ 实测：书籍页里就有这个链接，classid 就在其中。
      书籍页标题形如：渭北春天树txt下载_休屠城_架空小说_久久小说网
    """
    try:
        h = fetch_text(book_url, timeout=timeout, referer=IJJX_BASE + "/")
    except FetchError as exc:
        log(f"  [久久小说网] 书籍页失败 {book_url}: {exc}")
        return ""
    m = _IJJX_BOOK_DL_RE.search(h)
    if not m:
        log("  [久久小说网] 书籍页里没找到 /txt/dl-….html；片段: "
            + repr(re.sub(r"\s+", " ", h[:200])))
        return ""
    return IJJX_BASE + m.group(1)

def preprocess_txt(raw: str, *, fallback_title: str = ""):
    """原始 TXT → 「章节标题 → 正文」的循环。返回 (text, meta, 章节数)。

    ★ 用户要求：开头那整段 ==…== 《书名》作者：… 一句话简介：… 立意：… ==…== **全删**，
      只保留章节标题与正文；正文**绝不改写**（保留全角缩进）。
    """
    meta = {"title": fallback_title, "author": ""}
    raw = raw.replace("\r\n", "\n").replace("\r", "\n")
    raw = raw.replace("\ufeff", "").replace("\u200b", "")
    lines = raw.split("\n")

    # ① 头部元信息：**先解析，再整段丢**
    head_end = 0
    for i, ln in enumerate(lines[:40]):
        if "========" not in ln:
            continue
        m = _IJJX_HDR_RE.search("\n".join(lines[: i + 1]))
        if m:
            meta["title"] = (m.group("title") or "").strip() or meta["title"]
            meta["author"] = (m.group("author") or "").strip()
        head_end = i + 1
    if head_end:
        log(f"    预处理：删掉头部元信息 {head_end} 行"
            f"（书名={meta['title']!r} 作者={meta['author']!r}）")

    # ②③④ 逐行：广告行丢掉 / 标题独占一行 / 正文原样保留
    out, chapters = [], 0
    for ln in lines[head_end:]:
        s = ln.strip()
        if s and any(re.search(p, s, re.I) for p in _IJJX_AD_LINE):
            continue
        if not s:
            if out and out[-1] != "":
                out.append("")
            continue
        if _CHAPTER_RE.match(s):
            chapters += 1
            if out and out[-1] != "":
                out.append("")
            out.append("")            # 标题前空行
            out.append(s)             # ★ 标题独占一行
            out.append("")            # 标题后空行
            continue
        out.append(ln.rstrip())       # 正文：原样保留（含全角缩进）
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip("\n")

    # ⑤ 完整性校验：不通过就报失败，不许把残本当成功
    words = len(re.sub(r"\s", "", text))
    if chapters < 3:
        raise ValueError(f"章节标题只有 {chapters} 个（<3）→ 判定文本不完整")
    if words < 1000:
        raise ValueError(f"正文只有 {words} 字（<1000）→ 判定文本不完整")
    return text, meta, chapters


def _decode_txt(raw: bytes):
    for enc in ("utf-8", "gb18030"):
        try:
            txt = raw.decode(enc)
            if txt.count("\ufffd") == 0:
                return txt, enc
        except UnicodeDecodeError:
            continue
    log("    ⚠️ 编码没洗干净，按 gb18030 容错解码")
    return raw.decode("gb18030", "replace"), "gb18030(replace)"


def download_via_txt(src, title, *, out_dir="", author="", timeout=20.0, resume=True,
                     txt_url=""):
    """久久小说网：搜索 → 下载页 → download_1 直链 → 预处理 → 落盘。"""
    out_dir = out_dir or os.path.join(_HERE, "download")
    os.makedirs(out_dir, exist_ok=True)

    # ★ 解析出「**下载页**」URL（三种入口）：
    #   ① --txt-url 直接给 /txt/dl-…html → 就是下载页
    #   ② --txt-url 给书籍页 /txt/{id}.html  → 转一步到下载页
    #   ③ 正常：POST 搜索 → 命中 → 书籍页 → 下载页
    dl_page, hit_author = "", ""
    if txt_url:
        dl_page = txt_url if "/txt/dl-" in txt_url else _book_to_dl_page(txt_url, timeout=timeout)
    if not dl_page:
        hits = ijjx_search(title, timeout=timeout)
        if hits:
            hits.sort(key=lambda h: -_score(title, h["title"]))
            best = hits[0]
            if _score(title, best["title"]) < 0.6:
                log(f"  [久久小说网] 首条《{best['title']}》与 '{title}' 相似度 <0.6 → 放弃（不硬塞）")
                return None
            hit_author = best.get("author") or ""
            log(f"  [久久小说网] 选用《{best['title']}》作者={hit_author!r} → {best['page']}")
            dl_page = _book_to_dl_page(best["page"], timeout=timeout)
    if not dl_page:
        return None

    try:
        page = fetch_text(dl_page, timeout=timeout, referer=IJJX_BASE + "/")
    except FetchError as exc:
        log(f"  [久久小说网] 下载页失败: {exc}")
        return None
    page_meta = {}
    for key, rx in _IJJX_PAGE_META.items():
        m = rx.search(page)
        page_meta[key] = (m.group(1).strip() if m else "")
    log(f"  [久久小说网] 下载页 OK：书名={page_meta.get('title')!r} "
        f"作者={page_meta.get('author')!r} 大小={page_meta.get('size')!r}")

    m = _IJJX_DL1_RE.search(page)
    if not m:
        log("  [久久小说网] 下载页没找到 download_1 直链；片段: "
            + repr(re.sub(r"\s+", " ", page[:200])))
        return None
    dl_url = m.group(1).replace("&amp;", "&")
    log(f"  [久久小说网] TXT 直链: {dl_url[:88]}…")

    safe = re.sub(r'[\\/:*?"<>|]', "_", page_meta.get("title") or title).strip() or "book"
    txt_path = os.path.join(out_dir, safe + ".txt")
    if resume and os.path.exists(txt_path) and os.path.getsize(txt_path) > 1024:
        log(f"  [久久小说网] 已有成品，跳过重下：{txt_path}")
        return {"ok": True, "file": txt_path, "source": src.name, "book": safe,
                "chapters": 0, "failed": [], "cached": True}

    try:
        d = fetch(dl_url, headers={"User-Agent": UA, "Referer": dl_page,
                                   "Accept-Language": "zh-CN,zh;q=0.9"},
                  timeout=max(timeout, 60.0), tries=2)
    except FetchError as exc:
        log(f"  [久久小说网] 直链下载失败: {exc}")
        return None
    ctype = (d.headers.get("Content-Type") or "").lower()
    if d.status != 200 or len(d.body) < 1024 or "html" in ctype:
        log(f"  [久久小说网] 直链异常：HTTP {d.status} {len(d.body)}B CT={ctype!r}")
        return None
    log(f"  [久久小说网] 直链 OK：{len(d.body)} B")

    cd = d.headers.get("Content-Disposition") or ""
    mfn = re.search(r"filename\*=UTF-8''([^;]+)", cd) or re.search(r'filename="([^"]+)"', cd)
    if mfn:
        nm = urllib.parse.unquote(mfn.group(1)).strip().strip(chr(34))
        # ★ 实测：站点的 filename 会带《》与「作者：X」（如《剑来》作者：烽火戏诸侯.txt）
        #   → 清洗成干净的书名，否则产物名很难看
        nm = re.sub(r"[《》【】]", "", nm)
        nm = re.sub(r"作者[：:].*$", "", nm).strip()
        if nm:
            safe = re.sub(r'[\\/:*?"<>|]', "_", nm.rsplit(".", 1)[0]).strip() or safe
            txt_path = os.path.join(out_dir, safe + ".txt")

    raw_txt, enc = _decode_txt(d.body)
    log(f"  [久久小说网] 编码={enc}  原始 {len(raw_txt)} 字")
    try:
        text, meta, chapters = preprocess_txt(raw_txt, fallback_title=title)
    except ValueError as exc:
        log(f"  [久久小说网] 预处理校验不通过：{exc}")
        return None
    with open(txt_path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    log(f"  ✅ 久久小说网《{meta['title']}》作者={meta['author'] or hit_author or author!r} "
        f"{chapters} 章 → {txt_path}")
    return {"ok": True, "file": txt_path, "source": src.name, "book": meta["title"],
            "author": meta["author"] or hit_author, "chapters": chapters, "failed": []}

# 下载主流程
# ══════════════════════════════════════════════════════════════════════════
def _norm(s: str) -> str:
    return re.sub(r"[\s《》【】\[\]（）()·、,，。.!！?？:：\"'“”‘’\-—]+", "", s or "").lower()


def _score(query: str, cand: str) -> float:
    """标题匹配度（0~1）。★ 2026-09-30 收严（用户要求"网文匹配率要设得很高"）：

    1.00 = 去掉标点/空格后**完全相同**（正常情况：《赘婿》→《赘婿》）
    0.95 = 候选 = 书名 + **常见的版本后缀**（校对版/精校版/全本/完结/TXT下载…）
    0.80 = 其他"前缀包含"关系（《斗破苍穹》 vs 《斗破苍穹之萧宇》← **同名同人，必须卡掉**）
    ≤0.60 = 靠字符重叠算的

    调用方 `find_book(min_score=0.95)` → 只有前两档能过，第三档**故意不放行**。
    """
    q, c = _norm(query), _norm(cand)
    if not q or not c:
        return 0.0
    if q == c:
        return 1.0
    # 书名 + 版本后缀（例：《赘婿 校对版》）→ 视为同一本
    _SUFFIX = ("校对版", "精校版", "精校", "校对", "全本", "完结", "未删减", "无删减",
               "珍藏版", "典藏版", "新版", "修订版", "txt", "下载", "在线阅读", "免费阅读",
               "全集", "完整版", "完结版")
    if c.startswith(q):
        tail = c[len(q):]
        if tail and any(tail.startswith(s) for s in _SUFFIX) and len(tail) <= 10:
            return 0.95
        return 0.8
    if q.startswith(c):
        return 0.8
    common = sum(1 for ch in q if ch in c)
    return 0.6 * common / max(len(q), 1)


def find_book(title: str, *, author: str = "", sources=None, timeout: float = 20.0,
              min_score: float = 0.95):
    """按顺序找书：第一个「搜得到」的源就用到底。

    ★ 关键语义：某源搜到了但没有可用的目录/正文 → **继续下一个源**，不是报失败。
    ★★ 2026-10-09（用户：「新源要完美匹配的第一条才可以」）：
      `src.strict_first=True` 的源走**另一条判定**（见下面分支）——
      只认**站点搜索首条**且要求归一化后**完全相同**（_score==1.0），
      不再用「按作者/相似度重排 + 0.95 阈值」那套宽松办法。
    """
    for src in (sources or SOURCES):
        try:
            raw = search(src, title, timeout=timeout)
        except Exception as exc:
            log(f"  [{src.name}] 搜索失败: {type(exc).__name__}: {exc}")
            time.sleep(0.6)
            continue

        def _key(h, _a=author, _t=title):
            # 作者对得上（出现在该结果行的上下文里）优先，其次看书名相似度
            return (0 if (_a and _a in (h.get("ctx") or "")) else 1, -_score(_t, h["title"]))

        if getattr(src, "strict_first", False):
            # ★★★ 「完美匹配的第一条」闸门 ★★★
            #   · 用**站点返回的原始第一条**（raw[0]），不是重排后的第一条；
            #   · 必须 _score == 1.0（去标点/空格/大小写后完全相同）才用；
            #   · 否则**整源跳过**（连第二条也不看）→ 交给下一个源（久久 → 轻小说）。
            #   为什么要这么严：这一站同名的书一大堆（实测搜「赘婿」45 条），
            #   一旦按相似度挑，很容易把《赘婿风范》《我在赘婿文里…》当成目标书下下来。
            first = raw[0] if raw else None
            if not first:
                log(f"  [{src.name}] 搜索无结果 → 换下一个源")
                continue
            sc = _score(title, first["title"])
            # ★★ 2026-10-10：`site_exact` = 站点自己判定的"唯一命中"（实测 uukanshu：
            #   精确命中时直接 302 到书页，没有结果列表）。这种必须放行 ——
            #   那站是**繁體**，用户输简体时 `_score` 归一化后照样不相等
            #   （冬日重現 vs 冬日重现），不放行就等于永远用不了它。
            if sc < 1.0 and not first.get("site_exact"):
                log(f"  [{src.name}] 首条不是完美匹配（首条《{first['title']}》"
                    f"≠《{title}》，相似度 {sc:.2f}）→ 跳过本源")
                continue
            log(f"  [{src.name}] 首条完美匹配：《{first['title']}》"
                + ("（站点判定唯一命中）" if first.get("site_exact") and sc < 1.0 else ""))
            hits = [first]
        else:
            hits = sorted(raw, key=_key)
            if not hits or _score(title, hits[0]["title"]) < min_score:
                continue

        for cand in hits[:5]:
            # ★ site_exact（站点判定的唯一命中）**不受相似度闸门约束** ——
            #   繁體站上用户输简体，_score 天生很低，但这本书就是它。
            if _score(title, cand["title"]) < min_score and not cand.get("site_exact"):
                break
            try:
                chapters = toc(src, cand["url"], timeout=timeout)
            except Exception as exc:
                log(f"  [{src.name}] 目录页失败 {cand['url']}: {type(exc).__name__}")
                continue
            if len(chapters) < 2:
                log(f"  [{src.name}] 目录只有 {len(chapters)} 章 → 换下一个源")
                continue
            log(f"  ✅ 选用 [{src.name}] 《{cand['title']}》 共 {len(chapters)} 章")
            return {"source": src, "book": cand, "chapters": chapters}
    return None


def download(title: str, *, out_dir: str = "", limit: int = 0, sources=None, author: str = "",
             timeout: float = 20.0, gap: float = 0.025, resume: bool = True,
             workers: int = 16, txt_url: str = "", linovelib: bool = True,
             ln_auto_cookie: bool = False, ln_cookie_file: str = "", ln_base: str = "",
             ln_workers: int = 0, ln_pick_only: bool = False,
             ask_jx: bool = False, ordinary_first: bool = False) -> dict:
    """按顺序逐源尝试：kind='txt' 的源整本一次拿，其余走逐章。

    ★ 顺序即优先级（2026-10-09 用户拍板）：**笔趣阁爬虫源 → 久久小说网 → 轻小说源**。
      一个源失败（搜不到 / 首条不完美匹配 / 目录空 / 直链失效）就**继续下一个**，不是整体报失败。
    ★★ 2026-09-30（用户拍板）：**顺序改成"先网文、再轻小说"** ——
      · 删掉了原来跑在网文**前面**的那次轻小说预检（`index_only=True`）：它哪怕只查本地目录，
        也要单独起一趟解析，用户实测"网文的速度会慢很多"；
      · 现在直接进网文源；**网文源全没命中**才用轻小说（站内搜索 + 必应反查）兜一次。
      · 网文命中阈值同时收严到 `min_score=0.95`（见 find_book）——宁可漏，不可抓同名同人。
    ★★ 2026-10-01（接回"模块化"后的 linovelib.py）：`ln_*` 三个参数是**轻小说那一层的透传开关**
      （主进程 → 本脚本 → linovelib.py），本来一条都传不下去 —— 见 download_linovelib()。
    ★★ 2026-10-10：新增 `ask_jx` / `ordinary_first` 两个开关，实现"先走**精校源（UU看書）**探测；
      精校源有这本书就弹窗问要不要用精校源下载"这条交互。**精校源 = UU看書**（干净、慢、要 VPN），
      **普通源 = 笔趣阁·爬虫源**（快）。两者由主进程按界面的选择传下来：
      `--ask-jx` / `--ordinary-first`。
    """
    srcs = list(sources or SOURCES)

    # ★★★ 2026-10-10（用户：「先走 uu，如果 uu 有就弹出弹窗提示是否要下载精校源」）★★★
    #   `ask_jx`   = **只探测不下载**：拿 UU看書 去搜；搜到了就把书名/章数交回界面，
    #                由界面弹「要不要下精校源（笔趣阁·爬虫源）」—— 探测完就返回，不下正文。
    #   `ordinary_first` = 用户选了**普通源** → 把笔趣阁·爬虫源挪到最前，其余源仍作为兜底。
    #   ★ 源的身份（用户 2026-10-10 当面纠正）：**精校源 = UU看書**（正文干净，但走浏览器、
    #     非常慢、会多次失败重试、要挂 VPN）；**普通源 = 笔趣阁·爬虫源**（纯 HTTP，一分钟级别）。
    if ordinary_first:
        pt = [s for s in srcs if getattr(s, "crawler", "") == "xbiquzw"]
        if pt:
            srcs = pt + [s for s in srcs if s not in pt]
            log("  🔀 已按选择把【普通源·笔趣阁爬虫】排到最前（后面源仍作兜底）")
    if ask_jx:
        uu = next((s for s in srcs if getattr(s, "crawler", "") == "uukanshu"), None)
        if uu is None:
            ask_jx = False
        else:
            log(f"  🔎 先用 [{uu.name}] 探一下有没有这本书（只查不下）…")
            try:
                found = find_book(title, author=author, sources=[uu], timeout=timeout)
            except Exception as exc:  # noqa: BLE001
                log(f"  ⚠️ 探测失败：{type(exc).__name__}: {str(exc)[:80]} → 直接按原顺序下载")
                found = None
            finally:
                _crawler_close(uu)
            if found:
                n_ch = len(found["chapters"])
                log(f"  ✅ 精校源（UU看書）有《{found['book']['title']}》（{n_ch} 章）→ 交界面问要不要用精校源")
                return {"ok": False, "needJingxiao": True,
                        "title": found["book"]["title"], "chapters": n_ch,
                        "source": uu.name, "query": title}
            log("  ⚪ UU看書 没有这本书 → 按原顺序继续（会用其它源兜底）")

    if author:
        log(f"  ℹ️ 作者提示：{author}（用于同名书消歧）")
    for idx, src in enumerate(srcs, 1):
        kind = getattr(src, "kind", "chapter")
        log(f"\n  ── [{idx}/{len(srcs)}] {src.name}"
            + ("（TXT 直链型：整本一次拿）" if kind == "txt"
               else ("（站专用爬虫：逐章拿）" if kind == "crawler" else "（逐章型）")))
        if kind == "txt":
            res = download_via_txt(src, title, out_dir=out_dir, author=author,
                                   timeout=timeout, resume=resume, txt_url=txt_url)
            if res and res.get("ok"):
                return res
            continue
        found = find_book(title, author=author, sources=[src], timeout=timeout)
        if not found:
            continue
        try:
            return _download_chapters(found, out_dir=out_dir, limit=limit, gap=gap,
                                      resume=resume, workers=workers, timeout=timeout)
        finally:
            # ★★ 2026-10-10：浏览器型源用完**必须收摊** —— 否则那几个 headless Edge
            #   会一直挂着（用户看不到窗口，但进程/内存一直在）。
            _crawler_close(src)

    # ★ 网文源全没命中 → 再给轻小说一次机会（这次允许站内搜索 + 必应反查）
    if linovelib:
        log("\n  ── [轻小说] 网文源（爬虫源 / 久久）都没命中，再用哔哩轻小说站内搜索试一次")
        ln = download_linovelib(title, out_dir=out_dir, timeout=max(timeout, 25.0), index_only=False,
                                auto_cookie=ln_auto_cookie, cookie_file=ln_cookie_file,
                                base=ln_base, workers=ln_workers, pick_only=ln_pick_only)
        if ln:
            return ln

    return {"ok": False, "error": f"{len(srcs)} 个网文源和轻小说源都没找到这本书",
            "notfound": True}


# ══════════════════════════════════════════════════════════════════════════
# ★ 轻小说源（2026-09-30 新增）：哔哩轻小说 —— 命中后**不走 TXT**，直接出带封面的 EPUB
# ══════════════════════════════════════════════════════════════════════════
# 为什么单独一路：轻小说的**插图是正文的一部分**（段-图-段交错），拼成 TXT 会把插图全丢掉，
# 再走 TXT→EPUB 只能得到「纯文字版」。所以判定为轻小说后，直接交给同目录的 `linovelib.py`
# 抓章节 + 插图、组装**带封面的 EPUB**（用户要求：直接出 epub、要带封面、丢进 export、
# 同步进传输框、不走 txt —— 落书库/入队由 main.js + 界面负责，这一层只管把书做出来）。
#
# 书名 → 书号 用 novelmeta 的 bilinovel 源（站内搜索 + 别名容错，2026-09-30 打通）：
#   · `index_only=True`：**只查本地目录索引**（5331 部，0.1s、零网络）——避免拖慢普通网文下载；
#   · `index_only=False`：放开站内搜索 + 必应反查（几秒级），只在网文源全失败后才用。
import subprocess  # noqa: E402  （本文件其余部分不需要它，放这儿更贴近用途）

_LN_VOL_RE = re.compile(r"第\s*([0-9０-９一二三四五六七八九十百千]+)\s*[卷冊册]")
_LN_CN_DIGIT = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
                "六": 6, "七": 7, "八": 8, "九": 9}
_LN_SOURCE = None          # BilinovelSource 单例（索引/缓存复用）


def _cn_number(text: str) -> int:
    """中文数字 → int（只处理到 99：'九'→9、'十二'→12、'二十'→20、'二十三'→23）。"""
    if "十" in text:
        head, _, tail = text.partition("十")
        tens = _LN_CN_DIGIT.get(head, 1) if head else 1
        ones = _LN_CN_DIGIT.get(tail, 0) if tail else 0
        return tens * 10 + ones
    return _LN_CN_DIGIT.get(text, 0) if len(text) == 1 else 0


def _parse_volume(title: str):
    """从书名里抠卷号 → (卷号, 去掉卷号的干净书名)。

    实测用户就是这么写的：「败犬女主太多了 第九卷」「败北女角太多了 第9卷」。
    没写卷号 → 卷号 1（轻小说按卷出，默认第 1 卷；要别的卷去【轻小说】弹窗勾选）。
    """
    raw_title = (title or "").strip()
    m = _LN_VOL_RE.search(raw_title)
    if not m:
        return 1, raw_title
    digits = m.group(1).translate(str.maketrans("０１２３４５６７８９", "0123456789"))
    vol = int(digits) if digits.isdigit() else _cn_number(digits)
    clean = (raw_title[:m.start()] + raw_title[m.end():]).strip(" ·・-—–,，、")
    return (vol or 1), (clean or raw_title)


def _linovelib_lookup(title: str, *, timeout: float = 15.0, index_only: bool = False):
    """书名 +（可选）卷号 → 哔哩轻小说的书号。查不到返回 None。

    ★ 2026-09-30 改用 novelmeta 的统一解析 `resolve_bilinovel()`：
      精确命中查本地目录（0.1s）→ 否则**按站点相关度顺序**取第一条
      （修「无职转生」被外传《蛇足篇》抢走的 bug：索引里没有相关度，站点有）。
      `index_only=True` 只做精确命中（给"先快速判一下"的预检用，不拖慢普通网文）。
    """
    vol, clean = _parse_volume(title)
    log(f"  🔎 [轻小说] 查《{clean}》"
        + (f" 第 {vol} 卷" if vol > 1 else "")
        + ("（只查本地目录·精确命中）" if index_only else "（本地目录 + 站内搜索）"))
    try:
        from novelmeta.sources import resolve_bilinovel
    except ImportError as exc:
        log(f"  ⚠️ 轻小说源不可用（novelmeta 导入失败）：{exc}")
        return None
    try:
        # ★★★ 2026-09-30（用户要求「这一步不要了，不然网文的速度会慢很多」）：
        #   这里是"先快速判一下是不是轻小说"的预检，必须**真的只查本地目录**：
        #     · `allow_index_build=False` → 本地索引缓存不在时**立刻返回**，
        #       绝不再现爬那 178 页目录（几十分钟级别的坑，用户实测网文被拖死）；
        #     · `exact_only=True` → 只做精确命中，**不做站内搜索**（也不走必应）。
        #   代价：本地索引没缓存时，这一步永远判不出轻小说 —— 这正是用户要的
        #   （轻小说请用【轻小说】按钮，或先预热索引再从这里下）。
        #
        # ★★★ 2026-10-01（接回模块化 linovelib.py 时发现的**真接口分歧**）：
        #   `allow_index_build` 这个参数**只有随包的那份 novelmeta 有** ——
        #     · C:\work\epubui\EasyPub-New\novelmeta\sources.py : 有（def … allow_index_build=True）
        #     · C:\work\novelmeta\novelmeta\sources.py        : **没有**（只有 timeout/exact_only/allow_bing）
        #   两份是同一个库的两个副本，签名已经漂了。而 `novelmeta` 取哪一份**由 sys.path 顺序决定**
        #   （见本文件顶部：`_HERE` 在前，所以开发/打包都命中随包那份；但 linovelib.py 是从
        #    `C:\work\novelmeta` 那份开发出来的，它的注释里就记着"传了直接 TypeError"）。
        #   一旦哪次把开发主仓库同步进来 / NOVELMETA_HOME 指过去，这里就会 TypeError ——
        #   而它**被下面的 except Exception 吞掉**，表现为"轻小说源永远查不到"，
        #   用户只会看到"网文源都没命中"，很难联想到是签名问题。
        #   所以这里按**真实签名**调用：有就用，没有就走老签名。
        try:
            cand = resolve_bilinovel(clean, timeout=max(timeout, 15.0),
                                     exact_only=index_only, allow_bing=not index_only,
                                     allow_index_build=False)
        except TypeError:
            log("  ℹ️ [轻小说] 这份 novelmeta 没有 allow_index_build 参数 → 用老签名调用")
            cand = resolve_bilinovel(clean, timeout=max(timeout, 15.0),
                                     exact_only=index_only, allow_bing=not index_only)
    except Exception as exc:  # noqa: BLE001 —— 轻小说只是"多一层"，绝不能拖垮网文下载
        log(f"  ⚠️ [轻小说] 查询异常：{type(exc).__name__}: {str(exc)[:80]}")
        return None
    if cand is None or not cand.extra.get("novelId"):
        return None
    log(f"  ✅ [轻小说] 命中：《{cand.title}》 书号={cand.extra['novelId']}"
        f"（{cand.extra.get('matchedBy') or 'title'} / {cand.extra.get('resolvedBy') or ''}）")
    return {"novelId": cand.extra["novelId"], "title": cand.title, "author": cand.author,
            "vol": vol, "query": clean}


_LN_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)   # Windows：别弹控制台黑框


def _ln_popen(args):
    """跑一次 linovelib.py，返回合并后的输出（超时/异常直接抛给调用方）。

    ★ `creationflags=CREATE_NO_WINDOW`（Windows）＝别弹控制台黑框。
      这里**不能写 `windowsHide=True`** —— 那是 Node.js `child_process` 的选项，
      Python `Popen` 没有这个参数，传了直接 TypeError（2026-10-01 实测踩到）。
    """
    proc = subprocess.run(args, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=30 * 60,
                          creationflags=_LN_NO_WINDOW)
    return (proc.stdout or "") + "\n" + (proc.stderr or "")


def _ln_relay(out: str, vol_label: str) -> dict | None:
    """把 linovelib.py 的输出转发给界面，并取出末行 @@RESULT。

    ★ 2026-10-01：`stage:"note"` 的**纯文本提示**必须原样转发 —— 见下面的注释（原来的 bug）。
    """
    res = None
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("@@RESULT "):
            try:
                res = json.loads(line[len("@@RESULT "):])
            except Exception:
                res = None
            continue
        if line.startswith("@@PROGRESS "):
            # ★ 2026-09-30：**把进度转发给界面** —— main.js 会把本进程 stdout 逐行转成
            #   `download-log`，index.html 解析 `@@PROGRESS` 调 dnSetBar(done,total,extra)
            #   驱动进度条。不转发的话，界面就只剩那个"假动画"（15 章/秒、总数按 800 估）
            #   卡在 95%，看起来像卡死（用户实测就是这个现象）。
            #   顺手注入 source 字段 → 进度条右侧显示「哔哩轻小说 第N卷 · 章节/插图」。
            # ★★ 2026-10-01 修：新脚本会发 `{"stage":"note","text":…}`（限流重试 / 自动续期 /
            #   绕过分卷走目录页）。这种载荷**没有 done/total**，以前被统一改写成
            #   source="… · 章节" → 界面显示成「正在下载 0 / 0 章 · 哔哩轻小说 第1卷 · 章节」，
            #   把一句有用的提示变成一条假进度。note 原样传下去。
            if '"note"' in line:
                print(line, flush=True)
                continue
            try:
                payload = json.loads(line[len("@@PROGRESS "):])
                stage = "插图" if payload.get("stage") == "images" else "章节"
                payload["source"] = f"{vol_label} · {stage}"
                print("@@PROGRESS " + json.dumps(payload, ensure_ascii=False), flush=True)
            except Exception:
                print(line, flush=True)
            continue
        if line:
            # 其余行（@@INFO / 📖《书名》 / [卷1] 第 N 卷 — 16 章 …）原样转给界面当状态文字
            print(line, flush=True)
    return res


def download_linovelib(title: str, *, out_dir: str = "", timeout: float = 15.0,
                       index_only: bool = False, as_json: bool = False,
                       auto_cookie: bool = False, cookie_file: str = "", base: str = "",
                       workers: int = 0, pick_only: bool = False):
    """书名命中轻小说 → 交给 linovelib.py 直接组装带封面的 EPUB。

    返回 {"ok":True,"kind":"epub",...}（已产出 EPUB）；不是轻小说/失败 → None（交给网文源）。
    ★ 注意：**不产出 TXT**。落书库（export\\）与入传输框由主进程/界面负责。

    ★★★ 2026-10-01（用户：「我搜无职转生他怎么不给我跳选搜索结果和选卷，直接就开始下了」）★★★
      `pick_only=True` 时**只解析、不下载** —— 命中轻小说就返回
      {"needPick": True, "novelId", "title", "author", "vol"}，
      由界面弹【轻小说】窗（书号回填 + 候选列表 + 分卷勾选）让用户自己挑。
      为什么要这样：2026-09-30 删掉的是**跑在网文前面的那次预检**（站点搜索 5~15s，拖慢网文），
      「下载小说」就变成了静默直下、没有挑选环节。现在把挑选环节接回来，但**仍然只在
      网文源全失败之后**才查 —— 网文速度一点不受影响。

    ★★ 2026-10-01（接回"模块化"后的 linovelib.py）—— 这里是**唯一**调 linovelib.py 的地方，
      新脚本的三个开关以前一个都传不下去，等于白做：
        · `--auto-cookie`   ：cf_clearance 失效自动续期（Selenium+Edge）。不传的话，证一过期
                              这条路就永远报「正文被站方截断」，用户只能自己去浏览器重拷 Cookie；
        · `--cookie-file`   ：续期写哪个文件 / 读哪个文件。不传就用**脚本目录**，
                              打包后那是 Program Files（不可写）→ 续期必然失败；
        · `--base`          ：站点域名。linovelib.com 实测不必带证，bilinovel.com **必须**带证。
      另外两处顺手修掉的老问题：
        · `--vol N` 在"书被版权方屏蔽、卷册列表为空"的书上会 `指定的分卷不存在` 直接失败
          （新脚本这时会改走 /catalog 目录页、只有一个虚拟"全书"卷 = 下标 1）。
          现在卷号对不上就**去掉 --vol 重试一次**，把新脚本的"绕过分卷"能力真正用上；
        · 没有 `windowsHide`/`CREATE_NO_WINDOW` → 每次都会闪一个控制台黑框。
    """
    hit = _linovelib_lookup(title, timeout=timeout, index_only=index_only)
    if not hit:
        return None
    script = os.path.join(_HERE, "linovelib.py")
    if not os.path.exists(script):
        log("  ⚠️ [轻小说] 找不到 linovelib.py，跳过")
        return None
    # ★ 2026-10-01：只解析不下载 —— 把"这是轻小说 + 书号"交回界面，让用户自己挑候选/勾卷
    if pick_only:
        log(f"  🎯 [轻小说] 命中《{hit['title']}》书号={hit['novelId']}"
            f" → 交给界面选卷（不直接下载）")
        return {"ok": False, "needPick": True, "novelId": str(hit["novelId"]),
                "title": hit["title"], "author": hit.get("author") or "",
                "vol": hit["vol"], "query": hit.get("query") or title}
    args = [sys.executable, "-X", "utf8", script, "--get", str(hit["novelId"]),
            "--vol", str(hit["vol"]), "--json",
            "--out", out_dir or os.path.join(_HERE, "download")]
    if cookie_file:
        args += ["--cookie-file", cookie_file]
    if base:
        args += ["--base", base]
    if auto_cookie:
        args.append("--auto-cookie")
    if workers:
        args += ["--workers", str(max(1, min(32, int(workers))))]
    log(f"  📥 [轻小说] 开始组装 EPUB：《{hit['title']}》 第 {hit['vol']} 卷"
        + ("（封面 = 正文第一张插图）" if True else ""))
    t0 = time.time()
    try:
        out = _ln_popen(args)
    except Exception as exc:  # noqa: BLE001
        log(f"  ⚠️ [轻小说] 组装失败：{type(exc).__name__}: {str(exc)[:80]}")
        return None
    vol_label = f"哔哩轻小说 第{hit['vol']}卷"
    res = _ln_relay(out, vol_label)

    # ★ 卷号对不上（屏蔽书 / 目录页直取）→ 去掉 --vol 再来一次：新脚本这时会走目录页拿全书
    if (not res or not res.get("success")) and "--vol" in args \
            and res and "分卷不存在" in str(res.get("error") or ""):
        log("  ↩️ [轻小说] 指定卷号对不上（这本书的卷册被屏蔽了）→ 去掉 --vol 走目录页重试")
        i = args.index("--vol")
        args2 = args[:i] + args[i + 2:]
        try:
            out = _ln_popen(args2)
        except Exception as exc:  # noqa: BLE001
            log(f"  ⚠️ [轻小说] 重试失败：{type(exc).__name__}: {str(exc)[:80]}")
            return None
        res = _ln_relay(out, "哔哩轻小说 全书")

    if not res or not res.get("success") or not res.get("file"):
        log("  ⚠️ [轻小说] 没产出 EPUB" + (f"：{res.get('error')}" if res else ""))
        return None
    return {
        "ok": True, "kind": "epub",
        "file": res["file"],
        "source": "哔哩轻小说",
        "book": res.get("title") or hit["title"],
        "author": res.get("author") or hit.get("author") or "",
        "vol": hit["vol"],
        "cover": res.get("cover") or "",
        "chapters": res.get("chapters"),
        "images": res.get("images"),
        "words": res.get("words"),
        "failed": res.get("failed") or [],
        "elapsed": round(time.time() - t0, 1),
    }


def _download_chapters(found, *, out_dir="", limit=0, gap=0.025, resume=True,
                       workers=16, timeout: float = 20.0) -> dict:
    """逐章型源的下载主体（从旧 download() 原样搬来，**逻辑一行未改**）。

    ★★ 2026-10-10 两处新增（都是为了 uukanshu 这种**浏览器型源**）：
      · `src.max_workers`：浏览器源一个会话 = 一个真 Edge，开 16 个等于自杀 →
        这里把 `--workers` 夹到源声明的上限（uukanshu=2）。
      · **站点封锁熔断**：源抛出带 `site_blocked=True` 的异常（CF 硬封）时，
        立刻停掉整本，不再一章一撞墙 —— 实测教训：被封了还硬抓会被封更久。
        已抓的章节都在 `.cache` 里，封锁解除后重跑即可续传。
    """
    src, book, chapters = found["source"], found["book"], found["chapters"]
    if limit:
        chapters = chapters[:limit]
    cap = getattr(src, "max_workers", 0) or 0
    if cap and workers > cap:
        log(f"    ⚙️ [{src.name}] 是浏览器型源 → 并发从 {workers} 降到 {cap}（源声明上限）")
        workers = cap

    out_dir = out_dir or os.path.join(_HERE, "download")
    safe = re.sub(r'[\\/:*?"<>|]', "_", book["title"]).strip() or "book"
    cache_dir = os.path.join(out_dir, safe, ".cache")
    os.makedirs(cache_dir, exist_ok=True)

    parts, failed = [], []
    t0 = time.time()
    results = {}                        # 序号 -> 正文
    pending = []
    for i, ch in enumerate(chapters, 1):
        cache_file = os.path.join(cache_dir, f"{i:05d}.txt")
        if resume and os.path.exists(cache_file) and os.path.getsize(cache_file) > 0:
            with open(cache_file, encoding="utf-8") as fh:
                results[i] = fh.read()
        else:
            pending.append((i, ch, cache_file))
    cached = len(results)      # ★ 已命中缓存的章数（上次下到一半的续传）
    log(f"    📚 《{book['title']}》{len(chapters)} 章｜缓存命中 {cached}｜待抓 {len(pending)}")
    # ★ 进度条分母必须是**全书章数**，起点=缓存已命中数 → 条长就是真实的"已下载/全书"
    log("@@PROGRESS " + json.dumps({"done": cached, "total": len(chapters), "cached": cached,
                                    "failed": 0, "source": src.name}, ensure_ascii=False))

    limiter = RateLimiter(gap)
    # ★ 不print"预计耗时"：实测 8 线程下 20 章只要 1 秒，任何静态估算都会离谱。
    #   改成在进度行里报**实测速率**（自校准，越跑越准）。
    log(f"    ⚙️ {min(workers, 32)} 线程并发（同源内）｜全局限速 {gap}s/请求")

    # ★★ 2026-10-10：站点封锁熔断标志（源抛 `site_blocked=True` 的异常时置位）
    site_down = threading.Event()

    def _one(item):
        idx, ch, cache_file = item
        if site_down.is_set():
            return idx, "", f"{ch['name']}（站点封锁，已跳过）"
        err = None
        for attempt in (1, 2, 3):
            limiter.wait()
            try:
                text = chapter(src, ch["url"], timeout=timeout)
                if text:                      # ★ 成功不再 sleep：节流已由全局限速器负责
                    with open(cache_file, "w", encoding="utf-8") as fh:
                        fh.write(text)
                    return idx, text, None
            except Exception as exc:
                # ★ 站点封锁（CF 硬封）→ 立刻置位熔断，剩下的章节不再撞墙。
                if getattr(exc, "site_blocked", False):
                    if not site_down.is_set():
                        log(f"    ⛔ [{src.name}] {str(exc)[:110]}")
                        log("    ⛔ 已停止抓取（继续只会让封锁更久）；已抓章节都在缓存里，"
                            "封锁解除后重跑可续传")
                        site_down.set()
                    return idx, "", f"{ch['name']}（站点封锁）"
                # ★★ 铁律：**严禁吞错**。就是这里原来的 `except Exception: pass`
                #   把 `NameError: name 'timeout' is not defined` 吞了，导致 344 章全失败
                #   却连一行原因都不显示。现在把最后一次异常带出去。
                err = exc
            time.sleep(0.3 * attempt * 2)     # 只有失败才退避
        _why = f"{type(err).__name__}: {str(err)[:70]}" if err else "无异常"
        return idx, "", f"{ch['name']}（{_why}）"

    # ★ 同一本书、同一个源内并发（**绝不跨源**，所以正文不会串味）
    done = 0
    with ThreadPoolExecutor(max_workers=max(1, min(workers, 32))) as pool:
        for idx, text, failed_name in pool.map(_one, pending):
            done += 1
            if text:
                results[idx] = text
            elif site_down.is_set():
                pass                       # 封锁导致的"失败"不刷屏
            else:
                failed.append(failed_name)
                log(f"    ❌ 第 {idx} 章最终失败: {failed_name}")
            # ★ 逐章发结构化进度：界面进度条 = 已下载章数 / 全书章数，真实推进（不再每 5 章才发）
            log("@@PROGRESS " + json.dumps(
                {"done": len(results), "total": len(chapters), "cached": cached,
                 "failed": len(failed), "source": src.name}, ensure_ascii=False))
            if pending and (done % 5 == 0 or done == len(pending)):
                rate = done / max(time.time() - t0, 0.001)
                log(f"    进度 {len(results)}/{len(chapters)}  "
                    f"({len(results) * 100 // max(len(chapters), 1)}%)  {time.time() - t0:.0f}s  "
                    f"{rate:.1f} 章/秒  ❌{len(failed)}")
                # ★ 结构化进度行：界面靠它画进度条（@@ 前缀不会被 main.js 的 JSON 契约误扫）
                log("@@PROGRESS " + json.dumps(
                    {"done": done, "total": len(pending), "ok": len(results),
                     "failed": len(failed), "source": src.name}, ensure_ascii=False))

    for i in sorted(results):
        parts.append((chapters[i - 1]["name"], results[i]))

    # ★★ 2026-10-10：被站点封锁 → **不当成功**（哪怕已经抓到一部分）。
    #   已抓的每一章都在 `.cache` 里，封锁解除后重跑会直接续传，一个字不丢。
    if site_down.is_set():
        return {"ok": False, "blocked": True, "source": src.name,
                "chapters": len(results), "failed": failed,
                "error": f"[{src.name}] 站点把本机判定为机器人并封锁，已停止抓取"
                         f"（已抓到 {len(results)} 章，都在缓存里，稍后重跑可续传）"}
    if not parts:
        return {"ok": False, "error": "所有章节都抓取失败", "source": src.name}
    txt_path = os.path.join(out_dir, f"{safe}.txt")
    with open(txt_path, "w", encoding="utf-8") as fh:
        # ★ 2026-10-10（用户：「来源：… 这一句话删掉」→ 选了"以后所有源都去掉"）：
        #   TXT 头只留书名，不再写 `来源：<源名>` —— 正文里那行是阅读噪音；
        #   出处仍然打在应用日志里（`✅ 选用 [源名]《书名》`），要查随时能查。
        fh.write(f"{book['title']}\n\n")
        for name, text in parts:
            fh.write(f"\n\n{name}\n\n{text}\n")
    return {"ok": True, "file": txt_path, "source": src.name, "book": book["title"],
            "chapters": len(parts), "failed": failed, "elapsed": round(time.time() - t0, 1)}


# ══════════════════════════════════════════════════════════════════════════
# CLI（Phase 2 的验收就靠它）
# ══════════════════════════════════════════════════════════════════════════
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="EasyPub 2 书源聚合下载（纯 HTTP）")
    ap.add_argument("title", nargs="?", help="书名")
    # ★ main.js 的 download-novel 契约：argv = ['-X','utf8',script,
    #   '--download-dir=<dir>', <书名>, '--author=<作者>']；它靠"下载目录里出现新 .txt"
    #   判定成功，靠 stdout 的 {"notFound":true}/{"success":false} 判定失败。
    ap.add_argument("--download-dir", default="", help="下载目录（main.js 传的；等同 --out）")
    ap.add_argument("--author", default="", help="作者（用于同名书消歧，可选）")
    ap.add_argument("--search", action="store_true", help="只搜索，不下载（打印每个源命中数）")
    ap.add_argument("--probe", action="store_true", help="对每个源打印目录/正文样本")
    ap.add_argument("--limit", type=int, default=0, help="只抓前 N 章（0=全部）")
    ap.add_argument("--out", default="", help="输出目录（默认 ./download）")
    ap.add_argument("--source", default="", help="只用一个源（按名称匹配）")
    ap.add_argument("--gap", type=float, default=0.025,
                    help="**全局**最小请求间隔秒（默认 0.025 ≈ 40 请求/秒上限）")
    ap.add_argument("--workers", type=int, default=16,
                    help="同源内并发线程数（1~32，默认 16；绝不跨源）")
    ap.add_argument("--no-resume", action="store_true", help="忽略缓存重抓")
    ap.add_argument("--txt-url", default="",
                    help="直接给 TXT 下载页URL（如 /txt/dl-14-36568.html），跳过搜索")
    ap.add_argument("--url", default="", help="已知书籍详情页 URL（用于不支持搜索的源）")
    # ★★ 2026-10-01：轻小说源（linovelib.py）的三个透传开关 —— 主进程必须能一路传到底，
    #   否则新脚本加的「自动续 Cookie / 指定 Cookie 文件 / 切域名」在 App 里全用不上。
    ap.add_argument("--auto-cookie", action="store_true",
                    help="轻小说源：cf_clearance 过期时自动用 Selenium+Edge 续期（弹一次 Edge ~10s）")
    ap.add_argument("--cookie-file", default="",
                    help="轻小说源：cookies.txt 路径（主进程传 userData 下的那个；"
                         "不传则用 linovelib.py 脚本目录，打包后不可写）")
    ap.add_argument("--ln-base", default="",
                    help="轻小说源：站点域名（默认 linovelib.py 自己的 https://www.linovelib.com；"
                         "被限流/空壳时可切 https://www.bilinovel.com）")
    # ★ 2026-10-01（用户：「我搜无职转生他怎么不给我跳选搜索结果和选卷，直接就开始下了」）：
    #   命中轻小说时**不下载**，只把书号交回界面 → 界面弹【轻小说】窗让用户挑候选/勾卷。
    #   仍然只在"网文源全失败"之后才查，所以网文速度不受影响。
    ap.add_argument("--ln-picker", action="store_true",
                    help="轻小说命中时只回书号（needPick），不直接下载 —— 让界面弹选卷窗")
    # ★★ 2026-10-10（用户：「先走 uu，如果 uu 有就弹出弹窗提示是否要下载精校源」）：
    #   --ask-jx        只探测（精校源 UU看書 搜得到就回 {"needJingxiao":true,…}，不下正文）
    #   --ordinary-first 用户选了普通源 → 笔趣阁·爬虫源排最前（其余源仍兜底）
    ap.add_argument("--ask-jx", action="store_true",
                    help="先用精校源（UU看書）探测；有这本书就回 needJingxiao 交界面问要不要下精校源")
    ap.add_argument("--ordinary-first", action="store_true",
                    help="把【普通源·笔趣阁爬虫】排到最前（其余源仍作兜底）")
    args = ap.parse_args(argv)
    out_dir = args.download_dir or args.out

    picked = None
    if args.source:
        picked = [s for s in SOURCES if args.source in s.name]
        if not picked:
            print(f"没有匹配的源：{args.source}", file=sys.stderr)
            return 2

    if args.search:
        if not args.title:
            print("--search 需要书名", file=sys.stderr)
            return 2
        for src in (picked or SOURCES):
            t0 = time.time()
            try:
                hits = search(src, args.title)
            except Exception as exc:
                print(f"  [{src.name}] 失败 {type(exc).__name__}: {exc}", file=sys.stderr)
                continue
            for h in hits[:5]:
                print(f"    {h['title']}   {h['url']}")
            print(f"  [{src.name}] {len(hits)} 条  {time.time() - t0:.1f}s", file=sys.stderr)
        return 0

    if not args.title and not args.url:
        ap.print_help()
        return 2

    if args.url:                        # 直接给详情页 URL（给不支持搜索的源用）
        src = (picked or SOURCES)[-1]
        chapters = toc(src, args.url)
        print(json.dumps({"ok": bool(chapters), "source": src.name,
                          "chapters": len(chapters),
                          "first": chapters[:2]}, ensure_ascii=False, indent=2))
        return 0 if chapters else 1

    if args.probe:
        for src in (picked or SOURCES):
            print(f"===== {src.name}  ({src.note})", file=sys.stderr)
            try:
                hits = search(src, args.title)
                if not hits:
                    continue
                chapters = toc(src, hits[0]["url"])
                print(f"  目录 {len(chapters)} 章，前2: {[c['name'] for c in chapters[:2]]}",
                      file=sys.stderr)
                if chapters:
                    text = chapter(src, chapters[0]["url"])
                    ads = sum(1 for p in AD_PATTERNS if re.search(p, text, re.I))
                    print(f"  首章 {len(text)} 字 / 广告命中 {ads} / 开头: {text[:60]!r}",
                          file=sys.stderr)
            except Exception as exc:
                print(f"  FAIL {type(exc).__name__}: {exc}", file=sys.stderr)
        return 0

    result = download(args.title, out_dir=out_dir, limit=args.limit, sources=picked,
                      author=args.author, gap=args.gap, resume=not args.no_resume,
                      workers=args.workers, txt_url=args.txt_url,
                      # ★ 显式指定 --source 时关掉轻小说源，方便单源调试（否则会被轻小说抢答）
                      linovelib=not bool(picked),
                      # ★ 2026-10-01：轻小说那一层的透传开关（→ linovelib.py）
                      ln_auto_cookie=args.auto_cookie, ln_cookie_file=args.cookie_file,
                      ln_base=args.ln_base, ln_workers=args.workers,
                      ln_pick_only=args.ln_picker,
                      # ★ 2026-10-10：精校源那条交互的两个开关
                      ask_jx=args.ask_jx, ordinary_first=args.ordinary_first)
    # ★ main.js 的 stdout 契约（它逐行扫 JSON 行）：
    #   未找到 → {"notFound": true, "error": ...}
    #   出错   → {"success": false, "error": ...}
    #   成功   → {"success": true, "file": ...}（main.js 实际靠"下载目录里出现新 .txt"判定，
    #            这里照样给一行，便于日志排查）
    # ★ 2026-10-10 新增：`--ask-jx` 探测到 UU看書 有这本书 → {"needJingxiao": true, …}
    #   （**不是失败**：界面据此弹「要不要改用精校源」；和 needPick 一样要排在失败分支之前）
    if result.get("needJingxiao"):
        print(json.dumps({"needJingxiao": True,
                          "title": result.get("title"),
                          "chapters": result.get("chapters"),
                          "source": result.get("source"),
                          "query": result.get("query") or result.get("title")},
                         ensure_ascii=False))
        return 0
    # ★ 2026-10-01 新增：`--ln-picker` 命中轻小说 → {"needPick": true, "novelId": ...}
    #   （**不是失败**，界面据此弹【轻小说】选卷窗；所以要排在下面的失败分支之前）
    if result.get("needPick"):
        print(json.dumps({"needPick": True, "novelId": result.get("novelId"),
                          "title": result.get("title"), "author": result.get("author"),
                          "vol": result.get("vol"),
                          # ★ 回填界面输入框要用**书名**，不能用书号 —— 界面里"输纯数字"
                          #   会被当成"按书号查"，直接跳过"搜索结果候选列表"这一步。
                          "query": result.get("query") or result.get("title")},
                         ensure_ascii=False))
        return 0
    if not result.get("ok"):
        if result.get("blocked"):
            # ★ 站点封锁：给界面一句人话（不是"没找到"，也不是普通下载失败）
            print(json.dumps({"success": False, "blocked": True,
                              "error": result.get("error") or "站点封锁，已停止"}, ensure_ascii=False))
        elif result.get("notfound"):
            print(json.dumps({"notFound": True, "error": result.get("error") or "未找到本书"},
                             ensure_ascii=False))
        else:
            print(json.dumps({"success": False, "error": result.get("error") or "下载失败"},
                             ensure_ascii=False))
        return 1
    print(json.dumps({"success": True, "file": result.get("file"),
                      "source": result.get("source"), "chapters": result.get("chapters"),
                      "failed": result.get("failed") or []}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
