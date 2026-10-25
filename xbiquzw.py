#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""xbiquzw.py —— 笔趣阁系站（xbiquzw.net）**站专用爬虫源**：站内搜索 → 目录 → 正文（纯 TXT）。

★ 定位：它是 novel_dl.py 里的一个 `kind="crawler"` 源（不是独立 App、不出 EPUB）。
  · **站内搜索**：GET /modules/article/search.php?searchkey=<书名>
  · **无 Cookie**：全程不发 Cookie、不写任何 cookie 文件（站点也不下发 Set-Cookie）
  · **只要 TXT**：三件套（search / toc / chapter）产出的逐章文本，由 novel_dl.py 拼成
    `<书名>.txt`，后面接的还是 App 原有的「TXT →（可选）EPUB」链路。不做封面、不做插图。

═══════════════════════════════════════════════════════════════════════════════
■ 域名（2026-10-09 实测）
  · 用户指定的目标域：http://www.xbiquzw.net
  · **实测它全站 301 到 http://www.xbiquwk.com**（路径 + 查询原样保留）：
      HEAD http://www.xbiquzw.net/2_2386/1.html
        → 301  Location: http://www.xbiquwk.com/2_2386/1.html
      搜索 / 目录 / 正文 三种路径都验过，全部 301。
  · 但别名那台机器（172.247.240.51）**本身不稳**：
      - 串行 20 章：规范域 20/20 成功（avg 0.48s）｜别名域 18/20（avg 0.86s）
      - 并发 16 线程 × 48 章：规范域 48/48｜别名域 43/48（失败全是 ConnectionReset）
      - 某一刻连测 10 发：别名域 **10/10 全部连接被重置**（规范域同时 10/10 正常）
      - 还出现过一段 441B 的「跳广告站」JS 页（k8l.k8l70g.com）
  ⇒ 所以：**默认域名 = 用户指定的 xbiquzw.net**，一旦这条链失败/被重置，自动切
    xbiquwk.com 并**记住切换**（切回来之前不再反复试坏域名）。

■ 反爬（★ 本文件最关键的一段）
  2026-10-09 实测：**不带 `pop_enabled_t` 的请求会拿到一个 1133B 的 JS 页**（挑战页）：
      <script>(function(){function x(a,b){…XOR…}
        var u = x(e,k).split(',');            // XOR key = "123qwe"
        var L = +localStorage.getItem('redirect_last_time')||0, N = Date.now();
        if (L && N-L < 18e5) url = t(location.href);   // 30 分钟内：给当前 URL 加 pop_enabled_t 重新请求
        else { … url = u[i]; … }                       // 否则跳落地页（解出来是
                                                       //   bzh58.zhtzuj.cn / adh58.wppoey.cn 两个广告站）
        location.replace(url); })();</script>
  ⇒ 站点只认「URL 里带 `pop_enabled_t`」的请求是真人；浏览器走的就是"加个戳再重来"这一步。
  实测对照（同一分钟、同一 IP、交错发）：
      · 不带戳：反爬窗口内 连发 20 章 → 18 章是挑战页（正文 0 字）；另一轮 10/10 全是挑战页
      · 带戳  ：规范域 4 线程 24/24、8 线程 32/32、**16 线程 48/48**，正文均值 3817 字，48 章 1.6s
      · 戳的数值**不必新鲜**：用 -1 小时 / +1 小时的戳都能拿到正文（服务端只看"参数在不在"）
  ⇒ 本爬虫给**每个请求**都补 `pop_enabled_t=<毫秒>`；万一还是拿到挑战页，退避后换新戳重试，
    再不行换域名重试。这一条是"能不能稳定下到书"的分水岭：
    2026-10-09 00:33~00:38 那一段，不带戳的老路子（通用正则源）是 **0 条搜索 / 0 章目录**
    （静默降级成"这个源不命中"），带戳的这条路是 **48/48 全中**。

