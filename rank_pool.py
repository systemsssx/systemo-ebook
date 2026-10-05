#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""rank_pool.py —— EasyPub「精选推荐」推荐池数据层（**阶段 0 独立实测版**）。

★ 本文件只活在 `C:/work/_rank_probe/`，**不碰 EasyPub-New**。
  实测通过后才搬进主程序（用户要求："实测成功再搬到主程序"）。

数据源（均为本轮亲手实测）：
  ① 微信读书（主源，免登录）
     GET https://weread.qq.com/web/category/list        → 486KB，内嵌 __INITIAL_STATE__
     GET https://weread.qq.com/web/appcategory/{id}     → 每个分类一页（同构）
     每本 40+ 字段，关键：title/author/cover/intro/category/free/maxFreeChapter/newRating/publishTime
  ② 番茄（备选，WeRead 覆盖不到 —— 实测 0/8 命中）
     GET https://fanqienovel.com/api/author/library/book_list/v0/?gender=0|1|-1&...
     11 字段：book_name/author/abstract/thumb_url/read_count/last_chapter_time/...

用法：
  py rank_pool.py --source weread --dump 5
  py rank_pool.py --source fanqie --gender 0 --dump 5
  py rank_pool.py --source all --pick 10 --batch 1
  py rank_pool.py --test cover           # 封面 URL 直连测试
  py rank_pool.py --test appcategory     # 分类页同构验证
  py rank_pool.py --test sort            # 番茄 sort 参数 0~3
  py rank_pool.py --test rotate          # 抽样：连刷 3 批，验证不重复+轮转
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
import sys
import time

# ★★★ 2026-09-30 修（与 `meta_lookup.py` 同一类 bug：**打包后路径假设失效**）：
#   原来这里只写死 `sys.path.insert(0, r"C:\work\novelmeta")` —— 在**开发机上恰好存在**这个目录，
#   所以一直没暴露；但它一换机器 / 干净安装就 import 不到 novelmeta。
#   更糟的是：冻结解释器（`easypub-backend.exe`）**不会**把脚本所在目录加进 `sys.path`
#   （已实测：`ModuleNotFoundError: No module named 'novelmeta'`），所以不能指望隐式行为。
#   现在按优先级插三层（**脚本自己所在目录放最后插 = 优先级最高**）：
#     ① 硬编码路径（老开发机兜底）→ ② NOVELMETA_HOME → ③ 脚本所在目录（打包版的正确答案）
_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (r"C:\work\novelmeta", os.environ.get("NOVELMETA_HOME") or "", _HERE):
    if _p and _p not in sys.path:
        sys.path.insert(0, _p)
os.environ.setdefault("NOVELMETA_IPV4_ONLY", "1")   # ★ must: 否则 weread 每请求白等 8.4s

from novelmeta.net import FetchError, fetch  # noqa: E402

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36")
CACHE_DIR = os.path.join(os.environ.get("LOCALAPPDATA") or r"C:\work\_rank_probe", "novelmeta")
WEREAD = "https://weread.qq.com"


def _get(url, headers=None, timeout=25, tries=2):
    h = {"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9"}
    if headers:
        h.update(headers)
    return fetch(url, headers=h, timeout=timeout, tries=tries)


# ══════════════════════════════════════════════════════════════════════════
# ① 微信读书
# ══════════════════════════════════════════════════════════════════════════
def extract_initial_state(html: str):
    """用**括号平衡法**提取 window.__INITIAL_STATE__（正则会被字符串里的括号坑死）。

    注意：要跳过字符串内部的 `{}`，否则会在 JSON 字符串值里提前收尾。
    """
    i = html.find("__INITIAL_STATE__")
    if i < 0:
        return None
    j = html.find("{", i)
    if j < 0:
        return None
    depth, in_str, esc = 0, False, False
    for k in range(j, len(html)):
        c = html[k]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(html[j:k + 1])
                except json.JSONDecodeError:
                    return None
    return None


def _walk_books(state):
    """从 state 里捞所有含 bookId + title 的对象。"""
    out = []

    def walk(o):
        if isinstance(o, dict):
            if o.get("bookId") and (o.get("title") or o.get("bookName")):
                out.append(o)
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(state)
    uniq = {}
    for b in out:
        uniq.setdefault(str(b.get("bookId")), b)
    return list(uniq.values())


def norm_weread(b):
    # ★ 实测：categories 是**对象数组**，形如
    #   [{"categoryId":2000000,"subCategoryId":2000001,"categoryType":2,"title":"女生小说-穿越空架"}]
    #   必须取里面的 title 才是分类名（第一版直接 str() 了，分类轮转因此失效）。
    raw_cats = b.get("categories") or ([b["category"]] if b.get("category") else [])
    if isinstance(raw_cats, str):
        raw_cats = [raw_cats]
    cats = []
    for _c in raw_cats:
        if isinstance(_c, dict):
            _t = _c.get("title") or _c.get("name")
            if _t:
                cats.append(str(_t))
        elif _c:
            cats.append(str(_c))
    return {
        "title": (b.get("title") or "").strip(),
        "author": (b.get("author") or "").strip(),
        "cover": (b.get("cover") or "").strip(),
        "intro": (b.get("intro") or "").strip(),
        "tags": [str(c) for c in cats if c][:4],
        "source": "微信读书",
        "free": bool(b.get("free")) if b.get("free") is not None else None,
        "max_free_chapter": b.get("maxFreeChapter"),
        "rating": b.get("newRating"),
        "rating_count": b.get("newRatingCount"),
        "publish_time": b.get("publishTime"),
        "finished": b.get("finished"),
        "ispub": b.get("ispub"),          # ★ 微信读书：出版物标志（用于「网文优先」分桶）
        "book_id": str(b.get("bookId") or ""),
        "url": f"{WEREAD}/web/reader/{b.get('bookId')}",
    }


