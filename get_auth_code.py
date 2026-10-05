#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
脚本二：用授权码换取百度网盘 token（支持真正意义上的「重新授权」）

★ 2026-09-23 重要改动：换 token 不再交给 bypy，改成直接调百度 OAuth 接口
    `POST https://openapi.baidu.com/oauth/2.0/token`（见 _exchange_code_direct）。

    原因（实测）：bypy 换 token 失败时会自己重试 5 次（等 10/20/30/40 秒），
    **整整 103 秒**才返回 140，而且把百度返回的真实原因
    （`{"error":"invalid_grant","error_description":"invalid code , expired or revoked"}`）
    吞掉，只留一行 `Maximum number (5) of tries failed.` ——
    用户看到的就是「点提交 → 卡两分钟 → 授权失败」，完全不知道错在哪。
    直连接口后：0.6 秒返回，并把百度的原话原样打给用户。

为什么授权要用隔离配置目录、还做 token 备份：
    bypy 的 ByPy.__init__ 里只有
        if not self._load_local_json():
            result = self._auth()
    才会走交互式授权。也就是说——**本地已经有一份 token 时，
    不管喂什么授权码进去，bypy 只会读本地 token 然后照常干活**，
    粘贴进来的授权码被直接丢弃。旧实现于是永远显示「授权成功」，
    其实根本没换 token。

    修法：新 token 先进一个空的临时 configdir（并真打一次 API 验证通过），
    再覆盖到真正的 `~/.bypy/bypy.json`。验证通过前原 token 被挪到 .bak，
    绝不破坏；失败一律还原。
