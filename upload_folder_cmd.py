#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bypy 上传（命令行调用版本）—— 逐文件强制覆盖上传

★★ 2026-09-24 用户要求（原话：「你把这个云端识别的功能删掉呗。重发就重发」）：
   以前这里调的是 `bypy syncup <本地目录> <远端目录>`（目录模式增量同步）。
   bypy 的**目录**模式（syncup / upload 目录）会先查远端同名文件，再判定「一样」就跳过：
       bypy.py:1780  subresult = self._get_file_info(rfile, dumpex=False)
       bypy.py:1783  if const.ENoError == self._verify_current_file(self._remote_json, False):
       bypy.py:1786      self.pv("Remote file '{}' already exists, skip uploading")
   而判定逻辑（bypy.py:1273-1324）**默认只比文件大小**（只有加 -e/--verify 才比 md5）：
       bypy.py:1306  if self._current_file_size == rsize:  ...  return const.ENoError
   于是「云端已有一份大小相同的书」= 一个字节都不传，界面显示「云端已是最新，无需上传」。
   用户不要这个行为：他点了发送，书就必须真的传上去。

   改法：放弃目录模式，改成**逐个文件** `bypy upload <本地文件> <远端文件路径>`。
   单文件 upload 走 ByPy.upload()（bypy.py:1901-1915）→ _upload_file()（bypy.py:1832），
   **不做同文件比较**，直接把 pars={'method':'upload','path':...,'ondup':'overwrite'}
   交给百度（bypy.py:1746-1755），也就是真正覆盖云端那一份。
   代价：每次发送都真占一次上行（旧秒传接口已下线、回退普通上传，见下面 _flush_err 注释）。

   注意：main.js 侧现在会**先清空 sendqueue\\ 再放入本次要传的书**，所以这里"把目录里
   所有文件都传上去" == "只传用户这次点发送的那几本"，不会把历史滞留的书一起重传。
"""

import collections
import json
import os
import re
import subprocess
import sys
import time

# 诊断开关：bypy 的 -v（阶段提示）/ -d（HTTP 级调试：method+url、HTTP 状态码、ec 错误码）。
# 这些输出只进后台日志 logs\upload-<日期>.log，界面不显示原始行（用户 2026-09-23 认可）。
BYPY_FLAGS = ('-v', '-d')

# ================= 用户配置 =================
# 默认上传目录（自动指向本脚本同目录下的 download 文件夹，不再写死 E:\...）
DEFAULT_LOCAL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "download")
REMOTE_DIR = "/epub_download_backup"          # 网盘目标目录（位于 /apps/bypy/ 下）
ON_DUP = "overwrite"                          # 重名策略: overwrite / skip / prompt
# ===========================================

def _bypy_exe():
    """
    找到 bypy 可执行文件（仅作兜底）。

    开发环境优先用【当前解释器同目录 Scripts\\bypy.exe】的绝对路径 —— 以前只写
    `bypy`，靠 PATH 解析（本机恰好解析到 ...\\Python314\\Scripts\\bypy.exe）。
    PATH 里若有第二个 Python/bypy，就可能跑进别的环境（连 .bypy 授权文件都不是同一份）。
    """
    exe_dir = os.path.join(os.path.dirname(os.path.abspath(sys.executable)), 'Scripts')
    names = ('bypy.exe', 'bypy') if os.name == 'nt' else ('bypy',)
    for n in names:
        p = os.path.join(exe_dir, n)
        if os.path.isfile(p):
            return p
    return 'bypy'          # 找不到绝对路径就回退：交给 PATH


# ★★ 2026-09-23 实测（本机上传慢的真正原因）：
#   pcs.baidu.com 解析出**两个 IPv6 地址**（2409:8c00:...），而这台机器没有可用的
#   IPv6 出口——每个 IPv6 连接都要卡满 21 秒 TCP 超时才回退 IPv4：
#       IPv6 2409:8c00:6c21:1040:...  FAIL 21.02s
#       IPv6 2409:8c00:6c21:103f:...  FAIL 21.03s   ← 合计 42.05 秒
#       IPv4 111.63.96.140            OK    0.10s
#       IPv4 36.110.192.115           OK    0.05s
#   requests/urllib3 走 socket.create_connection（按顺序挨个试，不并行），所以
#   **每条 bypy 命令都白等 ~42 秒**——这正是「3MB 和 9.7MB 都卡 43 秒」的谜底，
#   跟文件大小、跟 bypy 逻辑都没关系。curl 快是因为它做 Happy Eyeballs（v4/v6 并行）。
#   解法：让子解释器在 bypy 之前把 getaddrinfo 的结果过滤成只剩 IPv4。
_IPV4_PRELUDE = (
    "import socket as _s;"
    "_g=_s.getaddrinfo;"
    "_s.getaddrinfo=lambda *a, **k: ([x for x in _g(*a, **k) if x[0]==_s.AF_INET] or _g(*a, **k));"
)
_BYPY_RUN = _IPV4_PRELUDE + "import sys;from bypy.bypy import main;sys.exit(main())"


def _bypy_cmd(*args):
    """
    组装 bypy 命令行。

    开发环境：用当前解释器跑一段带 IPv4 补丁的 -c 代码，直接调 bypy 的 main()
    （既强制 IPv4，也不再依赖 PATH 里有哪个 bypy.exe）。
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
    return [_bypy_exe()] + [str(a) for a in args]