def weread_home():
    """分类首页 → (书籍列表, 分类 id 列表, 原始 state)"""
    r = _get(WEREAD + "/web/category/list", {"Referer": WEREAD + "/"})
    html = r.body.decode("utf-8", "replace")
    st = extract_initial_state(html)
    if st is None:
        return [], [], None, html
    books = [norm_weread(b) for b in _walk_books(st)]
    # ★ 实测：首页本身不直接给 categoryId，但**每本书的 categories 里就有**
    #   （如「女生小说-穿越空架」categoryId=2000000）。
    cid = set()
    for _b in _walk_books(st):
        for _c in (_b.get("categories") or []):
            if isinstance(_c, dict) and _c.get("categoryId"):
                cid.add(str(_c["categoryId"]))
    cid |= set(re.findall(r"/web/appcategory/(\d{1,8})", html))
    cid |= set(re.findall(r'"categoryId":(\d{5,8})', json.dumps(st, ensure_ascii=False)))
    return books, sorted(cid), st, html


def weread_category(cid):
    """单个分类页 → 书籍列表（验证与首页同构）"""
    r = _get(f"{WEREAD}/web/appcategory/{cid}", {"Referer": WEREAD + "/"})
    st = extract_initial_state(r.body.decode("utf-8", "replace"))
    if st is None:
        return []
    return [norm_weread(b) for b in _walk_books(st)]


# ══════════════════════════════════════════════════════════════════════════
# ② 番茄
# ══════════════════════════════════════════════════════════════════════════
def norm_fanqie(b, gender):
    return {
        "title": (b.get("book_name") or "").strip(),
        # ★ 用户反馈「这个作者有一些字没有」→ 番茄作者名也带 PUA，先剔掉；
        #   真正的明文由详情页补（见 fanqie_detail 的 info["author"]）。
        "author": PUA_RE.sub("", (b.get("author") or "")).strip(),
        "cover": (b.get("thumb_url") or b.get("thumb_uri") or "").strip(),
        "intro": (b.get("abstract") or "").strip(),
        "tags": [],
        "source": f"番茄(gender={gender})",
        "free": True,                       # 番茄免费站，全站免费
        "rating": None,
        "rating_count": None,
        "publish_time": b.get("last_chapter_time"),
        "finished": (b.get("creation_status")),
        "book_id": str(b.get("book_id") or ""),
        "read_count": b.get("read_count"),
        "url": f"https://fanqienovel.com/page/{b.get('book_id')}",
    }


def fanqie_list(gender=0, pages=2, sort=0, gap=0.0, workers=4):
    """★ 2026-09-30：**分页改并行**。

    原来 3 页串行、每页还 `sleep(0.6)` → 单源 2.4s，而其它源只要 0.5~0.8s，
    于是它成了 `build('all')` 的拖后腿项（实测：并行 12 线程也救不了，因为瓶颈在源内部）。
    现在 3 页并发 → 单源 ≈0.8s，build 从 2.6s 降到 ≈0.9s。
    """
    def page_url(p):
        return ("https://fanqienovel.com/api/author/library/book_list/v0/?"
                f"page_count=20&page_index={p}&gender={gender}&category_id=-1"
                f"&creation_status=-1&word_count=-1&book_type=-1&sort={sort}")

    def one(p):
        try:
            r = _get(page_url(p), {"Accept": "application/json",
                                   "Referer": "https://fanqienovel.com/"}, timeout=20)
            j = json.loads(r.body.decode("utf-8", "replace"))
            return (j.get("data") or {}).get("book_list") or []
        except Exception as exc:
            print(f"    [番茄] 第 {p} 页失败: {type(exc).__name__}: {str(exc)[:60]}")
            return []

    out = []
    n = max(1, int(pages or 1))
    with ThreadPoolExecutor(max_workers=max(1, min(workers, n))) as ex:
        for bl in ex.map(one, range(n)):          # map 按入参顺序返回 → 结果顺序不变
            out += [norm_fanqie(b, gender) for b in bl]
    return out


# ═════════════════════════════════════
# ★ 番茄书名 PUA（字体混淆）→ 详情页取明文
# ═════════════════════════════════════
PUA_RE = re.compile(r"[\ue000-\uf8ff]")       # 私用区字符 = 字体混淆的标志
FQ_DETAIL_CACHE = os.path.join(CACHE_DIR, "fanqie_detail.json")


