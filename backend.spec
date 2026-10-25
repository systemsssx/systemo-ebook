# -*- mode: python ; coding: utf-8 -*-
"""
EasyPub Python 后端打包脚本（PyInstaller 6.x）

产物：dist-backend/easypub-backend/
        easypub-backend.exe      ← 冻结的解释器，扮演 "python.exe"
        _internal/               ← 所有第三方库 + 标准库

用法：
    python -m PyInstaller backend.spec --noconfirm

设计说明
--------
* 用 onedir 而不是 onefile：pandoc.exe 已经 233 MB，onefile 每次启动都要
  把几百 MB 解压到临时目录，启动会非常慢。
* 只做一个 exe（统一入口 backend_entry.py），main.js 里 11 个 Python
  调用点全部不用改：它们本来就是 `"<python>" -X utf8 "<script.py>"`，
  入口会把 `-X utf8` 吃掉再执行 script.py。
* backend_entry.py 里的 `if False:` 导入清单是喂给 PyInstaller 静态分析用的，
  新增依赖库时同步更新那里。
"""

import os

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

PROJ = SPECPATH  # noqa: F821  —— PyInstaller 注入，指向本 spec 所在目录

# ----------------------------------------------------------------
# 隐藏导入：脚本里那些"运行时才 import"的库，静态分析容易漏
# ----------------------------------------------------------------
hiddenimports = [
    # pywin32
    'win32api', 'win32gui', 'win32con', 'win32clipboard',
    'win32com', 'win32com.client', 'pythoncom', 'pywintypes',
    # pyautogui 一族
    'pyautogui', 'pyscreeze', 'pytweening', 'pymsgbox',
    # 本地超分（封面）
    'onnxruntime', 'onnxruntime.capi', 'onnxruntime.capi._pybind_state',
    # 其他
    'winreg', 'smtplib', 'email.message', 'ssl', 'socket', 'mimetypes',
]

# 整包收子模块，避免 selenium / webdriver_manager 这类深层导入漏掉
# 注意：qcloud_cos 已随腾讯云超分方案一起移除，不要再加回来
for _pkg in ('selenium', 'webdriver_manager', 'pywinauto', 'PIL'):
    try:
        hiddenimports += collect_submodules(_pkg)
    except Exception:
        pass

# bypy：脚本里是用 subprocess 调命令行的，这里要显式带上它的实现
try:
    hiddenimports += [m for m in collect_submodules('bypy') if m != 'bypy.gui']
except Exception:
    hiddenimports += ['bypy', 'bypy.bypy']

# ★ bypy 的**数据文件**（2026-09-24 修）：bypy.py:1026 在 ByPy.__init__ 里
#   无条件执行 pkgutil.get_data('bypy', 'res/auth.json') 读取授权服务器列表，
#   没有任何 try/except。只收子模块（上面那几行）不会把非 .py 文件带进包，
#   于是冻结后端里**任何** bypy 命令（upload / list / delete）都在 __init__
#   阶段抛 FileNotFoundError: ...\_internal\bypy\res\auth.json
#   → 打包版的百度网盘上传 100% 失败（开发版走 site-packages，所以本机测试
#   永远发现不了）。这里把 bypy 目录下所有非 .py 资源一起收进来：
#   res/auth.json（818 B，必需）与 bypy.cacerts.pem（313 KB，--cacerts 用）。
try:
    _bypy_datas = collect_data_files('bypy')
except Exception:
    _bypy_datas = []

# ----------------------------------------------------------------
# 排除：明显用不到的大件，能省几十 MB
# ----------------------------------------------------------------
excludes = [
    'tkinter', '_tkinter', 'mouseinfo',        # GUI，后端全程无窗口
    'bypy.gui',                                # 依赖 tkinter
    'matplotlib', 'scipy', 'pandas',
    'PyQt5', 'PyQt6', 'PySide2', 'PySide6',
    'IPython', 'jupyter', 'notebook', 'pytest',
]

a = Analysis(  # noqa: F821
    ['backend_entry.py'],
    pathex=[PROJ],
    binaries=[],
    datas=[
        # 封面超分模型（约 4.6MB），随后端一起发，运行时从 _MEIPASS/models 找
        (os.path.join(PROJ, 'models', 'realesr_general_x4v3.onnx'), 'models'),
        # bypy 的数据文件（res/auth.json 等），缺了会让所有 bypy 命令崩
    ] + _bypy_datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)  # noqa: F821

exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='easypub-backend',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,                  # ★ 必须保留控制台：main.js 靠 stdout 收 JSON
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)

coll = COLLECT(  # noqa: F821
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name='easypub-backend',
)