■ 站点结构（2026-10-09 原始响应留档，选择器都是照这个写的）
  · 搜索  GET /modules/article/search.php?searchkey=赘婿        → 21170B
      <table class="grid"> … 每个结果一行：
        <td class="odd"><a href="/2_2386/">赘婿</a></td>
        <td class="even"><a href="/2_2386/54545224.html">第一二六四章 …</a></td>
        <td class="odd">愤怒的香蕉</td><td class="even">10559K</td>
        <td class="odd">26-07-01</td><td class="even">连载</td>
  · 目录  GET /2_2386/                                        → 189346B / 1474 章（**一页出全**）
      <div id="list"><dl>
        <dd><a href="1190973.html" title="打发时间的精神食粮推荐（第一辑）">…</a></dd> …
      ⚠️ 章节 href 是**相对目录页**的（`1190973.html`，不是老的 `1.html`），必须 urljoin，
        而且**不能按数字排序**（老书写员 id 小、新章节 id 大，但不保证单调）→ 按出现顺序。
      ⚠️ 页面顶部还有一块"最新章节"，所以**必须限定在 `id="list"` 这个 div 内**取，否则章序错乱。
      （本站无分页：`下一页 / list_N.html / 共 N 页` 全 0 命中；仍留了多页保险，见 toc()。）
  · 正文  GET /2_2386/1191073.html                            → 17031B
      <h1>第八十八章 星星点灯</h1>
      <div id="content"><!--go-->
        &nbsp;&nbsp;&nbsp;&nbsp;第八十八章星星点灯（求月票）<br/><br/>
        &nbsp;&nbsp;&nbsp;&nbsp;“姑、姑爷，小婵……”<br/><br/> …
      ⚠️ 正文首行往往是**章节标题的复读**（还带"（求月票）"这种尾巴）→ 见 _drop_title_echo()。
      ⚠️ 正文页无分页（`本章未完 / 下一页 / _2.html` 全 0 命中），一章一请求。
  · 编码：Content-Type: text/html; charset=UTF-8（不用 GB18030 兜底，但留着也不亏）
  · HTTPS 不可用：两域 https 都是 404（548B），且别名域证书过期 → **只能 http**。

■ 实测数据（2026-10-09，本机）
  · 搜索「赘婿」36~45 条（站点自己会浮动）；目录 1474 章；单章正文 2753~4306 字
  · 带戳并发 16 线程：48 章 1.6s ≈ 30 章/秒；正文均值 3817 字；广告行命中 0
═══════════════════════════════════════════════════════════════════════════════

命令（独立调试用；App 里是 novel_dl.py 通过 `kind="crawler"` 调 search/toc/chapter）：
  py xbiquzw.py --search 赘婿
  py xbiquzw.py --info  http://www.xbiquzw.net/2_2386/
  py xbiquzw.py --toc   http://www.xbiquzw.net/2_2386/
  py xbiquzw.py --chapter http://www.xbiquzw.net/2_2386/1191073.html
  py xbiquzw.py --get   赘婿 --limit 5 --out C:\\work\\download     # 出 TXT（跟 App 同格式）
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

# ── 复用仓库自带的 HTTP 栈（IPv4-only / 超时 / 重试 / gzip / 跟随 301）──────────
#   ★ 不引 requests：和 novel_dl.py 保持一致，打包后不用带第三方依赖。
_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (os.environ.get("NOVELMETA_HOME") or "", _HERE):
    if _p and os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)
os.environ.setdefault("NOVELMETA_IPV4_ONLY", "1")
try:
    from novelmeta.net import FetchError, fetch
except ImportError as exc:      # 打包/路径异常时给一句人话
    print(json.dumps({"ok": False, "error": f"无法导入 novelmeta.net.fetch：{exc}"},
                     ensure_ascii=False))
    raise SystemExit(1)

NAME = "笔趣阁·爬虫源"
#: 用户指定的目标域（可用环境变量换，便于排查）
BASE = (os.environ.get("XBIQUZW_BASE") or "http://www.xbiquzw.net").rstrip("/")
#: 别名域 301 的落地域 —— 别名的连接质量实测更差，所以拿它做**兜底**而不是主域
FALLBACK_BASE = (os.environ.get("XBIQUZW_FALLBACK") or "http://www.xbiquwk.com").rstrip("/")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
#: ★ 不发 Cookie（用户要求"无 cookie"）；Referer 每次都带上（本站正文/目录都吃 Referer）
_HDR = {"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Encoding": "gzip, deflate", "Connection": "close"}

#: 反爬挑战页的指纹（1133B 那一段 JS）。正文/目录/搜索页都不含这两个词。
_CHALLENGE_MARK = ("redirect_last_time", "pop_enabled_t")
#: 别名域偶发回的空壳/跳广告页（441B）指纹 —— 实测它把浏览器甩去 k8l.k8l70g.com
_JUNK_MARK = ("k8l70g", "createElement(\"a\")", "f.submit()")