def _iter_local_files(local_path):
    """
    列出 local_path 下所有文件（含子目录）。

    返回 [(绝对路径, 相对路径 posix 形式)]，按相对路径排序（保证每次上传顺序稳定）。
    """
    items = []
    for root, _dirs, files in os.walk(local_path):
        for fn in files:
            full = os.path.join(root, fn)
            rel = os.path.relpath(full, local_path).replace(os.sep, '/')
            items.append((full, rel))
    items.sort(key=lambda it: it[1])
    return items


def _remote_file_path(remote_dir, rel):
    """把本地相对路径拼成远端文件完整路径（远端目录统一用 / 分隔）。"""
    base = str(remote_dir or REMOTE_DIR).strip().replace('\\', '/').rstrip('/')
    if not base:
        base = ''
    return base + '/' + rel


# ================================================================
# ★★ 2026-09-25：诊断与安全 —— 特化日志行（前缀 UPLOAD-）与凭据打码
# ================================================================
# 背景（用户 2026-09-25 报「打包后上传必然失败」，要求日志「针对列出的可能问题
# 方向做特化处理，集中定位问题」）：
#   这些 UPLOAD- 行的唯一用途是**一眼区分**下面几类失败，不用再猜：
#     · 源目录到底是什么（uploaded 数一直 0，很可能是把一个空目录/工作目录传了）
#     · 是不是冻结模式、本机有没有 bypy 令牌（`~\.bypy\bypy.json`）
#     · 冻结分支的 bypy 命令行长什么样、bypy 自己的 Verbose level 是几
#       （★ 这条就是本次真正的病根：Verbose level = 0 ⇒ 成功行被 pv() 静音）
#     · 每个文件的退出码/耗时/证据类型（OK 行 / 大小校验 / 31061 已存在）
#   注意：`📊 同步结果:` 那一行必须保持原样（main.js 用正则解析它），
#   所以新信息**只能**走新前缀行，绝不改动那一行。
#
# 打码理由：bypy 的 -d 会把 access_token / refresh_token 明文写进
# `logs\upload-<日期>.log`，而这个日志文件是放在交付目录里跟着移动硬盘走的。
_SECRET_RES = (
    re.compile(r'(access_token=)[^\s&"\']+'),
    re.compile(r"(['\"]?refresh_token['\"]?\s*[:=]\s*['\"]?)[^\s'\",}]+"),
    re.compile(r"(['\"]?session_secret['\"]?\s*[:=]\s*['\"]?)[^\s'\",}]+"),
)


