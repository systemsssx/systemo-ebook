#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""refresh_cookie.py —— cf_clearance 自动续期（Selenium 驱动 Edge 过人机验证，写回 cookies.txt）。

★ 2026-09-30：Cloudflare 给的「访客通行证」cf_clearance 故意设短时效（约 30 分钟~几小时）。
  浏览器会自动续期、人无感；脚本拿旧证跑会被拦（正文变截断版）。
  本脚本让脚本自己会续期：Selenium 开系统 Edge（和你手动过验证同一身份，最稳），
  打开章节页等 cf_clearance 出现 → 拼成 Cookie 请求头格式写回 cookies.txt。

★★★ 2026-10-01（接回 Electron）：**域必须跟 linovelib.py 的 --base 一致** ★★★
  cf_clearance 是按域签发的 —— 在 bilinovel.com 过的验证，拿去 linovelib.com 用是无效的。
  原来这里写死 `TARGET=bilinovel.com`，而 linovelib.py 默认跑 linovelib.com，
  两边一旦不一致就是「续了等于没续」。现在：
    · 默认域仍取 bilinovel.com（实测它才是**必须**带证的那个域）；
    · 但 `--url` / `--domain` 可覆盖，且 `--domain` 不填时**自动跟着 --url 的域名走**；
    · linovelib.py 的 `_refresh_cookie_via_browser()` 会显式传 `--url <当前 BASE>`。

用法：
  py refresh_cookie.py                    # 弹出小 Edge 窗口（~10 秒）完成续期
  py refresh_cookie.py --headless         # 无窗口（实测可用；CF 若拦就去掉这个开关）
  py refresh_cookie.py --cookie-file X    # 写到别的文件
  py refresh_cookie.py --url https://www.linovelib.com   # 换域续期
  import refresh_cookie; refresh_cookie.refresh()        # 供 linovelib.py 自动调用
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from urllib.parse import urlparse

_HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_COOKIE_FILE = os.path.join(_HERE, "cookies.txt")

# ★★★ 2026-10-02（用户定稿）：**这是唯一的取 Cookie 路径** ★★★
#   用户原话：「用 bilinovel 的那个网址，然后到 https://www.bilinovel.com/novel/2139/catalog
#   这个网址后直接弹窗提醒用户点一下 第一章 结束的开始，然后进正文继续爬」
#   「cookie 只保留这一条路径」
#
#   ⇒ 删掉以前那套「打开主页 → 脚本自己顺着链接瞎点」（_walk_to_body 那一族函数）：
#     · 它对**需要登录/权限**的书永远点不进去（实测 2013：目录页好好的，章节页全是
#       「沒有可閱讀的章節：…需要足夠的權限」）；
#     · 它一次最多点 10 步 / 75 秒，点不到就放弃；
#     · 它是"脚本猜路"，而现在改成"**真人点一次**"——最稳，也最符合站点对真人的判定。
#
#   现在的唯一流程：
#     ① 打开 https://www.bilinovel.com/novel/<书号>/catalog     ← 目录页直链
#     ② @@NEEDCLICK：让界面弹窗提醒用户「点一下第一章」
#     ③ 轮询 drv.current_url，**等用户点进章节页**（URL 形如 /novel/<id>/<数字>.html）
#        且该页面真的读得出正文
#     ④ 在那一刻抓 Cookie，非破坏性合并写回 cookie 文件
HOME = "https://www.bilinovel.com/"          # ★ 从主页进去（用户定稿）
CATALOG_TMPL = "https://www.bilinovel.com/novel/{id}/catalog"
_CATALOG_URL_RE = re.compile(r"/novel/\d+/catalog")
CATALOG_DOMAIN = "bilinovel.com"
# 章节页 URL 特征：/novel/2139/76674.html（注意**不是** /vol_*.html 那种分卷页）
_CHAPTER_URL_RE = re.compile(r"/novel/\d+/\d+\.html")
TARGET = CATALOG_TMPL.format(id="2013")     # 兜底落地页（没给书号时）
DOMAIN = CATALOG_DOMAIN

