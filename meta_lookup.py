# -*- coding: utf-8 -*-
"""EasyPub <-> novelmeta 桥接脚本。

主进程用 execFile 调它，**只读最后一行 JSON**（stdout 专用于结果，诊断一律走 stderr）。

    python meta_lookup.py --title "赘婿" [--author "愤怒的香蕉"] \
        [--want author+cover|cover] [--source weread,jjwxc] [--timeout 12] [--json]

输出（单行 JSON）：
    {"ok","want","title","author","cover","cover_data","source","url",
     "has_author","has_cover","ambiguous","candidates":[...],
     "diagnostics":[...],"elapsed_ms","error"}

为什么要有这层：
  · 原 search-author / search-cover 两个 IPC 各拉一个 Python 进程、各查一次；
    这里一次查询把作者和封面一起拿回来，主进程只需解析一行 JSON。
  · `cover_data` 沿用旧契约（data URI），因为渲染层是直接
    `coverPreview.src = result.cover_data; coverImagePath = result.cover_data`。
  · **永不抛异常给主进程**：任何失败都输出合法 JSON 且退出码 0，
    否则主进程只能拿到一句 "Command failed"（项目吃过这个亏）。

环境（由主进程 buildPythonEnv 注入）：
    NOVELMETA_HOME       novelmeta 包所在目录（默认本脚本同级）
    NOVELMETA_IPV4_ONLY  默认 1：本机 IPv6 无出口，首个地址是 AAAA 会让每次查询白等 8 秒
                         （实测 weread 8.40s -> 0.24s）。显式设 0 可关掉。
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
import time
from typing import Any

# --------------------------------------------------------------------------
# 1) 先把环境与 import 路径准备好，再 import novelmeta
# --------------------------------------------------------------------------
# IPv4-only 默认开启：这个应用历史上就栽在「IPv6 黑洞」上（百度上传 43s→0.9s）。
# 实测 novelmeta 走同一台机器：weread 8.40s → 0.24s。
os.environ.setdefault("NOVELMETA_IPV4_ONLY", "1")

_HOME = os.environ.get("NOVELMETA_HOME") or os.path.dirname(os.path.abspath(__file__))
# ★★★ 2026-09-30 修（用户实测「打包后整个找作者和封面都有问题」）：
#   打包版里主进程传的 `NOVELMETA_HOME = APP_ROOT`，而打包版的 `APP_ROOT = path.dirname(process.execPath)`
#   = **EasyPub.exe 所在目录**（`E:\…\win-unpacked`）——`novelmeta\` 却在
#   `resources\app.asar.unpacked\` 下 ✗。原来这里只把 `_HOME` 插进 sys.path，
#   **自己所在目录没插**（`or` 短路了），冻结解释器又不保证 sys.path[0] = 脚本目录
#   → `import novelmeta` 直接失败 → 脚本 ~250ms 就 `_fail`（日志表现：META-RESULT ok:false ms≈250，
#     连 META-ERROR 都没有，因为脚本确实跑起来了、只是 import 挂了）。
#   现在**两个路径都插**（与 `novel_dl.py` 第 78~81 行同一写法）：自己所在目录永远兜底。
_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (_HOME, _HERE):
    if _p and _p not in sys.path:
        sys.path.insert(0, _p)

# 封面转 data URI 的体积上限：IPC 传大 base64 会明显卡顿，
# 而封面本身很小（各源实测 70x100 ~ 400x592，最大几十 KB）。
MAX_COVER_BYTES = 4 * 1024 * 1024


def _stderr(msg: str) -> None:
    """诊断信息（不污染 stdout）。"""
    try:
        print(msg, file=sys.stderr)
    except Exception:  # noqa: BLE001 - 日志失败绝不能影响结果
        pass


def _fail(error: str, want: str, elapsed_ms: int = 0, **extra: Any) -> dict:
    out = {
        "ok": False, "want": want, "title": None, "author": None,
        "cover": None, "cover_data": None, "source": None, "url": None,
        "has_author": False, "has_cover": False, "ambiguous": False,
        "candidates": [], "diagnostics": [], "elapsed_ms": elapsed_ms,
        "error": error,
    }
    out.update(extra)
    return out


# --------------------------------------------------------------------------
# 2) 参数
# --------------------------------------------------------------------------
def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="meta_lookup.py", description="novelmeta 桥接（书名 → 作者/封面）")
    p.add_argument("--title", required=True, help="书名")
    p.add_argument("--author", default="", help="作者提示，用于同名消歧")
    p.add_argument("--want", default="author+cover", choices=["author+cover", "cover"],
                   help="只影响渲染层判成败，查询本身一样")
    p.add_argument("--source", default="", help="只用指定源，逗号分隔（调试用）")
    p.add_argument("--timeout", type=float, default=12.0, help="单源超时秒数（默认 12）")
    p.add_argument("--top", type=int, default=8, help="保留候选数量（默认 8）")
    p.add_argument("--index-file", default="",
                   help="哔哩轻小说索引文件路径（随包预置索引时可指定）")
    p.add_argument("--allow-index-build", action="store_true",
                   help="允许首次查询时现建哔哩轻小说索引（3~4 分钟；默认禁止，避免界面卡死）")
    p.add_argument("--no-cover-data", action="store_true", help="不下载封面（只回 URL，调试用）")
    p.add_argument("--json", action="store_true", help="（默认就是 JSON，保留以兼容调用方）")
    return p


# --------------------------------------------------------------------------
# 3) 封面 → data URI（复用 novelmeta 的下载/校验/尺寸择优）
# --------------------------------------------------------------------------
# ★ 自动填作者/封面所需的最低匹配度：低于它一律**不自动填**（改弹「多作者选择」）。
#   实测教训：长书名的相似度偏宽松，《弹珠汽水瓶里的千岁同学》曾被晋江同人
#   《[排球/影日]弹珠汽水》以 0.92 顶上来，界面就把**别人的作者**填进了书籍信息页。
AUTO_MIN = 0.95

def _norm_author(s: str) -> str:
    """作者名归一化（用于"用户手填的作者 vs 命中作者"比对）：去国别标记/标点/空格，忽略大小写。"""
    t = re.sub(r'[\[（(【][^\]）)】]{0,10}[\]）)】]', '', str(s or ''))   # 去掉 [英]（美）这类标记
    return re.sub(r'[\s·・.,，。、\-—_/|]+', '', t).lower()


def _author_matches(cand_author: str, hint: str) -> bool:
    """命中作者是否与用户手填的作者一致（允许互相包含：'萨拉·杜楠特' vs '萨拉杜楠特'）。"""
    h = _norm_author(hint)
    if not h:
        return True
    a = _norm_author(cand_author)
    if not a:
        return False
    return a == h or a in h or h in a


# 卷级后缀词（★ 要剥的都在这里，加词只改这一处）
_TAIL_DECOR = (r'特典小册子|特典|番外篇|番外|外传|外傳|短篇集|短編集|短篇|后日谈|後日談|前日谈|'
               r'小册子|限定版|特装版|同捆版|extra|special|ex|ss|sp')


def _strip_volume_tail(title: str) -> str:
    """把书名尾部的**卷级后缀**剥掉：`第 4 卷` / `6.5` / `特典` / `EX` / `番外` / `短篇集` / `外传`…

    ★★★ 2026-09-30（用户要求）：「特典、EX、短篇集这些也要剥掉，不只剥第几卷」。
      轻小说卷名里这些极常见（实测站点有「… 短篇集」「… 短篇集 Days of Endless Summer」），
      而各源都是**字面搜索** —— 多一个后缀就 0 结果。
    安全阀：剥完若短于 2 个字，就**放弃这次剥离**（宁可多带后缀，也不能把书名剥没）。
    """
    q = str(title or "").strip()
    for _ in range(5):
        before = q
        # ① 卷号（第 N 卷 / 6.5 卷）与卷级词（特典/EX/短篇集…），可带括号
        q = re.sub(r'[\s·・\-—–_,，、:：]*[（(【\[]?\s*(?:第\s*[0-9一二三四五六七八九十百零〇\.]+\s*[部卷册季集]'
                   r'|[0-9]+(?:\.[0-9]+)?\s*[部卷册季集]'
                   r'|' + _TAIL_DECOR + r')\s*[）)】\]]?\s*$', '', q, flags=re.I).strip()
        if q == before:
            # ② 带分隔符的裸数字尾巴（「… 4」）。**必须**有分隔符，
            #    否则《斗罗大陆3》这种书名里的数字会被误剥。
            q = re.sub(r'[\s·・\-—–_,，、:：]+[0-9]+(?:\.[0-9]+)?\s*$', '', q).strip()
        # ③ 卷级词后面还挂着副标题（「… 短篇集 Days of Endless Summer」）→ 从该词处截断
        _m = re.search(r'[\s·・\-—–_,，、:：]+[（(【\[]?\s*(?:' + _TAIL_DECOR + r')', q, flags=re.I)
        if _m and _m.start() >= 2:
            q = q[:_m.start()].strip()
        if q == before:
            break
        if len(q) < 2:                      # 安全阀
            return before
    return q or str(title or "").strip()


def _cover_data_uri(
    url: str,
    variants: list[str],
    referer: str,
    extra_urls: list[str] | None = None,
) -> tuple[str | None, str, str, tuple[int, int]]:
    """下载封面（同图最高清优选）并转 data URI；返回 (data_uri|None, note, chosen_url, (w, h))。"""
    import tempfile
    import shutil

    from novelmeta import download_cover
    from novelmeta.cover import CoverError

    tmp = tempfile.mkdtemp(prefix="easypub_cover_")
    try:
        got = download_cover(
            url,
            tmp,
            stem="cover",
            variants=variants or None,
            referer=referer or None,
            extra_urls=extra_urls or None,
        )
        if got.bytes > MAX_COVER_BYTES:
            return None, f"封面过大（{got.bytes} B），跳过 data URI", got.url, (got.width, got.height)
        with open(got.path, "rb") as fh:
            raw = fh.read()
        _EXT_MIME = {
            ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
            ".webp": "image/webp", ".gif": "image/gif", ".bmp": "image/bmp",
            ".avif": "image/avif",
        }
        ctype = _EXT_MIME.get(os.path.splitext(got.path)[1].lower())
        if not ctype:
            return (
                None,
                f"未知图片类型（后缀 {os.path.splitext(got.path)[1]!r}，Content-Type {got.content_type!r}），跳过 data URI",
                got.url,
                (got.width, got.height),
            )
        return (
            f"data:{ctype};base64," + base64.b64encode(raw).decode("ascii"),
            f"{got.width}x{got.height} {got.bytes}B {ctype}",
            got.url,
            (got.width, got.height),
        )
    except CoverError as exc:
        return None, f"封面下载失败: {exc}", url, (0, 0)
    except Exception as exc:  # noqa: BLE001 - 封面是加分项，绝不因此失败
        return None, f"封面处理异常: {type(exc).__name__}: {exc}", url, (0, 0)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _search_web_same_cover_urls(title: str, author: str = "", limit: int = 8) -> list[str]:
    """全网搜图补充高清同图候选（仅当首发源分辨率 < 580px 时触发，由 dHash 严格把关同一张图）。"""
    import urllib.parse
    from novelmeta.net import fetch

    q_parts = [str(title or "").strip(), str(author or "").strip(), "小说封面"]
    kw = " ".join(p for p in q_parts if p)
    urls: list[str] = []
    try:
        baidu_url = (
            "https://image.baidu.com/search/acjson?tn=resultjson_com&ipn=rj"
            "&ct=201326592&fp=result&ie=utf-8&oe=utf-8&pn=0&rn=12&word="
            + urllib.parse.quote(kw)
        )
        resp = fetch(
            baidu_url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/125.0.0.0 Safari/537.36",
                "Referer": "https://image.baidu.com/",
                "Accept": "application/json, text/plain, */*",
            },
            timeout=3.5,
            tries=1,
        )
        if resp.status == 200 and resp.body:
            data = json.loads(resp.text, strict=False)
            for item in (data.get("data") or []):
                if not isinstance(item, dict):
                    continue
                for k in ("objURL", "hoverURL", "middleURL", "thumbURL"):
                    u = (item.get(k) or "").strip()
                    if u.startswith("http") and ".gif" not in u.lower():
                        urls.append(u)
                        break
    except Exception:  # noqa: BLE001
        pass
    return list(dict.fromkeys(urls))[:limit]


# --------------------------------------------------------------------------
# 4) 主流程
# --------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    started = time.perf_counter()
    args = _build_parser().parse_args(argv)
    title = (args.title or "").strip()
    want = args.want

    if not title:
        print(json.dumps(_fail("书名不能为空", want), ensure_ascii=False))
        return 0

    try:
        from novelmeta import resolve
        from novelmeta.catalog import BILINOVEL_INDEX
        from novelmeta.sources import source_names
    except Exception as exc:  # noqa: BLE001
        print(json.dumps(_fail(f"novelmeta 导入失败: {type(exc).__name__}: {exc}", want),
                         ensure_ascii=False))
        return 0

    # --- 源选择 -----------------------------------------------------------
    if args.source:
        sources = [s.strip().lower() for s in args.source.split(",") if s.strip()]
        unknown = [s for s in sources if s not in source_names(include_overseas=True)]
        if unknown:
            print(json.dumps(_fail(f"未知数据源: {', '.join(unknown)}", want), ensure_ascii=False))
            return 0
    else:
        sources = list(source_names())

    # --- 哔哩轻小说索引：没有缓存就不查它，绝不在这里现爬 3~4 分钟 --------
    notes: list[str] = []
    if args.index_file:
        # 随包预置索引：直接让 novelmeta 用这份文件
        BILINOVEL_INDEX.path = args.index_file
        notes.append(f"使用随包索引 {args.index_file}")
    if "bilinovel" in sources and "bilinovel" not in (args.source or ""):
        has_index = BILINOVEL_INDEX.age() is not None
        if not has_index and not args.allow_index_build:
            # ★★★ 2026-09-30 改：**不再整源跳过**。`BilinovelSource.search()` 现在遇到冷索引
            #   会直接走站内搜索（秒级）、绝不现爬 178 页 —— 所以留在这个源里是安全的，
            #   而且轻小说的作者/封面往往只有这里有（用户实测：微读 0 条、番茄滑块、晋江同人）。
            notes.append("哔哩轻小说无本地索引 → 该源改走**站内搜索**（不现建索引）")
            _stderr("[meta_lookup] " + notes[-1])

    # --- 查询 -------------------------------------------------------------
    # ★★★ 2026-09-30（用户实测「这个找不到作者」）：**先把标题里的装饰剥掉再去搜**。
    #   制作页拿到的书名常常带卷名/影视后缀（实测传入的是 `弹珠汽水瓶里的千岁同学 第 4 卷`），
    #   而各源都是字面搜索 —— 多一个「 第 4 卷」就什么都搜不到：
    #     meta_lookup --title "弹珠汽水瓶里的千岁同学 第 4 卷" → ok:false, candidates:0, has_author:false
    #     meta_lookup --title "冒姓琅琊"                     → ok:true, 作者+封面齐全
    #   novelmeta 自带的 `search_title()` 就是干这个的（去《》括号、去卷次后缀、去前缀噪声），
    #   之前一直没在这里调用。剥完仍是站点自己的标题参与匹配，不影响结果。
    query = title
    try:
        from novelmeta.model import search_title as _search_title
        query = _search_title(title) or title
        if query != title:
            _stderr(f"[meta_lookup] 书名规范化：{title!r} → {query!r}")
    except Exception:  # noqa: BLE001
        pass
    # ★ 兜底：`search_title()` 的卷次正则要求「第4卷」紧挨着写，**带空格的「第 4 卷」它不认**
    #   （实测：`弹珠汽水瓶里的千岁同学 第 4 卷` 原样返回）。
    #   ★ 2026-09-30（用户要求）：**特典 / EX / 番外 / 短篇集 / 外传 这些也要剥** —— 见 _strip_volume_tail()。
    try:
        _q2 = _strip_volume_tail(query)
        if _q2 and _q2 != query:
            _stderr(f"[meta_lookup] 再去卷次/卷级后缀：{query!r} → {_q2!r}")
            query = _q2
    except Exception as exc:  # noqa: BLE001
        # ★ 别再静默：这里吞过一次 NameError（文件顶部漏了 `import re`），
        #   结果"剥卷名"整段失效、界面一直报"未找到作者"，查了半天才定位。
        _stderr(f"[meta_lookup] 卷次剥离失败（忽略）：{type(exc).__name__}: {exc}")

    # ★★★ 2026-09-30（用户要求）：「**找作者的也是先网文后轻小说**」——
    #   以前所有源并发一起查，最慢的哔哩轻小说（冷索引时走站内搜索）会把整条查询拖住
    #   （实测查《十日终焉》被拖到 37 秒）。现在分两轮、逐轮判定：
    #     第 1 轮：网文源（微信读书 / 晋江 / 番茄）—— 够硬（≥ AUTO_MIN）就直接用，秒回；
    #     第 2 轮：**只有第 1 轮不达标**才查哔哩轻小说（日轻的作者/封面主要靠它）。
    #   两轮的候选与诊断都会合并返回，界面的「多作者选择」弹窗照样能看到全部。
    _all = list(sources or source_names())
    _ln_name = "bilinovel"
    _net_sources = [s for s in _all if s != _ln_name]
    _ln_ok = _ln_name in _all

    def _conf(r) -> bool:
        return bool(r is not None and r.found and r.best is not None
                    and float(r.best.title_score or 0) >= AUTO_MIN)

    def _merge_into(dst, src) -> None:
        """把 src 的候选/诊断并进 dst（去重），供界面的选择弹窗使用。"""
        if dst is None or src is None:
            return
        _have = {(c.title or "", c.author or "") for c in
                 ([dst.best] if dst.best is not None else []) + list(dst.same_title)}
        for _c in ([src.best] if src.best is not None else []) + list(src.same_title) + list(src.alternatives):
            if _c is None:
                continue
            _key = (_c.title or "", _c.author or "")
            if _key in _have:
                continue
            _have.add(_key)
            dst.same_title.append(_c)
        dst.diagnostics = list(dst.diagnostics) + list(src.diagnostics)

    res = None
    try:
        if _net_sources:
            _stderr(f"[meta_lookup] 第 1 轮（网文源）：{', '.join(_net_sources)}")
            res = resolve(query, author_hint=args.author or "", sources=_net_sources,
                          timeout=args.timeout, top=max(1, args.top))
        if _ln_ok and not _conf(res):
            _stderr("[meta_lookup] 第 1 轮没有够硬的命中 → 第 2 轮（哔哩轻小说，站内搜索）")
            _res_ln = resolve(query, author_hint=args.author or "", sources=[_ln_name],
                              timeout=max(args.timeout, 20.0), top=max(1, args.top))
            if res is None or _conf(_res_ln):
                _merge_into(_res_ln, res)
                res = _res_ln
            else:
                _merge_into(res, _res_ln)
        if res is None:                       # 极端情况：没有任何源可选
            res = resolve(query, author_hint=args.author or "", sources=None,
                          timeout=args.timeout, top=max(1, args.top))
    except Exception as exc:  # noqa: BLE001
        elapsed = int((time.perf_counter() - started) * 1000)
        print(json.dumps(_fail(f"查询异常: {type(exc).__name__}: {exc}", want, elapsed),
                         ensure_ascii=False))
        return 0

    # ★★★ 2026-09-30（用户实测「我手动输入的作者变量是不是没传过去」）：
    #   用户**手填了作者**时，必须拿它做**硬校验** —— novelmeta 的 `author_hint` 只用于"加分排序"，
    #   不做过滤，于是出现：
    #     查询《烟花散尽》+ 作者「萨拉·杜楠特」（微读里没有这本）
    #     → 命中**另一个作者**的同名网文（路过天涯，匹配度 1.0）→ 把**别人的封面**配了上去 ✗✗
    #   规则：① 候选里有作者对得上的 → 就用那一条（顺便解决同名书消歧）；
    #         ② 一条都对不上 → **当作"没找到"**（宁可不配封面，也不配错）。
    _hint = (args.author or "").strip()
    if _hint:
        _pool = ([res.best] if res.best is not None else []) \
            + list(getattr(res, "same_title", [])) + list(getattr(res, "alternatives", []))
        _hit = next((c for c in _pool if c is not None and _author_matches(c.author, _hint)), None)
        if _hit is not None and res.best is not None and _hit is not res.best:
            _stderr(f"[meta_lookup] 按手填作者「{_hint}」改选候选："
                    f"{res.best.author!r} → {_hit.author!r}（{_hit.source}）")
            res.best = _hit          # `found` 是只读属性，由 best 推导，不用手动设
        elif _hit is None:
            _stderr(f"[meta_lookup] 手填作者「{_hint}」与所有候选作者都不符 → 视为未找到（不配对封面）"
                    + (f"；最接近的是 {res.best.author!r}" if res.best is not None else ""))
            if res.best is not None:
                res.weak = res.best
            res.best = None          # 由 best=None 自动变成 found=False

    diagnostics = [d.as_dict() for d in getattr(res, "diagnostics", [])]
    if notes:
        diagnostics.append({"source": "meta_lookup", "ok": False, "note": "；".join(notes)})

    if not res.found or res.best is None:
        elapsed = int((time.perf_counter() - started) * 1000)
        out = _fail("未找到匹配的书目", want, elapsed, diagnostics=diagnostics)
        if getattr(res, "weak", None) is not None:
            out["weak_match"] = {
                "title": res.weak.title, "author": res.weak.author,
                "source": res.weak.source, "score": round(res.weak.title_score, 4),
            }
        print(json.dumps(out, ensure_ascii=False))
        return 0

    best = res.best

    # --- 候选（给多作者选择弹窗用）---------------------------------------
    from novelmeta.model import norm_title as _nm_title
    from novelmeta.sources import cover_variants as _cover_variants

    seen: set[tuple[str, str]] = set()
    candidates: list[dict] = []
    for cand in ([best] + list(getattr(res, "same_title", [])) + list(getattr(res, "alternatives", []))):
        key = ((cand.title or "").strip(), (cand.author or "").strip())
        if key in seen:
            continue
        seen.add(key)
        _c_cov = cand.cover or ""
        if _c_cov:
            _c_vars = _cover_variants(_c_cov)
            if _c_vars:
                _c_cov = _c_vars[0]
        candidates.append({
            "title": cand.title, "author": cand.author or "", "cover": _c_cov,
            "source": cand.source, "score": round(cand.title_score, 4),
            "url": cand.url or "", "position": cand.position,
        })
        if len(candidates) >= max(1, args.top):
            break

    # --- 封面 -------------------------------------------------------------
    # ★★★ 2026-09-30（用户实测「这个找不到作者」，结果更糟：**给填了个错的**）：
    #   长书名的 `title_similarity` 偏宽松，会给同人打 0.92 这种"看着挺高"的分 ——
    #   实测查《弹珠汽水瓶里的千岁同学》→ 晋江《[排球/影日]弹珠汽水》(猫山有栖) 0.9218，
    #   于是界面把**别人的作者/封面**填进了书籍信息页。
    #   现在：匹配度 < AUTO_MIN 一律**不自动填**，改成 ambiguous=true + 候选列表 ——
    #   渲染层遇到 ambiguous 会弹「多作者选择」窗让用户自己点（别替他认领一本书）。
    #   （阈值 `AUTO_MIN` 定义在模块顶部，两处共用。）
    confident = float(best.title_score or 0) >= AUTO_MIN
    if not confident:
        _stderr(f"[meta_lookup] 匹配度 {best.title_score:.4f} < {AUTO_MIN} → 不自动填，"
                f"交给界面选（{best.title!r} / {best.author or '?'}）")

    # ★★★ 2026-09-30（用户实测「作者有了，但是他找的不是第四卷的封面」）：
    #   书名里带卷号时，封面要取**那一卷**的 —— 站点的书页上除了一张系列共用封面
    #   （`…/2770s.jpg`），每一卷条目还有自己的封面（`img3.readpai.com/cover/2770/180301.jpg`）。
    _vol_cover = ""
    _vol_cover_ref = ""
    if confident:
        _nid = str((best.extra or {}).get("novelId") or "")
        _vm = re.search(r'第\s*[0-9一二三四五六七八九十百零〇\.]+\s*[部卷册季集]|' + _TAIL_DECOR,
                        title or "", re.I)
        if _nid and _vm:
            try:
                from linovelib import volume_cover_for
                _vol_cover = volume_cover_for(_nid, title, title=best.title or "")
            except Exception as exc:  # noqa: BLE001
                _stderr(f"[meta_lookup] 取分卷封面失败（忽略）：{type(exc).__name__}: {exc}")
            if _vol_cover:
                # ★ readpai CDN **必须带 Referer**（实测不带 → HTTP 403，带站点页 → 200/48KB），
                #   所以分卷封面得自己塞一个 referer，不能沿用系列封面那套。
                _vol_cover_ref = f"https://www.linovelib.com/novel/{_nid}.html"
                _stderr(f"[meta_lookup] 用**分卷封面**（{_vm.group(0).strip()}）：{_vol_cover}")
            else:
                _stderr(f"[meta_lookup] 没取到「{_vm.group(0).strip()}」的封面 → 退回系列封面")

    cover_url = (_vol_cover or (best.cover or "")) if confident else ""
    cover_data = None
    cover_note = ""
    cover_dims = (0, 0)
    if cover_url and not args.no_cover_data:
        _same_book_extras: list[str] = []
        if not _vol_cover:
            _nb_title = _nm_title(best.title)
            _pool = ([best] + list(getattr(res, "same_title", [])) + list(getattr(res, "alternatives", [])))
            for _c in _pool:
                if _c is None:
                    continue
                if _nm_title(getattr(_c, "title", "")) != _nb_title:
                    continue
                if best.author and getattr(_c, "author", "") and not _author_matches(_c.author, best.author):
                    continue
                _cu = (getattr(_c, "cover", "") or "").strip()
                if _cu and _cu != cover_url:
                    _same_book_extras.append(_cu)
                _cex = getattr(_c, "extra", None) or {}
                for _vu in list(_cex.get("coverVariants") or []) + (_cover_variants(_cu) if _cu else []):
                    if _vu and _vu != cover_url:
                        _same_book_extras.append(_vu)
            _same_book_extras = list(dict.fromkeys(_same_book_extras))

        cover_data, cover_note, chosen_url, cover_dims = _cover_data_uri(
            cover_url,
            [] if _vol_cover else list(best.extra.get("coverVariants") or []),
            _vol_cover_ref or best.extra.get("coverReferer") or "",
            extra_urls=_same_book_extras,
        )
        if chosen_url and cover_data:
            cover_url = chosen_url
        if cover_note:
            _stderr(f"[meta_lookup] 封面同图优选: {cover_note} <- {cover_url}")

        # 若已拿到的同图分辨率仍 < 580px（如微读 t9_ 为 428x570 且不在起点图床），补充全网搜图找更大同图
        if cover_data and not _vol_cover and max(cover_dims) < 580:
            _web_urls = _search_web_same_cover_urls(best.title, best.author or "")
            if _web_urls:
                _w_data, _w_note, _w_url, _w_dims = _cover_data_uri(
                    cover_url,
                    list(best.extra.get("coverVariants") or []),
                    best.extra.get("coverReferer") or "",
                    extra_urls=_same_book_extras + _web_urls,
                )
                if _w_data and (_w_dims[0] * _w_dims[1]) > (cover_dims[0] * cover_dims[1]):
                    cover_data, cover_note, cover_url, cover_dims = _w_data, _w_note, _w_url, _w_dims
                    _stderr(f"[meta_lookup] 全网同图高清升级: {cover_note} <- {cover_url}")

    MIN_COVER_PX = 200

    def _px(note: str) -> int:
        _m = re.match(r'(\d+)x(\d+)', note or '')
        return max(int(_m.group(1)), int(_m.group(2))) if _m else 0

    if cover_data and not _vol_cover and _px(cover_note) < MIN_COVER_PX:
        _best_px = _px(cover_note)
        _tries = 0
        _pool = ([best] + list(getattr(res, "same_title", [])) + list(getattr(res, "alternatives", [])))
        for _c in _pool:
            if _tries >= 3 or _best_px >= MIN_COVER_PX:
                break
            if (getattr(_c, "title", "") or "") != (best.title or ""):
                continue
            if best.author and getattr(_c, "author", "") and not _author_matches(_c.author, best.author):
                _stderr(f"[meta_lookup] 小图升级跳过一个同名异作者候选："
                        f"《{_c.title}》{_c.author}（选中这本是 {best.author}）")
                continue
            _cex = getattr(_c, "extra", None) or {}
            _u = (getattr(_c, "cover", "") or "").strip()
            if not _u or _u == cover_url:
                continue
            _tries += 1
            _d2, _n2, _u2, _ = _cover_data_uri(_u, list(_cex.get("coverVariants") or []),
                                               _cex.get("coverReferer") or "")
            if _d2 and _px(_n2) > _best_px:
                cover_url, cover_data, cover_note = _u2, _d2, _n2
                _best_px = _px(_n2)
                _stderr(f"[meta_lookup] 小图升级 → {getattr(_c, 'source', '?')} 的封面 {_best_px}px")

    elapsed = int((time.perf_counter() - started) * 1000)
    out = {
        "ok": True,
        "want": want,
        "title": best.title,
        "author": (best.author or "") if confident else "",
        "cover": cover_url,
        "cover_data": cover_data,
        "source": best.source,
        "url": best.url or "",
        "has_author": bool(confident and (best.author or "").strip()),
        "has_cover": bool(cover_data),
        "cover_note": cover_note,
        "ambiguous": (not confident) or bool(getattr(res, "ambiguous", False)),
        "title_match": round(best.title_score, 4),
        "score": round(best.score, 4),
        "candidates": candidates,
        "diagnostics": diagnostics,
        "elapsed_ms": elapsed,
        "error": None,
    }
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