def _redact(line):
    """把 bypy 输出里的令牌打码后再回显/落盘。"""
    for rx in _SECRET_RES:
        line = rx.sub(lambda m: m.group(1) + '***', line)
    return line


def _jump(obj):
    """紧凑 JSON（中文不转义），UPLOAD- 行统一用这个格式。"""
    try:
        return json.dumps(obj, ensure_ascii=False, default=str)
    except Exception as e:
        return json.dumps({'json_error': str(e)}, ensure_ascii=False)


def _env_report():
    """本次运行的环境快照 —— 专治「是不是冻结后端 / 本机有没有授权」。"""
    up = os.environ.get('USERPROFILE') or os.path.expanduser('~')
    token_file = os.path.join(up, '.bypy', 'bypy.json')
    token_exists = os.path.isfile(token_file)
    token_mtime = None
    token_size = None
    if token_exists:
        try:
            token_mtime = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(os.path.getmtime(token_file)))
            token_size = os.path.getsize(token_file)
        except Exception:
            pass
    try:
        drive = os.path.splitdrive(os.path.abspath(__file__))[0]
    except Exception:
        drive = ''
    return {
        'exe': sys.executable,
        'frozen': bool(getattr(sys, 'frozen', False)),
        'backend_env': os.environ.get('EASYPUB_BACKEND') or '',
        'python': sys.version.split()[0],
        'cwd': os.getcwd(),
        'drive': drive,
        'userprofile': up,
        'token_file': token_file,
        'token_exists': token_exists,
        'token_size': token_size,
        'token_mtime': token_mtime,
        'stdout_encoding': getattr(sys.stdout, 'encoding', None),
        'filesystem_encoding': sys.getfilesystemencoding(),
        'preferred_encoding': getattr(sys, 'getpreferredencoding', lambda: None)(),
    }


class _Stats(object):
    """汇总这一次上传到底"做了什么"，给界面一句准确的话用。"""

    def __init__(self):
        self.uploaded = 0       # 整文件（或分片）上传成功
        self.rapid = 0          # 秒传命中（0 字节）
        self.copied = 0         # 远端直接创建/复制
        self.skipped = 0        # 被判定「云端已有」而跳过（改成逐文件上传后应该恒为 0）
        self.err_lines = []     # 真错误（<E> 行）
        self.warn_lines = []    # 已知的无害错误（秒传接口下线 31023）
        self.no_auth = False    # 这台电脑没有本机授权（bypy 找不到令牌 → 试图交互提问）
        # ★★ 2026-09-25 新增（专治用户报的「明明传上去了却报失败」）
        #   verbose_level：bypy 自己的 Verbose level，应当 ≥1。为 0 说明 `-v` 半路
        #   被当成 python 开关吃掉了（backend_entry.py 的 _strip_interpreter_flags
        #   曾在 argv 任意位置剥开关），那样成功行会被 pv() 静音、计数器只能数到 0。
        self.verbose_level = None
        self.tail = collections.deque(maxlen=40)   # bypy 输出末 40 行（失败时原样打出来）
        self.fallback_ok = 0    # 没有 OK 行、但被「大小校验 / 31061 已存在」确认在云端的文件数
        self._file_hits = 0     # 当前文件已命中的成功证据条数
        self._file_evidence = []  # 当前文件的证据标签（ok-line / size-match / exists-31061）
        self._file_http = []    # 当前文件看到的 HTTP 状态码（http200 / http400 …）

    # ---- 逐文件证据归集（避免同一个文件被重复计数）----
    def begin_file(self):
        self._file_hits = 0
        self._file_evidence = []
        self._file_http = []

    def note(self, tag):
        if tag not in self._file_evidence:
            self._file_evidence.append(tag)

    def feed(self, line):
        # bypy -v 的完成行（见 bypy.py）：
        #     "'x' ==> 'y' OK."                整文件单连接上传成功  (bypy.py:1740)
        #     "'x' >>==> 'y' OK."              分片上传成功          (bypy.py:1526)
        #     "'x' =C=> 'y' OK."               远端直接创建/复制      (bypy.py:1491)
        #     "RapidUpload: 'x' =R=> 'y' OK."  秒传命中（0 字节）    (bypy.py:1847)
        #     "Remote file 'x' already exists, skip uploading"      (bypy.py:1786)
        #   只认带 OK. 的行 —— 失败时 bypy 打的是 "'x' ==> 'y' FAILED."，
        #   以前不分 OK/FAILED 地数 '==>'，会把失败也算成"传成功"。
        #
        # ★ 2026-09-25：上面那些 OK 行是 pv()（bypy.py:523，verbose 门控）打的。
        #   一旦 verbose 被吃掉（=0），它们一条都不会出现 —— 于是这里必须再认两种
        #   「不带 OK 字样、但同样证明文件已经在云端」的 debug 级证据：
        #     · Local and remote file size matches                    上传 200 后的大小校验通过
        #     · Faking error_code 31061 to 0, this is safe to ignore.  （file already exists）
        m = re.search(r'HTTP(?: Response)? Status Code:\s*(\d+)', line)
        if m:
            tag = 'http' + m.group(1)
            if tag not in self._file_http:
                self._file_http.append(tag)
        if 'Verbose level =' in line:
            try:
                self.verbose_level = int(re.split(r'Verbose level\s*=\s*', line)[1].strip().split()[0])
            except Exception:
                pass
        evidence = None
        if 'OK.' in line:
            if '=R=>' in line:
                self.rapid += 1
            elif '>>==>' in line or '==>' in line:
                self.uploaded += 1
            elif '=C=>' in line:
                self.copied += 1
            else:
                return
            evidence = 'ok-line'
        elif 'Local and remote file size matches' in line:
            evidence = 'size-match'
        elif 'Faking error_code 31061 to 0' in line:
            evidence = 'exists-31061'
        if evidence:
            self._file_hits += 1
            self.note(evidence)

    def feed_skip(self, line):
        if 'already exists, skip uploading' in line:
            self.skipped += 1


