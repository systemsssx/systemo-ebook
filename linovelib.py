#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""linovelib.py —— 哔哩轻小说（linovelib.com）抓取 + EPUB 组装。

★ 路线：**不走站的 /download 接口**（那要积分、要登录），而是抓**公开章节页 + 插图**
  自己组装 EPUB → **零积分、免登录、带插图**。

★★★★★ 2026-10-01（用户拍板）：**路线简化成「书号 → 目录页 → 正文页」，分卷页那一层删掉了** ★★★★★
  目录页 GET https://www.linovelib.com/novel/{id}/catalog **一次请求就把整本书给全了**：
    · <title> → 书名 + 作者；<h1> → 干净书名
    · 每个 <div class="volume"> = 一卷：卷名（h2.v-line）、卷URL、**该卷封面**（img data-original）、
      **该卷全部章节**（ul.chapter-list 里的 <a href="/novel/{id}/{chid}.html">，顺序即阅读顺序）
  实测：3095 → 1 个请求拿到 12 卷 / 211 章；2013 → 1 个请求拿到 36 卷 / 595 章。
  于是 **书页（1 次）和分卷页（每卷 1 次）都不用请求了** —— 请求数 1+N → 1。
  · 书页只在「目录页解析不出分卷」时才碰一次，而且**只为了拿一句"被版权方屏蔽"的人话说明**
    （不再从书页解析卷册）；
  · 分卷页 vol_XXXXX.html **整个流程一次都不再请求**（章节直接来自目录页那一块）。
  · 想看某卷的详情/封面仍可用 `volume_covers()`，它现在也走目录页，**顺序天然是阅读顺序**
    （老实现读书页、站点按更新时间倒序，所以当时必须 reverse()——现在不需要了）。

实测依据（2026-09-29，逐项验过）：
  目录页 GET https://www.linovelib.com/novel/{id}/catalog      → 分卷 + 每卷章节 + 每卷封面（★ 主路）
  书页   GET /novel/{id}.html                                  → 仅兜底：屏蔽说明 + 系列封面
  章节页 正文容器 id="TextContent"：**静态 HTML**（21 个 script 全无 atob/base64/fromCharCode
         → 无混淆），正文是 <p>…</p> + 插图 <img data-src="https://img3.readpai.com/...">
  ★ 插图防盗链：无 Referer → HTTP 403；带 Referer: https://www.linovelib.com/ → 200 image/jpeg（原图 200~330KB）
  ★ 全程无 Cloudflare 挑战（下载页/主页/分卷/章节 均实测 200）
  ★ 插图按**原始 DOM 顺序**保留（段-图-段 交错也能还原）

命令：
  py linovelib.py --info https://www.linovelib.com/novel/3095.html
  py linovelib.py --get  https://www.linovelib.com/novel/3095.html --out C:\\work\\download
  py linovelib.py --get  3095 --vol 1            # 只下第 1 卷
  py linovelib.py --get  3095 --vol 1 --limit-chapters 3   # 抽 3 章（快速验证）
  py linovelib.py --get  3095 --json             # 机器可读输出（@@INFO / @@PROGRESS / 末行 JSON）