"""

import json
import io
import os
import re
import shutil
import subprocess
import socket
import sys
import tempfile
import time

# ★ 输出统一 UTF-8 + 容错（2026-09-23）：main.js 用 execFile(encoding:'utf-8') 读本脚本的
#   stdout/stderr，而本机默认区域编码是 GBK。只要有一处按区域编码写出中文，父进程
#   （或下面 _popen 读 bypy 输出时的 _readerthread）就会抛
#       UnicodeDecodeError: 'utf-8' codec can't decode byte 0xd0 …
#   把整个授权流程带崩。其他脚本（epub_generator.py / kindle_send.py …）早就这么包了，
#   本脚本原来漏了。errors='replace' 保证「最坏情况是乱码，绝不是崩溃」。
try:
    sys.stdin = io.TextIOWrapper(sys.stdin.buffer, encoding='utf-8', errors='replace')
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')
except Exception:
    pass

# ★ 换 token / 验证 token 都已经不经过 bypy：
#   · 换 token → 直连百度 OAuth（见 _exchange_code_direct）
#   · 验证     → 直连百度网盘开放平台 xpan 接口（见 verify_token）
#   所以不再需要「比 bypy 的 5 次重试(约 100 秒)更宽松」的那个 300 秒超时。
#   ★ 这两个上限和 main.js 里的外层上限是一套的，改一个要一起改：
#       OAUTH_TIMEOUT(30) + VERIFY_TIMEOUT(30) = 60 秒
#       → 重新授权外层 90 秒（main.js 授权分支的 authTimer）
#       → 静默续期外层 120 秒（main.js 的 --refresh 分支）
#   ★★ 2026-09-23 第二次实测（重要）：从这台机器打 uinfo 实测**要 15.85 秒**
#      才拿到 errno=0 —— 说明第一次请求正好在 15 秒上限上超时、靠重试才成功。
#      15 秒太紧，会把「百度慢」误报成「授权失败」。所以两个都放到 30 秒。
OAUTH_TIMEOUT = 30       # 单次直连百度 OAuth 接口（换 token / 续期）
VERIFY_TIMEOUT = 30      # 单次直连官方 xpan 接口（还顺便预热云端索引）


# ★★ 2026-09-23 实测：这台机器解析 pan.baidu.com / pcs.baidu.com 会先给出 **IPv6** 地址，
#   但本机没有可用的 IPv6 出口 —— 每个 IPv6 连接要卡满 21 秒 TCP 超时才回退 IPv4
#   （pcs.baidu.com 实测：IPv6 21.02s + 21.03s 全超时，IPv4 仅 0.05s）。
#   requests 按地址顺序挨个试、不并行，所以「百度慢」的一大块其实是这段白等
#   （见下面 VERIFY_TIMEOUT 注释里那次 15.85 秒）。这里把 getaddrinfo 过滤成只剩 IPv4。
#   curl 不受影响是因为它做 Happy Eyeballs（v4/v6 并行）。
_g = socket.getaddrinfo


def _ipv4_only(*args, **kwargs):
    try:
        got = _g(*args, **kwargs)
    except Exception:
        raise
    only4 = [x for x in got if x[0] == socket.AF_INET]
    return only4 or got          # 万一只剩 IPv6（本机确实没有 IPv4）就原样返回


socket.getaddrinfo = _ipv4_only

# bypy 子进程用的同一套补丁（内联成 -c 代码，子进程继承不到本进程的内存补丁）
_BYPY_RUN = (
    "import socket as _s;"
    "_g=_s.getaddrinfo;"
    "_s.getaddrinfo=lambda *a, **k: ([x for x in _g(*a, **k) if x[0]==_s.AF_INET] or _g(*a, **k));"
    "import sys;from bypy.bypy import main;sys.exit(main())"
)


def _bypy_cmd(*args):
    """
    组装 bypy 命令行。

    开发环境：用当前解释器跑带 IPv4 补丁的 -c 代码（见上面的 _force_ipv4 注释），
              直接调 bypy 的 main()。
    打包环境（PyInstaller 冻结）：转由随包的后端 exe 以 `__bypy__` 子命令代理执行
              （见 backend_entry.py 的 _run_bypy，那里同样做了 IPv4 过滤）。
    """
    if getattr(sys, 'frozen', False) or os.environ.get('EASYPUB_BACKEND'):
        return [sys.executable, '__bypy__'] + [str(a) for a in args]
    try:
        import importlib.util
        if importlib.util.find_spec('bypy') is not None:
            return [sys.executable, '-c', _BYPY_RUN] + [str(a) for a in args]
    except Exception:
        pass
    return ['bypy'] + [str(a) for a in args]


def _with_config_dir(cmd, configdir):
    """把 --config-dir 插到子命令之前（bypy 的全局参数在子命令前面）。

        [python,'__bypy__','info']  →  [python,'__bypy__','--config-dir',d,'info']
        ['bypy','info']            →  ['bypy','--config-dir',d,'info']
    """
    if not configdir:
        return cmd
    return cmd[:1] + ['--config-dir', configdir] + cmd[1:]


def _kill_tree(pid):
    """★ 连子进程一起杀。

    为什么不能只 p.kill()：bypy 会自己再起子进程，而子进程继承了
    我们传下去的 stdout/stderr 管道句柄。只杀掉直接子进程的话管道
    永远等不到 EOF —— 后续任何读管道的动作（communicate / text=True
    的析构）都会永久挂住，这正是 2026-09-23 那次「卡住不动」的原因。
    """
    if not pid:
        return
    try:
        subprocess.run(
            ['taskkill', '/F', '/T', '/PID', str(pid)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=15,
        )
    except Exception:
        pass


def _wait_with_timeout(process, timeout, stdin_text=None):
    """带超时的等待，且**超时后不会因为管道未关而死锁**。

    返回 (stdout, stderr)；超时返回 (None, None)。
    """
    try:
        if stdin_text is None:
            out, err = process.communicate(timeout=timeout)
        else:
            out, err = process.communicate(input=stdin_text, timeout=timeout)
        return out, err
    except subprocess.TimeoutExpired:
        _kill_tree(process.pid)
        try:
            process.communicate(timeout=10)   # 树已杀，管道会正常关闭
        except Exception:
            pass
        return None, None


def _popen(cmd):
    return subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding='utf-8',
        errors='replace',
        env={**os.environ, 'PYTHONIOENCODING': 'utf-8'},
    )


def _pulse(what, interval=5.0):
    """在等百度接口返回时，每 interval 秒打一行「还在等…」。

    ★ 为什么需要（2026-09-23 第二次实测）：从这台机器打百度接口实测要 15.85 秒
      才返回。这十几秒里脚本一个字都不输出，界面上看起来就是「点了没反应」，
      用户会以为程序卡死了。打印心跳让「在等网络」和「真卡死」能区分开。

    用法：with _pulse('换 token'): r = requests.post(...)
        或者 p = _pulse(...); p.start(); ...; p.stop()
    """
    import threading
    import time as _time

    class _Pulse:
        def __init__(self):
            self._stop = threading.Event()
            self._t = None

        def _run(self):
            t0 = _time.time()
            # 先等一个 interval：接口快的时候（亚秒）就完全不打印，不刷屏
            while not self._stop.wait(interval):
                print(f"⏳ 还在等百度返回（{what}）… 已等待 {int(_time.time() - t0)} 秒",
                      flush=True)

        def start(self):
            self._t = threading.Thread(target=self._run, daemon=True)
            self._t.start()
            return self

        def stop(self):
            self._stop.set()
            if self._t:
                self._t.join(timeout=1)

        def __enter__(self):
            return self.start()

        def __exit__(self, *exc):
            self.stop()
            return False

    return _Pulse()


def check_code_format(auth_code):
    """★ 只拦「一眼就不是授权码」的输入，不做严格校验。

    背景（实测）：bypy 拿到一个格式合法但内容无效的授权码，会自己重试
    5 次（等 10/20/30/40 秒）——**整整 103 秒**才返回 140。表现就是
    「点了提交，界面卡两分钟，然后报授权失败」，用户完全不知道哪里错了。

    所以明显的错误输入必须**立刻**报出来：
      · 带空格/换行/引号 → 多半是复制时连上下文一起拿进来了
      · 长度 < 16          → bypy 自己都会拒（ask() 要求 >=16）
      · 不是字母数字        → Baidu 的 code 永远是字母数字

    真码通过（不做长度上限、不做大小写限制），拿不准一律放行 —— 宁可
    让 bypy 去试，也不要误杀一个真码。

    返回 None 表示放行，否则返回给用户看的原因字符串。
    """
    code = (auth_code or '').strip()
    if not code:
        return "没有填授权码"
    if any(ch.isspace() for ch in code):
        return ("授权码里夹了空格或换行 —— 复制时多选到别的内容了，"
                "请只复制百度页面显示的那一串字母数字")
    if not re.fullmatch(r'[A-Za-z0-9]+', code):
        return ("授权码只能是字母和数字（Baidu 返回的是 32 位左右），"
                "现在这串里有其它字符 —— 很可能是把 API Key / Secret Key 粘进来了")
    if len(code) < 16:
        return "授权码太短了（Baidu 返回的通常是 32 位左右），请重新完整复制一次"
    return None


# ---- 百度 OAuth 客户端凭据（与 bypy 1.8.9 内置的 const.ApiKey / const.SecretKey 一致，
#      也与 open_auth_page.py 里生成授权码时用的 client_id 一致）----
BAIDU_CLIENT_ID = 'q8WE4EpCsau1oS0MplgMKNBn'
BAIDU_CLIENT_SECRET = 'PA4MhwB5RE7DacKtoP2i8ikCnNzAqYTD'
BAIDU_TOKEN_URL = 'https://openapi.baidu.com/oauth/2.0/token'
# ---- 百度网盘开放平台正式接口（官方文档：https://pan.baidu.com/union/doc/）----
#   GET …/rest/2.0/xpan/nas?method=uinfo&access_token=…
#   响应 {"errno":0,"baidu_name":…,"uk":…}；errno 0=成功 3=未登录/token 过期 4=无权限
BAIDU_UINFO_URL = 'https://pan.baidu.com/rest/2.0/xpan/nas'
BAIDU_QUOTA_URL = 'https://pan.baidu.com/rest/2.0/xpan/quota'


def _exchange_code_direct(auth_code, configdir):
    """★ 直接用百度 OAuth 接口把授权码换成 token，不再让 bypy 去换。

    为什么必须换掉 bypy 这一步（2026-09-23 实测）:
      · bypy 拿到一个换不成功的授权码会自己重试 5 次（等 10/20/30/40 秒），
        **整整 103 秒**才返回 140，而且只吐一行
        `Maximum number (5) of tries failed.` —— 真实原因（百度返回的
        JSON error_description）被它吞掉了，用户看到的就是「卡住 + 授权失败」。
      · 直接打接口能拿到百度的原始报文，例如：
          {"error":"invalid_grant","error_description":"invalid code , expired or revoked"}
        这句话才能告诉用户到底哪里错了，而且是**即时**返回（0.6 秒）。
      · 这一步失败时**不写任何文件**，所以不会破坏用户原有的 token。

    成功时按 bypy 的格式写 <configdir>/bypy.json（keys 与
    bypy.ByPy._store_json_only 一致），保证 bypy 后续仍认得这份文件；
    有效性由 verify_token() 直连官方 xpan 接口确认（不再用 bypy 验证）。
    """
    pars = {
        'grant_type': 'authorization_code',
        'code': auth_code,
        'client_id': BAIDU_CLIENT_ID,
        'client_secret': BAIDU_CLIENT_SECRET,
        'redirect_uri': 'oob',
    }
    try:
        import requests
    except Exception as e:
        print(f"❌ 需要 requests 库（bypy 的依赖，通常会随 bypy 一起装上）: {e}")
        return False

    print(f"📡 正在用百度 OAuth 接口换取 token（{BAIDU_TOKEN_URL}）…", flush=True)
    try:
        with _pulse('换 token'):
            r = requests.post(BAIDU_TOKEN_URL, data=pars, timeout=OAUTH_TIMEOUT)
    except Exception as e:
        # ★ 把「超时」和「连不上」分开报：两者用户要做的事完全不同 ——
        #   超时是百度慢（这台机器实测要 15 秒以上），重试一次通常就好；
        #   连不上是网络/代理问题，重试也没用。
        if 'timeout' in type(e).__name__.lower() or 'timed out' in str(e).lower():
            print(f"❌ 等百度响应超时（超过 {OAUTH_TIMEOUT} 秒）。")
            print("   这台机器打百度接口实测要 15 秒以上，属于「慢但能通」，"
                  "请直接重试一次。")
        else:
            print(f"❌ 连不上百度 OAuth 接口：{e}")
            print("   请检查网络/代理（这台机器上 curl 直连该接口会 TLS 失败，"
                  "但 Python requests 正常）。")
        return False

    text = (r.text or '').strip()
    if r.status_code != 200:
        print(f"❌ 百度拒绝了这次授权码（HTTP {r.status_code}）。")
        print(f"   百度原话：{text[:400]}")
        low = text.lower()
        if 'invalid_grant' in low or 'invalid code' in low:
            print("   → 授权码无效/已过期/已被用过。请点「打开浏览器授权」重新拿一个新的，")
            print("     拿到后**立刻**粘贴提交（授权码 10 分钟内有效，且只能用一次）。")
        elif 'redirect_uri' in low:
            print("   → redirect_uri 不匹配：拿码时用的回调地址和这里不一致。")
        elif 'client' in low:
            print("   → client_id / client_secret 不匹配：需要重新打包内置的凭据。")
        return False

    try:
        j = json.loads(text)
    except Exception:
        print(f"❌ 百度返回的不是合法 JSON：{text[:200]}")
        return False

    if 'error' in j:
        print(f"❌ 百度返回错误：{j.get('error')} — {j.get('error_description', '')}")
        return False
    if not j.get('access_token'):
        print(f"❌ 百度返回里没有 access_token：{text[:200]}")
        return False

    os.makedirs(configdir, exist_ok=True)
    token_path = os.path.join(configdir, 'bypy.json')
    with open(token_path, 'w', encoding='utf-8') as f:
        json.dump(j, f, ensure_ascii=False, indent=2)
    print(f"✅ 已拿到 access_token（{len(j['access_token'])} 字符），"
          f"expires_in={j.get('expires_in')}，已写入 {token_path}")
    return True


def authorize_with_bypy(auth_code, configdir=None):
    """用「直接打百度 OAuth 接口」的方式完成一次真正会写 token 的授权。

    ★ 这里**不能**用返回码判成功：bypy 只校验授权码的格式
      （ask() 要求长度 ≥16），**不校验内容有效性** —— 所以最后
      是否真的授权成功一律由 verify_token() 决定。
    """
    bad = check_code_format(auth_code)
    if bad:
        print(f"❌ 这次的授权码没有通过基本检查：{bad}")
        print("   获取方式：点「打开浏览器授权」→ 登录百度网盘并同意授权 → 页面/地址栏里")
        print("   会给出 code=xxxxxxxx（约 32 位字母数字），只复制等号后面那一串。")
        return False

    return _exchange_code_direct(auth_code, configdir)


def verify_token(configdir):
    """真打一次百度 API，确认这份 token 真的能用。

    ★ 为什么必须做：bypy 只校验授权码的「格式」（长度 ≥16），
      **不校验内容有效性**。少了这一步，随便贴 32 个字符都会被
      报成「授权成功」，然后把一份废 token 写到用户目录里 —— 这正是
      旧实现「界面说授权成功，其实什么都没换」的根因之一。

    ★★ 2026-09-23 重写（用户实测事故）：原来这里跑的是 `bypy info`
       （= bypy 的 quota()，打 pcs.baidu.com 的老接口）。它在这个环节
       **永远不返回**：bypy.py:866-895 是「默认 5 次重试 + 每次累加退避
       (5s/10s/15s/20s) + 每次各自 timeout」的循环，总时长必然超过这里
       的 60 秒上限，于是每一次授权都以
           ❌ 授权验证超时（60 秒）：网络异常，无法确认授权是否生效。
       收场 —— 而实际上 OAuth 那一步早就成功了、token 已经拿到。
       实测：默认配置 / 隔离配置 / CLI 三种调法在 25 秒内全部无输出、不退出，
       连 ~/.bypy 里的真 token 也一样。所以 bypy 不能当验证器。

    ★ 改成按百度网盘开放平台官方文档直接打 xpan 接口：
        GET https://pan.baidu.com/rest/2.0/xpan/nas?method=uinfo&access_token=…
        响应 {"errno":0,"baidu_name":…,"uk":…}
      errno 约定：0=成功，3=未登录/access_token 过期，4=无权限。
      好处：一次 HTTP 往返（亚秒级）、有确定答案、失败时能报出百度的原话，
      不再有「等 60 秒然后说不清为什么」。
    """
    token_path = os.path.join(configdir, 'bypy.json')
    try:
        with open(token_path, 'r', encoding='utf-8') as f:
            tok = json.load(f)
    except Exception as e:
        print(f"❌ 读不到新拿到的授权文件（{token_path}）：{e}")
        return False

    access_token = (tok.get('access_token') or '').strip()
    if not access_token:
        print("❌ 授权文件里没有 access_token。")
        return False

    try:
        import requests
    except Exception as e:
        print(f"❌ 需要 requests 库: {e}")
        return False

    print("🔎 正在向百度网盘确认这份授权能不能用…", flush=True)
    try:
        with _pulse('验证授权'):
            r = requests.get(
                BAIDU_UINFO_URL,
                params={'method': 'uinfo', 'access_token': access_token},
                timeout=VERIFY_TIMEOUT,
            )
    except Exception as e:
        print(f"❌ 验证时连不上百度接口：{e}")
        print("   请检查网络/代理后重试。")
        return False

    text = (r.text or '').strip()
    if r.status_code != 200:
        print(f"❌ 验证请求被百度拒绝（HTTP {r.status_code}）：{text[:300]}")
        return False

    try:
        j = json.loads(text)
    except Exception:
        print(f"❌ 验证时百度返回的不是合法 JSON：{text[:300]}")
        return False

    errno = j.get('errno')
    if errno == 0:
        who = j.get('baidu_name') or j.get('netdisk_name') or ('uk=' + str(j.get('uk', '')))
        # ★ 缓存账号名，让以后的 --check-fast（离线）也能显示是谁
        try:
            _remember_account_name(configdir, j.get('baidu_name') or j.get('netdisk_name') or '')
        except Exception:
            pass
        print(f"✅ 授权验证通过（账号：{who}）。")
        return True

    # 常见错误码给出人话解释（官方文档 errno 表）
    hints = {
        3: 'access_token 无效或已过期（授权码只能用来换一次 token，别重复提交同一个码）。',
        4: '这个应用没有访问该接口的权限。',
        -6: '身份验证失败。',
        31034: '触发百度风控（需要验证）—— 换网络或稍后再试。',
    }
    print(f"❌ 验证失败：errno={errno} errmsg={j.get('errmsg', '')!r}")
    if errno in hints:
        print(f"   → {hints[errno]}")
    print(f"   百度原话：{text[:300]}")
    return False


def refresh_access_token():
    """★ 静默续期：用 refresh_token 换一张新的 access_token。

    为什么需要它（2026-09-23 实测 + 读 bypy 源码确认）：
      · 百度 OAuth 的 refresh_token 是**长期凭据**，可以直接换新的
        access_token（实测 HTTP 200，expires_in=2592000 秒 = 30 天，
        而且会返回一个可轮换的新 refresh_token）。
      · 也就是说：**access_token 过期这件事，本来完全不需要用户参与** ——
        不该出现「授权过期 → 让用户去浏览器登录 → 复制授权码」这种流程。
      · bypy 内部虽然有 _refresh_token()（bypy.py:737 收到过期错误时调用），
        但那是被动触发的，而且它只在 API 报错时重试一次。

    所以这里提供一个主动续期入口：Electron 侧检查授权时如果发现 bypy info
    失败，就先调这个（`get_auth_code.py --refresh`），成功就直接恢复可用，
    连授权窗口都不用打开。

    返回 True/False；成功时已把新 token 写回 ~/.bypy/bypy.json。
    """
    home_config = os.path.join(os.path.expanduser('~'), '.bypy')
    live_token = os.path.join(home_config, 'bypy.json')
    if not os.path.isfile(live_token):
        print("❌ 本地没有授权文件，无法续期（需要重新授权）。")
        return False

    try:
        with open(live_token, 'r', encoding='utf-8') as f:
            tok = json.load(f)
    except Exception as e:
        print(f"❌ 授权文件读不出来（{e}），需要重新授权。")
        return False

    refresh = tok.get('refresh_token')
    if not refresh:
        print("❌ 本地授权文件里没有 refresh_token，需要重新授权。")
        return False

    try:
        import requests
    except Exception as e:
        print(f"❌ 需要 requests 库: {e}")
        return False

    print("🔄 正在用 refresh_token 静默续期（不需要重新登录）…", flush=True)
    pars = {
        'grant_type': 'refresh_token',
        'refresh_token': refresh,
        'client_id': BAIDU_CLIENT_ID,
        'client_secret': BAIDU_CLIENT_SECRET,
    }
    try:
        with _pulse('续期'):
            r = requests.post(BAIDU_TOKEN_URL, data=pars, timeout=OAUTH_TIMEOUT)
    except Exception as e:
        print(f"❌ 连不上百度 OAuth 接口：{e}")
        return False

    text = (r.text or '').strip()
    if r.status_code != 200:
        print(f"❌ 续期被百度拒绝（HTTP {r.status_code}）。")
        print(f"   百度原话：{text[:400]}")
        low = text.lower()
        if 'invalid_grant' in low or 'refresh' in low:
            print("   → refresh_token 也失效了，只能重新走浏览器授权。")
        return False

    try:
        j = json.loads(text)
    except Exception:
        print(f"❌ 百度返回的不是合法 JSON：{text[:200]}")
        return False
    if 'error' in j or not j.get('access_token'):
        print(f"❌ 百度返回错误：{j.get('error')} — {j.get('error_description', '')}")
        return False

    # 按 bypy 的格式回写（保留它认识的 key，新增 expires_at 供下次判断是否该续期）
    new_tok = {
        'access_token': j['access_token'],
        'refresh_token': j.get('refresh_token') or refresh,
        'expires_in': j.get('expires_in', tok.get('expires_in', 2592000)),
        'scope': j.get('scope', tok.get('scope', 'basic netdisk')),
        'session_key': tok.get('session_key', ''),
        'session_secret': tok.get('session_secret', ''),
        'expires_at': int(time.time()) + int(j.get('expires_in') or 2592000),
    }

    # ★ 2026-09-23：续期拿到的新 token 必须先**验证过**才准覆盖 live 文件。
    #   以前这里换到 token 就直接写回，万一百度给的是个不能用的 token，
    #   用户原本还能用的授权就被一个废 token 顶掉了。
    tmpdir = tempfile.mkdtemp(prefix='easypub_refresh_')
    try:
        with open(os.path.join(tmpdir, 'bypy.json'), 'w', encoding='utf-8') as f:
            json.dump(new_tok, f, ensure_ascii=False, indent=2)
        if not verify_token(tmpdir):
            print("❌ 续期拿到的 token 验证没通过，**不动**本地原有授权。")
            return False
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    try:
        shutil.copyfile(live_token, live_token + '.bak')
    except Exception:
        pass
    try:
        with open(live_token, 'w', encoding='utf-8') as f:
            json.dump(new_tok, f)
        print("✅ 续期成功！新的 access_token 已保存（有效期 30 天左右）。")
        return True
    except Exception as e:
        print(f"❌ 写回授权文件失败：{e}")
        return False


def _purge_stale_auth_data():
    """★ 每次提交授权码之前，先把上一次授权留下的残渣清干净（用户要求）。

    清的是两样东西，都不是用户的真授权：
      1. **僵尸锁** ~/.bypy/easypub_auth.lock —— 上一次授权进程被强杀/关窗时
         留下的（实测 13:33:42 那把锁挡掉了之后每一次提交，用户连点 8 次都
         报「已经有一个授权流程正在运行」，而其实一个都没在跑）。
         _acquire_single_instance_lock 里已经有 pid 存活校验会自愈；这里再兜一层。
      2. **上一次的半成品临时目录** %TEMP%\\easypub_bypy_* / easypub_refresh_* ——
         每次重新授权都会新建一个（今天已经堆了 3 个）。里面的 bypy.json 可能是
         上一次没验证通过的新 token，留着只会让人分不清"现在用的到底是哪一份"。

    ★ 只删这两个明确前缀的目录，绝不碰别的；也不碰 ~/.bypy/bypy.json（那是用户
      正在用的真授权，必须留到新 token 验证通过才覆盖）。
    ★ 必须在**拿到锁之后**调用：否则可能删掉另一个正在跑的授权进程的工作目录。
    ★ 本函数自己新建的 tmpdir 在调用之后才创建，所以不会被自己删掉。
    """
    removed = 0

    # 1) 清僵尸锁（拿锁前调用方已自愈过一次，这里是兜底；活的锁也会被清掉，
    #    因为调用时本进程已经持锁，任何残留的锁文件都是别人的残渣）
    for lock in (_lock_path(),):
        try:
            if os.path.isfile(lock):
                os.remove(lock)
                removed += 1
                print("🧹 已清理上一次授权残留的锁文件。")
        except Exception as e:
            print(f"⚠️ 清理锁文件失败（忽略）: {e}")

    # 2) 清半成品临时目录
    tmp_root = tempfile.gettempdir()
    for prefix in ('easypub_bypy_', 'easypub_refresh_'):
        try:
            names = [n for n in os.listdir(tmp_root) if n.startswith(prefix)]
        except Exception:
            names = []
        for name in names:
            full = os.path.join(tmp_root, name)
            if not os.path.isdir(full):
                continue
            try:
                shutil.rmtree(full, ignore_errors=True)
                if not os.path.isdir(full):
                    removed += 1
            except Exception:
                pass
    if removed:
        print(f"🧹 已清理上一次授权留下的临时数据（{removed} 项）。")


def _acquire_single_instance_lock(_retry=True):
    """
    ★ 单实例锁（2026-09-23 事故后加的护栏）。

    事故经过：授权窗口的「提交」被连点/自动提交与手动提交撞车，两个
    get_auth_code.py 同时跑同一个重新授权流程 → 都去搬 ~/.bypy/bypy.json →
    第一个把文件 move 到 .bak，第二个把 .bak 删掉，然后两个都失败 →
    **用户的百度授权文件直接消失**（表现为「昨天还好好的，今天全要重新授权」）。

    所以：同一时间只允许一个进程做重新授权，第二个直接带着明确原因退出。

    ★★ 2026-09-23 第二次事故（僵尸锁）：上面这版锁只靠「文件存不存在」判断，
       一旦持有者进程死了而文件没删掉（进程被杀、关窗口、断电），锁就**永久**
       卡死后续所有授权。实测：13:33:42 留下的锁挡掉了 14:14 之后的每一次提交，
       用户连点 8 次全是「已经有一个授权流程正在运行」，而一个都没在跑。

       → 现在锁里记 pid，拿到锁之前先校验持有者**是否还活着**：
         死了就把僵尸锁清掉、自己接手（只重试一次，不会死循环）。

    返回 (lock_path, holder)：
        lock_path 非 None  → 拿到锁（holder 无意义）
        lock_path 为 None  → 没拿到
            holder 非 None → 被活着的 pid=holder 挡下
            holder 为 None → 拿不到锁但不是「有活进程在跑」（创建失败/清理失败）

    ★ 调用方不再用 os.path.isfile() 二次判断「锁到底在不在」：实测在受限环境下
      os.path.exists/isfile 会对真实存在的文件返回 False（假阴性），那样脚本会带着
      成功口吻退出（returncode 0），而界面按「stdout 里有没有『授权成功』」判定 →
      可能把「没授权成功」显示成绿字。锁的状态由本函数自己持有，必须由它自己回答。
    """
    lock_path = _lock_path()
    try:
        os.makedirs(os.path.dirname(lock_path), exist_ok=True)
    except Exception:
        pass

    def _try_create():
        fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        try:
            os.write(fd, str(os.getpid()).encode('ascii'))
        finally:
            os.close(fd)

    try:
        _try_create()
        return lock_path, None
    except FileExistsError:
        read_pid = _read_lock_pid(lock_path)
        if read_pid is not None and _pid_alive(read_pid):
            print("❌ 已经有一个授权流程正在运行（上一次还没结束）。")
            print(f"   持有者进程 pid = {read_pid}，它确实还活着。")
            print("   请不要连续点「提交」—— 等上一次出结果，或者关掉授权窗口重新打开。")
            print(f"   如果确认没有别的授权在跑，删掉这个文件再试：{lock_path}")
            return None, read_pid
        # 持有者已经死了（或锁文件是空的/坏的）→ 僵尸锁，清掉后自己接手
        why = '进程已不存在' if read_pid is not None else '内容无法解析'
        print(f"🧹 发现僵尸授权锁（pid={read_pid}，{why}）—— 已自动清理，本次提交继续。")
        if not _retry:
            print(f"❌ 僵尸锁清理后仍被占用，请手动删掉这个文件再试：{lock_path}")
            return None, None
        try:
            os.remove(lock_path)
        except FileNotFoundError:
            pass                      # 别人先清掉了，直接重试即可
        except Exception as e:
            print(f"❌ 清理僵尸锁失败：{e}")
            print(f"   请手动删掉这个文件再试：{lock_path}")
            return None, None
        return _acquire_single_instance_lock(_retry=False)
    except Exception as e:
        print(f"⚠️ 无法创建单实例锁（忽略，继续）: {e}")
        return None, None


def _pid_alive(pid):
    """那个 pid 现在还活着吗？

    ★ 2026-09-23 事故（第二次）：锁文件里只有 pid，没有存活校验 ——
      上一次会话在 13:33:42 建的锁（pid 18296）随进程一起死了，但**文件留在了
      ~/.bypy/easypub_auth.lock**。此后每一次「提交授权码」都在 0.1 秒内被
      这个僵尸锁挡掉，用户看到的是连点 8 次、每条都「已经有一个授权流程正在运行」，
      而其实一个授权流程都没有在跑。锁必须能自愈。
    """
    if not pid:
        return False
    try:
        os.kill(pid, 0)          # Windows 上等价于 OpenProcess 探测
        return True
    except OSError:
        return False
    except Exception:
        return False


def _lock_path():
    return os.path.join(os.path.expanduser('~'), '.bypy', 'easypub_auth.lock')


def _read_lock_pid(lock_path):
    """读出锁里记的 pid；读不出（空文件/内容坏了）返回 None。"""
    try:
        with open(lock_path, 'r', encoding='ascii', errors='ignore') as f:
            raw = f.read().strip()
        return int(raw) if raw else None
    except Exception:
        return None


def _release_single_instance_lock(lock_path):
    if not lock_path:
        return
    try:
        os.remove(lock_path)
    except Exception:
        pass


def check_token(configdir=None):
    """★ 只做检查、不改任何东西：读本地授权文件，打一次官方接口看它还有效吗。

    给 Electron 侧「检查授权状态」用（get_auth_code.py --check）。

    ★★ 2026-09-23 新增：以前这一步是 main.js 里直接跑
       `python -c "import bypy; bypy.ByPy().info()"`，然后用输出里有没有
       "Quota" 判断。两个致命问题：
         1. bypy info 会自己重试 5 次（累加退避 5/10/15/20 秒），必然超过
            main.js 给的 12 秒上限 → 每次检查都报「检查授权状态超时」；
         2. 就算跑完，未授权时 bypy 会**等 stdin 交互**而不是报错，
            execFile 下就是一直挂着。
       现在改成一次官方接口往返（亚秒级），退出码即结论：
          0=授权有效  3=未授权/过期（需要重新授权）  其它=检查出错
    """
    home_config = os.path.join(os.path.expanduser('~'), '.bypy')
    cfg = configdir or home_config
    token_path = os.path.join(cfg, 'bypy.json')

    if not os.path.isfile(token_path):
        print("❌ 没有本地授权文件，需要重新授权。")
        return 3

    try:
        with open(token_path, 'r', encoding='utf-8') as f:
            tok = json.load(f)
    except Exception as e:
        print(f"❌ 授权文件读不出来（{e}），需要重新授权。")
        return 3

    access_token = (tok.get('access_token') or '').strip()
    if not access_token:
        print("❌ 授权文件里没有 access_token，需要重新授权。")
        return 3

    try:
        import requests
    except Exception as e:
        print(f"❌ 需要 requests 库: {e}")
        return 1

    try:
        with _pulse('检查授权'):
            r = requests.get(
                BAIDU_UINFO_URL,
                params={'method': 'uinfo', 'access_token': access_token},
                timeout=OAUTH_TIMEOUT,
            )
    except Exception as e:
        print(f"❌ 连不上百度接口，无法确认授权状态：{e}")
        return 1

    try:
        j = json.loads(r.text or '{}')
    except Exception:
        print(f"❌ 百度返回的不是合法 JSON：{(r.text or '')[:200]}")
        return 1

    errno = j.get('errno')
    if errno == 0:
        who = j.get('baidu_name') or j.get('netdisk_name') or ('uk=' + str(j.get('uk', '')))
        # ★ 顺手把账号名缓存进 bypy.json，让 --check-fast 以后不用联网也能显示账号
        try:
            _remember_account_name(cfg, j.get('baidu_name') or j.get('netdisk_name') or '')
        except Exception:
            pass
        print(f"✅ 授权有效（账号：{who}）。")
        return 0

    if errno == 3:
        print("❌ 授权已失效（access_token 过期且无法自动续期），需要重新授权。")
        return 3

    print(f"❌ 授权检查失败：errno={errno} errmsg={j.get('errmsg', '')!r}")
    return 1


def reauthorize(auth_code):
    """
    ★ 重新授权主流程：

        1. 在空的临时 configdir 里跑一次授权（旧 token 完全不参与）
        2. 用这份新 token 真打一次官方 xpan 接口验证（见 verify_token）
        3. 成功 → 覆盖 ~/.bypy/bypy.json；失败 → 原 token 原样保留
        4. 期间原 token 先**复制**一份到 .bak，验证通过前**绝不 move 走**
           （★ 2026-09-23 修复：以前是 shutil.move —— 只要进程在验证阶段被
             打断/被杀，live token 就处于「已被搬走、还没搬回」的消失状态。
             改成 copy 之后，live 文件在成功之前一直在原地，永远丢不了。）
        5. 任何出口（包括异常）都走 finally 自愈：live 文件不存在就从 .bak 补回来

    main.js 侧按 stdout 里的「✅ 授权成功！」判定结果。
    """
    home_config = os.path.join(os.path.expanduser('~'), '.bypy')
    live_token = os.path.join(home_config, 'bypy.json')
    tmpdir = tempfile.mkdtemp(prefix='easypub_bypy_')
    backup = None
    moved = False

    try:
        # 1) 申请一个保证为空的 configdir，让 bypy 必然走交互授权
        new_config_dir = os.path.join(tmpdir, 'config')
        os.makedirs(new_config_dir, exist_ok=True)

        # 2) 备份原 token —— 只**复制**，不搬走（成功前 live 文件一直在）。
        #    ★ 必须无条件设置 backup 路径（哪怕此刻 live 不存在）：finally 的
        #      自愈兜底就是靠这个路径把「只剩 .bak」的现场救回来的。
        backup = live_token + '.bak'
        if os.path.isfile(live_token):
            try:
                shutil.copyfile(live_token, backup)
                moved = True
            except Exception as e:
                print(f"⚠️ 无法备份原有授权文件（继续尝试授权）: {e}")
                backup = None
                moved = False
        elif os.path.isfile(backup):
            # live 不在、.bak 在（上一次非正常退出留下的现场）→ 现在就补回来，
            # 别等 finally：授权跑起来之前先让用户处于「有授权」的状态。
            try:
                shutil.copyfile(backup, live_token)
                moved = True
                print("🩹 检测到原授权文件缺失，已从备份恢复。")
            except Exception as e:
                print(f"⚠️ 从备份恢复原授权失败: {e}")
                backup = None
                moved = False

        # 3) 在隔离目录里完成授权
        ok = authorize_with_bypy(auth_code, configdir=new_config_dir)

        new_token = os.path.join(new_config_dir, 'bypy.json')
        made_token = ok and os.path.isfile(new_token)

        if made_token:
            # 4) 关键一步：真打一次 API 验证这份 token 是不是真的能用
            if not verify_token(new_config_dir):
                print("❌ 授权验证失败：token 已经换到手，但拿它访问百度网盘被拒绝。")
                print("   上面的 errno 就是百度的原话。最常见的是 errno=3（token 无效/过期）。")
                made_token = False

        if made_token:
            # 5) 成功：新 token 落到真正的 ~/.bypy/（覆盖写，写坏了还有 .bak 兜底）
            os.makedirs(home_config, exist_ok=True)
            shutil.copyfile(new_token, live_token)
            print("✅ 授权成功！新的授权已保存。")
            if moved and backup and os.path.isfile(backup):
                try:
                    os.remove(backup)
                except Exception:
                    pass
            return True

        # 6) 失败：什么都不用搬（live 文件本来就在原地），只报告状态
        if not os.path.isfile(live_token) and backup and os.path.isfile(backup):
            # 有备份先救回来，再报告
            try:
                shutil.copyfile(backup, live_token)
                print("⚠️ 授权未完成，已恢复原来的授权。")
                return False
            except Exception as e:
                print(f"⚠️ 恢复原授权失败（原文件仍在 {backup}）: {e}")
        if os.path.isfile(live_token):
            print("⚠️ 授权未完成，本地仍保留原授权。")
        else:
            print("⚠️ 授权未完成，且本地没有可用的授权（原授权文件不存在）。")
        return False

    finally:
        # ★ 自愈兜底：无论因为什么退出（异常 / 被 taskkill / 断电前一刻），
        #   只要 live token 不见了而 .bak 还在，就补回来 —— 用户的授权
        #   绝不能因为一次失败的重新授权而消失。
        try:
            if not os.path.isfile(live_token) and backup and os.path.isfile(backup):
                shutil.copyfile(backup, live_token)
                print("🩹 已自动恢复原来的授权文件（.bak → bypy.json）。")
        except Exception as e:
            print(f"⚠️ 自动恢复授权文件失败: {e}")
        shutil.rmtree(tmpdir, ignore_errors=True)


def _remember_account_name(configdir, name):
    """★ 把账号名记到 bypy.json 里，供 --check-fast 离线显示。

    为什么：--check-fast 只读本地文件、不打网络，本来就拿不到账号名
    （账号名是百度 uinfo 接口返回的）。但用户已经联网验证过一次的话，
    把那次拿到名字缓存下来，之后切通道/重开应用就能直接显示，
    不用再为「显示个名字」付 20 秒。
    只写 bypy.json 自身的新 key，不动 access_token/refresh_token。
    """
    name = (name or '').strip()
    if not name:
        return False
    token_path = os.path.join(configdir or os.path.join(os.path.expanduser('~'), '.bypy'), 'bypy.json')
    try:
        with open(token_path, 'r', encoding='utf-8') as f:
            tok = json.load(f)
        if not isinstance(tok, dict):
            return False
        if (tok.get('baidu_name') or '').strip() == name:
            return False
        tok['baidu_name'] = name
        tmp = token_path + '.tmp-account'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(tok, f, ensure_ascii=False, indent=2)
        os.replace(tmp, token_path)
        return True
    except Exception:
        return False


def check_token_fast(configdir=None):
    """★ 只读本地文件、**不发任何网络请求**的快速授权检查。

    给「切换到苹果图书通道 / 启动时」用（get_auth_code.py --check-fast）。

    为什么需要它（用户要求「检查授权太慢了，能不能直接看有没有 token」）：
      `--check` 要往 pan.baidu.com 打一次 uinfo，这台机器实测 21.88 秒
      （百度接口慢 + _pulse 心跳），每次切通道都卡那么久是不能接受的。
      而「本地有没有 token」这个信号本身又快又够用：
        · 有 token    → 就认为「已授权」（实测 0.08 秒）
        · 没有 token  → 「未授权」
      真过期了怎么办：**不是靠轮询发现的，是靠上传失败发现的** ——
      上传报「未授权 / errno=3 / -6」时 Electron 侧会跑 `--wipe` 清掉本地
      token 并把状态改成未授权（闭环，见 wipe_local_auth）。

    退出码约定与 check_token 一致（0=已授权 3=需要重新授权），另外：
        · 本地有 token 但按 expires_at 判断已过期 → 打印提示，但仍然返回 0
          （因为 refresh_token 通常还能静默续期，此时上传照样能成功；
           真不行了上传会失败并触发 --wipe）
    """
    home_config = os.path.join(os.path.expanduser('~'), '.bypy')
    cfg = configdir or home_config
    token_path = os.path.join(cfg, 'bypy.json')

    if not os.path.isfile(token_path):
        print("❌ 本地没有授权文件，需要重新授权。")
        return 3

    try:
        with open(token_path, 'r', encoding='utf-8') as f:
            tok = json.load(f)
    except Exception as e:
        print(f"❌ 授权文件读不出来（{e}），需要重新授权。")
        return 3

    access_token = (tok.get('access_token') or '').strip()
    if not access_token:
        print("❌ 授权文件里没有 access_token，需要重新授权。")
        return 3

    # 有 token 就算已授权；顺带报告一下本地看到的有效期（不作为判定依据）
    who = ''
    try:
        who = (tok.get('baidu_name') or tok.get('netdisk_name') or '').strip()
    except Exception:
        who = ''

    expires_at = tok.get('expires_at')
    expired_locally = False
    try:
        if expires_at:
            expired_locally = int(expires_at) <= int(time.time())
    except Exception:
        expired_locally = False

    if expired_locally:
        print(f"✅ 已授权（本地 token 已到期，上传时会自动续期）{'（账号：' + who + '）' if who else ''}。")
    else:
        print(f"✅ 已授权{'（账号：' + who + '）' if who else ''}。")
    return 0


# ★ 明确列出「本地授权数据」到底指哪些文件 —— 只删这些，绝不多删。
_LOCAL_AUTH_FILES = ('bypy.json', 'bypy.json.bak', 'bypy.json.bak-before-install')


def wipe_local_auth(configdir=None):
    """★ 清掉本地保存的百度授权数据，让状态回到「需要重新授权」。

    谁在什么情况下调用它（用户要求）：
      「如果上传失败提示未授权，就删掉 bypy 的数据、把状态改为未授权」。
      由 main.js 在上传日志/结果里出现「未授权 / errno=3 / 身份验证失败」时调
      （get_auth_code.py --wipe），调用方随后把通道状态刷成「未授权」。

    ★ 安全边界（这座桥只能这样走）：
      · 只删 ~/.bypy 下的 bypy.json / bypy.json.bak / bypy.json.bak-before-install；
      · 不碰 ~/.bypy 里的其它文件（bypy.json 配置、hashcache、setting 等）；
      · 不碰正在跑的授权流程的临时目录；
      · 「删掉」只在本函数里发生 —— _purge_stale_auth_data() 出于安全**永不**删
        bypy.json，两条路径不要混。
      退出码：0=清掉了（或本来就没有）；1=有文件但删不掉。
    """
    home_config = os.path.join(os.path.expanduser('~'), '.bypy')
    cfg = configdir or home_config

    ok = True
    removed = []
    for name in _LOCAL_AUTH_FILES:
        fp = os.path.join(cfg, name)
        try:
            if os.path.isfile(fp):
                os.remove(fp)
                removed.append(name)
        except Exception as e:
            ok = False
            print(f"⚠️ 删不掉 {name}：{e}")

    if removed:
        print("🧹 已清除本地百度授权数据：" + "、".join(removed) + "。")
    else:
        print("ℹ️ 本地没有授权数据可清（本来就没授权）。")
    if ok and removed:
        print("👉 请点「重新授权」，重新复制一次授权码即可。")
    return 0 if ok else 1


if __name__ == "__main__":
    # ★ --check-fast：只看本地有没有 token，不发网络请求（亚毫秒）。
    #   给启动/切通道用 —— --check 要打百度接口，实测 15～22 秒，太慢。
    if len(sys.argv) > 1 and sys.argv[1] in ('--check-fast', '--fast'):
        sys.exit(check_token_fast())

    # ★ --wipe：清掉本地授权数据（上传失败提示未授权时由 main.js 调用）。
    if len(sys.argv) > 1 and sys.argv[1] in ('--wipe',):
        sys.exit(wipe_local_auth())

    # ★ --check：只检查当前授权还有效吗（打完就退出，不改任何文件）。
    #   给 Electron 侧「检查授权状态」用 —— 退出码 0=有效 3=需要重新授权
    #   1=检查出错。以前这步在 main.js 里跑 bypy info，必然超时（见 check_token）。
    if len(sys.argv) > 1 and sys.argv[1] in ('--check', '-c'):
        sys.exit(check_token())

    # ★ --refresh：静默续期，不需要授权码、不打开浏览器。
    #   （授权过期时的第一选择；只有 refresh_token 也失效才需要重新授权。）
    if len(sys.argv) > 1 and sys.argv[1] in ('--refresh', '-r'):
        sys.exit(0 if refresh_access_token() else 1)

    # 支持命令行参数：python get_auth_code.py "授权码"
    if len(sys.argv) > 1:
        auth_code = sys.argv[1].strip()
    else:
        # ★ 修复：原来无条件 input() 等输入，而 Electron 侧 execFile 不写 stdin 也不关闭
        #   → 永久阻塞。非交互时直接报错退出。
        if not sys.stdin or not sys.stdin.isatty():
            print("❌ 未收到授权码，且当前非交互运行无法从终端读取。", file=sys.stderr)
            sys.exit(1)
        print("=" * 60)
        print("请粘贴你在浏览器中复制的【授权码】：")
        print("=" * 60)
        auth_code = input("📝 授权码: ").strip()

    if not auth_code:
        print("❌ 授权码不能为空！")
        sys.exit(1)

    # ★ 诊断：把提交的码打出来（打码显示），这样失败时能一眼看出
    #   「是码本身不对」还是「流程/时间的问题」。
    print(f"📥 收到授权码：{auth_code[:6]}…{auth_code[-4:]}（共 {len(auth_code)} 位）")
    _fmt = check_code_format(auth_code)
    if _fmt:
        print(f"❌ 授权码格式检查未通过：{_fmt}")
        sys.exit(1)

    # ★ 单实例锁：绝不允许两个授权流程同时动 ~/.bypy/bypy.json
    #   （僵尸锁会在 _acquire_single_instance_lock 里自动清理）
    lock_path, holder = _acquire_single_instance_lock()
    if lock_path is None and holder is not None:
        # 另一个**活着的**授权进程持有锁 → 明确失败。这里绝不能用 os.path.isfile()
        # 二次确认（受限环境下有假阴性，会把失败当成功放过去）。
        sys.exit(4)
    # ★ 拿到锁之后再清上一次的残渣（僵尸锁 / 半成品临时目录）。
    #   必须在锁之后：否则可能删掉另一个正在跑的授权进程的工作目录。
    #   也必须在 reauthorize() 之前：它自己那个 tmpdir 是后面才建的，不会被误删。
    #   ★ 注意：这里**不碰** ~/.bypy/bypy.json —— 那是用户当前正在用的真授权，
    #     只有新 token 验证通过才允许覆盖（见 reauthorize 第 5 步）。
    _purge_stale_auth_data()

    try:
        ok = reauthorize(auth_code)
    finally:
        _release_single_instance_lock(lock_path)
    sys.exit(0 if ok else 1)