# 2026-09-24：新电脑上「上传失败」最常见的原因就是**这台电脑还没有本机授权**。
# bypy 找不到令牌时会打印一句 Please visit: https://openapi.baidu.com/oauth/2.0/authorize?...
# 然后 ask() 读不到输入（Electron 里没有 TTY）→ EOFError 直接崩。以前这一整段
# 只有 bypy 自己的英文输出，界面把 Node 的 "Command failed: <命令行>" 原样显示给用户，
# 完全看不出是「没授权」。这三个特征串是实测出来的（见 _p_unauth 探针）。
_NO_AUTH_MARKERS = (
    'Error while loading baidu pcs token',
    'openapi.baidu.com/oauth/2.0/authorize',
    'EOF when reading a line',
)


def _looks_like_no_auth(line):
    return any(m in line for m in _NO_AUTH_MARKERS)


def _write_stderr(msg):
    """往 stderr 写一行。

    ★ 必须是 stderr：main.js 的 authFailed 正则只拿 stderr + stdout + error.message
    一起匹配，而同步计数行（📊 同步结果:）走 stdout 被另一条正则解析，不能被污染。
    """
    try:
        sys.stderr.write(msg + '\n')
        sys.stderr.flush()
    except Exception:
        pass


def _run_bypy(cmd, stats):
    """
    跑一条 bypy 命令：边收边打印（main.js 会把这些行实时写进 logs\\upload-<日期>.log），
    同时把 <E> 错误行归类汇总。返回子进程退出码。
    """
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding='utf-8',
        errors='replace',
        bufsize=1,
    )
    cur_err = []

    def _flush_err():
        """把一段连续的 <E> 行归类：无害（秒传）还是真错误。

        ★★ 2026-09-23 实测（关键）：`method=rapidupload`（旧秒传接口）**已被百度下线**，
           现在每次都返回 HTTP 400 + `{"error_code":31023,"error_msg":"param error"}`；
           bypy 收到后会自动回退成整文件上传并**成功**（实测：十日终焉.epub 7183474 B
           先报 8 条 <E>，随后 `==> OK`，文件确实传上去了）。
           所以「有 <E> 行就算失败」是错的 —— 会把一次成功的上传判成失败。
           这里把带有 rapidupload / 31023 的连续错误块归到 warn_lines。
        """
        if not cur_err:
            return
        blob = ' '.join(cur_err)
        if 'rapidupload' in blob or '31023' in blob:
            stats.warn_lines.extend(cur_err)
        elif 'Failed to save settings' in blob:
            # ★ 2026-09-25：这只是 bypy 退出时没能更新 ~\.bypy\bypy.setting.json
            #   （目录只读 / 被占用 / 沙箱拦截），跟「文件有没有传上去」毫无关系。
            #   若当失败处理，就会把一次成功的上传判成失败 —— 与「不许再误报」相反。
            stats.warn_lines.extend(cur_err)
        else:
            stats.err_lines.extend(cur_err)
        del cur_err[:]

    _verbose_reported = False
    try:
        for line in proc.stdout:
            # ★ 2026-09-25：回显前给令牌打码 —— bypy 的 -d 级日志原本会把
            #   access_token / refresh_token 明文写进 logs\upload-<日期>.log，
            #   而那个文件是放在交付目录里跟着移动硬盘走的。只改我们回显的副本。
            safe_line = _redact(line)
            sys.stdout.write(safe_line)
            sys.stdout.flush()
            stats.tail.append(safe_line.rstrip())
            if not _verbose_reported and 'Verbose level =' in line:
                # ★ 本次真正的病根就在这一行：冻结分支的 `-v` 被 backend_entry 当成
                #   python 开关剥掉 → bypy 的 verbose=0 → 成功行被 pv() 静音。
                #   抄成我们自己的行，以后一眼可见。
                _verbose_reported = True
                stats.feed(line)
                print('UPLOAD-BYPY-VERBOSE ' + _jump({
                    'level': stats.verbose_level,
                    'note': 'bypy 的 verbose 级别；=0 说明 -v 没传进 bypy，成功行会被 pv() 静音',
                }), flush=True)
            if line.lstrip().startswith('<E>'):
                cur_err.append(line.strip())
            else:
                _flush_err()
            stats.feed(line)
            stats.feed_skip(line)
            if not stats.no_auth and _looks_like_no_auth(line):
                stats.no_auth = True
    finally:
        code = proc.wait()
        _flush_err()
    return code