# ★★★ 2026-10-01（实测踩到的坑，必看）：**必须强制 stdout/stderr 走 UTF-8** ★★★
#   本脚本是**被 linovelib.py 用 subprocess 拉起来的**，而那条命令没有 `-X utf8`
#   （`[sys.executable, "refresh_cookie.py", …]`）。被拉起来的进程 stdout 是管道，
#   Windows 下 Python 就按**本地代码页 GBK** 编码 —— 于是最后那句
#   `print("✅ 已写入 …")` 直接抛 `UnicodeEncodeError: 'gbk' codec can't encode …`
#   → **退出码 1**（尽管 cookies.txt 其实已经写成功了）。
#   后果非常隐蔽：`_refresh_cookie_via_browser()` 只看 returncode，判成"续期失败"
#   就不再重载 Cookie —— 新证明明落盘了，抓取却还在用旧的，继续报「正文被站方截断」。
#   （和 linovelib.py 顶部那段 `sys.stdout.reconfigure` 是同一个坑、同一个解法。）
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass


def domain_of(url: str) -> str:
    """URL → 用于筛 Cookie 的注册域（`www.bilinovel.com` → `bilinovel.com`）。"""
    host = (urlparse(url).hostname or "").lower()
    parts = host.split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def _parse_cookie_line(line: str) -> dict:
    """`a=1; b=2` → {a:1, b:2}（保持顺序）。"""
    out = {}
    for p in (line or "").split("; "):
        p = p.strip()
        if "=" in p:
            k, v = p.split("=", 1)
            if k:
                out[k] = v
    return out


def _merge_cookie_lines(old_line: str, new_line: str) -> str:
    """★ 非破坏性合并：**新证覆盖同名项，其余旧项原样保留**。

    用户实测的原话：「他用的是 se 独立窗口的 cookie」—— Se de 独立窗口 = 全新临时配置，
    只有匿名访客那几项；而用户从浏览器 F12 拷出来的那份带着登录态等更多项。
    直接覆写 = 把好东西冲掉，所以只更新同名项。
    """
    d = _parse_cookie_line(old_line)
    d.update(_parse_cookie_line(new_line))
    return "; ".join(f"{k}={v}" for k, v in d.items())


def _page_has_body(html: str) -> bool:
    """当前页面里**真的读得出正文**吗（正文容器里至少有 <p>，且没有截断标记）。"""
    m = max(html.find('id="TextContent"'), html.find('id="acontent"'))
    if m < 0:
        return False
    seg = html[m:]
    if ("內容加載失敗" in seg) or ("内容加载失败" in seg):
        return False
    return bool(re.search(r"<p[\s>]", seg, re.I))


def _collect_novel_links(drv) -> list:
    """页面上所有指向本书站的**书页/目录页/章节页**链接（去重）。"""
    try:
        return drv.execute_script("""
            var out = [], seen = {};
            document.querySelectorAll('a[href]').forEach(function (a) {
                var h = a.href || '';
                if (!/^https?:\\/\\/[^\\/]+\\/novel\\//.test(h)) return;
                if (seen[h]) return;
                seen[h] = 1;
                out.push({href: h,
                          text: (a.textContent || '').replace(/\\s+/g, ' ').trim().slice(0, 40)});
            });
            return out;
        """) or []
    except Exception:                                     # noqa: BLE001
        return []