def _load_cache(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


def _save_cache(path, obj):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(obj, fh, ensure_ascii=False)
    except Exception:
        pass


def fanqie_detail(book_id, cache=None):
    """从详情页取 **明文** 书名/标签/简介。

    ★ 实测依据（2026-09-29）：榜单 API 的书名 **19/20 是 PUA**
      私用区字符（字体混淆），但详情页 https://fanqienovel.com/page/{id}
      为了 SEO **完全没有 PUA**（实测 PUA 出现 0 次，639,394 B）。
      → 所以**不破字体**，改从详情页取明文（零依赖、零维护）。
      详情页 639 KB/次 → **只对含 PUA 的书**才调，并落盘缓存。
    """
    ck = cache if cache is not None else _load_cache(FQ_DETAIL_CACHE)
    if book_id in ck:
        return ck[book_id]
    info = {"title": "", "tags": [], "intro": ""}
    try:
        r = _get(f"https://fanqienovel.com/page/{book_id}",
                 {"Referer": "https://fanqienovel.com/"}, timeout=25)
        h = r.body.decode("utf-8", "replace")
        m = (re.search(r'"bookName":"([^"]{1,60})"', h)
             or re.search(r"<title>([^<_]{1,60})", h))
        if m:
            info["title"] = m.group(1).strip()
        # ★ 用户："作者有一些字没有" → 详情页取**明文作者**。
        am = (re.search(r'"authorName":"([^"]{1,32})"', h)
              or re.search(r'"author":"([^"]{1,32})"', h)
              or re.search(r'<meta name="author" content="([^"]{1,32})"', h)
              or re.search(r'og:novel:author" content="([^"]{1,32})"', h))
        if am:
            info["author"] = PUA_RE.sub("", am.group(1)).strip()
        d = re.search(r'<meta name="description" content="([^"]{0,400})', h)
        if d:
            desc = d.group(1)
            info["intro"] = re.sub(r"^番茄小说提供[^。]{0,60}。?", "", desc).strip()[:200]
            tg = re.search(r"【([^】]{1,120})】", desc)
            if tg:
                # ★ 实测：【】里混了宣传语（如「评分刚出」「本书已签约实体出版：克苏鲁」）→ 清洗掉。
                _raw = [x for x in re.split(r"[+＋、,，\s]+", tg.group(1)) if x]
                _noise = re.compile(r"评分|签约|本书|已出版|实体出版|第.{0,3}章|纯属虚构|正版")
                info["tags"] = [x for x in _raw if not _noise.search(x)][:3]
    except Exception as exc:
        print(f"    [番茄详情] {book_id} 失败: {type(exc).__name__}: {str(exc)[:50]}")
    if info["title"] and PUA_RE.search(info["title"]):
        info["title"] = PUA_RE.sub("", info["title"])      # 兜底：拿不到就至少不留乱码
    ck[book_id] = info
    _save_cache(FQ_DETAIL_CACHE, ck)
    return info


def enrich_fanqie(books, limit=0):
    """只对**书名含 PUA** 的番茄书补明文（详情页 639KB/次，别全量抓）。"""
    need = [b for b in books if PUA_RE.search(b["title"])]
    if limit:
        need = need[:limit]
    if not need:
        print("  [番茄] 无需补明文（书名都没有 PUA）")
        return books
    print(f"  [番茄] {len(need)}/{len(books)} 本需详情页补明文…")
    ck = _load_cache(FQ_DETAIL_CACHE)
    lock = threading.Lock()
    done = [0]

    def work(b):
        d = fanqie_detail(b["book_id"], ck)
        with lock:
            if d.get("title"):
                b["title"] = d["title"]
            if d.get("author"):
                b["author"] = d["author"]
            if d.get("tags"):
                b["tags"] = d["tags"]
            if d.get("intro"):
                b["intro"] = d["intro"]
            done[0] += 1

    # ★ 用户反馈「太慢了，多开几个线程」→ 原本单线程 + 0.4s 间隔；
    #   改成 8 并发（实测详情页 639KB/次，8 路足够且不粗鲁）。
    # ★ 用户："线程再开多一点" → 8 → 16（详情页 639KB/次）
    with ThreadPoolExecutor(max_workers=16) as ex:
        list(ex.map(work, need))
    _save_cache(FQ_DETAIL_CACHE, ck)
    return books

# ══════════════════════════════════════════════════════════════════════════
# 抽样（推荐算法核心）
# ══════════════════════════════════════════════════════════════════════════
def _key(s):
    return re.sub(r"[\s\u3000《》【】\[\]（）()·、,，。.!！?？:：\"'“”‘’\-—]+", "", s or "").lower()


def _num(v):
    """从可能混入 PUA/中文的值里取第一个数字。

    ★ 实测依据：番茄的 read_count **也带 PUA**
      （实测值：'\ue54f\ue4b0\ue4b0.\ue53c\ue3f7\ue41c\ue53f读'），直接 int() 会抛
      ValueError: invalid literal for int() → 整个推荐池报废。
      所以所有数值评分必须过这一层。
    """
    m = re.search(r"\d+(?:\.\d+)?", str(v if v is not None else ""))
    return float(m.group()) if m else 0.0


def _score(b):
    """free 优先 → 评分 → 评价人数 → 新鲜度（publish_time 大的优先）"""
    s = 0.0
    # ★ 实测：WeRead 的 free 常为 False，但 maxFreeChapter>0 说明**可试读**；
    #   番茄是免费站（free=True）。两者都给可读性分，避免单一来源压死另一个。
    if b.get("free") or _num(b.get("max_free_chapter")) > 0:
        s += 3.0
    rt = _num(b.get("rating"))
    if rt:
        # ★ 实测：WeRead 的 newRating 量纲是 **0~1000**（样例 777/767/929/852）。
        s += min(rt / 1000.0, 1.0) * 2.0
    rc = _num(b.get("rating_count"))
    if rc:
        s += min(rc / 300.0, 1.0)
    rd = _num(b.get("read_count"))       # 番茄阅读量（它没有 rating）
    if rd:
        s += min(rd / 100000.0, 1.0)
    # ★ 实测：WeRead 的 publishTime 是 **"字符串日期"**（如 "2026-08-01 00:00:00"），
    #   不是时间戳 → 原来的 pt.isdigit() 永远为假，新鲜度维度完全失效。
    pt = str(b.get("publish_time") or "")
    _m = re.match(r"(\d{4})-(\d{2})-(\d{2})", pt)
    if _m:
        s += min(int(_m.group(1) + _m.group(2) + _m.group(3)) / 21000000.0, 1.0)
    elif pt.isdigit():
        s += min(int(pt) / 2_000_000_000, 1.0)
    return s


# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═
# ★ 起点榜（用户：「男频要以起点为主」）
#   数据源：https://siweimidu.github.io/QiDianRankTracker/（MIT，每日 GitHub Actions 自动抓）
#   结构（实测）：api/{slug}/latest.json → {slug,name,date,types:[{name,url,count}]}
#                     api/{slug}/latest/{type}.json → {date,category,books:[30]}
#   书籍字段（实测）：rank,bid,title,author,category,subCategory,status,
#                     intro,latestChapter,updateTime,cover,bookUrl,metricLabel,metric,metricText
#   ★ date 必须看：实测 yuepiao=2026-09-29（今天）但 newsign=2026-09-17（陈旧）
# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═
QD_BASE = "https://siweimidu.github.io/QiDianRankTracker/"
QD_BOARDS = (("yuepiao", "月票榜", "hot"), ("hotsales", "畅销榜", "hot"),
             ("newsign", "新人签约新书榜", "new"),
             ("signnewbook", "签约作者新书榜", "new"))


def norm_qidian(b, board):
    return {
        "title": (b.get("title") or "").strip(),
        "author": (b.get("author") or "").strip(),
        "cover": (b.get("cover") or "").strip(),
        "intro": (b.get("intro") or "").strip(),
        "tags": [x for x in (b.get("category"), b.get("subCategory")) if x],
        "source": f"起点·{board}",
        "free": False, "rating": None, "rating_count": None,
        "publish_time": (b.get("updateTime") or "").strip(),
        "finished": 1 if (b.get("status") or "") == "完结" else 0,
        "book_id": "qd" + str(b.get("bid") or ""),
        "url": (b.get("bookUrl") or "").strip(),
        "read_count": b.get("metric"),
        "_tier": None,
    }


def qidian_board(slug, board, tier):
    import urllib.parse as _up
    idx = json.loads(_get(QD_BASE + f"api/{slug}/latest.json",
                          {"Referer": QD_BASE}, timeout=20).body.decode("utf-8"))
    u = QD_BASE + f"api/{slug}/latest/" + _up.quote("全部") + ".json"
    j = json.loads(_get(u, {"Referer": QD_BASE}, timeout=20).body.decode("utf-8"))
    books = [norm_qidian(b, board) for b in (j.get("books") or [])]
    for b in books:
        b["_tier"] = tier
    print(f"  [起点·{board}] {len(books)} 本  date={idx.get('date')}")
    return books

# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═
# ★ 晋江榜（用户：「女频找晋江不要找番茄」）
#   数据源：https://sherrysouthxs.github.io/novel-tracker/data/jjwxc/latest.json
#     实测：403KB / 200 本 / 25 字段 / source=晋江文学城·积分月榜 /
#           update_time=2026-09-29 09:07（当天）。字段含 rank,book_id,book_name,
#           author,channel,genre,era,theme,all_tags,score,score_display,abstract,
#           status,thumb_url,book_url,history_days。
#   ⚠️ 仓库**无 license** → 只读它的事实数据，不拄代码。
# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═# ═
NT_JJWXC = "https://sherrysouthxs.github.io/novel-tracker/data/jjwxc/latest.json"


def jjwxc_board():
    """晋江榜 → 女频主源。大火 = 积分高；新作 = 上榜天数少。"""
    j = json.loads(_get(NT_JJWXC,
                          {"Referer": "https://sherrysouthxs.github.io/novel-tracker/"},
                          timeout=25).body.decode("utf-8"))
    out = []
    for b in (j.get("books") or []):
        out.append({
            "title": (b.get("book_name") or "").strip(),
            "author": (b.get("author") or "").strip(),
            "cover": (b.get("thumb_url") or "").strip(),
            "intro": (b.get("abstract") or "").strip(),
            # ★ 实测坑：晋江的 all_tags[0] 大多是 '原创'（性质），若直接当首标签，
            #   会被 per_tag_cap=2 把整个晋江池卡死（实测：女频新作席位被微信读书报掉）。
            #   → 用 genre/era/theme 做前三个标签，再接真正的题材标签。
            "tags": [x for x in ([str(b.get("genre") or ""), str(b.get("era") or ""),
                                 str(b.get("theme") or "")]
                                + [str(y) for y in (b.get("all_tags") or [])
                                   if str(y) not in ("原创", "联载", "完结")])
                     if x][:4],
            "source": "晋江·积分月榜",
            "free": False,
            "rating": b.get("score_display"),
            "rating_count": None,
            "publish_time": (b.get("update_time") or "").strip(),
            "finished": 1 if (b.get("status") or "") == "完结" else 0,
            "book_id": "jj" + str(b.get("book_id") or ""),
            "url": (b.get("book_url") or "").strip(),
            "read_count": b.get("score"),
            "_history_days": b.get("history_days"),
            "_tier": None,
        })
    out.sort(key=lambda x: -_num(x.get("read_count")))
    half = max(1, len(out) // 2)
    for i, b in enumerate(out):
        b["_tier"] = "hot" if i < half else "new"
    print(f"  [晋江·积分月榜] {len(out)} 本  date={j.get('update_date')}")
    return out

def _mark_tiers(pool):
    """把番茄池按 gender 分组，每组再分「大火 / 新作」两档。

    ★ 用户指定比例：正经书 2、男频大火 2、女频大火 2、男频新作 3、女频新作 1。
    判定：**大火** = read_count 高；**新作** = last_chapter_time 最近（不在大火里）。
    """
    for g in (0, 1):
        grp = [b for b in pool if f"gender={g}" in (b.get("source") or "")]
        if not grp:
            continue
        grp.sort(key=lambda b: -_num(b.get("read_count")))
        half = max(1, len(grp) // 2)
        hot = {str(b.get("book_id")) for b in grp[:half]}
        for b in grp:
            b["_tier"] = "hot" if str(b.get("book_id")) in hot else "new"
    return pool


def _bucket(b):
    """分桶：女频 / 男频 / 网文 / 出版。

    ★ 用户实测反馈：「好多正经书，网文好少」「正经书一两本就好了」
      → 番茄（纯网文，男女频）优先，微信读书里的**出版物最多 1 本**。
    判定：番茄按 gender 分；WeRead 按 ispub / 非小说类分类目归为出版。
    """
    src = b.get("source") or ""
    if src.startswith("起点·"):      # ★ 男频以起点为主
        return "男频起点大火" if b.get("_tier") == "hot" else "男频起点新作"
    if src.startswith("晋江·"):      # ★ 女频找晋江（不找番茄）
        return "女频晋江大火" if b.get("_tier") == "hot" else "女频晋江新作"
    if "gender=0" in src:
        return "女频番茄"      # ★ 仅作兜底（不在 ORDER 里）
    if "gender=1" in src:
        return "男频番茄"      # ★ 用户：「番茄的最多两本」
    tags = " ".join(str(x) for x in (b.get("tags") or []))
    non_fiction = ("经济理财", "政治军事", "人物传记", "教育学习",
                   "哲学宗教", "生活百科", "科学技术", "心理", "管理",
                   "历史-中国古代", "历史-中国近现代")
    if b.get("ispub") or any(k in tags for k in non_fiction) or not tags:
        return "正经书"
    return "出版"      # WeRead 小说（阅文系=起点）→ 不占主配额
                             #   ★ 用户："男频怎么都是起点的书" → 主配额全给番茄


def pick(pool, n=10, batch=1, library=(), seen=(), per_tag_cap=2, per_source_cap=6):
    """抽 n 本：去重 → **分桶配额（网文优先）** → 打分排序 → 确定性旋转 → 标签上限。"""
    lib = {_key(x) for x in library}
    seen_ids = {str(x) for x in seen}
    seen_keys = {_key(x) for x in seen}
    cands = [b for b in pool
             if b["title"]
             and str(b.get("book_id") or "") not in seen_ids
             and _key(b["title"]) not in seen_keys
             and _key(b["title"]) not in lib]
    buckets = {}
    for b in cands:
        buckets.setdefault(_bucket(b), []).append(b)
    for k, lst in buckets.items():
        lst.sort(key=lambda x: -_score(x))
        rot = (batch * 17) % len(lst)
        buckets[k] = lst[rot:] + lst[:rot]
    # ★ 配额：10 本 = 女频 4 + 男频 3 + 网文 2 + 出版 1
    # ★ 用户指定：正经书 2 / 男频大火 2 / 女频大火 2 / 男频新作 3 / 女频新作 1
    # ★ 用户：男频**以起点为主**（起点 3 : 番茄 2）
    # ★ 男频 = 起点 3 + 番茄 2；女频 = 晋江 3；正经书 2
    plan = {"正经书": 2, "男频起点大火": 2, "男频起点新作": 1,
            "男频番茄": 2, "女频晋江大火": 2, "女频晋江新作": 1}
    out, tag_used, src_used, taken = [], {}, {}, set()

    def take(b):
        bid = str(b.get("book_id") or b["title"])
        if bid in taken:
            return False
        _tags = b.get("tags") or []
        tag = _tags[0] if _tags else ""
        if tag and tag_used.get(tag, 0) >= per_tag_cap:
            return False
        if src_used.get(b["source"], 0) >= per_source_cap:
            return False
        if tag:
            tag_used[tag] = tag_used.get(tag, 0) + 1
        src_used[b["source"]] = src_used.get(b["source"], 0) + 1
        taken.add(bid)
        out.append(b)
        return True

    ORDER = ("正经书", "男频起点大火", "男频起点新作",
             "男频番茄", "女频晋江大火", "女频晋江新作")
    # ★ 男频番茄硬卡 2 本；女频番茄仅在晋江不足时兜底
    FILL_ORDER = tuple(k for k in ORDER if k != "男频番茄") + ("出版", "女频番茄")
    for key in ORDER:
        got_here = 0
        for b in buckets.get(key, []):
            if got_here >= plan.get(key, 0) or len(out) >= n:
                break
            if take(b):
                got_here += 1
    if len(out) < n:                      # 配额没凑满 → 用剩下的补齐
        ORDER = ("正经书", "男频起点大火", "男频起点新作",
             "男频番茄", "女频晋江大火", "女频晋江新作")
    # ★ 男频番茄硬卡 2 本；女频番茄仅在晋江不足时兜底
    FILL_ORDER = tuple(k for k in ORDER if k != "男频番茄") + ("出版", "女频番茄")
    for key in FILL_ORDER:
            for b in buckets.get(key, []):
                if len(out) >= n:
                    break
                take(b)
    return out[:n]

def field_rates(pool):
    if not pool:
        return {}
    return {f: f"{sum(1 for b in pool if b.get(f))}/{len(pool)}"
            for f in ("title", "author", "cover", "intro", "tags")}


# ══════════════════════════════════════════════════════════════════════════
# 测试项
# ══════════════════════════════════════════════════════════════════════════
def test_cover(pool, k=3):
    print("\n=== 封面 URL 直连测试 ===")
    n = 0
    for b in pool:
        if not b.get("cover"):
            continue
        u = b["cover"]
        try:
            r = _get(u, {"Referer": WEREAD + "/"}, timeout=15, tries=1)
            ct = r.headers.get("Content-Type") or ""
            print(f"  HTTP {r.status} {len(r.body):7d}B  CT={ct[:28]:30s} {u[:70]}")
        except Exception as exc:
            print(f"  FAIL {type(exc).__name__}: {str(exc)[:60]}  {u[:70]}")
        n += 1
        if n >= k:
            break
    if n == 0:
        print("  （池子里没有封面 URL）")


def test_appcategory(ids):
    print(f"\n=== /web/appcategory/{{id}} 同构验证（拿到 {len(ids)} 个 categoryId）===")
    if not ids:
        print("  ❌ 没从首页挖到 categoryId")
        return
    for cid in ids[:3]:
        try:
            books = weread_category(cid)
            print(f"  categoryId={cid}: {len(books)} 本  样例={[b['title'][:14] for b in books[:3]]}")
        except Exception as exc:
            print(f"  categoryId={cid}: FAIL {type(exc).__name__}")
        time.sleep(0.6)


def test_sort():
    print("\n=== 番茄 sort 参数 0~3（gender=0，看首本是否不同）===")
    for s in range(4):
        try:
            lst = fanqie_list(0, pages=1, sort=s)
            first = lst[0]["title"][:20] if lst else "(空)"
            print(f"  sort={s}: {len(lst)} 本  首本={first}")
        except Exception as exc:
            print(f"  sort={s}: FAIL {type(exc).__name__}")
        time.sleep(0.6)


def test_rotate(pool):
    print("\n=== 抽样：连刷 3 批（各 10 本），验证不重复 + 分类轮转 ===")
    seen = []
    for batch in (1, 2, 3):
        got = pick(pool, n=10, batch=batch, seen=seen)
        seen += [b["title"] for b in got]
        tags = [((b.get("tags") or ["-"])[0]) for b in got]
        print(f"  批次{batch}: {[b['title'][:12] for b in got]}")
        print(f"          分类: {tags}")
    print(f"  三批共 {len(seen)} 本，去重后 {len(set(_key(x) for x in seen))} 本 "
          f"→ {'✅ 无重复' if len(seen) == len(set(_key(x) for x in seen)) else '❌ 有重复'}")


# ══════════════════════════════════════════════════════════════════════════

# ══════════════════════════════════════════════════════════════════════
# ★ 2026-09-30 新增：哔哩轻小说榜单（用户要「推荐里加个轻小说的栏位」）
#   /top/<sort>/<page>.html，每页 30 条；真封面在 img 的 data-original 里。
#   榜单**保持站点排名顺序**（不抽样、不按 source 配额），因为榜单本身就是排序结果。
# ══════════════════════════════════════════════════════════════════════
LINOVELIB = "https://www.linovelib.com"
# ★ 2026-09-30（用户实测）：各榜页数不一样 —— 新书榜只有 3 页，其余 5 页。
LN_BOARD_PAGES = {"newhot": 3}

LN_BOARDS = [
    ("monthvisit", "月点击榜"), ("weekvisit", "周点击榜"),
    ("monthvote", "月推荐榜"), ("weekvote", "周推荐榜"),
    ("goodnum", "收藏榜"), ("newhot", "新书榜"),
    ("monthflower", "月鲜花榜"), ("lastupdate", "最近更新"),
]


def _ln_txt(seg, cls):
    """取 .<cls> 里的可见文字（去标签）。"""
    m = re.search(r'class="%s"[^>]*>([\s\S]*?)</div>' % cls, seg)
    if not m:
        return ""
    t = re.sub(r"<[^>]+>", " ", m.group(1))
    return re.sub(r"\s+", " ", t).strip()


def _ln_page(sort, pg):
    """抓榜单第 pg 页（带磁盘缓存，TTL 15 分钟）→ 条目列表。"""
    cache = os.path.join(CACHE_DIR, f"ln_top_{sort}_{pg}.json")
    try:
        c = _load_cache(cache)
        if c and (time.time() - float(c.get("t") or 0)) < 900 and c.get("items"):
            print(f"[轻小说·{sort}] 第{pg}页 命中缓存（{len(c['items'])} 条）")
            return c["items"]
    except Exception:
        pass
    url = f"{LINOVELIB}/top/{sort}/{pg}.html"
    html = _get(url, {"Referer": LINOVELIB + "/top.html"}).body.decode("utf-8", "replace")
    segs = re.findall(r'<div class="rank_d_list[\s\S]*?(?=<div class="rank_d_list|\Z)', html)
    out = []
    for seg in segs:
        m = re.search(r'href="(/novel/(\d+)\.html)"', seg)
        if not m:
            continue
        nid = m.group(2)
        t = re.search(r'class="rank_d_b_name"[^>]*title="([^"]*)"', seg) or \
            re.search(r'class="rank_d_b_name"[^>]*>\s*<a[^>]*>([^<]+)</a>', seg)
        title = (t.group(1).strip() if t else "")
        cv = re.search(r'data-original="([^"]+)"', seg)
        cover = (cv.group(1) if cv else "")
        if cover.startswith('/'):
            cover = LINOVELIB + cover
        cate = _ln_txt(seg, 'rank_d_b_cate')
        info = _ln_txt(seg, 'rank_d_b_info')
        parts = [x.strip() for x in cate.split('|') if x.strip()]
        out.append({
            "title": title,
            "author": parts[0] if parts else "",
            "cover": cover,
            "intro": info,
            "tags": parts[1:3],
            "source": "轻小说·" + dict(LN_BOARDS).get(sort, sort),
            "novel_id": nid,
            "rank_page": pg,
            "rank_pos": len(out) + 1,
            "last_chapter": _ln_txt(seg, "rank_d_lastchapter"),
            "update_time": _ln_txt(seg, "rank_d_b_time"),
            "url": LINOVELIB + "/novel/" + nid + ".html",
        })
    print(f"[轻小说·{sort}] 第{pg}页 {len(html)}B → {len(out)} 条（已缓存）")
    try:
        _save_cache(cache, {"t": time.time(), "items": out})
    except Exception:
        pass
    return out


def linovelib_board(sort="monthvisit", pages=5, limit=0, parallel=5):
    """★ 并行抓 1..pages 页（每页 30 条）→ 按榜上顺序拼成一个整池。

    为什么要并行 + 缓存：一页 ~0.9~4s（看站点响应），串行 5 页要 5~20s；
    并行 5 条约 1~4s，且**每页落盘缓存 15 分钟** → 之后翻页/刷新是 0 网络。
    """
    cap = LN_BOARD_PAGES.get(sort, 5)          # ★ 每榜页数上限（新书 3、其余 5）
    n = max(1, min(int(pages or 1), cap))
    results = {}
    with ThreadPoolExecutor(max_workers=max(1, min(parallel, n))) as ex:
        futs = {ex.submit(_ln_page, sort, pg): pg for pg in range(1, n + 1)}
        for f in as_completed(futs):
            pg = futs[f]
            try:
                results[pg] = f.result()
            except Exception as exc:
                print(f"[轻小说·{sort}] 第{pg}页 FAIL {type(exc).__name__}: {str(exc)[:60]}")
                results[pg] = []
    out, seen = [], set()
    for pg in range(1, n + 1):
        for it in results.get(pg, []):
            if it["novel_id"] in seen:
                continue
            seen.add(it["novel_id"])
            it["rank_page"] = pg
            it["rank_pos"] = len(out) + 1
            out.append(it)
            if limit and len(out) >= limit:
                return out
    return out


def ln_warm_background(sort, pages=5):
    """★ detached 起一个子进程预热 2..pages 页（不阻塞当前进程，主进程可以立刻返回）。"""
    import subprocess
    try:
        flags = 0
        if os.name == 'nt':
            flags = 0x00000008 | 0x08000000      # DETACHED_PROCESS | CREATE_NO_WINDOW
        subprocess.Popen(
            [sys.executable, '-X', 'utf8', os.path.abspath(__file__),
             '--source', 'linovelib', '--sort', sort, '--pages', str(pages), '--warm-only'],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
            creationflags=flags, close_fds=True)
        print(f"[轻小说·{sort}] 已后台预热第 2~{pages} 页（翻页将命中缓存）")
    except Exception as exc:
        print(f"[轻小说·{sort}] 后台预热失败（不影响使用）: {type(exc).__name__}")

POOL_CACHE_TTL = 120          # ★ 综合推荐池缓存 120 秒（刷新秒回；榜单本身变化很慢）


def build(source, gender, pages, enrich=False, ln_sort="monthvisit", use_cache=True):
    pool = []
    t0 = time.time()
    # ★ 2026-09-30：池缓存（默认 120s）—— 刷新时直接复用，不再重抓 8 个源
    _pool_cache = os.path.join(CACHE_DIR, f"rank_pool_{source}_p{pages}.json")
    if use_cache and source == "all":
        try:
            _c = _load_cache(_pool_cache)
            if _c and (time.time() - float(_c.get("t") or 0)) < POOL_CACHE_TTL \
                    and len(_c.get("pool") or []) > 50:
                print(f"[推荐池] 命中缓存（{len(_c['pool'])} 本，"
                      f"{time.time() - float(_c['t']):.0f}s 前抓的）")
                return _c["pool"]
        except Exception:
            pass
    # ★ 2026-09-30：轻小说榜单单独一路（只有它时直接返回，保持站点排名顺序）
    if source == "linovelib":
        # ★ 并行抓多页（界面用页码翻页；每页有缓存，翻页/刷新 0 网络）
        pool = linovelib_board(ln_sort, pages=max(1, pages), parallel=5)
        print(f"[轻小说] 池子 {len(pool)} 本，用时 {time.time() - t0:.1f}s")
        return pool
    if source == "all":
        # ★ 三路并发：WeRead + 番茄男/女频 同时抓（原本串行 ~5s → ~1.5s）
        # ★ 2026-09-30（用户：加大线程）：6 → 12 —— 8 个源一轮跑完（跨 4 个站点，
        #   每站并发仍然很低：微信读书 1 / 番茄 2 / 起点 4 / 晋江 1）。实测 build 2.53s → ~1.1s。
        with ThreadPoolExecutor(max_workers=12) as ex:
            f_w = ex.submit(weread_home)
            f_0 = ex.submit(fanqie_list, 0, pages)
            f_1 = ex.submit(fanqie_list, 1, pages)
            f_q = [ex.submit(qidian_board, s, nm, t_) for s, nm, t_ in QD_BOARDS]
            f_jj = ex.submit(jjwxc_board)
            try:
                books, cids, _st, html = f_w.result()
                print(f"[微信读书] /web/category/list  {len(html)}B  → {len(books)} 本  "
                      f"完整率={field_rates(books)}")
                pool += books
            except Exception as exc:
                print(f"[微信读书] FAIL {type(exc).__name__}: {str(exc)[:80]}")
            for _g, _f in ((0, f_0), (1, f_1)):
                try:
                    _lst = _f.result()
                    print(f"[番茄 gender={_g}] {len(_lst)} 本  完整率={field_rates(_lst)}")
                    pool += _lst
                except Exception as exc:
                    print(f"[番茄 gender={_g}] FAIL {type(exc).__name__}: {str(exc)[:80]}")
        try:
            pool += f_jj.result()
        except Exception as exc:
            print(f"  [晋江] FAIL {type(exc).__name__}: {str(exc)[:70]}")
        for _f in f_q:
            try:
                pool += _f.result()
            except Exception as exc:
                print(f"  [起点] FAIL {type(exc).__name__}: {str(exc)[:70]}")
        _mark_tiers(pool)
        # ★ 2026-09-30：写池缓存（120s 内「刷新」直接复用，不再重抓 8 个源）
        try:
            _save_cache(os.path.join(CACHE_DIR, f"rank_pool_{source}_p{pages}.json"),
                        {"t": time.time(), "pool": pool})
        except Exception:
            pass
        return pool
    if source in ("weread", "all"):
        try:
            books, cids, _st, html = weread_home()
            print(f"[微信读书] /web/category/list  {len(html)}B  → {len(books)} 本  "
                  f"完整率={field_rates(books)}  用时 {time.time()-t0:.1f}s")
            pool += books
        except Exception as exc:
            print(f"[微信读书] FAIL {type(exc).__name__}: {str(exc)[:80]}")
    if source in ("fanqie", "all"):
        for g in ([gender] if gender is not None else [0, 1]):
            try:
                t1 = time.time()
                lst = fanqie_list(g, pages=pages)
                print(f"[番茄 gender={g}] {len(lst)} 本  完整率={field_rates(lst)}  "
                      f"用时 {time.time()-t1:.1f}s")
                pool += lst                  # ★ 补明文改到「抽完之后」（只补选中的 10 本）
            except Exception as exc:
                print(f"[番茄 gender={g}] FAIL {type(exc).__name__}: {str(exc)[:80]}")
    return pool


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default="all",
                    choices=["weread", "fanqie", "all", "linovelib"])
    ap.add_argument("--gender", type=int, default=None)
    ap.add_argument("--warm-only", action="store_true",
                    help="只预热缓存，不输出 @@RANK（供后台子进程用）")
    ap.add_argument("--sort", default="monthvisit",
                    help="轻小说榜单排序键：monthvisit/weekvisit/monthvote/weekvote/"
                         "goodnum/newhot/monthflower/lastupdate")
    ap.add_argument("--pages", type=int, default=5)
    ap.add_argument("--dump", type=int, default=0, help="打印前 N 本")
    ap.add_argument("--pick", type=int, default=0, help="抽样 N 本")
    ap.add_argument("--batch", type=int, default=1)
    ap.add_argument("--json", action="store_true",
                    help="向 stdout 输出一行 @@RANK {json}（供主程序解析）")
    ap.add_argument("--enrich", action="store_true",
                    help="番茄：对书名含 PUA 的书调详情页补明文")
    ap.add_argument("--test", default="", choices=["", "cover", "appcategory", "sort", "rotate"])
    args = ap.parse_args(argv)

    if args.test == "sort":
        test_sort()
        return 0
    if args.test == "appcategory":
        _b, cids, _s, _h = weread_home()
        test_appcategory(cids)
        return 0


    if args.warm_only:
        got = linovelib_board(getattr(args, "sort", "monthvisit"), pages=max(1, args.pages))
        print(f"[预热] {args.sort} 共 {len(got)} 条已入缓存")
        return 0

    pool = build(args.source, args.gender, args.pages, enrich=args.enrich,
                 ln_sort=getattr(args, 'sort', 'monthvisit'))
    print(f"\n池子合计 {len(pool)} 本；来源分布: "
          + str({s: sum(1 for b in pool if b['source'] == s) for s in {b['source'] for b in pool}}))
    # ★ 轻小说榜单：要的是**排名顺序**，所以直接取前 N，不抽样、不按 source 配额。
    if args.source == "linovelib":
        # ★ 整池返回（页面自己按 30 条/页切片），--pick 只作为上限保险
        got = pool[:args.pick] if args.pick else pool
        if not args.warm_only and args.pages < 5:
            ln_warm_background(args.sort, pages=5)   # 预热剩余页，翻页秒开
        if args.json:
            print("@@RANK " + json.dumps({
                "ok": True, "pool": len(pool), "batch": args.batch, "picked": len(got),
                "sources": {s: sum(1 for b in pool if b["source"] == s)
                            for s in {b["source"] for b in pool}},
                "items": got,
                "pages_total": LN_BOARD_PAGES.get(args.sort, 5),
            }, ensure_ascii=False))
        else:
            print(f"\n=== 轻小说·{args.sort} 前 {len(got)} 本 ===")
            for i, b in enumerate(got, 1):
                print(f"  {i:2d}. {b['title'][:24]:26s} {b['author'][:10]:12s} {b['source']}")
        return 0
    _pua = sum(1 for b in pool if PUA_RE.search(b["title"]))
    print(f"书名含 PUA（未解码）: {_pua} 本  "
          + ("✅ 全部可用" if _pua == 0 else "⚠️ 还有未解码"))

    if args.test == "cover":
        test_cover(pool)
    if args.test == "rotate":
        test_rotate(pool)

    if args.dump:
        print(f"\n=== 前 {args.dump} 本 ===")
        for b in pool[:args.dump]:
            print(f"  {b['title'][:22]:24s} {b['author'][:10]:12s} "
                  f"free={b['free']} rate={b['rating']} tags={b['tags']}")
            print(f"      cover={b['cover'][:78]}")
    if args.json or args.pick:
        num = args.pick or 10
        # ★ 已推历史落盘：否则刷新时尾部那几本番茄会每次都重复出现
        #   （实测：无 seen 时批次2/3 的 4 本番茄完全重复）。
        SEEN_PATH = os.path.join(CACHE_DIR, "rank_seen.json")
        _seen = _load_cache(SEEN_PATH).get("titles", []) if args.json else []
        got = pick(pool, n=num, batch=args.batch, seen=_seen)
        # ★ 实测优化：番茄详情页 639 KB/次，全量（120 本）要 ~90s；
        #   只对**选中的 10 本**补明文 → 首屏约 15s。
        if args.enrich:
            enrich_fanqie(got)          # 只补**选中的**那几本（详情页 639KB/次）
        if args.json:
            _save_cache(SEEN_PATH,
                        {"titles": (_seen + [str(b.get("book_id") or b["title"])
                                             for b in got])[-300:]})
        if args.json:
            # ★ 一行机器可读输出（主程序扫 "@@RANK " 前缀）
            print("@@RANK " + json.dumps({
                "ok": True, "pool": len(pool), "batch": args.batch, "picked": len(got),
                "sources": {s: sum(1 for b in pool if b["source"] == s)
                            for s in {b["source"] for b in pool}},
                "items": got,
            }, ensure_ascii=False))
        else:
            print(f"\n=== 批次{args.batch} 抽 {num} 本 ===")
            for i, b in enumerate(got, 1):
                print(f"  {i:2d}. {b['title'][:24]:26s} {b['author'][:10]:12s} {b['source']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