#: 当前生效的域名（失败自动切换后记住）
_state = {"base": BASE, "switched": False}


def log(msg: str) -> None:
    """进度走 stdout（跟 novel_dl.py 的约定一致：带 `{` 的行才会被当 JSON 契约扫）。"""
    print(msg, flush=True)


# ══════════════════════════════════════════════════════════════════════════
# 反爬：pop_enabled_t 戳 + 域名兜底
# ══════════════════════════════════════════════════════════════════════════
def _stamp(url: str) -> str:
    """给 URL 补 `pop_enabled_t=<毫秒>` —— 站点的 JS 就是这么做的（见文件头实测）。"""
    if "pop_enabled_t=" in url:
        return url
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}pop_enabled_t={int(time.time() * 1000)}"


def _gate_reason(html: str) -> str:
    """这一页是不是"墙"？是就返回人话原因，不是返回 ""。

    两种墙（都是实测出来的）：
      · 1133B **反爬挑战页**：`pop_enabled_t` / `redirect_last_time` 那段 JS；
      · 441B **跳广告空壳页**：`<script>var t="https://k8l.k8l70g.com:52000/video"…`
        （别名域 xbiquzw.net 偶发，实测 2026-10-09 出现过一次 → 搜索被判成"0 条"
          而不是"域名不通"，于是在那儿干等着。现在两种墙都会走"换戳重试 → 换域名"。）
    ★ 本站正常页面最小也是 6~20KB（正文页最少的"请假"类也有 6.8KB），
      所以"小于 2KB 且带跳转脚本"只可能是墙，不会误伤真内容。
    """
    if len(html) < 3000 and any(k in html for k in _CHALLENGE_MARK):
        return "反爬挑战页"
    if len(html) < 2000 and any(k in html for k in _JUNK_MARK):
        return "跳广告空壳页"
    return ""


def _on_base(url: str, base: str) -> str:
    """把 URL 换到指定域名（保留 path + query）。"""
    u = urllib.parse.urlsplit(url)
    b = urllib.parse.urlsplit(base)
    return urllib.parse.urlunsplit((b.scheme, b.netloc, u.path, u.query, u.fragment))


def _base_order() -> list:
    """先当前域，再另一个域。"""
    cur = _state["base"]
    others = [b for b in (BASE, FALLBACK_BASE) if b != cur]
    return [cur] + others


def _switch_base(base: str, why: str) -> None:
    if base == _state["base"]:
        return
    log(f"  ↩️ [{NAME}] 换域名：{_state['base']} → {base}（{why}）")
    _state["base"] = base
    _state["switched"] = True


def fetch_page(url: str, *, referer: str = "", timeout: float = 20.0,
               tries: int = 2) -> dict:
    """取一页 HTML，返回 {"html", "url"}。**每个请求都带反爬戳**。

    三轮兜底：
      ① 当前域 + 新鲜戳；
      ② 还是挑战页 → 退避一下、换个新戳再来（戳是按毫秒生成的，天然不同）；
      ③ 连接层失败（ConnectionReset/超时）→ 换另一个域名重试。
    全是失败就抛 FetchError（跟 novelmeta 的语义一致，让上层能 `except FetchError`）。
    """
    errors = []
    for base in _base_order():
        target = _on_base(url, base)
        ref = _on_base(referer, base) if referer else base + "/"
        for attempt in (1, 2):
            headers = dict(_HDR)
            headers["Referer"] = ref
            try:
                resp = fetch(_stamp(target), headers=headers, timeout=timeout, tries=tries)
            except FetchError as exc:
                errors.append(f"{base}: {exc}")
                break                      # 连接层就不通 → 直接换域
            if resp.status != 200:
                errors.append(f"{base}: HTTP {resp.status}")
                break
            html = resp.body.decode("utf-8", "replace")
            why = _gate_reason(html)
            if why:
                errors.append(f"{base}: {why}")
                log(f"  🛡️ [{NAME}] {why}（{len(html)}B）→ 换新戳重试 [{attempt}/2] {target}")
                time.sleep(0.4 * attempt)
                continue
            if base != _state["base"]:
                _switch_base(base, "上一个域名不通")
            return {"html": html, "url": resp.url}
    raise FetchError(url, errors or ["unknown"])