def _fail(stats, reason, extra=None):
    """打印一条 UPLOAD-FAIL：把「为什么判失败」+ bypy 输出末尾几行一次写清。"""
    payload = {'reason': reason}
    if extra:
        payload.update(extra)
    if stats is not None:
        payload['verbose_level'] = stats.verbose_level
        payload['err_lines'] = stats.err_lines[:10]
        payload['warn_count'] = len(stats.warn_lines)
        payload['counts'] = {
            'uploaded': stats.uploaded, 'rapid': stats.rapid, 'copied': stats.copied,
            'skipped': stats.skipped, 'fallback_ok': stats.fallback_ok,
        }
        payload['tail'] = list(stats.tail)
    print('UPLOAD-FAIL ' + _jump(payload), flush=True)


def syncup_folder(local_path, remote_path, ondup='overwrite'):
    """
    把 local_path 里的每个文件**逐个强制上传**到 remote_path（覆盖云端同名文件）。

    函数名沿用 `syncup_folder` 是为了不动 main.js 的调用点（调用契约是硬约束）。
    """
    # 检查本地文件夹是否存在
    if not os.path.isdir(local_path):
        print(f"❌ 本地文件夹不存在: {local_path}")
        _fail(None, '本地文件夹不存在', {'local': local_path})
        return False

    dup = str(ondup or '').strip().lower()
    if dup not in ('overwrite', 'skip', 'prompt'):
        print(f"⚠️ 未知的重名策略 {ondup!r}，回退为 overwrite")
        dup = 'overwrite'
    if dup == 'prompt':
        # Electron 里没有 TTY，bypy 的 prompt 会 EOFError 直接崩，界面只该用 覆盖/跳过。
        print("⚠️ 重名策略 prompt 在无终端环境不可用，本次按 overwrite 处理")
        dup = 'overwrite'

    files = _iter_local_files(local_path)
    if not files:
        print(f"❌ 本地文件夹是空的: {local_path}")
        _fail(None, '本地文件夹是空的', {'local': local_path})
        return False

    print(f"📚 待上传文件: {len(files)} 个（逐文件强制上传，不再做「云端已有就跳过」的判定）")
    # ★ 特化日志：这次到底对哪个目录、哪些文件动手（源目录对不对，一眼可判）。
    plan = []
    for _full, _rel in files:
        try:
            _size = os.path.getsize(_full)
        except Exception:
            _size = None
        plan.append({'rel': _rel, 'size': _size})
    print('UPLOAD-PLAN ' + _jump({
        'local': os.path.abspath(local_path), 'remote': remote_path,
        'ondup': dup, 'count': len(files), 'files': plan,
    }), flush=True)

    stats = _Stats()
    for i, (full, rel) in enumerate(files, 1):
        remote_file = _remote_file_path(remote_path, rel)
        print("\n" + "─" * 18 + f" [{i}/{len(files)}] {rel} " + "─" * 18)
        # --on-dup 是 bypy 的【全局选项】，必须排在子命令 upload 前面。
        cmd = _bypy_cmd(*(list(BYPY_FLAGS) + ['--on-dup', dup, 'upload', full, remote_file]))
        if i == 1:
            if cmd[1:2] == ['__bypy__']:
                mode = '打包后端 __bypy__'
                ipv4 = 'backend_entry._run_bypy（冻结分支自带 IPv4 过滤）'
            elif cmd[1:2] == ['-c']:
                mode = '当前解释器 + 强制 IPv4（不再等 IPv6 的 42 秒）'
                ipv4 = '命令行 -c 前缀里'
            else:
                mode = 'bypy.exe（PATH/绝对路径兜底）'
                ipv4 = '无（兜底路径没有 IPv4 过滤）'
            print(f"🔧 bypy: {cmd[0]}（{mode}）")
            print('UPLOAD-MODE ' + _jump({
                'mode': mode, 'ipv4_filter': ipv4, 'cmd': cmd,
                'frozen': bool(getattr(sys, 'frozen', False)),
                'note': '本行用来判断「冻结分支 vs 开发分支是否等价」，含 -v 有没有被吃掉',
            }), flush=True)
        stats.begin_file()
        _t0 = time.time()
        code = _run_bypy(cmd, stats)
        _ms = int((time.time() - _t0) * 1000)
        # ★ 兜底判定：bypy 的 "OK." 成功行是 pv()（verbose 门控）打的，一旦 verbose
        #   被吃掉就一条都没有。此时若命中了「大小校验通过 / 31061 已存在」，说明这个
        #   文件确实已经在云端，按成功计 —— 否则就会出现「HTTP 200 传上去了却报失败」。
        if stats._file_hits and 'ok-line' not in stats._file_evidence:
            stats.uploaded += 1
            stats.fallback_ok += 1
            stats.note('counted-as-uploaded')
        try:
            _size = os.path.getsize(full)
        except Exception:
            _size = None
        print('UPLOAD-FILE-END ' + _jump({
            'i': i, 'n': len(files), 'rel': rel, 'size': _size,
            'rc': code, 'ms': _ms,
            'evidence': list(stats._file_evidence), 'http': list(stats._file_http),
        }), flush=True)
        if code != 0:
            print(f"❌ 第 {i}/{len(files)} 个文件上传结束，返回码: {code}（继续传下一个）")

    # 机器可读的结论行（main.js 用正则取这几个数字，写进界面与后台日志）。
    # 注意：这行会被界面侧的 stripLogNoise 过滤掉，不会显示在小字里。
    print(f"📊 同步结果: uploaded={stats.uploaded} rapid={stats.rapid} copied={stats.copied} "
          f"skipped={stats.skipped} failed={len(stats.err_lines)} warn={len(stats.warn_lines)}")
    print('UPLOAD-COUNTS ' + _jump({
        'files': len(files),
        'books_ok': stats.uploaded + stats.rapid + stats.copied,
        'fallback_by_size_or_exists': stats.fallback_ok,
        'verbose_level': stats.verbose_level,
        'verbose_note': 'verbose_level 为 0/None 时，bypy 不会打成功行，只能靠上面的兜底判定',
    }), flush=True)

    if stats.no_auth:
        # 说人话 + 给 main.js 一个能识别的「需要重新授权」标记（走 stderr）。
        # main.js 的正则：/not\s+authorized|invalid[_ ]?token|expired[_ ]?token|\b401\b/i
        # 命中 → authFailed=true → 界面显示「上传失败，请检查授权」而不是原始的 Command failed。
        print("❌ 这台电脑还没有百度网盘授权：请回到「传输」页点「重新授权」，按提示登录一次再发。")
        _write_stderr('[AUTH] not authorized: 本机缺少百度网盘令牌，请先在界面点「重新授权」')
        _fail(stats, 'no_auth: 本机缺少百度网盘令牌（bypy 在问授权码，Electron 没有 TTY）')
        return False

    if stats.err_lines:
        print(f"❌ bypy 报了 {len(stats.err_lines)} 条错误（退出码可能仍是 0），按失败处理：")
        for ln in stats.err_lines[:10]:
            print(f"   {ln}")
        _fail(stats, f'bypy 报了 {len(stats.err_lines)} 条 <E> 错误')
        return False
    if stats.warn_lines:
        # 无害：秒传接口 31023，bypy 已回退成普通上传且成功（详见上面 _flush_err 注释）
        print(f"（提示）bypy 报了 {len(stats.warn_lines)} 条秒传接口错误，已自动回退为普通上传，不影响结果")

    total = stats.uploaded + stats.rapid + stats.copied
    if total <= 0:
        # 逐文件上传时不该出现「一个成功行都没有」——真出现说明每个文件都没传上去。
        print("❌ 一个文件都没传成功：bypy 没有输出任何成功行")
        _fail(stats, '一个文件都没传成功：bypy 没有输出任何成功行，也没有大小校验证据')
        return False

    print(f"✅ 同步完成：成功上传 {total} 个文件（其中秒传 {stats.rapid} 个）")
    print('UPLOAD-OK ' + _jump({
        'books': total, 'fallback_by_size_or_exists': stats.fallback_ok,
        'verbose_level': stats.verbose_level, 'warn_count': len(stats.warn_lines),
    }), flush=True)
    return True