★ 2026-10-01：**Electron 主进程用的那套参数**（main.js 的 runLinovelib 按这个拼 argv）：
  <python> -X utf8 linovelib.py <target> --get --json
           --out <中转目录>            # 产物先落这里，成功后再由主进程搬进书库
           [--vol 1 3 …]               # 前端勾了哪几卷（1 起）；不传 = 全部卷
           [--base https://www.bilinovel.com]
           [--cookie-file <userData>\\linovelib-cookies.txt]
           [--auto-cookie]             # cf_clearance 过期时自动续期（会弹一次 Edge）
  --refresh-cookie 是**独立的"只续期"入口**（配 --cookie-file/--base），给界面按钮用，
  不需要 target。打包后 cookie 文件与 _ln_tmp 都必须由主进程指到可写目录（见下面各自的注释）。
"""

from __future__ import annotations

import argparse
import gzip
import html as H
import json
import os
import re
import sys
import tempfile
import threading
import time
import urllib.request
import zipfile
import zlib
from concurrent.futures import ThreadPoolExecutor

_HERE = os.path.dirname(os.path.abspath(__file__))
# ★ 2026-10-01（接回 Electron）：novelmeta 的查找路径**不再写死开发机的 C:\work\novelmeta**。
#   打包后 `_HERE` = resources\app.asar.unpacked（package.json 的 asarUnpack 已含 `novelmeta/**`），
#   所以第一顺位永远是**脚本同级的 novelmeta/**；只有它不存在时才看环境变量 NOVELMETA_HOME
#   （以及旧的开发机路径，且**必须真实存在**才加），避免把无效路径塞进 sys.path。
_NOVELMETA_DIRS = [_HERE, os.environ.get("NOVELMETA_HOME", ""), r"C:\work\novelmeta"]
for _p in _NOVELMETA_DIRS:
    if _p and os.path.isdir(_p) and _p not in sys.path:
        sys.path.append(_p)
os.environ.setdefault("NOVELMETA_IPV4_ONLY", "1")

from novelmeta.net import fetch  # noqa: E402

# ★★★ 2026-10-02（用户：「但是之前很快啊」→ 实测定位）★★★
#   **默认域名从 linovelib.com 换成 bilinovel.com。**
#   实测（同一份 cookie、同一天、隔几分钟各测一次）：
#     www.bilinovel.com   HTTP 200  140KB  **2.1 ~ 2.7 秒**   稳定
#     www.linovelib.com   HTTP 200   84KB  **1.2 秒 → 53 秒 → 107 秒**   ★ 抖动极大
#     tw.linovelib.com    ❌ 超时
#   而 linovelib.com 慢的时候，界面表现就是「点了查询半天没反应 / 看不到分卷」。
#   ⚠️ 站内搜索不受影响：那走的是 novelmeta 的域名常量（见 novelmeta/catalog.py），
#      跟这里的 BASE 是两套。`_ALT_BASES` 也保留，`negotiate_base()` 仍会在需要时换域。
#   目录页解析已同步适配 bilinovel 的结构（`catalog-volume` / `<h3>` / `data-src`），
#   实测 2013：**36 个卷块，卷名/章节数/封面全部正确**。
BASE = "https://www.bilinovel.com"
REF = BASE + "/"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36")
_HDR = {"User-Agent": UA, "Referer": REF, "Accept-Language": "zh-CN,zh;q=0.9",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"}

# ★★★ 2026-09-30（实测打通 2139《Re:从零开始的异世界生活》）：
#   站方对**没过 Cloudflare 人机验证的请求**只发截断正文（~530 字 +「（內容加載失敗！請刷新或更換瀏覽器）」），
#   浏览器带着 cf_clearance 等 Cookie 才发全文（同一 IP 实测：无 Cookie 533 字截断 / 带 Cookie 612 字完整）。
#   从浏览器 F12 拷出 Cookie 存到 <脚本目录>/cookies.txt（或 --cookie-file / env LINOVELIB_COOKIE）即可。
#   ⚠️ cf_clearance 有时效（通常约 30 分钟~数小时）：过期后正文重新变截断，
#   脚本会以 ChapterBlocked 报「正文被站方截断」——回浏览器刷新章节页、重拷 Cookie 再跑。


def _apply_cookie(cookie: str) -> None:
    cookie = (cookie or "").strip()
    if cookie:
        _HDR["Cookie"] = cookie
    else:
        _HDR.pop("Cookie", None)


def _load_cookie_file(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return ""


# ★★★ 2026-10-01（接回 Electron）：**"当前 Cookie 文件"必须记成全局状态** ★★★
#   原来的 bug：主进程如果传 `--cookie-file D:\userData\linovelib-cookies.txt`，
#   启动时确实读了它；但 `_refresh_cookie_via_browser()` 续期成功后**又回去读
#   `<脚本目录>/cookies.txt`** —— 于是续期白做（新证写到了 A，程序还在读 B）。
#   现在只认这一个变量：`--cookie-file` / env / 默认值都写它，续期也写它、读它。
_COOKIE_FILE = os.environ.get("LINOVELIB_COOKIE_FILE") or os.path.join(_HERE, "cookies.txt")


def _set_cookie_file(path: str) -> None:
    """记住本次要用的 Cookie 文件路径（主进程用 --cookie-file 传进来的那个）。"""
    global _COOKIE_FILE
    path = (path or "").strip()
    if path:
        _COOKIE_FILE = os.path.abspath(path)


# 导入时先看 env 与脚本旁 cookies.txt
_apply_cookie(os.environ.get("LINOVELIB_COOKIE", "")
              or _load_cookie_file(_COOKIE_FILE))


# ★★★ 2026-10-01（接回 Electron）：**插图中转目录必须可写** ★★★
#   已落地的图先写到 _ln_tmp，再由 build_epub 从那儿读进 EPUB。
#   原来写死 `<脚本目录>/_ln_tmp`。开发模式没问题；**打包后脚本目录是
#   resources\app.asar.unpacked\（perMachine 安装 = Program Files）**，
#   普通用户既建不了目录也写不进文件 → `os.makedirs` 直接 PermissionError，
#   整个下载在"下插图"这一步崩掉。现在按顺序挑第一个真能写的：
#     ① env LINOVELIB_WORK_DIR（主进程可指到 userData）
#     ② <脚本目录>/_ln_tmp（开发模式，保持原样）
#     ③ %TEMP%\linovelib_tmp（打包后的兜底）
def _probe_writable(path: str) -> bool:
    try:
        os.makedirs(path, exist_ok=True)
        probe = os.path.join(path, ".w_test")
        with open(probe, "w", encoding="utf-8") as fh:
            fh.write("1")
        os.remove(probe)
        return True
    except OSError:
        return False


def _work_dir() -> str:
    """插图中转目录（可写的那一个）。"""
    cands = [os.environ.get("LINOVELIB_WORK_DIR", ""), os.path.join(_HERE, "_ln_tmp"),
             os.path.join(tempfile.gettempdir(), "linovelib_tmp")]
    for d in cands:
        if d and _probe_writable(d):
            return d
    return tempfile.gettempdir()          # 理论上到不了；实在不行也别让主流程崩

# ★★★ 2026-09-30（用户：「如何设计一个自动脚本在失效后去拿cookie」）：
#   refresh_cookie.py（Selenium+Edge 过人机验证）自动续期；两种触发——
#   ① 主动：cf_clearance 签发满 25 分钟就续（趁没过期）；
#   ② 被动：抓到「正文被站方截断」= 通行证已过期 → 立刻续期并重试。
#   开关：--auto-cookie（要弹一次小 Edge 窗口，约 10 秒）。
COOKIE_MAX_AGE = 25 * 60          # cf_clearance 通常 30 分钟~几小时有效，25 分钟主动续


def _cf_cookie_age() -> float:
    """cf_clearance 值里的 10 位时间戳 = 签发时刻 → 返回年龄（秒）；取不到返回 inf。"""
    m = re.search(r"cf_clearance=[^;]*?-(\d{10})-", _HDR.get("Cookie", ""))
    if not m:
        return float("inf")
    return max(0.0, time.time() - int(m.group(1)))


def _refresh_cookie_via_browser(novel_id: str = "", landing: str = "",
                                timeout: float = 300.0) -> bool:
    """取 Cookie —— ★★★ 2026-10-02（用户定稿）：**这是唯一的取 Cookie 路径** ★★★

    用户原话：「用 bilinovel 的那个网址，然后到
              https://www.bilinovel.com/novel/2139/catalog 这个网址后
              直接弹窗提醒用户点一下 第一章 结束的开始，然后进正文继续爬」
             「cookie 只保留这一条路径」

    流程：打开 `<bilinovel>/novel/<书号>/catalog` → 界面弹窗提醒用户点**第一章**
          → 等用户点进正文页 → 在那一刻抓证 → 非破坏性合并写回 `_COOKIE_FILE`。

    ★ 以前那套「证龄超 25 分钟就自动续」「换域名就 force 续」「截断就 force 续」**全部删掉**：
      · 它们会**反复弹 Edge**（实测 2 分钟弹 3 次），因为 cf_clearance 按域签发、
        而三个镜像域名共用一个 cookie 文件 → 互相覆盖 → 永远续不完；
      · 对一个**需要权限**的书，续期再多次也没用（站点原话「需要足夠的權限」）。
      现在只有一条路，且**由用户决定什么时候点**。

    成功判据仍是 **cookie 文件里的 cf_clearance 变新了**（不看 returncode ——
    实测过"证已经换好了、子进程却因为打印 emoji 失败而退出码 1"）。
    """
    import subprocess
    try:
        os.makedirs(os.path.dirname(_COOKIE_FILE) or ".", exist_ok=True)
        before = _cf_cookie_age()
        # ★ 带 `-X utf8`：子进程 stdout 是管道，不带的话 Windows 按 GBK 编码，
        #   脚本最后那句话会 UnicodeEncodeError → 退出码 1
        #   （refresh_cookie.py 里也做了 reconfigure 兜底，这里是双保险）。
        #   打包后 sys.executable 是 easypub-backend.exe，后端入口会自动剥掉 `-X utf8`。
        cmd = [sys.executable, "-X", "utf8", os.path.join(_HERE, "refresh_cookie.py"),
               "--cookie-file", _COOKIE_FILE, "--timeout", str(float(timeout))]
        if landing:
            cmd += ["--landing", landing]
        elif novel_id:
            cmd += ["--novel-id", str(novel_id)]
        # ★★★ 2026-10-02（用户定稿）：**必须实时转发子进程的 @@ 行** ★★★
        #   refresh_cookie.py 会「从主页一路点，点到目录页就发 @@NEEDCLICK 让你弹窗」，
        #   而它是在**等用户点第一章**的过程中发的 —— 用 `subprocess.run(capture_output=True)`
        #   会把它憋到进程结束才拿到，界面根本弹不出来（等于没提醒）。
        #   所以改成 Popen + **逐行读 stdout**：`@@` 开头的原样转发到**我们自己的 stdout**
        #   （main.js 的 `@@NEEDCLICK` 分支就在那儿接），其余诊断进 stderr。
        #   stderr 另开线程读，避免它写满管道把子进程卡死。
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             text=True, encoding="utf-8", errors="replace", bufsize=1,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        _errbuf = []

        def _drain_err():
            try:
                for _l in p.stderr:
                    _l = _l.rstrip("\n")
                    if _l.strip():
                        _errbuf.append(_l)
                        _say(_l)
            except Exception:  # noqa: BLE001
                pass

        _t = threading.Thread(target=_drain_err, daemon=True)
        _t.start()
        try:
            for line in p.stdout:
                line = line.rstrip("\n")
                if not line.strip():
                    continue
                if line.startswith("@@"):
                    log(line)            # ★ 转发 → 界面实时收到（弹窗靠这条）
                else:
                    _say(line)
            p.wait(timeout=float(timeout) + 90.0)
        except subprocess.TimeoutExpired:
            p.kill()
            _say("取 Cookie 超时（进程已杀）")
        finally:
            try:
                _t.join(timeout=3.0)
            except Exception:  # noqa: BLE001
                pass
        _apply_cookie(_load_cookie_file(_COOKIE_FILE))
        after = _cf_cookie_age()
        if after < before - 1 or (before == float("inf") and after < COOKIE_MAX_AGE):
            _say(f"✅ Cookie 已取到（浏览器里进正文页后抓的证，新证年龄 {after/60:.1f} 分钟）")
            return True
        _say(f"取 Cookie 失败: {(' | '.join(_errbuf[-3:]) or '(无输出)')[:300]}")
        return False
    except Exception as exc:  # noqa: BLE001
        _say(f"取 Cookie 异常: {type(exc).__name__}: {str(exc)[:120]}")
        return False


def _ask_user_click_first_chapter(novel_id: str, volumes: list) -> bool:
    """★★★ 唯一的取 Cookie 路径 ★★★ —— 提醒用户点第一章，然后等他把证拿回来。

    先发 `@@NEEDCLICK {…}`（界面据此弹窗，见 main.js / index.html），
    再调 `_refresh_cookie_via_browser()`（它会打开目录页并在那里等）。

    `chapter` 用**本卷第一章的标题**（用户原话「点一下 第一章 结束的开始」），取不到就留空。
    """
    first = ""
    for v in (volumes or []):
        chs = v.get("chapters") or []
        if chs:
            first = (chs[0][0] or "").strip()
            break
    landing = f"https://www.bilinovel.com/novel/{novel_id}/catalog"
    log("@@NEEDCLICK " + json.dumps({
        "novelId": str(novel_id), "catalog": landing, "chapter": first,
    }, ensure_ascii=False))
    log(f"  👉 这本书要登录/权限 —— 已打开 {landing}")
    log(f"  👉 请在浏览器里点一下第一章{f'「{first}」' if first else ''}，点进去后自动继续…")
    return _refresh_cookie_via_browser(novel_id=str(novel_id), timeout=300.0)


# ★★★ 2026-10-02（用户定稿「cookie 只保留这一条路径」）★★★
#   原来的 `_ensure_cookie(auto, force)`（主动：证龄>25 分钟就续；force：换域名/截断时立刻续）
#   **整个删掉了**。它有三个触发点，全都会**反复弹 Edge**：
#     · linovelib.py:1378 每卷一开跑就 _ensure_cookie(auto_cookie)
#     · linovelib.py:1299 协商换域名时 _renew_for() → force=True（**无条件**）
#     · linovelib.py:1435 检测到「站方截断」→ force=True
#   实测后果（日志 upload-2026-10-02.log）：**2 分钟内续 3 次**，其中 2 次
#   `NoSuchWindowException`（连续开 Edge 撞车）。根因是 cf_clearance **按域签发**、
#   而三个镜像域名**共用一个 cookie 文件** → 给 B 续期就覆盖 A 的证 → 下次探 A 又报
#   截断 → 再续 A → 覆盖 B → **永远续不完**（10 次续期 10 次成功，却永远不收敛）。
#   现在取 Cookie 只有一条路：`_ask_user_click_first_chapter()` → `refresh_cookie.py`。
COOKIE_FETCH_TIMEOUT = 300.0      # 等用户点第一章的秒数


def log(msg: str) -> None:
    print(msg, flush=True)


# ★ 2026-09-30：后台跑（输出重定向到文件）时 Windows 默认 GBK，emoji/书名号会
#   UnicodeEncodeError 直接崩——强制 stdout/stderr 走 UTF-8。
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass


def _urllib_get(url: str, timeout: float, headers: dict):
    """★ 2026-10-01（实测定位，必看）：**本站必须走 urllib，不能走 novelmeta.net.fetch。**

    同一个 URL、同一套请求头、同一张 Cookie，只换 HTTP 栈：

        linovelib.com/novel/2139/76674.html
          · urllib.request          → ✅ 4019 字完整正文（连跑多次稳定）
          · novelmeta.net.fetch     → ❌ 「沒有可閱讀的章節」锁页
        bilinovel.com/novel/2139/76674.html
          · urllib.request          → ✅ 4055 字
          · novelmeta.net.fetch     → ✅ 4055 字（这个域它没事）

    排除过程（都不是原因）：请求头（把 novelmeta 的默认头 Connection: close /
    Accept-Encoding 全加上，urllib 照样过）、`NOVELMETA_IPV4_ONLY` 开关（1 和 0 都锁）、
    UA、Accept、Accept-Language 的**取值**（换成我们的旧值也过）、域名、Cookie。
    差异只在 fetch 的连接层：它绕开 urllib，自己 `http.client` + 把请求钉到某个解析 IP +
    自己的头顺序 —— 站点/CF 据此把它识别成机器人。

    所以这里直接用标准库发请求；`_get()` 只在 urllib 真失败时才退回 fetch。
    """
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
        enc = (r.headers.get("Content-Encoding") or "").lower()
        status = r.status
    if enc == "gzip":
        try:
            raw = gzip.decompress(raw)
        except OSError:
            pass
    elif enc == "deflate":
        try:
            raw = zlib.decompress(raw, -zlib.MAX_WBITS)
        except zlib.error:
            try:
                raw = zlib.decompress(raw)
            except zlib.error:
                pass
    return _Resp(status, raw)


class _Resp:
    """和 novelmeta.net.fetch 的 Response 保持同样的两个字段（status / body），
    这样调用方（含插图下载）一行都不用改。"""

    __slots__ = ("status", "body")

    def __init__(self, status: int, body: bytes):
        self.status = status
        self.body = body


def _get(url: str, timeout: float = 25.0, tries: int = 2):
    """优先 urllib（见 _urllib_get 的实测说明），失败才退回 novelmeta 的 fetch。"""
    last = None
    last_st = None
    for i in range(max(1, int(tries))):
        try:
            return _urllib_get(url, timeout, _HDR)
        except Exception as exc:  # noqa: BLE001
            last, last_st = exc, f"{type(exc).__name__}: {str(exc)[:80]}"
            if i + 1 < max(1, int(tries)):
                time.sleep(0.4 * (i + 1))
    try:
        return fetch(url, headers=_HDR, timeout=timeout, tries=1)
    except Exception:  # noqa: BLE001
        raise last if last is not None else RuntimeError(last_st or "请求失败")


def _text(url: str, timeout: float = 25.0) -> str:
    # ★ 所有 HTML 页面请求都过全局节流（图片不走这里、走 CDN，不受影响）
    _THROTTLE.wait()
    # ★★★ 2026-10-01：受保护的书（2139/2013）urllib 只能拿到锁页 ——
    #   这时改由**常驻浏览器**去 fetch 同一 URL（见 _BrowserSession 的注释）。
    if _BROWSER_ON:
        html = _browser_get(url)
        if html:
            return html
        _say(f"⚠️ 浏览器取页没成功，回退普通请求：{url}")
    return _get(url, timeout=timeout).body.decode("utf-8", "replace")


def parse_id(url_or_id: str) -> str:
    m = re.search(r"/novel/(\d+)", url_or_id or "")
    if m:
        return m.group(1)
    return (url_or_id or "").strip() if (url_or_id or "").strip().isdigit() else ""


# ══════════════════════════════════════════════════════════════════════════
# 解析
# ══════════════════════════════════════════════════════════════════════════
def _clean_text(s: str) -> str:
    """去标签 + 压空白。"""
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", s or "")).strip()


def _volume_label(raw: str, title: str, idx: int) -> str:
    """卷名归一化：去掉重复的书名前缀 → 纯数字补「第 N 卷」→ 空的用序号兜底。

    例：「败北女角太多了！ 8.5」→「第 8.5 卷」；「… SSS短篇集」→「SSS短篇集」；
        「无职转生 ～到了异世界就拿出真本事～ 1 幼年期」→「1 幼年期」。
    """
    n = (raw or "").strip()
    if title and n.startswith(title):
        n = n[len(title):].strip(" -_·|/　")
    if re.fullmatch(r"\d+(?:\.\d+)?", n or ""):
        n = f"第 {n} 卷"
    return n or f"第 {idx} 卷"


# 目录页里每个分卷是一个 <div class="volume clearfix"> —— 注意**必须按"类名完全等于
# volume 这个词"来认**：用 `\bvolume\b` 会把 `volume-list` / `volume-info` / `volume-cover`
# 一起匹配进来（`-` 也算词边界），于是块被切得乱七八糟。
_DIV_CLASS_RE = re.compile(r'<div\b[^>]*\bclass="([^"]*)"[^>]*>', re.I)


def catalog_tree(novel_id: str, *, timeout: float = 25.0) -> dict:
    """★★★ 2026-10-01（用户拍板「拿到书号直接去正文页」）：**目录页 = 唯一的书单来源** ★★★

    一次 `GET /novel/{id}/catalog` 就把整本书的结构拿全了（实测 3095=12 块 / 2013=36 块）：

      <title>败北女角太多了！小说在线阅读_雨森焚火作品_小学馆_哔哩轻小说</title>   ← 书名 + 作者
      <h1>败北女角太多了！</h1>                                                  ← 干净书名
      <div class="volume clearfix">
          <a class="volume-cover"><img src="/images/book-cover-no.svg"
                 data-original="https://img3.readpai.com/cover/3095/200726.jpg"></a>  ← 该卷封面
          <h2 class="v-line"><a href="/novel/3095/vol_154930.html">…1</a></h2>          ← 卷名 + 卷URL
          <ul class="chapter-list">
              <li class="col-4"><a href="/novel/3095/154931.html">插图</a></li>          ← 该卷章节（有序）
              …
          </ul>
      </div>

    **所以书页和分卷页这两跳可以整个省掉** —— 请求数从「1（书页）+ N（分卷页）」降到 **1**。
    返回 {"title","author","cover","volumes":[{name,url,cover,chapters:[(标题,URL)]}]}。
    """
    h = _text(f"{BASE}/novel/{novel_id}/catalog", timeout=timeout)
    title = ""
    mh = re.search(r"<h1[^>]*>([\s\S]*?)</h1>", h)
    if mh:
        title = _clean_text(mh.group(1))
    if not title:
        mt = re.search(r"<title>([^<]+)</title>", h)
        if mt:      # 「书名小说在线阅读_作者作品_…」→ 去掉尾巴
            title = re.sub(r"(小说在线阅读|分卷章节目录).*$", "", mt.group(1)).strip(" _-")
    author = ""
    mt = re.search(r"<title>[^<]*?_([^_]{2,20})作品", h)
    if mt:
        author = mt.group(1).strip()
    if not author:
        ma = re.search(r"作者[：:]\s*([^<\s]{2,20})", h)
        if ma:
            author = ma.group(1).strip()

    # ★★★ 2026-10-02（用户报「连分卷信息都看不到了」）★★★
    #   **两个镜像域名的目录页结构不一样**，这里必须都认：
    #
    #   linovelib.com（老结构）：
    #       <div class="volume clearfix">
    #         <h2 class="v-line"><a href="/novel/3095/vol_154930.html">…1</a></h2>
    #         <ul class="chapter-list"><li class="col-4"><a href="…">插图</a></li>…</ul>
    #
    #   bilinovel.com（新结构，实测 2013）：
    #       <div class="catalog-volume"><ul class="volume-chapters">
    #         <li class="chapter-bar chapter-li">
    #             <a href="/novel/2013/vol_72033.html"><h3>无职转生 … 1 幼年期</h3></a></li>
    #         <li class="volume-cover chapter-li">
    #             <a class="volume-cover-img"><img src="…no.svg" data-src="https://img3.readpai.com/cover/2013/163854.jpg" alt="卷名"/></a></li>
    #         <li class="chapter-li jsChapter"><a href="/novel/2013/122012.html"><span class="chapter-index ">插图</span></a></li>…
    #
    #   ⚠️ 原来只判 `"volume" in m.group(1).split()` —— `catalog-volume` 是**一个整词**，
    #      所以 bilinovel 上一个卷都切不出来 → `"volumes": []` → 界面看不到分卷。
    starts = [m.start() for m in _DIV_CLASS_RE.finditer(h)
              if ("volume" in m.group(1).split()) or ("catalog-volume" in m.group(1).split())]
    if not starts:
        # 再兜一层：完全按结构无关的方式切 —— 用 `vol_NNN.html` 链接的位置切块
        # （实测两个域名都有 72 个 vol_ 链接、595 个章节链接，链接是齐的）
        starts = [m.start() for m in re.finditer(
            r'href="/novel/%s/vol_\d+\.html"' % re.escape(str(novel_id)), h)]
    _say(f"  目录页解析：{len(starts)} 个卷块（BASE={BASE}）")
    vols = []
    for i, s in enumerate(starts):
        e = starts[i + 1] if i + 1 < len(starts) else len(h)
        blk = h[s:e]
        mu = re.search(r'href="(/novel/%s/vol_\d+\.html)"' % re.escape(str(novel_id)), blk)
        mn = re.search(r'<h2[^>]*class="[^"]*v-line[^"]*"[^>]*>([\s\S]*?)</h2>', blk)
        if not mn:
            # ★ 2026-10-02：bilinovel 的卷名在 `<a href="…vol_….html"><h3>卷名</h3></a>` 里
            mn = re.search(r'href="[^"]*vol_\d+\.html"[^>]*>\s*<h3[^>]*>([\s\S]*?)</h3>', blk)
        if not mn:
            # 再兜：卷链接自身的文字
            mn = re.search(r'href="[^"]*vol_\d+\.html"[^>]*>([^<]{2,120})<', blk)
        raw_name = _clean_text(mn.group(1)) if mn else ""
        if not raw_name:                       # 兜底：封面图的 alt 就是卷名
            mAlt = re.search(r'<img[^>]+alt="([^"]{1,120})"', blk)
            raw_name = (mAlt.group(1) if mAlt else "").strip()
        mc = (re.search(r'data-original="([^"]+)"', blk)
              or re.search(r'data-src="([^"]+)"', blk)
              or re.search(r'<img[^>]+src="([^"]+)"', blk))
        cover = (mc.group(1) if mc else "").strip()
        if cover.startswith("//"):
            cover = "https:" + cover
        elif cover.startswith("/"):
            cover = BASE + cover
        chs, seen = [], set()
        for cm in re.finditer(
                r'href="(/novel/%s/\d+\.html)"[^>]*>([\s\S]*?)</a>' % re.escape(str(novel_id)), blk):
            u, inner = cm.group(1), cm.group(2)
            if u in seen:
                continue
            seen.add(u)
            chs.append((H.unescape(_clean_text(inner) or "（无标题）"), BASE + u))
        vols.append({"name": _volume_label(raw_name, title, i + 1),
                     "url": BASE + mu.group(1) if mu else "",
                     "cover": cover, "chapters": chs})

    cover = next((v["cover"] for v in vols if v["cover"]), "")
    return {"title": title, "author": author, "cover": cover, "volumes": vols}


def _book_page_note(novel_id: str, title: str) -> tuple:
    """★ 只在目录页**解析不出分卷**时才走这里 —— 去书页拿一句人话说明 + 系列封面。

    返回 (cover, message)。**不再从书页解析卷册**（那是旧的"分卷页路线"，已删除）。
    ★ 用词要准（用户纠正过）：**「本站不给下」不等于「书下架了」** —— 只陈述
      "这个站在版权方要求下屏蔽了全部卷册、我们在这儿拿不到"，不下"这本书没了"的结论。
    """
    try:
        h = _text(f"{BASE}/novel/{novel_id}.html")
    except Exception:  # noqa: BLE001
        return "", ""
    cover = ""
    mc = re.search(r'<img[^>]+src="([^"]*?/files/article/image/[^"]+)"', h)
    if mc:
        cover = mc.group(1).split("?")[0]
        if cover.startswith("/"):
            cover = BASE + cover
    if ("已下架" in h) or ("现已屏蔽" in h) or ("版权方" in h and "屏蔽" in h):
        return cover, (f"《{title or novel_id}》在哔哩轻小说被版权方要求屏蔽了全部卷册"
                       f"（站点只保留作品简介，此处拿不到正文）")
    return cover, f"《{title or novel_id}》的目录页没有可分卷（可能书号错误、页面结构变了，或需要登录）"


def book_info(novel_id: str, *, with_chapters: bool = False) -> dict:
    """书 → {id, title, author, url, volumes:[{name, url, cover[, chapters]}], cover, offline, error}

    ★★★ 2026-10-01（用户拍板「拿到书号直接去正文页」）：**分卷页那一层整个删掉了** ★★★
      唯一的书单来源是目录页 `/novel/{id}/catalog`（见 catalog_tree）—— 一次请求同时给出
      书名、作者、每一卷的名字/封面/该卷全部章节。

      老路线是「书页 → 分卷页×N → 章节页」，现在只剩「目录页 → 章节页」：
        · 正常情形**不会**再请求书页，也**不会**再请求任何 `vol_XXXXX.html` 分卷页；
        · 只有目录页解析不出分卷块时，才去书页取一句"被版权方屏蔽/书号可能有误"的人话
          （`_book_page_note`，只取说明与封面，不解析卷册）。

      `with_chapters=True` 时每卷带上 `chapters`（下载用）；`--info` 用 False，避免把
      几百章的清单塞进 stdout 的 JSON 里。
    """
    cat = catalog_tree(novel_id)
    if cat["volumes"]:
        vols = cat["volumes"]
        if not with_chapters:
            vols = [{"name": v["name"], "url": v["url"], "cover": v["cover"]} for v in vols]
        return {"id": novel_id, "title": cat["title"], "author": cat["author"],
                "url": f"{BASE}/novel/{novel_id}.html", "volumes": vols,
                "cover": cat["cover"], "offline": False, "error": None}
    # ── 兜底：目录页没有分卷块 → 去书页要一句人话（拿不到就自己拼一句）
    cover, note = _book_page_note(novel_id, cat.get("title") or "")
    note = note or f"《{cat.get('title') or novel_id}》的目录页没有分卷"
    return {"id": novel_id, "title": cat.get("title") or "", "author": cat.get("author") or "",
            "url": f"{BASE}/novel/{novel_id}.html", "volumes": [],
            "cover": cat.get("cover") or cover,
            "offline": True, "error": note}


def volume_covers(novel_id: str, *, timeout: float = 20.0) -> list:
    """[(卷名, 该卷封面URL)] —— **每卷都有独立封面**（顺序＝目录页顺序＝阅读顺序）。

    ★★★ 2026-09-30（用户实测「作者有了，但是他找的不是第四卷的封面」）：
      书页里除了顶部那张**系列共用**封面（`…/image/2/2770/2770s.jpg`），
      每一卷的条目还各自带一张封面。元数据查询默认只拿到系列封面，所以做某卷时
      封面是"总封面"而不是那一卷的。

    ★★★ 2026-10-01（用户拍板「拿到书号直接去正文页」）：**改成从目录页取**。
      目录页每个 `<div class="volume">` 里的
        `<img src="/images/book-cover-no.svg" data-original="https://img3.readpai.com/cover/3095/200726.jpg">`
      就是**那一卷自己的封面**（实测 3095 → …/cover/3095/200726.jpg）。
      好处：① 不用再请求书页；② 顺序天然是阅读顺序，**不再需要 reverse()**
      （老实现读的是书页，站点按更新时间倒序，所以当时必须倒过来；目录页本来就是正的）。
      返回顺序与 `book_info()` 的分卷顺序**一致**（两者现在同源，不会再错位）。
    """
    return [(v["name"], v["cover"]) for v in catalog_tree(novel_id, timeout=timeout)["volumes"]]


def volume_cover_for(novel_id: str, volume: str, *, title: str = "", timeout: float = 20.0) -> str:
    """**按卷名**取那一卷的封面URL（取不到返回 ""）。★ 绝不按顺序取。

    ★★★ 2026-09-30（用户提醒）：站点各卷的名字是「… 4」「… 6.5」「… 短篇集」这种，
      必须**按名字精确对**，否则「第 6 卷」会撞上「6.5」那一卷（`"6" in "6.5"` 为真）。
      规则：
        ① 数字卷号 → **按数值相等**比较（6 与 6.5 不相等；"4" 与 "4.0" 相等）；
        ② 数字卷号在名字里但带别的字（如「4 特装版」）→ 用**独立数字**正则兜一层；
        ③ 文字卷名（短篇集 / 特典 / Days of …）→ 按名字包含匹配。
      全程不看数组下标，所以站点加减卷、改名都不会串。
    """
    raw = str(volume or "").strip()
    _m = (re.search(r'([0-9]+(?:\.[0-9]+)?)\s*[部卷册季集]', raw)
          or re.search(r'第\s*([0-9]+(?:\.[0-9]+)?)', raw))
    want_num = float(_m.group(1)) if _m else None
    want_txt = ""
    if want_num is None:
        # ★ 文字卷名（短篇集 / 特典 / EX …）：取"书名之后的那一段"来对，**不按下标**。
        #   例：查询「弹珠汽水瓶里的千岁同学 短篇集 Days of Endless Summer」
        #       → want_txt =「短篇集 Days of Endless Summer」→ 精确命中第 12 卷那张封面。
        want_txt = raw
        if title and want_txt.startswith(title):
            want_txt = want_txt[len(title):].strip(" -_·|/　")
        want_txt = re.sub(r'^第\s*', '', want_txt).strip()
    try:
        vols = volume_covers(novel_id, timeout=timeout)
    except Exception:  # noqa: BLE001 —— 取封面失败绝不能影响主流程
        return ""

    norm = []
    for nm, cover in vols:
        short = nm or ""
        if title and short.startswith(title):
            short = short[len(title):].strip(" -_·|/　")
        short = re.sub(r'^第\s*', '', short).strip()
        _mm = re.fullmatch(r'([0-9]+(?:\.[0-9]+)?)\s*[部卷册季集]?', short)
        norm.append((short, float(_mm.group(1)) if _mm else None, cover))

    if want_num is not None:
        for short, num, cover in norm:                      # ① 数值精确相等
            if cover and num is not None and abs(num - want_num) < 1e-9:
                return cover
        _pat = r'(?<![0-9.])%s(?![0-9.])' % re.escape(('%g' % want_num))
        for short, num, cover in norm:                      # ② 名字里带这个独立数字
            if cover and re.search(_pat, short):
                return cover
        return ""
    if want_txt:
        # ★★ 比较时**忽略空格**（站点的「短篇集 1」与下载名的「短篇集1」要算同一卷）
        def _k(s):
            return re.sub(r'\s+', '', s or '')

        _w = _k(want_txt)
        for short, num, cover in norm:                      # ③-1 文字卷名：精确（空格无关）
            if cover and _k(short) == _w:
                return cover
        # ③-2 包含匹配 —— **两边都必须 ≥2 字**，并取"最贴合"（最长）的那个。
        #   ★ 用户实测的坑：`短篇集1` 曾因为单字符短名 `1` 被当成"包含"而返回**第 1 卷**的封面。
        _cands = []
        for short, num, cover in norm:
            _s = _k(short)
            if cover and len(_s) >= 2 and len(_w) >= 2 and (_s in _w or _w in _s):
                _cands.append((len(_s), cover))
        if _cands:
            _cands.sort(key=lambda t: -t[0])
            return _cands[0][1]
    return ""


def volume_chapters(vol_url: str) -> list:
    """分卷页/目录页 → [(章节标题, 章节URL)]（含"插图"这类特殊章，按文档顺序）。

    ★ 2026-09-30：兼容目录页 /novel/{id}/catalog 的嵌套链接结构（标题包在
      <div><h4> 里、不在 `>` 之后），也兼容旧的简洁结构；只取**本书**的章节链接，
      顺序 = 目录页顺序 = 阅读顺序。绕过分卷直取章节就靠它。
    """
    h = _text(vol_url)
    out, seen = [], set()
    m_id = re.search(r"/novel/(\d+)/", vol_url)
    bid = m_id.group(1) if m_id else r"\d+"
    for m in re.finditer(
            r'<a[^>]*href="(/novel/%s/\d+\.html)"[^>]*>([\s\S]*?)</a>' % bid, h):
        u, inner = m.group(1), m.group(2)
        if u in seen:
            continue
        seen.add(u)
        name = re.sub(r"<[^>]+>", " ", inner)
        name = re.sub(r"\s+", " ", name).strip() or "（无标题）"
        out.append((H.unescape(name), BASE + u))
    return out


class ChapterBlocked(RuntimeError):
    """页面拿到了，但**正文容器不在**（站点在并发过高/被限流时返回的空壳页）。

    ★ 2026-09-30 实测教训：以前 `_cut_body()` 取不到容器就 `return ""`，
      于是并发一高（12 线程）站点返回空壳页时，整章被**静默截断/清空** ——
      同一卷 16 章，串行 99,165 字，12 线程只剩 42,767 字（第 5/7/9/10 章截断、
      第 14/15/16 章整章为空），而 `success` 依旧是 true。**必须抛出来重试/报错，不能装没事。**
    """


class ChapterLocked(ChapterBlocked):
    """★ 2026-10-01 新增：**站方明确不给正文**（不是限流，重试一万次也没用）。

    实测（2013《无职转生 ～到了异世界就拿出真本事～》，2026-10-01）：
      目录页 595 章一个不少，但每个章节页的 `#TextContent` 里只有一句
        `<center class="center-note">o(～￣▽￣)～ 沒有可閱讀的章節
         內容可能審核未通過或需要足夠的權限</center>`
      —— 没有 `<p>`，于是被老的判据当成"限流空壳页" → **每章串行重试 3 轮、每章白等十几秒**，
      最后报「一章都没抓到」（用户看到的还是"站点限流"这种误导性说法）。

    必须和 `ChapterBlocked` 区分开：
      · `ChapterBlocked`（限流空壳）→ **该重试**，隔几秒再抓往往就有了；
      · `ChapterLocked`（站方上锁）→ **绝不能重试**，直接如实报「这本书在这个站拿不到」。
    继承自 ChapterBlocked，所以老的 `except ChapterBlocked` 依旧能兜住。
    """


class _Throttle:
    """HTML 页面请求的**全局节流**（AIMD）：被限流就拉长间隔，顺利就慢慢缩回。

    ★ 2026-09-30 实测：并发数本身不是根因 —— 同一卷分别用 6/8/12 线程跑，
      失败章数是 9/4/1（非单调），说明真正决定成败的是**站点侧对我们这个 IP 的限流状态**。
      所以正确做法是控**总请求速率**并在被限流时退避，而不是一味加线程。
      做法与 `novelmeta/catalog.py` 爬文库列表时的 AIMD 同款（那边实测 429 后靠它跑完 178 页 0 失败）。
    """

    # ★★★ 2026-09-30 实测定稿（★ 用户拍板走"全速"路线）：
    #   默认 **0.15s/请求**（配合 6 线程 = 全速），踩到限流由 AIMD 自动拉长 + 三轮串行重试兜底。
    #   对照数据（同一天、同一本书、站点处于限流状态）：
    #     A) 6 线程 / 0.25s： 8 章 + 16 图 = 113.0s，首轮 7/8 章被打回 → 重试补齐
    #     B) 串行   / 0.25s：15 章 + 14 图 = 105.2s，首轮 7/15 章被打回
    #     C) 串行   / 0.6s ：17 章 + 15 图 =  93.8s，零重试
    #     D) 串行   / 0.4s ：11 章 + 15 图 = 104.4s，零重试
    #   → 用户选择：**要快**（宁可被打回后重试），所以取全速档；被打回不会丢章（见上面的重试轮）。
    # ★★★ 2026-09-30（用户拍板）：**16 线程 + 近乎不节流** —— "趁站点还没反应过来先冲完"。
    #   设计：起手几乎不限速（interval≈0.02s）；**一旦被打回（空壳页）立刻把间隔跳到 ≥0.3s**，
    #   之后按 1.6 倍继续退（0.3 → 0.48 → 0.77 …最高 4s）；没抓到的章再串行重试三轮。
    #   也就是说：顺利时全速爆发，被限流时自动后退 —— 两头都不吃亏。
    #   ★ 注意：间隔为 0 时 `delay*1.6` 还是 0（AIMD 会失效），所以 penalise() 里加了 0.3s 下限。
    def __init__(self, delay: float = 0.02, *, floor: float = 0.0, ceiling: float = 4.0):
        self.delay = delay
        self.floor = floor
        self.ceiling = ceiling
        self._lock = threading.Lock()
        self._next = 0.0
        self.penalties = 0

    def wait(self) -> None:
        with self._lock:
            now = time.time()
            sleep_for = max(0.0, self._next - now)
            self._next = max(now, self._next) + self.delay
        if sleep_for > 0:
            time.sleep(sleep_for)

    def penalise(self, factor: float = 1.6) -> None:
        with self._lock:
            # ★ 被打回一次就把间隔至少抬到 0.3s（否则 delay=0 时乘多少都还是 0，退不下来）
            self.delay = min(self.ceiling, max(self.delay * factor, 0.3))
            self.penalties += 1

    def reward(self) -> None:
        with self._lock:
            self.delay = max(self.floor, self.delay * 0.92)


_THROTTLE = _Throttle()
# ★ 插图并发（固定）：图片走 CDN、不吃站点 HTML 节流，和 `--workers` 解耦。
#   2026-09-30 用户要"最快"，从 4 提到 8。
IMG_WORKERS = 8


def _cut_body(h: str) -> str:
    """取 id="TextContent" 区域（★ 不要在 <script 处截断 —— 内容区里就有 script）。

    ★ 取不到（空壳页/被拦）→ 抛 `ChapterBlocked`，由调用方重试或计为失败。
    """
    i = max(h.find('id="TextContent"'), h.find('id="acontent"'))   # ★ linovelib=TextContent / bilinovel=acontent
    if i < 0:
        raise ChapterBlocked("页面里没有正文容器（八成是限流空壳页）")
    i = h.find(">", i) + 1
    j = len(h)
    for mk in ('id="show-more-images"', 'id="hidden-images"', 'class="chapter-end"',
               'id="foot"', "<footer", 'class="bottom"',
               # ★ 2026-09-30：bilinovel 主题的阅读器 UI（翻上页/呼出功能/翻下页）在
               #   acontent 之后、id 为 toptext/operatetip/footlink —— 不切掉会混进正文结尾
               'id="toptext"', 'id="operatetip"', 'id="footlink"'):
        k = h.find(mk, i)
        if k > 0:
            j = min(j, k)
    seg = h[i:min(j, i + 200000)]
    seg = re.sub(r"<script[\s\S]*?</script>", " ", seg)
    seg = re.sub(r"<ins[\s\S]*?</ins>", " ", seg)
    seg = re.sub(r'<div[^>]*class="[^"]*(?:ad|adsbygoogle|recommend|comment)[^"]*"[\s\S]*?</div>', " ", seg)
    if not re.search(r"<p[\s>]", seg, re.I):
        # ★★★ 2026-10-01：先分辨"站方上锁"和"限流空壳" —— 两者都是"容器里没有 <p>"，
        #   但**处置完全相反**：前者重试多少次都没用（而且每章要白等 3 轮），后者隔几秒再抓就有。
        #   实测标记（2013 每一章都是它）：
        #     <center class="center-note">o(～￣▽￣)～ 沒有可閱讀的章節
        #       內容可能審核未通過或需要足夠的權限</center>
        #   ★★ 判据**只认站点那句原话**，**不许拿 CSS 类名 `center-note` 当判据**：
        #     那只是个居中样式，站点完全可能把它用在别的提示上（"请稍候""正在加载"之类）。
        #     一旦误命中，后果是**跳过全部重试 + 报一个错误的结论**（"站方上锁了，换书源吧"），
        #     用户实测就撞到过这个假报。宁可漏判（退化成老行为：重试 3 轮后报"空壳页"），
        #     也绝不能误判。
        if ("沒有可閱讀的章節" in seg) or ("没有可阅读的章节" in seg):
            raise ChapterLocked("站方不给正文（页面写着「沒有可閱讀的章節：內容可能審核未通過"
                                "或需要足夠的權限」）—— 这本书在哔哩轻小说被锁了，换书源/换站吧")
        # ★ 报错时把"容器里到底写了什么"带上 —— 不然只剩一句"没有 <p>"，
        #   分不清是限流空壳、还是要登录、还是站点结构又变了。
        raise ChapterBlocked(f"正文容器是空的（没有 <p>）；容器里写的是「{_clean_text(seg)[:70]}」")
    # ★★★ 2026-09-30：站方对没过人机验证的请求**静默截断**正文（~530 字处拼上
    #   「（內容加載失敗！請刷新或更換瀏覽器）」）。必须显式报错，绝不能当完整章节存。
    if ("內容加載失敗" in seg) or ("内容加载失败" in seg):
        raise ChapterBlocked("正文被站方截断（「內容加載失敗」标记）——多半是 Cookie 里 "
                             "cf_clearance 过期：浏览器刷新章节页、重拷 Cookie 到 cookies.txt 再跑")
    return seg


def _img_url(tag: str) -> str:
    """从 <img ...> 里取真实图片 URL。

    ★★ 实测（2026-09-29，用户指出的问题）：**正文中间的插图和「插图」章写法不同**——
      插图章  : <img src="/images/sloading.svg" data-src="https://img3.readpai.com/…" class="imagecontent lazyload">
      正文中间: <img src="https://img3.readpai.com/3/3095/154931/200736.jpg" class="imagecontent">
    只认 data-src 就会把正文里的插图全漏掉（实测那些章 0 张）。
    """
    m = (re.search(r'data-src="([^"]+)"', tag, re.I)
         or re.search(r'src="([^"]+)"', tag, re.I))
    if not m:
        return ""
    u = m.group(1).strip()
    if not u or u.endswith("sloading.svg") or u.startswith("/images/"):
        return ""                      # 懒加载占位图（不是真图）
    return u if u.startswith("http") else (BASE + u)


def _blocks(body: str) -> list:
    """按**原始顺序**切成 [('p', 文本) | ('img', 图片URL)]（正文中的插图保持原位）。"""
    out = []
    for m in re.finditer(r"<img\b[^>]*>|<p[^>]*>([\s\S]*?)</p>", body, re.I):
        tag = m.group(0)
        if tag[:4].lower() == "<img":
            u = _img_url(tag)
            if u:
                out.append(("img", u))
        else:
            t = H.unescape(re.sub(r"<[^>]+>", "", m.group(1)))
            t = re.sub(r"[\u200b\ufeff]", "", t).strip()
            if t:
                out.append(("p", t))
    return out


def _next_url(h: str) -> str:
    """取「下一页」按钮的 href。

    ★★ 实测（2026-09-29）——这个按钮会“变脸”：
      本章还有下一页 → <a href="/novel/3095/154935_2.html">下一页</a>   （**章内翻页**）
      本章只有一页 → <a href="/novel/3095/154934.html">下一页</a>     （**下一章**）
    不追这个按钮的话，每章只会拿到第 1 页（实测：11,191 字的章只拿到 2,985 字 = 27%）。
    参考：_1.html 是第 1 页的别名（与原页同内容），所以从原页开始跟就对了。
    """
    m = re.search(r'<a\s+href="([^"]+)"[^>]*>\s*下一页\s*</a>', h)
    if m:
        u = m.group(1)
        return (BASE + u) if u.startswith("/") else u
    # ★ 2026-09-30：bilinovel 主题的翻页按钮是 JS 接线的（<a class="nextlink"> 无 href），
    #   真正的去向在 ReadParams.url_next（章内翻页 → xxx_2.html；章末 → 下一章）。
    m = re.search(r"url_next:'([^']*)'", h)
    if m and m.group(1):
        u = m.group(1)
        return (BASE + u) if u.startswith("/") else u
    return ""


def chapter_blocks(ch_url: str) -> list:
    """抓**完整一章**：从第 1 页开始，跟着「下一页」一直翻，
    直到下一页指向的**章节 ID 变了**（=本章结束）。"""
    m = re.search(r"/(\d+)(?:_\d+)?\.html", ch_url)
    cid = m.group(1) if m else ""
    blocks, seen, url, page = [], set(), ch_url, 1
    while url and url not in seen and page <= 40:
        seen.add(url)
        h = _text(url)
        try:
            body = _cut_body(h)
        except ChapterBlocked:
            _THROTTLE.penalise()          # ★ 被限流 → 拉长全局间隔，再抛给上层重试
            raise
        _THROTTLE.reward()                # 顺利 → 慢慢缩回间隔
        blocks += _blocks(body)
        nxt = _next_url(h)
        if not nxt:
            break
        m2 = re.search(r"/(\d+)(?:_(\d+))?\.html$", nxt)
        if not m2 or m2.group(1) != cid:      # → 下一页已经是别的章了
            break
        url = nxt
        page += 1
    return blocks


# ══════════════════════════════════════════════════════════════════════════
# 组装 EPUB
# ★★ 样式路线（2026-09-30 用户指定：「去看我 epub_generator 里写的，老是自己乱写」）：
#   底子照 `epub_generator.py`，**用户当次点掉的几处就不照抄**（以用户当次的话为准）：
#     · 金色 #b8956a（GOLD_HEX）
#     · **不写 font-family** → 用阅读器自己的首选字体
#     · 正文 16px · line-height 2.0 · 两端对齐
#     · 标题：24px · **不加粗**（normal）· **左对齐**· **在每个空格处断行**（<br/>）
#     · 标题正下方**一条满宽（100%）金色细实线**（border-top 画的，不随字体变）
#     · **内联 style="…!important"**，与 epub_generator「完全弃用外部 CSS」同一路线：
#       不生成 style.css、不链样式表 → 既和站内 TXT→EPUB 产物同一副面孔，
#       也不会再出现「xhtml 引用了不存在的 style.css → 严格阅读器拒开」的老坑。
#   结构仍然最素：<h2>标题</h2> + 满宽金线 + <p>正文</p> + <p><img/></p>。
#   保留：toc.ncx（目录跳转）、nav.xhtml（EPUB3 必需的导航文档）、章内翻页与正文插图。
#   ★★ 目录：**有目录、没有目录页**（用户 2026-09-30）—— nav.xhtml/toc.ncx 都在，
#      阅读器的「目录」菜单能列全章并跳转；但 nav.xhtml 不进 spine，书里不再有目录页。
#
#   ★ 想关掉那条金线（更极简）就把 DECO_LINE_ON 改成 False —— 只影响这一条线。

DECO_LINE_ON = True

GOLD_HEX = "#b8956a"                       # = epub_generator.py:53
GOLD_RGB = "rgb(184,149,106)"              # = epub_generator.py:54（当前装饰线用 border 画，用不到它；留着备查）

# ↓↓↓ 以下样式串**逐字对位** epub_generator.py:228-252 / 363-378，改这里就等于改全站风格
# ★★ 唯一的例外（用户 2026-09-30 追加）：「**不要字体索引**，让它用阅读器的官方首选字体」
#    → **全篇不写 font-family**。阅读器（Apple Books / 多看 / 文石 / Neat Reader …）自己的
#      「首选字体 / 正文字体」设置直接生效；我们不再把字体摁死成宋体族。
#    （其余数值 —— 金色、字号、行距、两端对齐、装饰线 —— 照旧对位 epub_generator。）
_TITLE_STYLE = (
    f"color:{GOLD_HEX} !important; "
    "font-size:24px !important; "
    # ★ 用户 2026-09-30：「标题怎么自己加粗了」→ 明确写 normal。
    #   注意**不能只删掉 bold**：<h2> 在 HTML/阅读器里默认就是粗体，必须显式压回 normal 才真的不粗。
    "font-weight:normal !important; "
    # ★ 用户 2026-09-30：「标题不要改成居中了，改成左对齐」← 原来是 center
    "text-align:left !important; "
    "margin:0 !important; "
    "padding-top:3.5em !important; "
    "padding-bottom:0.1em !important; "
    "font-style:normal !important; "
    "text-decoration:none !important; "
    "border:none !important; "
    "background:none !important"
)
_P_STYLE = (
    "color:#1e1e1e !important; "
    "font-size:16px !important; "
    "line-height:2.0 !important; "
    "text-indent:0 !important; "
    "margin:0.5em 0 !important; "
    "text-align:justify !important; "
    "font-style:normal !important; "
    "text-decoration:none !important; "
    "padding:0 !important"
)
_BODY_STYLE = "margin:0 !important"
_IMG_STYLE = "width:100% !important"
# 封面页的 <p>：贴边、无行高残留（图片下方不留那条 baseline 缝）
_COVER_P_STYLE = ("margin:0 !important; padding:0 !important; "
                  "text-align:center !important; line-height:0 !important;")
# ★ 装饰线（用户 2026-09-30 定稿）：「改成一条长线，放在标题下面，要最长」
#   = 一条**满宽**（width:100%）的 1px 金线，就压在标题正下方。
#   ★ 为什么不再用 `────── ⋅ ──────` 那种字符画：它的粗细/连续性**随阅读器字体变**
#     （已实测：雅黑下与宋体下不一样），而 border 是画出来的线，换任何字体都一模一样。
DECO_LINE = (
    '<hr style="'
    'border:none !important; '
    f'border-top:1px solid {GOLD_HEX} !important; '
    'height:0 !important; '
    'width:100% !important; '
    'margin:0.9em 0 4em 0 !important; '
    'padding:0 !important; '
    'background:none !important;'
    '"/>'
)


def _title_html(title: str) -> str:
    """章节标题 → HTML：**在每个空格处断行**（用户 2026-09-30：「在章节标题的空格那里换行」）。

    实测本书标题用的都是半角空格（U+0020），例如
      「～第一败～ 专业青梅竹马 八奈见杏菜的惨烈败相」→ 三行；
      「插图」「序」「后记」没有空格 → 保持一行。
    全角空格 U+3000 和连续空格同样当断点；不换行空格 U+00A0 不算（它本来就不该断）。
    """
    t = H.escape(title or "").strip()
    return re.sub(r"[ \u3000]+", "<br/>", t)


def _page(title, body):
    return ('<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE html>\n'
            '<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="zh-CN" lang="zh-CN">\n<head>\n'
            '<meta charset="utf-8"/>\n'
            f'<title>{H.escape(title)}</title>\n'
            '</head>\n'
            f'<body style="{_BODY_STYLE}">\n' + body + '\n</body>\n</html>')


def _chapter_xhtml(idx, title, blocks, img_map, disp_no=None):
    """章节页：与 epub_generator 的成品同一副样式（内联 !important，无外部 CSS）。"""
    parts = [f'<h2 style="{_TITLE_STYLE}">{_title_html(title)}</h2>']
    if DECO_LINE_ON:
        parts.append(DECO_LINE)
    for kind, val in blocks:
        if kind == "p":
            parts.append(f'<p style="{_P_STYLE}">{H.escape(val)}</p>')
        elif kind == "img" and val in img_map:
            parts.append(f'<p style="{_P_STYLE}">'
                         f'<img style="{_IMG_STYLE}" src="../images/{img_map[val]}" alt=""/></p>')
    return _page(title, "\n".join(parts))


def _nav_xhtml(entries, title):
    items = "".join(f'<li><a href="{h}">{H.escape(c)}</a></li>' for _n, c, h, _l in entries)
    return ('<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE html>\n'
            '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" '
            'xml:lang="zh-CN" lang="zh-CN">\n<head>\n<meta charset="utf-8"/>\n'
            f'<title>{H.escape(title)}</title>\n</head>\n'
            f'<body style="{_BODY_STYLE}">\n'
            '<nav epub:type="toc" id="toc"><h1>目录</h1><ol>' + items
            + '</ol></nav>\n</body>\n</html>')


def _cover_xhtml(img_name):
    """封面页：**只放这一张图**，不加标题、不加装饰（用户要「最简洁好看」）。"""
    return _page("封面",
                 f'<p style="{_COVER_P_STYLE}">'
                 f'<img style="{_IMG_STYLE}" src="../images/{img_name}" alt=""/></p>')


def build_epub(out_path, title, author, chapters, img_map, *, series="", translator="",
               cover_image=""):
    entries = [("", ct, f"text/c{i}.xhtml", False) for i, (ct, _b) in enumerate(chapters, 1)]

    manifest = ['<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>',
                '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>']
    # ★★ 封面（2026-09-30 用户要求：「要带封面」「封面用下下来的插图第一张」）★★
    #   · 不额外抓封面图：直接拿**正文里第一张插图**（linovelib 每卷第一张就是该卷封面画）
    #   · 那个文件本来就在 images/ 里，这里只把它**标成 properties="cover-image"**
    #     （不再复制一份，避免同一个资源在 manifest 里出现两次）
    #   · 再补一个 text/cover.xhtml 放它，并进 spine 第一项；另加 EPUB2 兼容的 <meta name="cover">
    if cover_image and cover_image in set(img_map.values()):
        manifest.append(f'<item id="cover-img" href="images/{cover_image}" '
                        f'media-type="image/jpeg" properties="cover-image"/>')
        manifest.append('<item id="coverpage" href="text/cover.xhtml" '
                        'media-type="application/xhtml+xml"/>')
    else:
        cover_image = ""
    # ★★ 目录**保留**，但**目录页不要**（用户 2026-09-30：「只是删掉目录页，而不是舍弃目录」）：
    #   · nav.xhtml 仍在 manifest 里（EPUB3 要求 properties="nav" 的导航文档必须存在）
    #     → 阅读器的「目录」菜单照样能列出全部章节、点击跳转；toc.ncx 也照旧给老阅读器用；
    #   · 但 nav.xhtml **不进 spine**（阅读顺序）→ 翻开书第一页就是正文，不再插一张「目录」页。
    #     EPUB 3.3 §7.5「Using in the spine」：导航文档**不是必须**进 spine（epubcheck 也不报错）。
    spine = []
    if cover_image:
        spine.append('<itemref idref="coverpage"/>')     # 封面排在最前
    for i, (ct, _b) in enumerate(chapters, 1):
        manifest.append(f'<item id="c{i}" href="text/c{i}.xhtml" media-type="application/xhtml+xml"/>')
        spine.append(f'<itemref idref="c{i}"/>')
    for _u, name in img_map.items():
        # 封面那张已经作为 cover-img 声明过（带 properties），这里跳过，避免同一资源出现两个 item
        if name == cover_image:
            continue
        manifest.append(f'<item id="{name[:-4]}" href="images/{name}" media-type="image/jpeg"/>')

    opf = ('<?xml version="1.0" encoding="utf-8"?>\n'
           '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bid" xml:lang="zh-CN">\n'
           '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">\n'
           f'<dc:identifier id="bid">linovelib-{int(time.time())}</dc:identifier>\n'
           f'<dc:title>{H.escape(title)}</dc:title>\n'
           f'<dc:creator>{H.escape(author or "")}</dc:creator>\n'
           '<dc:language>zh-CN</dc:language>\n'
           + (f'<meta name="cover" content="cover-img"/>\n' if cover_image else '')
           + f'<meta property="dcterms:modified">{time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}</meta>\n'
           '</metadata>\n<manifest>\n' + "\n".join(manifest) + '\n</manifest>\n'
           '<spine toc="ncx">\n' + "\n".join(spine) + '\n</spine>\n</package>')

    navmap = "".join(
        f'<navPoint id="np{i}" playOrder="{i}"><navLabel><text>{H.escape(ct)}</text></navLabel>'
        f'<content src="text/c{i}.xhtml"/></navPoint>' for i, (ct, _b) in enumerate(chapters, 1))
    ncx = ('<?xml version="1.0" encoding="utf-8"?>\n'
           '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">\n<head>\n'
           f'<meta name="dtb:uid" content="linovelib-{int(time.time())}"/>\n'
           '<meta name="dtb:depth" content="1"/>\n<meta name="dtb:totalPageCount" content="0"/>\n'
           '<meta name="dtb:maxPageNumber" content="0"/>\n</head>\n'
           f'<docTitle><text>{H.escape(title)}</text></docTitle>\n<navMap>\n{navmap}\n</navMap>\n</ncx>')

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with zipfile.ZipFile(out_path, "w") as z:
        z.writestr(zipfile.ZipInfo("mimetype"), "application/epub+zip", zipfile.ZIP_STORED)
        z.writestr("META-INF/container.xml",
                   '<?xml version="1.0" encoding="utf-8"?>\n<container version="1.0" '
                   'xmlns="urn:oasis:names:tc:opendocument:xmlns:container">\n<rootfiles>'
                   '<rootfile full-path="OEBPS/content.opf" '
                   'media-type="application/oebps-package+xml"/></rootfiles>\n</container>')
        z.writestr("OEBPS/content.opf", opf, zipfile.ZIP_DEFLATED)
        z.writestr("OEBPS/toc.ncx", ncx, zipfile.ZIP_DEFLATED)
        z.writestr("OEBPS/nav.xhtml", _nav_xhtml(entries, title), zipfile.ZIP_DEFLATED)
        if cover_image:
            z.writestr("OEBPS/text/cover.xhtml", _cover_xhtml(cover_image), zipfile.ZIP_DEFLATED)
        for i, (ct, blocks) in enumerate(chapters, 1):
            z.writestr(f"OEBPS/text/c{i}.xhtml", _chapter_xhtml(i, ct, blocks, img_map),
                       zipfile.ZIP_DEFLATED)
        for _u, name in img_map.items():
            fp = os.path.join(_work_dir(), name)
            if os.path.exists(fp):
                z.write(fp, f"OEBPS/images/{name}")


def _say(msg: str) -> None:
    """诊断行 → **stderr**（stdout 只留给 @@PROGRESS/@@INFO 和最终 JSON）。

    ★ 2026-09-30：主进程现在把 stderr 也转进 logs\\upload-*.log —— 以前只转 stdout，
      于是「按书名查询卡住」时日志里只有一行，看不出到底卡在哪一步（用户实测三次如此）。
    """
    print("  " + msg, file=sys.stderr, flush=True)


def resolve_by_name(name: str, timeout: float = 25.0) -> dict:
    """★ 2026-09-30：**书名 → 书号**（用户：「我现在用书名请求网站获得书号」）。

    复用 novelmeta 的哔哩轻小说源（**已缓存的**本地目录索引 → 站内搜索三步守卫 → 必应反查），
    所以**译名也能命中**（实测「败犬女主太多了」→ 3095《败北女角太多了！》，靠站点别名栏）。
    ★ 冷缓存时**绝不现爬**那 178 页目录（受站点限流要数分钟，界面会像卡死）——
      直接走站内搜索；要预热索引请用 CLI：`meta_lookup.py --title x --allow-index-build`。
    返回 {"id","title","author","candidates":[...]}；失败返回 {"error": "..."}。
    """
    key = (name or "").strip()
    if not key:
        return {"error": "书名为空"}
    try:
        from novelmeta import sources as _nm_sources
        from novelmeta.sources import resolve_bilinovel
    except ImportError as exc:  # 打包/路径异常给一句人话
        return {"error": f"novelmeta 不可用（无法按书名解析）：{exc}"}
    try:  # 索引/搜索的进度回调也接到 stderr（主进程会转进日志）
        _nm_sources.BILINOVEL_PROGRESS = _say
    except Exception:  # noqa: BLE001
        pass
    try:
        _say("① 本地目录索引：只读缓存（未缓存则跳过现爬）")
        # ★ 走统一解析：本地目录命中 = 0.1s 零网络；没缓存就直接进②站内搜索
        # ★★★ 2026-10-01（用户：「那你的意思是他要爬完哔轻整个站的内容？」—— 一针见血）★★★
        #   **必须传 `allow_index_build=False`**。`resolve_bilinovel` 的这个参数默认是
        #   **True**，含义是"本地索引缓存不在时，就现爬 /wenku/ 那 178 页把它建出来"
        #   —— 实测后果：冷缓存下敲一次「无职转生」，它会闷头爬完 178 页
        #   （缓存目录里 `bilinovel_index.json` 1.7MB，就是这么来的），界面像卡死好几分钟。
        #   这正是本函数文档头那句「冷缓存时**绝不现爬**那 178 页目录」要保证的事，
        #   而旧代码本来也传了 —— 模块化时被删掉，注释理由是
        #   "novelmeta 的 resolve_bilinovel() 没有这个参数（传了直接 TypeError）"。
        #   那句话对 `C:\work\novelmeta` 那份成立，对**随包的这份不成立**
        #   （EasyPub-New\novelmeta\sources.py 的签名里就有 allow_index_build）。
        #   所以：先按带参数的签名调，真遇到老版 novelmeta 再退回老签名。
        #   冷缓存时正确行为 = 跳过①，直接走②站内搜索（一个请求，~1 秒）。
        try:
            best = resolve_bilinovel(key, timeout=max(timeout, 15.0), allow_bing=False,
                                     allow_index_build=False)
        except TypeError:
            _say("  ℹ️ 这份 novelmeta 没有 allow_index_build 参数 → 用老签名调用")
            best = resolve_bilinovel(key, timeout=max(timeout, 15.0), allow_bing=False)
        _say("② 站内搜索 → " + (best.title if best is not None else "无结果"))
    except Exception as exc:  # noqa: BLE001
        return {"error": f"查询失败：{type(exc).__name__}: {str(exc)[:80]}"}
    if best is None or not best.extra.get("novelId"):
        return {"error": f"没找到《{key}》（站内搜索没命中；可直接填书号，"
                         f"或稍后重试 —— 站点搜索偶发不回数据）"}
    # ★★★ 2026-10-01（用户：「我要的多选就是与站内搜索结果相同」）★★★
    #   **无条件**去站点搜索取候选，顺序 = **站点自己的相关度顺序**
    #   （就是你在站内搜索页上看到的那两条：「无职转生 ～到了…」+「无职转生 ～蛇足篇…」）。
    #
    #   原来这里是 `if best.extra.get("resolvedBy", "").startswith("search")` —— 只有
    #   "靠站内搜索解析出来的"才填候选。后果：**本地目录索引命中的书**
    #   （无职转生这种热门书就是）拿到的是**空 candidates** →
    #   界面 `renderCands()` 在 `cands.length <= 1` 时直接隐藏 →
    #   **"搜索结果卡片弹窗"根本不出现**，用户看到的是"直接列分卷"，
    #   与他期望的"先选一部再下"不符。
    cands = []
    try:
        from novelmeta.sources import bilinovel_search_candidates
        for c in bilinovel_search_candidates(key, timeout=max(timeout, 15.0))[:30]:
            cid = str(c.extra.get("novelId") or "")
            if cid:
                cands.append({"id": cid, "title": c.title, "author": c.author or "",
                              "cover": c.cover or ""})
    except Exception:  # noqa: BLE001
        cands = []
    # 兜底：站点搜索没回数据（偶发）时，至少把解析到的那一本作为唯一候选，
    # 界面照旧不会弹卡片（只有 1 条），但不至于把书名卡弄空。
    if not cands:
        cands = [{"id": str(best.extra.get("novelId")), "title": best.title,
                  "author": best.author or "", "cover": best.cover or ""}]
    return {
        "id": str(best.extra.get("novelId")),
        "title": best.title,
        "author": best.author or "",
        "cover": best.cover or "",
        "matchedBy": best.extra.get("matchedBy") or "title",
        "resolvedBy": best.extra.get("resolvedBy") or "",
        "candidates": cands,
    }


# ══════════════════════════════════════════════════════════════════════════
# ★★★ 2026-10-01：**浏览器取页模式** —— 受保护的书只能这么拿 ★★★
# ══════════════════════════════════════════════════════════════════════════
# 实测（2139《Re:从零开始的异世界生活》，逐项排除过）：
#   同一个 URL、同一张 cf_clearance、同一个 User-Agent，两种客户端结果完全不同：
#     · urllib（novelmeta.net.fetch） → `#TextContent` 里只有「沒有可閱讀的章節」锁页
#     · 浏览器里 fetch() 同 URL      → **11,194 字节完整正文**（"。这下真的很糟糕…"）
#   排除过程：换域名 ✗、换 Cookie（含浏览器原生 jar）✗、换 UA ✗ —— 都不是。
#   结论：站方对这些**受版权保护的书**是按**传输层指纹**（TLS/HTTP2 指纹 + 浏览器
#   请求头）放行的。urllib 在 Python 层面怎么伪装都过不去，只有真浏览器能拿。
#   （对照：3095/2770 这类普通书，urllib 一直正常 —— 所以这是**按书**加严的。）
# 做法：把 Selenium 当"取页代理"**常驻**一个，用它 fetch 每章 HTML，其余流程一律不变
#   （解析、节流、重试、插图、EPUB 组装全都复用）。插图走 CDN，不受影响，仍用 urllib。
_BROWSER = None                # 常驻会话（懒加载）
_BROWSER_ON = False            # 是否走浏览器取页（--browser 或自动兜底时打开）

_FETCH_JS = """
var url = arguments[0], cb = arguments[arguments.length - 1];
fetch(url, {credentials: 'include'})
  .then(function (r) { return r.text(); })
  .then(function (t) { cb(t); })
  .catch(function (e) { cb('__FETCH_ERR__' + String(e)); });
"""


class _BrowserSession:
    """常驻的 Selenium 会话：只用它取 HTML（指纹 = 真浏览器）。"""

    def __init__(self, base: str, headless: bool = True):
        from selenium import webdriver
        from selenium.webdriver.edge.options import Options

        opt = Options()
        opt.add_argument("--start-minimized")
        opt.add_argument("--log-level=3")
        opt.add_experimental_option("excludeSwitches", ["enable-logging"])
        if headless:
            opt.add_argument("--headless=new")
        self.drv = webdriver.Edge(options=opt)
        self.drv.set_script_timeout(45)
        # ★ 必须落在**同一个域**上：fetch 是同源策略，跨域读不到响应体。
        self.drv.get(base.rstrip("/") + "/")
        end = time.time() + 60
        while time.time() < end:
            try:
                if any(c["name"] == "cf_clearance" for c in self.drv.get_cookies()):
                    break
            except Exception:  # noqa: BLE001
                pass
            time.sleep(1)

    def fetch(self, url: str):
        try:
            out = self.drv.execute_async_script(_FETCH_JS, url)
        except Exception as exc:  # noqa: BLE001
            _say(f"浏览器取页失败（{type(exc).__name__}）: {url}")
            return None
        if isinstance(out, str) and out.startswith("__FETCH_ERR__"):
            _say(f"浏览器取页失败: {out[14:120]}  {url}")
            return None
        return out

    def close(self) -> None:
        try:
            self.drv.quit()
        except Exception:  # noqa: BLE001
            pass


def _browser_get(url: str):
    """用常驻浏览器 fetch 一个 URL 的 HTML；失败返回 None。"""
    global _BROWSER
    if _BROWSER is None:
        _say("🌐 启用浏览器取页（这些书 urllib 拿到的是锁页，只有真浏览器能读）…")
        try:
            _BROWSER = _BrowserSession(BASE)
        except Exception as exc:  # noqa: BLE001
            _say(f"浏览器启动失败：{type(exc).__name__}: {str(exc)[:100]}")
            _BROWSER = False          # 记成 False，别反复重试
            return None
    if _BROWSER is False:
        return None
    return _BROWSER.fetch(url)


def close_browser() -> None:
    global _BROWSER
    if _BROWSER and _BROWSER is not False:
        _BROWSER.close()
    _BROWSER = None


# ══════════════════════════════════════════════════════════════════════════
# ★★★ 2026-10-01：**镜像域名自动协商**（实测 2139 才发现的真问题）★★★
# ══════════════════════════════════════════════════════════════════════════
# 站点的几个域名**内容并不一致**。实测《Re:从零开始的异世界生活》(2139) 同一章：
#     https://www.linovelib.com       → 「沒有可閱讀的章節」锁页
#     https://www.bilinovel.com       → 612 字真正文 ✅
#     https://tw.linovelib.com        → 612 字真正文 ✅
#     https://w.linovelib.com         → 612 字真正文 ✅
# 而《败北女角太多了！》(3095) 恰恰相反：linovelib 能读、bilinovel 的**目录页结构又不一样**。
# 所以正确做法不是"把默认域名换成某一个"，而是：
#   **书单结构照旧用当前域名（这套解析器就是为它写的），开跑前先探一章** ——
#   若探到的是"锁页"，就换一个镜像域名（章节 ID 全域通用，只换 origin），
#   整本都用换后的域名抓。多花 1~2 个请求，把"这本书下不了"变成"能下"。
_ALT_BASES = ("https://www.bilinovel.com", "https://tw.linovelib.com")
_BASE_PINNED = False          # 用户显式给了 --base 就置 True：尊重用户选择，不再自动换


def _switch_base(new_base: str) -> None:
    global BASE, REF
    BASE = new_base.rstrip("/")
    REF = BASE + "/"
    _HDR["Referer"] = REF


def _rebase(url: str) -> str:
    """把 URL 换成**当前 BASE** 的域名（路径/章节 ID 全域通用）。"""
    m = re.match(r"https?://[^/]+(/.*)$", url or "")
    return (BASE + m.group(1)) if m else (url or "")


def _rebase_vol(v: dict) -> dict:
    out = dict(v)
    out["url"] = _rebase(v.get("url") or "")
    out["chapters"] = [(_t, _rebase(_u)) for _t, _u in (v.get("chapters") or [])]
    return out


def _probe_verdict(base: str, probe_url: str) -> str:
    """在 base 上探这一章，返回：ok / locked / truncated / other / error。

    ★ truncated = 页面带「內容加載失敗」= **这张证不是这个域名的**（Cloudflare 通行证按域签发）。
      这是能否换域名的关键区分：locked 是内容层面（换域名才有用），truncated 是证件层面
      （**换了域名必须重新领证**，否则照样只给 ~530 字）。
    """
    _switch_base(base)
    try:
        h = _text(_rebase(probe_url))
    except Exception:  # noqa: BLE001
        return "error"
    if ("內容加載失敗" in h) or ("内容加载失败" in h):
        return "truncated"
    try:
        _cut_body(h)
        return "ok"
    except ChapterLocked:
        return "locked"
    except Exception:  # noqa: BLE001
        return "other"


def negotiate_base(vols: list, auto_cookie: bool = False) -> list:
    """当前域名若对这本书是"锁页"，自动换一个镜像域名抓正文（换一次，整本生效）。

    返回（可能已重定域的）vols。

    ★★★ 2026-10-01（实测踩到的连锁坑）：**换域名必须同时重新领证** ★★★
      cf_clearance 是**按域签发**的。原来只换域名不换证，于是在 bilinovel.com 上
      拿到的是「內容加載失敗」截断版（~533 字），一路报"Cookie 过期"—— 怎么续期都没用，
      因为续期时 `--url` 传的还是**旧域名**。这里 probe 到 `truncated` 就地为新域名续一张再试。
    """
    if _BASE_PINNED:
        return vols
    probe = ""
    for v in vols:
        ch = v.get("chapters") or []
        if len(ch) >= 2:                 # 优先拿第 2 章：第 1 章常常是「插图」那种特殊页
            probe = ch[1][1]
            break
        if ch:
            probe = ch[0][1]
            break
    if not probe:
        return vols

    start = BASE

    def _renew_for(base: str) -> str:
        """★ 2026-10-02：**不再在这里续期了**（用户定稿「cookie 只保留这一条路径」）。

        原来这里是 `_ensure_cookie(True, force=True)` —— 「换个镜像域名就 force 续一张证」。
        但那会给**每个**域名各续一次、都写同一个 cookie 文件 → 互相覆盖 → 永远续不完
        （实测 2 分钟弹 3 次 Edge）。现在只换域名、不碰 Cookie；
        真需要证时走 `_ask_user_click_first_chapter()`（用户点一下第一章）。
        """
        _switch_base(base)
        return _probe_verdict(base, probe)

    v0 = _probe_verdict(start, probe)
    if v0 == "ok":
        return vols
    if v0 == "truncated" and auto_cookie:
        v0 = _renew_for(start)
        if v0 == "ok":
            return vols

    for alt in _ALT_BASES:
        if alt.rstrip("/") == start.rstrip("/"):
            continue
        v = _probe_verdict(alt, probe)
        if v == "truncated" and auto_cookie:
            v = _renew_for(alt)          # ★ 换了域名 → 重新领这个域名的证
        if v == "ok":
            _say(f"🔁 {start} 对这本书是锁页 → 自动切到 {BASE} 抓正文"
                 + ("（已为新域名续 Cookie）" if auto_cookie else ""))
            return [_rebase_vol(x) for x in vols]

    _switch_base(start)
    _say(f"⚠️ {start} 对这本书是锁页，{len(_ALT_BASES)} 个镜像域名也一样"
         + ("（限流空壳/证件问题不算锁页，按原域名继续）" if v0 not in ("locked",) else ""))
    # ★★★ 2026-10-01：所有域名都锁 → **最后一条路：改用浏览器取页**。
    #   实测 2139 就是这种：urllib 在**所有**域名上都是锁页，而常驻浏览器 fetch 同一 URL
    #   能拿到 11,194 字节完整正文（站方按传输层指纹放行）。
    global _BROWSER_ON
    if v0 == "locked" or v0 == "truncated":
        _BROWSER_ON = True
        v = _probe_verdict(start, probe)
        if v == "ok":
            _say(f"✅ 切到浏览器取页后 {start} 能读了 → 这本书全程用浏览器取正文")
        else:
            _say(f"⚠️ 浏览器取页也不行（{v}）—— 这本书可能确实要登录或被锁")
    return vols


def do_download(novel_id: str, out_dir: str, vols=None, limit_chapters=0,
                workers=6, as_json=False, auto_cookie=False) -> dict:
    # ★★★★ 阉割版（2026-10-02）：**下载执行已移除**（保留签名，避免别处 import 出错）★★★★
    #   本副本只保留 --info（查书号 / 列分卷 / 卷封面）。
    return {"success": False, "notAvailable": True, "error": "该部分暂不开放使用"}
    """auto_cookie=True：cf_clearance 失效时自动用 Selenium 续期（见 _ensure_cookie）。

    ★ 被动触发只认**无 Cookie 的结果特征**：「（內容加載失敗！請刷新或更換瀏覽器）」标记
      （正文 530 字处截断）——限流空壳（容器里没有 <p>）绝不出这个标记，不触发续期。
    """
    info = book_info(novel_id, with_chapters=True)     # ★ 目录页一次拿全：分卷 + 每卷章节
    if not info["volumes"]:
        # ★★★ 2026-09-30（用户拍板「你不用管，你就绕过分卷直接爬」）：
        #   目录页连分卷块都没有（书号错误 / 页面结构变了 / 需要登录）时的最后兜底：
        #   把整本当"一卷"直接抓目录页上的所有章节链接（实测 413 章全在、顺序即阅读顺序）。
        log(f"  ℹ️ 目录页没给出分卷（{info.get('error') or '页面结构变了'}）"
            f"→ 把整本当一卷，直接从目录页抓章节")
        info["volumes"] = [{"name": "全书", "url": f"{BASE}/novel/{novel_id}/catalog",
                            "chapters": None}]
        if not volume_chapters(info["volumes"][0]["url"]):
            return {"success": False, "error": info.get("error")
                    or "目录页也没解析到章节（可能书号错误或页面结构变了）"}
    if as_json:
        log("@@INFO " + json.dumps({"title": info["title"], "author": info["author"],
                                    "volumes": [v["name"] for v in info["volumes"]]}, ensure_ascii=False))
    log(f"📖 《{info['title']}》 作者={info['author'] or '?'}  分卷 {len(info['volumes'])} 个")

    picked = info["volumes"]
    if vols:
        picked = [v for i, v in enumerate(info["volumes"], 1) if i in vols]
        if not picked:
            return {"success": False, "error": f"指定的分卷不存在（共 {len(info['volumes'])} 卷）"}

    # ★ 2026-10-01：开跑前先探一章 —— 当前域名给"锁页"就换个镜像域名（见 negotiate_base），
    #   换域名时会**就地为新域名续 Cookie**（cf_clearance 按域签发，不换证照样只给截断版）。
    picked = negotiate_base(picked, auto_cookie=auto_cookie)

    chapters, img_urls, failed_chapters = [], [], []
    # ★★★ 2026-09-30（用户拍板）：**16 线程爆发**（"趁站点还没反应过来先下完"）。
    #   被打回不丢章：AIMD 自动后退 + 三轮串行重试（见下面的重试轮）。
    ch_workers = max(1, min(int(workers), 32))
    # ★ 2026-10-02：**这次运行里有没有请用户点过第一章**。只请一次，
    #   免得用户不点的时候反复弹窗（用户定稿：cookie 只保留这一条路径）。
    _manual_tried = False
    for vi, vol in enumerate(picked, 1):
        # ★ 2026-10-02：这里原来是 `_ensure_cookie(auto_cookie)`（每卷一开跑就主动续期）
        #   —— 已删。取 Cookie 现在只有一条路，且**由用户决定什么时候点**
        #   （见 _ask_user_click_first_chapter）。
        # ★★★ 2026-10-01（用户拍板「拿到书号直接去正文页」）★★★
        #   **章节直接来自目录页那一块**（book_info(with_chapters=True) 已经解析好），
        #   **不再请求任何分卷页 vol_XXXXX.html**。
        #   只有最后兜底那卷（chapters=None）才回去抓一次目录页。
        chs = vol.get("chapters") or volume_chapters(vol["url"])
        if limit_chapters:
            chs = chs[:limit_chapters]
        log(f"  [卷{vi}] {vol['name']} — {len(chs)} 章"
            + (f"（{ch_workers} 线程爆发抓取）" if ch_workers > 1 else "（串行抓取）"))
        # 单章内部的「下一页」翻页本来就串行（同一章的连续页必须按序）；并发只作用在"不同章"之间。
        def _fetch_one(item, _vol_name=vol["name"]):
            idx, (ct, cu) = item
            try:
                return idx, ct, chapter_blocks(cu), None
            except Exception as exc:
                return idx, ct, None, f"{type(exc).__name__}: {str(exc)[:60]}"

        done_n = 0
        results = []

        def _fetch_all(chs_local):
            """抓一整卷（并发）。抽出来是为了"用户点完第一章后能**重抓同一卷**"。"""
            res = []
            with ThreadPoolExecutor(max_workers=ch_workers) as ex:
                for r in ex.map(_fetch_one, list(enumerate(chs_local, 1))):
                    res.append(r)
                    if as_json:
                        log("@@PROGRESS " + json.dumps(
                            {"stage": "chapters", "done": len(res), "total": len(chs_local),
                             "vol": vi, "vols": len(picked),
                             "volName": vol.get("name") or ""}, ensure_ascii=False))
            return res

        results = _fetch_all(chs)

        # ★★ 没抓到的章 → **串行重试两轮**（间隔递增；此时全局节流已被 penalise 拉长）。
        #    实测：站点限流时同一章隔几秒再抓就能拿到全量内容。
        # ★★★ 2026-10-01：**站方上锁的章（ChapterLocked）绝不参与重试** —— 它们和限流空壳
        #    长得一样（容器里都没有 <p>），但重试一万次也没用。以前不区分，于是遇到
        #    2013 这种"目录 595 章、每章都是「沒有可閱讀的章節」"的书：
        #    595 章 × 3 轮 × 递增等待，纯粹白跑；现在整批全是锁的就**立刻收手并如实报错**。
        #    ⚠️ 触发条件（判据必须严，宁可不触发也不能误报）：
        #      ① 本卷**每一章**都失败（一章能读就不算）；
        #      ② 每一章的失败原因都是 ChapterLocked，而 ChapterLocked **只认站点原话**
        #         「沒有可閱讀的章節」（不认 CSS 类名，见 _cut_body）；
        #      ③ 且至少有 2 章 —— 防止"某卷只有 1 章、恰好那 1 章异常"就下结论。
        def _all_locked(res_list):
            lk = [r for r in res_list if r[2] is None and (r[3] or "").startswith("ChapterLocked")]
            return lk, (len(lk) >= 2 and len(lk) == len(res_list))

        _locked, _is_locked = _all_locked(results)
        if _is_locked:
            log(f"  🚫 本卷 {len(_locked)} 章**每一章**都是「站方不给正文」（沒有可閱讀的章節）"
                f"—— 不是限流，重试没有意义")
            # ★★★ 2026-10-02（用户定稿）：**唯一的取 Cookie 路径就挂在这里** ★★★
            #   站点那句原话是「…或**需要足夠的權限**」= 要登录/要权限。
            #   脚本自己怎么点都进不去，但**真人点一下第一章**（顺带登录）就进去了。
            #   所以：发 @@NEEDCLICK 让界面弹窗 → 等用户点进正文页 → 拿回新证 →
            #   **把本卷重抓一次**。只做一次（`_manual_tried`），免得用户不点时反复弹。
            if auto_cookie and not _manual_tried:
                _manual_tried = True
                log("  👉 这多半只是「没登录 / 没权限」—— 按你定的做法：请点一下第一章")
                if _ask_user_click_first_chapter(novel_id, picked):
                    log("  🔄 已拿到新证 → 本卷重抓一次")
                    results = _fetch_all(chs)
                    _locked, _is_locked = _all_locked(results)
                    if _is_locked:
                        log(f"  ⚠️ 重抓后仍然 {len(_locked)} 章全锁 → 收手")
                    else:
                        log("  ✅ 重抓拿到正文了，本卷继续")
                else:
                    log("  ⚠️ 没拿到新证（超时 / 没点 / 窗口被关）→ 按失败处理")
            if _is_locked:
                return {"success": False,
                        "error": f"站方不给正文：这本书在哔哩轻小说被锁了 —— 本卷 {len(_locked)} 章"
                                 f"每一章的正文位置都只有站点那句「沒有可閱讀的章節：內容可能審核未通過"
                                 f"或需要足夠的權限」。请在浏览器里点一下**第一章**（可能要登录），"
                                 f"点进去后会自动继续；仍不行就换书源。"}
        for round_no, pause in ((1, 0.8), (2, 3.0), (3, 6.0)):
            retry = [r for r in results
                     if r[2] is None and not (r[3] or "").startswith("ChapterLocked")]
            if not retry:
                break
            # ★ 2026-10-02：这里原来是「检测到『站方截断』→ force 续期一次」。
            #   **已删**（用户定稿「cookie 只保留这一条路径」）。理由：
            #     · 截断的签名是「(r[3] or "")」里含「站方截断」，而它**同时**也出现在
            #       "这本书被锁"的情况下 → 于是每次都触发续期，续完还是锁，**无限循环**；
            #     · 现在真要证，走 `_ask_user_click_first_chapter()`（用户点第一章），
            #       而且只在"整卷全锁"那一个地方触发，不会在这里反复弹。
            #   没抓到的章仍然照常串行重试三轮（限流才需要重试，锁页在第 1418 行就收手了）。
            log(f"    ↩️ {len(retry)} 章没抓到（站点限流），串行重试第 {round_no} 轮（每章间隔 {pause}s，"
                f"当前请求间隔 {_THROTTLE.delay:.2f}s）…")
            for idx, ct, _b, _err in list(retry):
                time.sleep(pause)
                try:
                    results[idx - 1] = (idx, ct, chapter_blocks(chs[idx - 1][1]), None)
                except Exception as exc:
                    log(f"      ⚠️ {ct} 第 {round_no} 轮仍失败: {type(exc).__name__}: {str(exc)[:50]}")

        for idx, ct, blocks, err in results:
            if blocks is None:
                log(f"    ⚠️ {ct} 抓取失败（已跳过）: {err}")
                failed_chapters.append(ct)
                continue
            chapters.append((ct, blocks))
            img_urls += [v for k, v in blocks if k == "img"]

    if not chapters:
        return {"success": False,
                "error": "一章都没抓到（要么站点在限流返空壳页，要么这本书被站方锁了"
                         "「沒有可閱讀的章節」）—— 稍后重试，或改用别的书源"}

    uniq = list(dict.fromkeys(img_urls))
    log(f"  🖼 待下插图 {len(uniq)} 张（带 Referer，否则 403）")
    tmp = _work_dir()
    os.makedirs(tmp, exist_ok=True)
    img_map, done = {}, [0]

    def _one(item):
        # ★ 名字必须在**主线程预先分配**（原来在线程里用 len(img_map)+1 会竞态撞名）
        idx, u = item
        try:
            r = _get(u, timeout=40, tries=2)
            if r.status == 200 and r.body[:2] == b"\xff\xd8" and len(r.body) > 512:
                name = f"i{idx:04d}.jpg"
                with open(os.path.join(tmp, name), "wb") as fh:
                    fh.write(r.body)
                return u, name
        except Exception:
            pass
        return u, None

    # ★ 插图走 CDN（img3.readpai.com 一类），**不参与站点的 HTML 节流**，所以这里保持小并发，
    #   不受 `--workers` 影响：章节串行时没必要让图片也串行（那纯粹是等网络）。
    with ThreadPoolExecutor(max_workers=IMG_WORKERS) as ex:
        for u, name in ex.map(_one, list(enumerate(uniq, 1))):
            done[0] += 1
            if name:
                img_map[u] = name
            if as_json and (done[0] % 10 == 0 or done[0] == len(uniq)):
                log("@@PROGRESS " + json.dumps({"stage": "images", "done": done[0],
                                                "total": len(uniq)}, ensure_ascii=False))
    log(f"  ✅ 插图下载成功 {len(img_map)}/{len(uniq)}")

    # ★★★ 2026-09-30（用户要求）：文件名 + 书内标题都要带**卷号**。
    #   原因：同一本书的各卷正文标题是一样的（都叫《败北女角太多了！》），
    #   以前一律写成 `<书名>.epub` → 下完第 1 卷再下第 3 卷，**第 1 卷被覆盖**
    #   （书库里也只剩一本）。现在：单卷 → `书名 第N卷.epub`；多卷 → `书名 第1卷+第3卷.epub`。
    _vol_names = [str(v.get("name") or "").strip() for v in picked]
    _vol_tag = ""
    if len(_vol_names) == 1 and _vol_names[0]:
        _vol_tag = " " + _vol_names[0]
    elif len(_vol_names) > 3:
        # ★ 2026-09-30：整本（多卷）时卷名全拼会撑爆 Windows 260 字符路径（2139 有 47 卷）
        _vol_tag = " 全书"
    elif _vol_names:
        _vol_tag = " " + "+".join(n for n in _vol_names if n)
    else:                                   # 没勾卷（理论上不会）→ 退回总卷数说明
        _vol_tag = ""
    _full_title = (info["title"] + _vol_tag).strip()
    safe = re.sub(r'[\\/:*?"<>|]', "_", _full_title).strip() or f"linovelib-{novel_id}"
    os.makedirs(out_dir, exist_ok=True)
    epub = os.path.join(out_dir, safe + ".epub")
    # ★ 封面 = **正文里第一张插图**（用户 2026-09-30：「封面用下下来的插图第一张」）。
    #   `img_map` 的插入顺序 = 图片抓取顺序 = 正文出现顺序（ThreadPoolExecutor.map 按序返回），
    #   所以第一个值就是正文第一张图 —— 轻小说每卷的第一张本来就是该卷封面画。
    cover_image = next(iter(img_map.values()), "")
    # ★ 2026-09-30（用户要求）：**书内标题不带卷名**（就写《书名》），只有**文件名**带卷号 ——
    #   书内标题是"这本书叫什么"，卷号体现在文件名上就够了。
    build_epub(epub, info["title"], info["author"], chapters, img_map, cover_image=cover_image)
    words = sum(len(v) for _t, bl in chapters for k, v in bl if k == "p")
    if failed_chapters:
        log(f"  ⚠️ 有 {len(failed_chapters)} 章没抓到（站点限流/空壳页）：{', '.join(failed_chapters[:5])}"
            + (" …" if len(failed_chapters) > 5 else ""))
    return {"success": True, "file": epub, "title": info["title"], "volumes": _vol_names,
            "fileName": os.path.basename(epub), "author": info["author"],
            "chapters": len(chapters), "images": len(img_map), "words": words,
            "cover": cover_image, "failed": failed_chapters}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="哔哩轻小说 抓取 + EPUB 组装（零积分路线）")
    # ★ 2026-10-01（接回 Electron）：target 改成可选 —— `--refresh-cookie` 这条
    #   "只续期、不下载"的路不需要书号（界面上的「刷新 Cookie」按钮走它）。
    ap.add_argument("target", nargs="?", default="", help="书的网址或数字书号")
    ap.add_argument("--info", action="store_true", help="只列分卷（不下载）")
    ap.add_argument("--get", action="store_true", help="抓取并组装 EPUB")
    ap.add_argument("--vol", nargs="*", type=int, default=None, help="只下指定卷（1 起）")
    ap.add_argument("--out", default="", help="输出目录（默认 <脚本目录>/download）")
    ap.add_argument("--limit-chapters", type=int, default=0, help="每卷只抓前 N 章（调试用）")
    ap.add_argument("--workers", type=int, default=16,
                    help="章节抓取并发（默认 16＝爆发式：趁站点没反应过来先下完；"
                         "被打回会自动后退 + 三轮重试补齐）。想温柔点设 1")
    ap.add_argument("--throttle", type=float, default=0.02,
                    help="HTML 页面请求的最小间隔秒数（默认 0.02≈不限速；被打回一次会自动抬到 ≥0.3s）")
    ap.add_argument("--base", default="",
                    help="站点域名（默认 https://www.linovelib.com；被限流/空壳时可切 "
                         "https://www.bilinovel.com 或 https://tw.linovelib.com）")
    ap.add_argument("--cookie-file", default="",
                    help="浏览器 Cookie 文本文件（默认 <脚本目录>/cookies.txt；"
                         "cf_clearance 过期后从浏览器重拷再跑）。"
                         "★ 打包后请由主进程指到 userData，别用脚本目录（Program Files 不可写）")
    ap.add_argument("--auto-cookie", action="store_true",
                    help="cf_clearance 失效时自动用 Selenium+Edge 续期（弹一次小窗口约 10 秒）："
                         "① 每卷开始前超 25 分钟主动续；② 检测到「內容加載失敗」截断特征被动续")
    ap.add_argument("--refresh-cookie", action="store_true",
                    help="只取 Cookie（打开目录页让用户点第一章 → 抓证），不抓书")
    # ★★★ 2026-10-02：取 Cookie 必须知道是**哪本书**（要打开它的目录页让用户点第一章），
    #   所以 --refresh-cookie 配这个用。主进程从界面的「刷新 Cookie」按钮传下来。
    ap.add_argument("--novel-id", default="",
                    help="书号 → 打开 https://www.bilinovel.com/novel/<书号>/catalog "
                         "让用户点第一章（配 --refresh-cookie；也可用 target 位置参数）")
    ap.add_argument("--json", action="store_true", help="输出 @@INFO/@@PROGRESS + 末行 JSON")
    a = ap.parse_args(argv)

    global BASE, REF, _BASE_PINNED
    if a.base:
        BASE = a.base.rstrip("/")
        REF = BASE + "/"
        _HDR["Referer"] = REF
        _BASE_PINNED = True       # ★ 用户显式指定了域名 → 不要自动换（见 negotiate_base）
    if a.cookie_file:
        # ★ 记住路径：续期写的就是这个文件、读的也是这个文件（原来只读不记，见 _COOKIE_FILE）
        _set_cookie_file(a.cookie_file)
        _apply_cookie(_load_cookie_file(_COOKIE_FILE))

    if a.refresh_cookie:                              # ★ 只取 Cookie，不抓书
        # ★★★ 2026-10-02（用户定稿）★★★
        #   取 Cookie = 「**从主页一路点**，点到**任何一本**书的目录页就弹窗让用户点第一章」。
        #   cf_clearance 是**按域名签发**的，跟哪本书无关 → **书号可有可无**：
        #     · 给了：点点点没命中时，兜底直接开那本书的目录页；
        #     · 没给：纯靠从主页点（点不到才用脚本里写死的兜底目录页）。
        #   ⚠️ 上一版这里硬要求书号，用户点「刷新 Cookie」直接被挡住 —— 已去掉。
        _rid = (getattr(a, "novel_id", "") or "").strip() or parse_id(a.target or "") or ""
        if not _rid:
            _say("ℹ️ 没给书号 → 纯从主页点点点（点到哪本书的目录页都行，证是按域名签发的）")
        ok = _refresh_cookie_via_browser(novel_id=_rid)
        print(json.dumps({"success": ok, "cookieFile": _COOKIE_FILE,
                          "error": None if ok else "Cookie 续期失败（人机验证没过 / 没装 Edge / 缺 selenium）"},
                         ensure_ascii=False))
        return 0 if ok else 1

    if not a.target:
        ap.error("需要 target（书的网址或数字书号），或改用 --refresh-cookie")

    if a.throttle and a.throttle > 0:                 # ★ 全局节流起点（被限流会自动拉长）
        _v = max(0.0, min(_THROTTLE.ceiling, float(a.throttle)))
        _THROTTLE.delay = _v
        # 显式给了更小的间隔时，下限也要跟着降 —— 否则 reward() 会被 floor 顶住、参数形同虚设
        if _v < _THROTTLE.floor:
            _THROTTLE.floor = _v

    nid = parse_id(a.target)
    _hit_cands, _hit_note = [], ""    # ★ 只有"按书名解析"这条路才会填；书号/网址路径保持空
    if not nid:
        # ★ 2026-09-30：target 也可以是**书名** —— 界面「下载小说」框和【轻小说】弹窗
        #   都可以直接填「败北女角太多了」，这里走 novelmeta 解析成书号（译名也认）。
        log(f"🔎 「{a.target}」不是书号/网址，按书名解析…")
        hit = resolve_by_name(a.target)
        if not hit.get("id"):
            msg = hit.get("error") or "解析不出书号"
            print(json.dumps({"success": False, "error": msg}, ensure_ascii=False))
            return 1
        nid = hit["id"]
        _hit_cands = hit.get("candidates") or []
        _hit_note = hit.get("resolvedBy") or ""
        log(f"  ✅ 书号 {nid}：《{hit.get('title')}》 作者={hit.get('author') or '?'}"
            f"（{hit.get('matchedBy')}）")
        if len(hit.get("candidates") or []) > 1:
            others = "、".join(c["title"] for c in hit["candidates"][1:4])
            log(f"  ℹ️ 另有相似候选：{others}")
    out_dir = a.out or os.path.join(_HERE, "download")

    try:
        if a.info or not a.get:
            info = book_info(nid)
            if a.json:
                # ★ 带上 id（界面预检要把解析出来的书号回填输入框）+ 封面 + 多候选
                payload = {"success": True, "id": nid, "candidates": _hit_cands,
                           "resolvedBy": _hit_note, **info}
                print(json.dumps(payload, ensure_ascii=False))
            else:
                log(f"《{info['title']}》 作者={info['author'] or '?'}  {len(info['volumes'])} 卷")
                for i, v in enumerate(info["volumes"], 1):
                    log(f"  {i:2d}. {v['name']}   {v['url']}")
            return 0
        # ★★★★ 阉割版（2026-10-02）：**下载执行已移除** ★★★★
        #   本副本只保留 --info（查书号 / 列分卷 / 卷封面）；--get 一律拒绝。
        log("⛔ 该部分暂不开放使用（阉割版不含下载执行）")
        res = {"success": False, "notAvailable": True, "error": "该部分暂不开放使用"}
        if a.json:
            log("@@RESULT " + json.dumps(res, ensure_ascii=False))
        else:
            print(json.dumps(res, ensure_ascii=False))
        return 3
    except Exception as exc:
        res = {"success": False, "error": f"{type(exc).__name__}: {exc}"}
    if a.json:
        log("@@RESULT " + json.dumps(res, ensure_ascii=False))
    else:
        log(json.dumps(res, ensure_ascii=False, indent=2) if not res.get("success")
            else f"\n✅ 完成: {res['file']}\n   章节 {res['chapters']} · 插图 {res['images']} · 正文 {res['words']} 字")
    return 0 if res.get("success") else 1


if __name__ == "__main__":
    raise SystemExit(main())