def _pick_next_link(links: list, clicked: set) -> str:
    """挑下一个该点的链接：**章节页 > 目录页 > 书页**，"插图"章排最后（它没有 <p>）。"""
    def score(l):
        h, t = l.get("href") or "", l.get("text") or ""
        s = 0
        if re.search(r"/novel/\d+/\d+\.html$", h):
            s += 60                                       # 正文页：最想要
        if re.search(r"/novel/\d+/catalog$", h):
            s += 40                                       # 目录页
        if re.search(r"/novel/\d+\.html$", h):
            s += 20                                       # 书页
        if re.search(r"阅读|目录|开始|章节|最新|正文|全文|下一", t):
            s += 15
        if "插图" in t:
            s -= 25                                       # 插图章没有 <p>，别浪费一步
        return s

    cand = [l for l in links if (l.get("href") or "") not in clicked]
    if not cand:
        return ""
    cand.sort(key=score, reverse=True)
    return cand[0]["href"]


def _click_href(drv, href: str) -> bool:
    """**真的去点那个 <a>**（不是 driver.get）—— 真人怎么点我们就怎么点，
    这样 Referer/浏览历史都是自然的，站点看到的是"顺着按钮走进来的访客"。"""
    try:
        return bool(drv.execute_script("""
            var want = arguments[0];
            var a = Array.prototype.slice.call(document.querySelectorAll('a[href]'))
                      .filter(function (x) { return x.href === want; })[0];
            if (!a) return false;
            try { a.scrollIntoView({block: 'center'}); } catch (e) {}
            a.click();
            return true;
        """, href))
    except Exception:                                     # noqa: BLE001
        return False


def _follow_new_tab(drv, before: list) -> bool:
    """点完看看有没有开新标签；有就切过去，并在**安全前提**下关掉旧的。

    ★★★ 2026-10-01 实测踩到的坑：linovelib 主页上的章节链接带 `target="_blank"` ——
      点一下开一个新标签页，当前窗口的 URL 纹丝不动。真人这时会切到新标签去。

    ★★★ 2026-10-02 实测又踩到两个坑（用户报「到对应网址程序没有弹窗」）：
      ① **大多数链接并不带 `_blank`** —— 原来找不到新句柄就死等 25×0.3 = **7.5 秒**，
         一次刷新白等 50 多秒（日志实测：52 秒才点了几步）。
         → 等待缩到 1.5 秒，而且**没开新标签就直接返回 True**（当前页已经导航过去了）。
      ② **关旧标签把关到"最后一个窗口"时，整个 WebDriver 会话就废了** —
         后面任何 `drv.get()` 都抛 `InvalidSessionIdException`（实测就是这么死的）。
         → **只剩一个窗口时绝不 close**，并全程吞掉异常、判活后再操作。
    """
    # ① 短等：新标签一般 0.2~0.6 秒就出来了
    new = None
    for _ in range(5):                                    # 最多等 ~1.5s
        try:
            cur = drv.window_handles
        except Exception:                                 # noqa: BLE001
            return False                                  # 会话已经没了
        extra = [h for h in cur if h not in before]
        if extra:
            new = extra[-1]
            break
        time.sleep(0.3)
    if not new:
        # ★ 没开新标签 = 当前标签自己导航过去了，这很正常 —— **不是失败**
        return True
    try:
        drv.switch_to.window(new)
        # ② 只有在**还剩不止一个窗口**时才关旧的，否则会把会话关死
        for h in before:
            if h == new:
                continue
            try:
                if len(drv.window_handles) <= 1:
                    break                                 # ★ 最后一个窗口，绝不关
                if h in drv.window_handles:
                    drv.switch_to.window(h)
                    drv.close()
            except Exception:                             # noqa: BLE001
                pass
        if new in drv.window_handles:
            drv.switch_to.window(new)
        return True
    except Exception:                                     # noqa: BLE001
        return False


def _wait_ready(drv, seconds: float = 12.0) -> None:
    t0 = time.time()
    while time.time() - t0 < seconds:
        try:
            if drv.execute_script("return document.readyState") == "complete":
                break
        except Exception:                                 # noqa: BLE001
            pass
        time.sleep(0.4)
    time.sleep(0.8)