# ══════════════════════════════════════════════════════════════════════════
# HTML 工具（本模块自备，**不复用 novel_dl 的**，避免 import 成环）
# ══════════════════════════════════════════════════════════════════════════
_SCRIPT_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.S | re.I)
_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t\u3000]+")

#: 广告/站标行（逐行命中即删；只删这些行，**绝不改写正文**）
_AD_PATTERNS = [
    r"请记住本站", r"手机版阅读", r"最新章节", r"笔趣阁", r"笔尖中文", r"xbiqu",
    r"天才一秒记住", r"无弹窗", r"全文免费阅读", r"最快更新", r"加入书签", r"投推荐票",
    r"求月票", r"求收藏", r"求推荐", r"本站网址", r"一秒记住", r"免费阅读", r"更新最快",
    r"^\s*www\.[a-z0-9\-]+\.(?:com|net|org|cc|la|info)\s*$",
    r"[（(]\s*本章完\s*[)）]", r"^\s*[-=*—＿_·]{6,}\s*$",
    r"^\s*\d+\s*/\s*\d+\s*页\s*$",
    # ★★ 2026-10-09 实测新增（统计口径：这一站《冬日重现》624 章全本 TXT 里数出来的）★★
    #   注意：**只加"能指名道姓的站方样板"，绝不按"重复出现的短行"一刀切** ——
    #   实测同样高频的 `……`(92×)、`“什么？”`(51×)、`「什麽？」`(39×) 都是**正文对白**，
    #   一刀切会把小说删出窟窿。
    r"内容更新后，请重新刷新页面",       # 105× —— 正文尾部固定贴
    r"请重新刷新页面，即可获取最新更新",
    r"正在手打中，请稍等片刻",           # 91× —— 章名后接这句
    r"本书首发", r"看书网", r"追书神器",   # 6× —— 换源广告
    r"温馨提示：方向键左右", r"前后翻页",  # 17× —— 阅读器操作提示
    # ★ 下面这些是"取最长 div"那条兜底路径才会带出来的导航块（正文容器缺失时）。
    #   修了短章闸门（见 chapter()）之后正常不再出现，留作保险。**整行精确匹配**，不误伤正文。
    r"^(?:首页|我的书架|排行榜单|热门推荐：|新书推荐：|全本小说)$",
    r"^(?:玄幻|修真|都市|穿越|网游|科幻|言情|历史|军事|竞技|灵异|二次元)小说$",
]
_CHAPTER_WORD_RE = re.compile(
    r"^\s*(?:正文|VIP章节|收费章节)?\s*[:：]?\s*"
    r"(第[一二三四五六七八九十百千零〇0-9]+[章回节卷][^\n]{0,40}"
    r"|楔子[^\n]{0,40}|序章[^\n]{0,40}|序[^\n]{0,20}|前言[^\n]{0,20}"
    r"|后记[^\n]{0,20}|尾声[^\n]{0,20}|番外[^\n]{0,40})\s*$")


def strip_html(html: str, *, keep_breaks: bool = True, drop_ads: bool = True) -> str:
    """HTML → 纯文本：删脚本/注释、<br> 转换行、去标签、逐行清广告。

    ★ `drop_ads=False` 用于**书名/章节名这类短标题**，见 clean_text()。
    """
    html = _SCRIPT_RE.sub("", html)
    html = _COMMENT_RE.sub("", html)
    if keep_breaks:
        html = re.sub(r"<br\s*/?>|</p>|</div>|</dd>", "\n", html, flags=re.I)
    text = _TAG_RE.sub("", html)
    text = (text.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<")
                .replace("&gt;", ">").replace("&quot;", '"').replace("&#39;", "'")
                .replace("&ldquo;", "“").replace("&rdquo;", "”").replace("&hellip;", "…"))
    out = []
    for raw in text.split("\n"):
        line = _WS_RE.sub(" ", raw).strip()
        if not line:
            continue
        if drop_ads and any(re.search(p, line, re.I) for p in _AD_PATTERNS):
            continue
        if out and out[-1] == line:         # 相邻重复段（有些站会重复输出）
            continue
        out.append(line)
    return "\n".join(out)


