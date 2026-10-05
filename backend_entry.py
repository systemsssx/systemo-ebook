#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
EasyPub 打包后端统一入口（PyInstaller 冻结后扮演 "python.exe" 的角色）

背景
----
main.js 里所有 Python 调用都是同一个模板：

    "<pythonPath>" -X utf8 "<script.py>" [args...]

打包后 pythonPath 指向本 exe（resources/backend/easypub-backend.exe），
script.py 仍然是随包发布的真实 .py 文件（app.asar.unpacked/ 下）。
本入口负责：

  1. 吃掉 `-X utf8` / `-u` 这类解释器开关，还原出真正的 argv
  2. 用当前冻结解释器执行目标 .py（等价于 `python script.py args`）
  3. 复刻 `-X utf8` 的效果：把 stdin/stdout/stderr 切成 UTF-8
     （main.js 是按 UTF-8 解析 stdout 里的 JSON 的，不切会乱码）
  4. 把后端目录塞进 PATH，让脚本里 `subprocess(['bypy', ...])` 能找到
     随包的同名命令（见下面 __bypy__ 分支）

本文件不参与 Electron 的 asar 打包逻辑，只被 PyInstaller 使用。
"""

import os
import sys


# ================================================================
# ★★★ 仅供 PyInstaller 静态分析用的导入清单 ★★★
# ================================================================
# 下面这些模块本文件运行时【从不导入】，但 PyInstaller 是靠静态扫描
# import 语句来决定把哪些库打进包里的。因为 .py 脚本是运行时从磁盘
# exec 的（它扫不到），所以必须在这里"喂"给它看。
#
# 以后新增依赖库时，记得在这里补一行，否则冻结后运行会 ModuleNotFoundError。
if False:  # noqa: F401  —— 永远为假，运行时不执行
    import epub_generator      # noqa: F401  用到 subprocess / tempfile
    import ai_cover            # noqa: F401  selenium / webdriver_manager / requests
    # ★ 2026-09-30：main.py / ai_author.py 已删除（DeepSeek 与旧下载器下线），
    #   这里两行死引用一并去掉 —— 顺带 **openai 不再被打进包**（ai_cover 已不用它）。
    import image_enhancer      # noqa: F401  onnxruntime / PIL（本地超分）
    import onnxruntime         # noqa: F401  封面超分推理引擎（CPU）
    import wifi_upload         # noqa: F401  selenium / pyautogui / win32clipboard
    import open_auth_page      # noqa: F401  selenium
    import kindle_send         # noqa: F401  smtplib / email
    import encoding_converter  # noqa: F401  纯标准库
    import upload_folder_cmd   # noqa: F401  subprocess 调 bypy
    import get_auth_code       # noqa: F401  subprocess 调 bypy
    import library_scan        # noqa: F401  书库：读 EPUB 元数据 / 改元数据 / 删除


# 解释器开关：这些参数是 python.exe 的，不是脚本的，必须剥掉
_FLAGS_WITH_VALUE = {'-X', '-W'}
_FLAGS_ALONE = {'-u', '-B', '-E', '-s', '-S', '-I', '-O', '-OO', '-v', '-b', '-q'}


def _strip_interpreter_flags(argv):
    """把 `-X utf8`、`-u` 之类的解释器开关从 argv **头部**剥掉。

    ★★ 2026-09-25 重要修正（用户报「打包后上传必然失败」的真正病根）：
       以前是在 argv 的**任意位置**剥这些开关。但 `_FLAGS_ALONE` 里含 `-v`
       （本意是剥 `python -v`），而冻结分支跑 bypy 用的是

           [sys.executable, '__bypy__', '-v', '-d', '--on-dup', 'overwrite', 'upload', ...]

       （见 upload_folder_cmd.py 的 _bypy_cmd），`-v` 落在「脚本名之后」——
       那是 bypy 自己的 --verbose，不是 python 的。被吃掉以后 bypy 的
       `self.verbose` 变成 0（日志里能看到 `Verbose level = 0`），而 bypy 的
       成功行 `'x' ==> 'y' OK.` 是 `pv()`（bypy.py:523）门控的，于是**一条成功行
       都不打印**；upload_folder_cmd.py 的计数器只认带 `OK.` 的行 → 数到 0 →
       明明 HTTP 200 已经传上去，却打「一个文件都没传成功」并 exit 1，界面报失败。

       修正：解释器开关只可能出现在**脚本名之前**（python 的语法就是这样），
       所以遇到第一个非开关参数就停下，后面的原样交给脚本。
    """
    i = 0
    n = len(argv)
    while i < n:
        a = argv[i]
        if a in _FLAGS_WITH_VALUE and i + 1 < n:
            i += 2                      # 连它的值一起吃掉，例如 `-X utf8`
            continue
        if len(a) > 2 and a[:2] in _FLAGS_WITH_VALUE:
            i += 1                      # 粘连写法，例如 `-Xutf8`
            continue
        if a in _FLAGS_ALONE:
            i += 1
            continue
        break                           # 第一个非开关参数 = 目标脚本，它就是 argv 的尽头
    return argv[i:]


def _force_utf8_stdio():
    """复刻 `python -X utf8` 的效果：管道里的中文/JSON 按 UTF-8 走。"""
    os.environ['PYTHONUTF8'] = '1'
    os.environ['PYTHONIOENCODING'] = 'utf-8'
    for stream_name in ('stdin', 'stdout', 'stderr'):
        stream = getattr(sys, stream_name, None)
        if stream is None:
            continue
        try:
            stream.reconfigure(encoding='utf-8', errors='replace')
        except Exception:
            pass


def _prepend_path():
    """把后端自带目录塞进 PATH 最前面，供脚本里的 subprocess 找随包命令。"""
    dirs = []
    if getattr(sys, 'frozen', False):
        dirs.append(os.path.dirname(os.path.abspath(sys.executable)))
    meipass = getattr(sys, '_MEIPASS', None)
    if meipass:
        dirs.append(meipass)
    dirs = [d for d in dirs if d and os.path.isdir(d)]
    if not dirs:
        return
    old = os.environ.get('PATH', '')
    os.environ['PATH'] = os.pathsep.join(dirs + [old])


def _resolve_script(target):
    """把 main.js 传来的脚本路径解析成真实存在的 .py 文件。"""
    candidates = []

    if os.path.isabs(target) or os.sep in target or '/' in target:
        candidates.append(target)
    else:
        # 裸文件名：优先用上一次执行脚本所在的目录（支持脚本之间互相调用）
        base = os.environ.get('EASYPUB_PY_DIR')
        if base:
            candidates.append(os.path.join(base, target))
        candidates.append(os.path.join(os.getcwd(), target))
        if getattr(sys, 'frozen', False):
            candidates.append(os.path.join(os.path.dirname(os.path.abspath(sys.executable)), target))
        candidates.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), target))

    for c in candidates:
        if c and os.path.isfile(c):
            return os.path.abspath(c)
    return None


def _run_script(script, args):
    """等价于 `python <script> <args...>`。"""
    with open(script, 'r', encoding='utf-8-sig') as f:
        source = f.read()

    code = compile(source, script, 'exec')

    # 让脚本内部再调 `sys.executable xxx.py` 时能定位到同目录
    os.environ['EASYPUB_PY_DIR'] = os.path.dirname(script)

    sys.argv = [script] + list(args)
    globs = {
        '__name__': '__main__',
        '__file__': script,
        '__builtins__': __builtins__,
        '__spec__': None,
        '__package__': None,
        '__loader__': None,
        '__cached__': None,
        '__doc__': None,
    }
    exec(code, globs)


def _run_bypy(args):
    """以子命令方式跑 bypy 命令行，替代 `bypy.exe`（冻结后系统里没有它）。

    ★ 2026-09-23：先强制 IPv4。这台机器解析 pcs.baidu.com 会先给出连不通的 IPv6
      地址，requests 挨个试会白等 21 秒 x2（实测共 42 秒）才回退 IPv4；
      而 IPv4 直连只要 0.05 秒。
    """
    import socket
    _g = socket.getaddrinfo
    socket.getaddrinfo = lambda *a, **k: (
        [x for x in _g(*a, **k) if x[0] == socket.AF_INET] or _g(*a, **k)
    )
    from bypy.bypy import main as bypy_main
    sys.argv = ['bypy'] + list(args)
    bypy_main()


def main():
    _force_utf8_stdio()
    _prepend_path()

    argv = _strip_interpreter_flags(sys.argv[1:])
    if not argv:
        sys.stderr.write('easypub-backend: 缺少要执行的脚本参数\n')
        return 2

    target, rest = argv[0], argv[1:]

    # 脚本里 `subprocess(['bypy', ...])` 在冻结环境下会被改写成
    # `[sys.executable, '__bypy__', ...]`，走这个分支
    if target == '__bypy__':
        _run_bypy(rest)
        return 0

    script = _resolve_script(target)
    if not script:
        sys.stderr.write('easypub-backend: 找不到脚本 %r\n' % target)
        return 2

    _run_script(script, rest)
    return 0


if __name__ == '__main__':
    sys.exit(main() or 0)