def _wait_url_change(drv, prev: str, timeout: float = 3.0) -> str:
    """★★★ 2026-10-02（用户定稿）：「我只要到网页就要弹窗，**不需要加载完**」★★★

    点完只等 **URL 变**（或开了新标签），**不等 `readyState==complete`** ——
    站点首页很重，等加载完每步要 6~7 秒，12 步就把 90 秒预算吃光了（实测）。

    返回当前 URL（拿不到就返回 ""）。
    """
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            cur = drv.current_url or ""
        except Exception:                                 # noqa: BLE001
            return ""
        if cur and cur != prev:
            return cur
        try:
            if len(drv.window_handles) > 1:                # 开新标签也算"变了"
                return cur
        except Exception:                                 # noqa: BLE001
            return ""
        time.sleep(0.25)
    try:
        return drv.current_url or ""
    except Exception:                                     # noqa: BLE001
        return ""


def _wait_for_chapter(drv, timeout: float = 300.0, poll: float = 0.6,
                      helper_clicks: int = 3) -> str:
    """★★★ 2026-10-02（用户定稿）★★★ **不检测目录页了** —— 只等"进到正文页"。

    用户原话：「你就不要检测了，你就一开始弹弹窗提示用户到达目录页时点击第一章」

    流程：
      · 开头先弹窗（在 refresh() 里，@@NEEDCLICK）——**不等任何检测结果**；
      · 本函数先**帮点几下**，把浏览器带到某个 `/novel/` 页面（最多 helper_clicks 次），
        到了就**立刻停手**，不跟用户抢鼠标；
      · 之后**只轮询 URL**，一旦命中 `/novel/<id>/<数字>.html` 且读得出正文 → 返回它。

    ★ 全程**没有**"是不是目录页"这种判断 —— 少一层判断就少一层出 bug 的机会。
    """
    deadline = time.time() + max(30.0, float(timeout))
    last, clicked, clicks = "", set(), 0
    while time.time() < deadline:
        try:
            cur = drv.current_url or ""
        except Exception:                                 # noqa: BLE001
            print("⚠️ 浏览器窗口已被关闭 → 放弃等待", file=sys.stderr)
            return ""
        if cur != last:
            print(f"   👀 {cur}", file=sys.stderr)
            last = cur
        # ★ 唯一的成功判据：URL 是章节页 + 那一页真读得出正文
        if _CHAPTER_URL_RE.search(cur):
            try:
                if _page_has_body(drv.page_source):
                    print(f"   ✅ 已经进到正文页了：{cur}", file=sys.stderr)
                    return cur
                print(f"   ⏳ 已在章节页但还读不出正文（可能要登录）：{cur}", file=sys.stderr)
            except Exception:                             # noqa: BLE001
                pass
        # 帮点：只点到"进入某个 /novel/ 页面"为止，之后交给用户
        if clicks < helper_clicks and "/novel/" not in cur:
            nxt = _pick_next_link(_collect_novel_links(drv), clicked)
            if nxt:
                clicked.add(nxt)
                clicks += 1
                print(f"   👆 帮你点一下 → {nxt}", file=sys.stderr)
                before = list(drv.window_handles)
                try:
                    _click_href(drv, nxt)
                    _follow_new_tab(drv, before)
                except Exception:                         # noqa: BLE001
                    pass
                time.sleep(0.8)
                continue
        time.sleep(poll)
    print(f"⏰ 等了 {timeout:.0f} 秒也没等到你点进正文页", file=sys.stderr)
    return ""


def _wait_user_click(drv, timeout: float = 300.0, poll: float = 1.5) -> str:
    """等用户自己点进正文页（URL 命中章节页格式且读得出正文）。返回那样一页的 URL；超时返回 ""。"""
    deadline = time.time() + max(30.0, float(timeout))
    last = ""
    while time.time() < deadline:
        try:
            cur = drv.current_url or ""
        except Exception:                                 # noqa: BLE001
            print("⚠️ 浏览器窗口已被关闭 → 放弃等待", file=sys.stderr)
            return ""
        if cur != last:
            print(f"   👀 现在停在 {cur}", file=sys.stderr)
            last = cur
        if _CHAPTER_URL_RE.search(cur):
            try:
                if _page_has_body(drv.page_source):
                    print(f"   ✅ 已经进到正文页了：{cur}", file=sys.stderr)
                    return cur
                print(f"   ⏳ 已在章节页但还读不出正文（可能要登录）：{cur}", file=sys.stderr)
            except Exception:                             # noqa: BLE001
                pass
        time.sleep(poll)
    print(f"⏰ 等了 {timeout:.0f} 秒也没等到正文页", file=sys.stderr)
    return ""