def clean_text(html: str) -> str:
    """**行内短文本**清洗（书名 / 章节名）：去标签、还原实体、不留换行，**不过滤广告行**。

    ★★ 2026-10-09 踩的坑（真 bug，实测数据）：这一站有 33 个章节名里带「（求月票）」
      这类尾巴（如 `第八十八章星星点灯（求月票）`），而 `strip_html` 的广告表里有 `求月票`
      → 整行被删 → 名字变空字符串。后果：
        · 本爬虫初版：`if not name: continue` 直接把这 33 章**丢了**（目录 1474 → 1441）；
        · 老源（novel_dl 的通用正则源）：不丢，但那 33 章**标题是空的**。
      标题归标题、正文归正文 —— 广告过滤只该作用在**正文**上。
    """
    return strip_html(html, keep_breaks=False, drop_ads=False).replace("\n", " ").strip()


def _extract_div(html: str, start_idx: int) -> str:
    """从 `<div …>` 开始，**按 div 深度**找配对的 `</div>`，返回中间的内容。

    ★ 绝不能用非贪婪 `.*?</div>`：第一个内层 `</div>` 就会截断
      （项目里因为同类写法出过 4 次事故，这里沿用 novel_dl._extract_div 的做法）。
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


def _norm(s: str) -> str:
    """归一化：只留中日韩文字与字母数字（用来比对"首行是不是标题复读"）。"""
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", s or "")


def _drop_title_echo(text: str, h1: str, *, min_rest: int = 30) -> str:
    """删掉正文首行的**标题复读**。

    实测（2026-10-09）：<h1>第八十八章 星星点灯</h1>，而正文首行是
    `第八十八章星星点灯（求月票）` —— 同一句话的复读，还带装饰尾巴。

    ★★★ 但**删之前必须保证删完还剩得下东西** —— 2026-10-09 实测踩到的误删：
      公告章 <h1>「更新推迟到半夜」正文只有一行
      `更新推迟到半夜，不建议等，抱歉。`（正文正好以标题那几个字开头）
      → 被当成复读删掉 → 整章变空 → 这本书的 624 章里有 1 章报"正文为空"失败。
      所以加两道闸：① h1 归一化后至少 4 个字；② **删完剩余正文 ≥ min_rest 字**才删。
      代价：极短章可能留一行重复标题 —— 比"把章删没"好得多。
    """
    if not text or not h1:
        return text
    n_h1 = _norm(h1)
    if len(n_h1) < 4:
        return text
    lines = text.split("\n")
    first = _norm(lines[0])
    if not (first and first.startswith(n_h1) and len(first) <= len(n_h1) + 12):
        return text
    rest = "\n".join(lines[1:]).strip()
    if len(re.sub(r"\s", "", rest)) < min_rest:
        return text                      # 删了就没正文了 → 保留原样
    return rest


# ══════════════════════════════════════════════════════════════════════════
# 三件套：search / toc / chapter（novel_dl.py 按这三个函数调）
# ══════════════════════════════════════════════════════════════════════════
_ROW_RE = re.compile(
    r'<td class="odd"><a href="(/[0-9]+_[0-9]+/)"[^>]*>([^<]{1,60})</a></td>'
    r"(.*?)</tr>", re.S)
_OG_RE = r'<meta\s+property="og:%s"\s+content="([^"]*)"'


def info(book_url: str, *, timeout: float = 20.0) -> dict:
    """书籍详情/目录页 → 元数据（og: 标签，实测这一站给得很全）。

    · og:novel:book_name / author / category / status / update_time /
      latest_chapter_name / latest_chapter_url / og:image（封面）/ og:description（简介）
    ★ 只做只读解析，TXT 那条链路用不到封面；留着是给 --info 调试和以后接封面用的。
    """
    page = fetch_page(book_url, referer=_state["base"] + "/", timeout=timeout)
    html = page["html"]

    def g(key, default=""):
        m = re.search(_OG_RE % re.escape(key), html, re.I)
        return (m.group(1) or default).strip() if m else default

    h1 = re.search(r"<h1[^>]*>([^<]*)</h1>", html)
    return {
        "url": page["url"],
        "title": g("novel:book_name") or (clean_text(h1.group(1)) if h1 else ""),
        "author": g("novel:author"),
        "category": g("novel:category"),
        "status": g("novel:status"),
        "update_time": g("novel:update_time"),
        "latest_chapter": g("novel:latest_chapter_name"),
        "cover": g("image"),
        "intro": g("description"),
    }


def search(title: str, *, timeout: float = 20.0) -> list:
    """书名 → [{"title","author","url","ctx"}]（字段形状跟 novel_dl 的其它源一致）。

    · `ctx` 里带上「最新章节 / 作者 / 字数 / 更新 / 状态」，给 novel_dl 的同名书消歧用；
    · 站点在**精确命中唯一书**时会直接 302 到书籍页（不是结果表）→ 这里兜一下：
      没有结果行、但页面是书籍页（有 `id="list"` 或 og:book_name）就当 1 条命中。
    """
    url = f"{_state['base']}/modules/article/search.php?searchkey={urllib.parse.quote(title)}"
    log(f"  [{NAME}] 搜索: {url}")
    page = fetch_page(url, referer=_state["base"] + "/", timeout=timeout)
    html = page["html"]

    hits, seen = [], set()
    for m in _ROW_RE.finditer(html):
        href, name, rest = m.group(1), m.group(2), m.group(3)
        clean = clean_text(name)            # ★ 书名用 clean_text：不过滤广告词（书名可能含"求月票"等）
        if not clean or clean in seen:
            continue
        seen.add(clean)
        tds = [clean_text(x) for x in re.findall(r"<td[^>]*>(.*?)</td>", rest, re.S)]
        author = tds[0] if tds else ""
        ctx = " ｜ ".join(x for x in tds if x)
        hits.append({"title": clean, "author": author,
                     "url": urllib.parse.urljoin(page["url"], href), "ctx": ctx})
    if hits:
        log(f"  [{NAME}] 命中 {len(hits)} 条")
        return hits

    # 兜底：站点直接把你送到书籍页了（杰奇系常见行为）
    if re.search(r'<div[^>]+id="list"', html, re.I) or "og:novel:book_name" in html:
        meta = info(page["url"], timeout=timeout)
        if meta["title"]:
            log(f"  [{NAME}] 搜索被站点直接 302 到书籍页：《{meta['title']}》")
            return [{"title": meta["title"], "author": meta["author"], "url": page["url"],
                     "ctx": f"{meta['author']} ｜ {meta['status']} ｜ {meta['latest_chapter']}"}]
    log(f"  [{NAME}] 命中 0 条（页面 {len(html)}B）")
    return []


_LIST_RE = re.compile(r'<div[^>]+id="list"[^>]*>', re.I)
_CHAP_A_RE = re.compile(r'<a[^>]+href="([^"#]+\.html?)"[^>]*?(?:title="([^"]*)")?[^>]*>([^<]{0,80})</a>', re.I)
_NEXT_RE = re.compile(r'<a[^>]+href="([^"]+)"[^>]*>\s*(?:下一页|下页|下一贝|&gt;&gt;)\s*</a>', re.I)


def toc(book_url: str, *, timeout: float = 20.0) -> list:
    """目录页 → [{"name","url"}]，**按目录页出现顺序**（不按 URL 数字排序）。

    · 只在 `<div id="list">` 里取（页首还有一块"最新章节"，不限定就会章序错乱）；
    · 章节 href 是**相对目录页**的（`1190973.html`），用 urljoin 到 page_url；
    · 多页目录的保险：若目录里出现"下一页"且仍在同一本书路径下，就跟着翻（实测本站
      单页出全 1474 章、翻页链接 0 命中，这条分支是防站点以后改版）。
    """
    page = fetch_page(book_url, referer=_state["base"] + "/", timeout=timeout)
    page_url, html = page["url"], page["html"]
    book_path = urllib.parse.urlparse(page_url).path
    if not book_path.endswith("/"):
        book_path = book_path.rsplit("/", 1)[0] + "/"

    out, seen, pages = [], set(), 0
    while True:
        pages += 1
        m = _LIST_RE.search(html)
        scope = _extract_div(html, m.start()) if m else html
        for href, title_attr, text in _CHAP_A_RE.findall(scope):
            absu = urllib.parse.urljoin(page_url, href)
            if not urllib.parse.urlparse(absu).path.startswith(book_path):
                continue                      # 混进来的导航/推荐链接
            if absu in seen:
                continue
            seen.add(absu)
            # ★ 章节名用 clean_text：不过滤广告词。实测有 33 章名字里带「（求月票）」，
            #   走 strip_html 会被整行删掉、名字变空 → 章节被丢弃（1474 → 1441）。
            name = clean_text(text) or clean_text(title_attr or "")
            if not name:
                continue
            out.append({"name": name, "url": absu})
        nm = _NEXT_RE.search(html)
        if not nm:
            break
        nxt = urllib.parse.urljoin(page_url, nm.group(1))
        if not urllib.parse.urlparse(nxt).path.startswith(book_path) or pages >= 30:
            break
        log(f"  [{NAME}] 目录分页 → {nxt}")
        page = fetch_page(nxt, referer=page_url, timeout=timeout)
        page_url, html = page["url"], page["html"]

    log(f"  [{NAME}] 目录 {len(out)} 章" + (f"（{pages} 页）" if pages > 1 else ""))
    return out


_CONTENT_ID_RE = r'<div[^>]+id="content"[^>]*>'
#: 模板变了才用的备选容器（class 匹配，必须配长度闸门，见 chapter()）
_CONTENT_RES = (r'<div[^>]+class="[^"]*\b(?:showtxt|content|text)\b[^"]*"[^>]*>',)


def _longest_text_block(html: str) -> str:
    """通用兜底：取纯文本最长的那个 div（模板小改时不至于立刻 0 字）。"""
    body = _SCRIPT_RE.sub("", html)
    best = ""
    for m in re.finditer(r"<div\b[^>]*>", body):
        txt = strip_html(_extract_div(body, m.start()))
        if len(txt) > len(best):
            best = txt
    return best


def chapter(url: str, *, timeout: float = 20.0) -> str:
    """正文页 → 纯文本（保留段落换行，删广告行，删首行的标题复读）。

    ★★ 2026-10-09 实测修的一个串味 bug（短章）：
      `<div id="content">` 是**站方明确的正文容器**（每页恰好 1 个），但**短章它也很短**——
      例如公告章「请假」正文只有 55 字。老写法（novel_dl 的 _pick_content 同款）在这里卡了
      一道 `len(text) >= 80` 的闸门：短章被判成"没找到正文" → 退回"取纯文本最长的 div"
      → 抓回来的是**页面里别的块**（导航/推荐/榜单），输出 397 字**串味内容**。
      ⇒ 现在：`id="content"` **无条件信**（只在几乎为空时才往下退）；
         class 匹配那种"泛容器"才需要长度闸门。
    """
    page = fetch_page(url, referer=_state["base"] + "/", timeout=timeout)
    html = page["html"]
    h1 = re.search(r"<h1[^>]*>([^<]*)</h1>", html)
    h1 = clean_text(h1.group(1)) if h1 else ""     # ★ 标题行可能带「（求月票）」，别被广告表吃掉

    body = ""
    m = re.search(_CONTENT_ID_RE, html, re.S | re.I)
    if m:
        body = strip_html(_extract_div(html, m.start()))
    if len(body) < 10:                 # 模板改版 / 容器没了 → 退到泛容器（带长度闸门）
        for cre in _CONTENT_RES:
            m = re.search(cre, html, re.S | re.I)
            if m:
                cand = strip_html(_extract_div(html, m.start()))
                if len(cand) >= 80:
                    body = cand
                    break
    if len(body) < 10:                 # 最后一层兜底
        body = _longest_text_block(html)
    return _drop_title_echo(body, h1)


# ══════════════════════════════════════════════════════════════════════════
# CLI（独立调试 / 出 TXT）
# ══════════════════════════════════════════════════════════════════════════
def _download(title_or_url: str, *, limit: int = 0, out_dir: str = "", workers: int = 8,
               timeout: float = 20.0) -> dict:
    """书名（或书籍页 URL）→ 目录 → 逐章 → `<书名>.txt`。

    ★ 落盘格式**跟 novel_dl._download_chapters 一致**（`书名\\n\\n` + 每章
      `\\n\\n{章节名}\\n\\n{正文}\\n`），这样 App 后面的 TXT→EPUB 链路拿到的还是同一种输入。
      （2026-10-10 起不再写 `来源：…` 那一行 —— 用户要求，全源统一去掉了。）
    """
    if title_or_url.startswith("http"):
        book_url = title_or_url
        hits = []
    else:
        hits = search(title_or_url, timeout=timeout)
        if not hits:
            return {"ok": False, "error": f"搜索不到《{title_or_url}》"}
        book_url = hits[0]["url"]
    meta = info(book_url, timeout=timeout)
    book_title = meta["title"] or (hits[0]["title"] if hits else title_or_url)

    chapters = toc(book_url, timeout=timeout)
    if len(chapters) < 2:
        return {"ok": False, "error": f"目录只有 {len(chapters)} 章"}
    if limit:
        chapters = chapters[:limit]

    out_dir = out_dir or os.path.join(_HERE, "download")
    os.makedirs(out_dir, exist_ok=True)
    results, failed = {}, []
    t0 = time.time()

    def _one(item):
        i, ch = item
        err = None                      # ★ 必须初始化：`chapter()` 返回空串（不抛异常）时，
                                        #   老写法会走到最后一行引用未赋值的 err → UnboundLocalError
                                        #   （2026-10-09 实测踩到，整本书直接崩）
        for attempt in (1, 2, 3):
            try:
                text = chapter(ch["url"], timeout=timeout)
                if text:
                    return i, ch["name"], text, None
            except Exception as exc:        # noqa: BLE001 —— 绝不吞错，最后一次带出去
                err = exc
            time.sleep(0.3 * attempt * 2)
        return i, ch["name"], "", (f"{type(err).__name__}: {str(err)[:60]}" if err else "正文为空")

    with ThreadPoolExecutor(max_workers=max(1, min(workers, 32))) as pool:
        for i, name, text, err in pool.map(_one, list(enumerate(chapters, 1))):
            if text:
                results[i] = (name, text)
            else:
                failed.append(f"{name}（{err}）")
                log(f"    ❌ 第 {i} 章失败: {name}（{err}）")
            if len(results) % 10 == 0 or len(results) == len(chapters):
                log(f"    进度 {len(results)}/{len(chapters)}  {time.time() - t0:.0f}s  ❌{len(failed)}")

    if not results:
        return {"ok": False, "error": "所有章节都抓取失败"}
    safe = re.sub(r'[\\/:*?"<>|]', "_", book_title).strip() or "book"
    txt_path = os.path.join(out_dir, f"{safe}.txt")
    with open(txt_path, "w", encoding="utf-8") as fh:
        fh.write(f"{book_title}\n\n")
        for i in sorted(results):
            name, text = results[i]
            fh.write(f"\n\n{name}\n\n{text}\n")
    return {"ok": True, "file": txt_path, "book": book_title, "author": meta["author"],
            "chapters": len(results), "failed": failed, "elapsed": round(time.time() - t0, 1)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=f"{NAME}：xbiquzw.net 站专用爬虫（纯 TXT）")
    ap.add_argument("target", nargs="?", help="书名（配合 --get）")
    ap.add_argument("--search", default="", help="只搜索，打印命中")
    ap.add_argument("--info", default="", help="只解析书籍页元数据（og: 标签）")
    ap.add_argument("--toc", default="", help="只解析目录页")
    ap.add_argument("--chapter", default="", help="只抓一章正文")
    ap.add_argument("--get", default="", help="书名或书籍页 URL → 出 TXT")
    ap.add_argument("--limit", type=int, default=0, help="只抓前 N 章（0=全部）")
    ap.add_argument("--workers", type=int, default=8, help="并发线程（1~32，默认 8）")
    ap.add_argument("--out", default="", help="输出目录（默认 ./download）")
    ap.add_argument("--json", action="store_true", help="结果用 JSON 打印")
    args = ap.parse_args(argv)

    if args.search:
        hits = search(args.search)
        print(json.dumps(hits, ensure_ascii=False, indent=2) if args.json
              else "\n".join(f"  {h['title']}  {h['author']}  {h['url']}" for h in hits[:20]))
        return 0 if hits else 1
    if args.info:
        print(json.dumps(info(args.info), ensure_ascii=False, indent=2))
        return 0
    if args.toc:
        chs = toc(args.toc)
        print(f"共 {len(chs)} 章；前 3：{[c['name'] for c in chs[:3]]}；"
              f"末 3：{[c['name'] for c in chs[-3:]]}")
        return 0 if chs else 1
    if args.chapter:
        text = chapter(args.chapter)
        print(f"{len(text)} 字\n{text[:300]}")
        return 0 if text else 1
    if args.get or args.target:
        res = _download(args.get or args.target, limit=args.limit, out_dir=args.out,
                        workers=args.workers)
        print(json.dumps(res, ensure_ascii=False))
        return 0 if res.get("ok") else 1
    ap.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