def main():
    # 支持命令行参数：python upload_folder_cmd.py [本地路径] [远程路径] [重名策略]
    local_dir = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_LOCAL_DIR
    remote_dir = sys.argv[2] if len(sys.argv) > 2 else REMOTE_DIR
    ondup = sys.argv[3] if len(sys.argv) > 3 else ON_DUP

    print("=" * 60)
    print("开始上传文件夹到百度网盘（逐文件强制上传）")
    print(f"本地路径: {local_dir}")
    print(f"远程路径: {remote_dir}")
    print("=" * 60)

    # ★ 特化日志（2026-09-25）：这次上传的完整环境快照。
    #   只记「令牌文件在不在 / 多大 / 什么时候被改过」，**绝不打印令牌内容**。
    _args = dict(_env_report())
    _args.update({
        'local': os.path.abspath(local_dir),
        'local_exists': os.path.isdir(local_dir),
        'remote': remote_dir,
        'ondup': ondup,
        'argv': list(sys.argv),
    })
    print('UPLOAD-ARGS ' + _jump(_args), flush=True)

    success = syncup_folder(local_dir, remote_dir, ondup)
    if success:
        print("🎉 上传成功！")
    else:
        print("💥 上传失败，请检查日志。")
        sys.exit(1)


if __name__ == "__main__":
    main()