def _catalog_url(novel_id: str) -> str:
    """书号 → 目录页 URL。**固定用 bilinovel.com**（用户定稿）。"""
    return CATALOG_TMPL.format(id=str(novel_id or "").strip())


def refresh(cookie_file: str = DEFAULT_COOKIE_FILE, *, headless: bool = False,
            timeout: float = 300.0, url: str = "", domain: str = "",
            novel_id: str = "", landing: str = "", wait_click: bool = True) -> str:
    """打开**目录页** → 等用户点进第一章 → 抓那一刻的 Cookie 写盘。失败抛 RuntimeError。

    ★★★ 2026-10-02（用户定稿）：**这是唯一的取 Cookie 路径** ★★★
      用户原话：「用 bilinovel 的那个网址，然后到
                  https://www.bilinovel.com/novel/2139/catalog 这个网址后
                  直接弹窗提醒用户点一下 第一章 结束的开始，然后进正文继续爬」
                 「cookie 只保留这一条路径」

    落地页优先级：`landing` > （`novel_id` → `CATALOG_TMPL`）> `url` > `TARGET`。
    `wait_click=True`（默认）：**不自己点**，只轮询等用户点进章节页（`_wait_user_click`）。
    `wait_click=False`：落页后若本身就有正文就直接抓（给"已经是章节页"的场景用）。

    为什么必须真人点：这本书要权限/登录（站点原话「沒有可閱讀的章節：內容可能審核未通過
    或需要足夠的權限」），脚本自己点一万次也进不去；真人点一下（顺带登录）就进去了。
    """
    from selenium import webdriver
    from selenium.webdriver.edge.options import Options
    from selenium.webdriver.edge.service import Service  # noqa: F401  (Selenium Manager 用)
    from selenium.webdriver.support.ui import WebDriverWait

    target_url = (landing or "").strip()
    if not target_url and (novel_id or "").strip():
        target_url = _catalog_url(novel_id)
    if not target_url:
        target_url = (url or "").strip() or TARGET

    dom = (domain or "").strip() or domain_of(HOME)
    opt = Options()
    opt.add_argument("--start-minimized")
    opt.add_argument("--log-level=3")
    opt.add_experimental_option("excludeSwitches", ["enable-logging"])
    # ★★★ 2026-10-02（用户问「你不能获取正在加载的网址信息吗」—— 问到根子上了）★★★
    #   **必须改 page_load_strategy**，否则读 URL 这个动作本身会被卡住：
    #   Selenium 默认 `normal` = 每条命令都等 `document.readyState === 'complete'`。
    #   站点页面挂着一堆广告/统计脚本，complete 迟迟不到 → `drv.current_url`、
    #   `drv.execute_script(...)` **全部阻塞**，最长等到 pageLoadTimeout（默认 300 秒）。
    #   实测证据：点了目录页后日志停在 `👆 [1] …/catalog` 53 秒没有任何输出。
    #   `eager` = 只等 DOMContentLoaded（文档解析完、链接可点）→ 立即返回，
    #   于是**页面还在加载时就能读到 URL**（正是用户要的「到网页就弹窗」）。
    opt.page_load_strategy = "eager"
    if headless:
        opt.add_argument("--headless=new")
    try:
        drv = webdriver.Edge(options=opt)                # Selenium Manager 自动配 msedgedriver
    except Exception as exc:                              # noqa: BLE001
        raise RuntimeError(f"启动 Edge 失败: {str(exc)[:150]}") from exc
    # ★ 双保险：任何一次导航最多 20 秒，绝不允许 300 秒的默认值把整个流程钉死
    try:
        drv.set_page_load_timeout(20)
    except Exception:                                     # noqa: BLE001
        pass
    body_url, landed_url, cookies = "", "", []
    try:
        # ① **主页进去**（用户定稿：「按原来的从主页进去」）—— 比直接跳目录页更像真人
        print(f"🌐 打开主页：{HOME}", file=sys.stderr)
        drv.get(HOME)
        # Cloudflare 验证异步落地：轮询等 cf_clearance（一般 2~8 秒）
        WebDriverWait(drv, min(timeout, 60.0), poll_frequency=1).until(
            lambda d: any(c["name"] == "cf_clearance" for c in d.get_cookies()))
        _wait_ready(drv, 6.0)
        # ② **一直点点点**，每一步都看 URL（用户定稿：「检测到网址跟我那个格式一样时弹弹窗」）
        print(f"🔗 从主页开始顺着链接点，直到命中目录页/正文页…", file=sys.stderr)
        # ★★★ 2026-10-02（用户定稿）★★★
        #   「你就不要检测了，你就**一开始弹弹窗**提示用户到达目录页时点击第一章」
        #   ⇒ **先弹窗，再干活**。弹窗不等任何检测结果，落地页一开出来就弹。
        payload = {"catalog": (landing or "").strip(), "novelId": str(novel_id or "")}
        print("@@NEEDCLICK " + json.dumps(payload, ensure_ascii=False), flush=True)
        print(f"👉 已打开浏览器 —— 请一路点到**目录页**，然后在目录页点一下**第一章**"
              f"（我这边等你进了正文页就自动抓证，最多等 {timeout:.0f} 秒）…", file=sys.stderr)
        # 然后再等正文页（内部会先帮点几下到 /novel/ 页面，之后交给用户）
        body_url = _wait_for_chapter(drv, timeout=timeout) if wait_click else ""
        landed_url = drv.current_url                     # ★ 最终落在哪个域名（镜像会互相跳）
        cookies = drv.get_cookies()                      # ④ 那一刻的 Cookie
    finally:
        try:
            drv.quit()
        except Exception:                                 # noqa: BLE001
            pass

    if not any(c["name"] == "cf_clearance" for c in cookies):
        raise RuntimeError(f"没等到 cf_clearance（{dom} 的人机验证没过）")
    if not body_url:
        # ★ 如实报出来：证是拿到了，但**没走到有正文的页**
        #   —— 这种证基本没用（继续跑只会得到截断版/锁页），必须让调用方知道。
        raise RuntimeError(
            f"拿到 cf_clearance 了，但从主页点 + 等 {timeout:.0f} 秒都没到正文页"
            f"（书号 {novel_id or '未给'}）—— 点一下第一章的链接，或先登录再点")
    # ★★★ 2026-10-01（实测踩到的坑）：**必须按"最终落地域名"筛，不能按 --url 请求的域名** ★★★
    #   站点的几个镜像域名会互相跳转：从 www.bilinovel.com 点进章节页，**会被跳到
    #   www.linovelib.com**（实测）。cookie 于是全落在 `.linovelib.com` 上；
    #   若还按请求的 `bilinovel.com` 去筛 → 一项都筛不出来，报
    #   「拿到了 Cookie，但没有一项属于 bilinovel.com（--url 的域选错了？）」。
    #   而这个"跳到哪了"其实是个**有用的信息**：它告诉我们哪个域名才真的有正文。
    parts = [f'{c["name"]}={c["value"]}' for c in cookies
             if dom in (c.get("domain") or "")]
    if not parts:
        # 请求域名一项都没有 → 用 cookie jar 里真实存在的域名兜底（并如实说明）
        doms = sorted({(c.get("domain") or "").lstrip(".") for c in cookies if c.get("domain")})
        alt = next((d for d in doms if d), "")
        if alt:
            parts = [f'{c["name"]}={c["value"]}' for c in cookies
                     if alt in (c.get("domain") or "")]
            print(f"ℹ️ 请求的域是 {dom}，但浏览器最后落在 {alt}（镜像域名互相跳转）"
                  f"→ 按 {alt} 取 Cookie", file=sys.stderr)
    if not parts:
        raise RuntimeError(f"拿到了 Cookie，但没有一项可用（请求域 {dom}，"
                           f"jar 里的域={sorted({(c.get('domain') or '') for c in cookies})}）")
    fresh = "; ".join(parts)

    # ★★★ 2026-10-01（用户指出「他用的是 se 独立窗口的 cookie」—— 完全正确）★★★
    #   Selenium 每次起的是**全新临时配置**（相当于一个刚装的 Edge）：里面只有匿名访客
    #   那几项，**没有**你原生浏览器里的登录态/其它票据。
    #   所以**绝不能直接覆写** cookie 文件 —— 那会把你从 F12 拷进来的原生 Cookie 冲掉，
    #   只剩 Selenium 拿得到的那几项。改成**非破坏性合并**：
    #     同名项用新证覆盖（cf_clearance 就是要它新）；**其余项一律保留**。
    old_line = ""
    try:
        with open(cookie_file, "r", encoding="utf-8") as fh:
            old_line = fh.read().strip()
    except OSError:
        pass
    line = _merge_cookie_lines(old_line, fresh)

    # ★ 先写临时文件再改名：主进程/抓取进程随时可能读这个文件，
    #   直接覆写会让并发读到"写了一半"的 Cookie 串。
    d = os.path.dirname(os.path.abspath(cookie_file)) or "."
    os.makedirs(d, exist_ok=True)
    tmp = cookie_file + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(line)
    os.replace(tmp, cookie_file)
    return line


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="取 cf_clearance：打开目录页 → 等用户点第一章 → 抓证（唯一的取 Cookie 路径）")
    ap.add_argument("--cookie-file", default=DEFAULT_COOKIE_FILE)
    ap.add_argument("--headless", action="store_true", help="无窗口（★ 要用户点第一章时别开）")
    ap.add_argument("--timeout", type=float, default=300.0,
                    help="等用户点第一章的超时秒数（默认 300）")
    # ★★★ 2026-10-02（用户定稿）：唯一路径 = 目录页 + 等用户点第一章
    ap.add_argument("--novel-id", default="",
                    help=f"书号 → 目录页 {CATALOG_TMPL}（推荐用这个）")
    ap.add_argument("--landing", default="",
                    help="直接给落地页 URL（覆盖 --novel-id）")
    ap.add_argument("--no-wait-click", action="store_true",
                    help="不等人点，落页若有正文就直接抓（给已经是章节页的场景）")
    # ★ 兼容旧参：--url 现在只作兜底落地页
    ap.add_argument("--url", default="",
                    help=f"兜底落地页（--landing/--novel-id 都没有时用，最后兜到 {TARGET}）")
    ap.add_argument("--domain", default="",
                    help="筛 Cookie 用的域（默认跟着落地页自动推导）")
    a = ap.parse_args(argv)
    try:
        line = refresh(a.cookie_file, headless=a.headless, timeout=a.timeout,
                       url=a.url, domain=a.domain, novel_id=a.novel_id,
                       landing=a.landing, wait_click=not a.no_wait_click)
    except Exception as exc:                              # noqa: BLE001
        print(f"❌ 取 Cookie 失败: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    names = [p.split("=", 1)[0] for p in line.split("; ")]
    land = a.landing or (CATALOG_TMPL.format(id=a.novel_id) if a.novel_id else (a.url or TARGET))
    print(f"✅ 已写入 {a.cookie_file}（落地域 {domain_of(land)}，{len(names)} 项: "
          f"{', '.join(names[:6])}…）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())