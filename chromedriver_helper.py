#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
chromedriver_helper.py —— Chrome / ChromeDriver 自动版本适配与降级管理（全标准库）

核心作用（解决 Chrome 自动更新导致随包静态 chromedriver.exe 失效的问题）：
  1. 定位本机 Chrome 可执行文件并读取主版本号（快速读版本目录 → 注册表 → PE FileVersion）
  2. 对比「缓存驱动」与「随包驱动」的主版本号：
     · 主版本一致 → 零网络秒级复用
     · 主版本不一致 / 驱动缺失 → 自动按大版本号从国内阿里 npmmirror 镜像拉取匹配驱动
       （镜像不可用时回退 Google Chrome-for-Testing 官方清单，或交由 Selenium Manager）
     · 缓存持久化至 %APPDATA%\\easypub-new\\drivers\\chrome-<major>\\chromedriver.exe
  3. 启动 webdriver.Chrome 时若仍遇到版本不匹配异常（SessionNotCreatedException），
     自动强制刷新同主版本驱动并重试一次；仍失败或无 Chrome 时优雅回退本机 Microsoft Edge。
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile

CREATE_NO_WINDOW = 0x08000000

NPMMIRROR_INDEX = 'https://registry.npmmirror.com/-/binary/chrome-for-testing/'
GCF_MILESTONES_URL = 'https://googlechromelabs.github.io/chrome-for-testing/latest-versions-per-milestone-with-downloads.json'


def _log(log_fn, msg):
    if log_fn:
        try:
            log_fn(msg)
            return
        except Exception:
            pass
    try:
        print(msg, file=sys.stderr)
    except Exception:
        pass


def _ver_tuple(ver_str):
    if not ver_str:
        return ()
    parts = []
    for p in str(ver_str).strip().split('.'):
        try:
            parts.append(int(p))
        except Exception:
            break
    return tuple(parts)


def major_of(ver_str):
    t = _ver_tuple(ver_str)
    return t[0] if t else None


def find_chrome_binary():
    """查找本机 Chrome 浏览器可执行文件路径（按优先级去重返回列表）。"""
    candidates = []

    try:
        import winreg
        reg_specs = [
            (winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe'),
            (winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe'),
            (winreg.HKEY_CURRENT_USER, r'SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe'),
        ]
        for hive, sub in reg_specs:
            try:
                with winreg.OpenKey(hive, sub) as k:
                    val, _ = winreg.QueryValueEx(k, '')
                    if val and os.path.isfile(val):
                        candidates.append(val)
            except Exception:
                continue
    except ImportError:
        pass

    common_paths = [
        r'C:\Program Files\Google\Chrome\Application\chrome.exe',
        r'C:\Program Files (x86)\Google\Chrome\Application\chrome.exe',
        os.path.expandvars(r'%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe'),
        os.path.expandvars(r'%PROGRAMFILES%\Google\Chrome\Application\chrome.exe'),
        os.path.expandvars(r'%PROGRAMFILES(X86)%\Google\Chrome\Application\chrome.exe'),
        r'D:\Program Files\Google\Chrome\Application\chrome.exe',
        r'D:\Program Files (x86)\Google\Chrome\Application\chrome.exe',
    ]
    for p in common_paths:
        if p and os.path.isfile(p):
            candidates.append(p)

    try:
        r = subprocess.run(
            ['where', 'chrome'],
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=3,
            creationflags=CREATE_NO_WINDOW,
        )
        for line in (r.stdout or '').splitlines():
            line = line.strip()
            if line and os.path.isfile(line):
                candidates.append(line)
    except Exception:
        pass

    seen, unique = set(), []
    for c in candidates:
        nc = os.path.normcase(os.path.abspath(c))
        if nc not in seen:
            seen.add(nc)
            unique.append(c)
    return unique


def find_edge_binary():
    """查找本机 Microsoft Edge 浏览器可执行文件路径（按优先级去重返回列表）。"""
    candidates = []

    try:
        import winreg
        for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            for sub in (
                r'SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\msedge.exe',
                r'SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\App Paths\msedge.exe',
            ):
                try:
                    with winreg.OpenKey(hive, sub) as k:
                        val, _ = winreg.QueryValueEx(k, '')
                        if val and os.path.isfile(val):
                            candidates.append(val)
                except Exception:
                    continue
    except ImportError:
        pass

    for p in (
        r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',
        r'C:\Program Files\Microsoft\Edge\Application\msedge.exe',
        os.path.expandvars(r'%LOCALAPPDATA%\Microsoft\Edge\Application\msedge.exe'),
    ):
        if p and os.path.isfile(p):
            candidates.append(p)

    try:
        r = subprocess.run(
            ['where', 'msedge'],
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=3,
            creationflags=CREATE_NO_WINDOW,
        )
        for line in (r.stdout or '').splitlines():
            line = line.strip()
            if line and os.path.isfile(line):
                candidates.append(line)
    except Exception:
        pass

    seen, unique = set(), []
    for c in candidates:
        nc = os.path.normcase(os.path.abspath(c))
        if nc not in seen:
            seen.add(nc)
            unique.append(c)
    return unique


def get_chrome_version(chrome_bin=None):
    """读取本机 Chrome 完整版本号（如 '157.0.8103.79'）。读不到返回 None。"""
    if not chrome_bin:
        bins = find_chrome_binary()
        chrome_bin = bins[0] if bins else None

    # ① 最快路径：Chrome 目录下的版本号子目录（如 157.0.8103.79\），零子进程耗时
    if chrome_bin and os.path.isfile(chrome_bin):
        app_dir = os.path.dirname(os.path.abspath(chrome_bin))
        best = None
        try:
            for entry in os.listdir(app_dir):
                if re.match(r'^\d+\.\d+\.\d+\.\d+$', entry) and os.path.isdir(os.path.join(app_dir, entry)):
                    if best is None or _ver_tuple(entry) > _ver_tuple(best):
                        best = entry
            if best:
                return best
        except Exception:
            pass

    # ② 次优：Windows 注册表
    try:
        import winreg
        reg_keys = [
            (winreg.HKEY_CURRENT_USER, r'Software\Google\Chrome\BLBeacon', 'version'),
            (winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\WOW6432Node\Google\Update\Clients\{8A69D345-D564-463c-AFF1-A69D9E530F96}', 'pv'),
            (winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\Google\Update\Clients\{8A69D345-D564-463c-AFF1-A69D9E530F96}', 'pv'),
        ]
        for hive, sub, name in reg_keys:
            try:
                with winreg.OpenKey(hive, sub) as k:
                    val, _ = winreg.QueryValueEx(k, name)
                    if val and re.match(r'^\d+\.\d+', str(val)):
                        return str(val).strip()
            except Exception:
                continue
    except ImportError:
        pass

    # ③ 兜底：通过 PowerShell 读 PE 文件版本
    if chrome_bin and os.path.isfile(chrome_bin):
        try:
            escaped = chrome_bin.replace("'", "''")
            r = subprocess.run(
                ['powershell', '-NoProfile', '-Command', f"(Get-Item -LiteralPath '{escaped}').VersionInfo.ProductVersion"],
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='replace',
                timeout=6,
                creationflags=CREATE_NO_WINDOW,
            )
            out = (r.stdout or '').strip()
            m = re.search(r'(\d+\.\d+\.\d+\.\d+)', out)
            if m:
                return m.group(1)
        except Exception:
            pass

    return None


def get_chromedriver_version(driver_path):
    """运行 `chromedriver --version` 读取驱动版本号。失败返回 None。"""
    if not driver_path or not os.path.isfile(driver_path):
        return None
    try:
        r = subprocess.run(
            [driver_path, '--version'],
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=5,
            creationflags=CREATE_NO_WINDOW,
        )
        txt = (r.stdout or '') + ' ' + (r.stderr or '')
        m = re.search(r'ChromeDriver\s+(\d+\.\d+\.\d+\.\d+)', txt) or re.search(r'(\d+\.\d+\.\d+\.\d+)', txt)
        if m:
            return m.group(1)
    except Exception:
        pass
    return None


def get_driver_cache_dir():
    """返回持久化驱动缓存目录，避免每次重启重新下载。"""
    env_dir = (os.environ.get('EASYPUB_DRIVER_CACHE_DIR') or '').strip()
    if env_dir:
        return os.path.abspath(env_dir)
    appdata = (os.environ.get('APPDATA') or '').strip()
    if appdata:
        return os.path.join(appdata, 'easypub-new', 'drivers')
    return os.path.join(tempfile.gettempdir(), 'easypub-drivers')


def _http_get_bytes(url, timeout=15, use_easypub_proxy=False):
    handlers = []
    if use_easypub_proxy:
        proxy = (os.environ.get('EASYPUB_PROXY') or '').strip()
        if proxy and proxy.upper() != 'DIRECT':
            handlers.append(urllib.request.ProxyHandler({'http': proxy, 'https': proxy}))
    else:
        # 国内镜像走直连，不受系统或局部代理干扰
        handlers.append(urllib.request.ProxyHandler({}))
    opener = urllib.request.build_opener(*handlers)
    req = urllib.request.Request(url, headers={'User-Agent': 'EasyPub-DriverSync/1.0'})
    with opener.open(req, timeout=timeout) as resp:
        return resp.read()


def _extract_chromedriver_zip(zip_bytes, target_exe):
    parent = os.path.dirname(target_exe)
    os.makedirs(parent, exist_ok=True)
    tmp_zip = target_exe + '.zip.tmp'
    tmp_exe = target_exe + '.tmp'
    try:
        with open(tmp_zip, 'wb') as f:
            f.write(zip_bytes)
        found = False
        with zipfile.ZipFile(tmp_zip, 'r') as zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                if os.path.basename(info.filename).lower() == 'chromedriver.exe':
                    with zf.open(info, 'r') as src, open(tmp_exe, 'wb') as dst:
                        shutil.copyfileobj(src, dst)
                    found = True
                    break
        if not found or not os.path.isfile(tmp_exe):
            return False
        os.replace(tmp_exe, target_exe)
        return True
    finally:
        for p in (tmp_zip, tmp_exe):
            try:
                if os.path.isfile(p):
                    os.remove(p)
            except Exception:
                pass


def _prune_old_drivers(cache_dir, keep=2):
    try:
        if not os.path.isdir(cache_dir):
            return
        dirs = []
        for name in os.listdir(cache_dir):
            m = re.match(r'^chrome-(\d+)$', name)
            full = os.path.join(cache_dir, name)
            if m and os.path.isdir(full):
                dirs.append((int(m.group(1)), full))
        dirs.sort(reverse=True)
        for _, full in dirs[keep:]:
            shutil.rmtree(full, ignore_errors=True)
    except Exception:
        pass


def download_matching_chromedriver(major, cache_dir=None, log_fn=None, force=False):
    """按 Chrome 主版本号下载对应 chromedriver.exe 并缓存至本地，失败返回 None。"""
    if not major:
        return None
    cache_root = cache_dir or get_driver_cache_dir()
    target = os.path.join(cache_root, f'chrome-{major}', 'chromedriver.exe')

    if not force and os.path.isfile(target):
        cached_ver = get_chromedriver_version(target)
        if major_of(cached_ver) == major:
            return target

    prefix = f'{major}.'

    # 1. 优先走国内阿里 npmmirror 镜像（无需梯子，直连）
    try:
        raw_index = _http_get_bytes(NPMMIRROR_INDEX, timeout=10, use_easypub_proxy=False)
        entries = json.loads(raw_index.decode('utf-8'))
        matching_vers = [
            e['name'].rstrip('/')
            for e in entries
            if isinstance(e, dict) and e.get('type') == 'dir' and str(e.get('name', '')).startswith(prefix)
        ]
        matching_vers.sort(key=_ver_tuple, reverse=True)
        for ver in matching_vers[:3]:
            for arch, zip_name in (('win64', 'chromedriver-win64.zip'), ('win32', 'chromedriver-win32.zip')):
                zip_url = f'{NPMMIRROR_INDEX}{ver}/{arch}/{zip_name}'
                try:
                    _log(log_fn, f'🔄 正在从国内镜像更新 Chrome {major} 驱动 ({ver})…')
                    zbytes = _http_get_bytes(zip_url, timeout=40, use_easypub_proxy=False)
                    if _extract_chromedriver_zip(zbytes, target):
                        got_ver = get_chromedriver_version(target)
                        if major_of(got_ver) == major:
                            _prune_old_drivers(cache_root, keep=2)
                            _log(log_fn, f'✅ 已更新适配 ChromeDriver: {got_ver} → {target}')
                            return target
                except Exception:
                    continue
    except Exception as e:
        _log(log_fn, f'⚠️ 国内镜像查询失败，尝试官方源: {e}')

    # 2. 回退 Google Chrome-for-Testing 官方清单（支持内置代理）
    try:
        raw_json = _http_get_bytes(GCF_MILESTONES_URL, timeout=15, use_easypub_proxy=True)
        payload = json.loads(raw_json.decode('utf-8'))
        milestone = (payload.get('milestones') or {}).get(str(major)) or {}
        downloads = (milestone.get('downloads') or {}).get('chromedriver') or []
        by_plat = {d.get('platform'): d.get('url') for d in downloads if isinstance(d, dict)}
        for plat in ('win64', 'win32'):
            url = by_plat.get(plat)
            if not url:
                continue
            try:
                _log(log_fn, f'🔄 正在从官方源下载 Chrome {major} 驱动…')
                zbytes = _http_get_bytes(url, timeout=60, use_easypub_proxy=True)
                if _extract_chromedriver_zip(zbytes, target):
                    got_ver = get_chromedriver_version(target)
                    if major_of(got_ver) == major:
                        _prune_old_drivers(cache_root, keep=2)
                        _log(log_fn, f'✅ 已下载适配 ChromeDriver: {got_ver} → {target}')
                        return target
            except Exception:
                continue
    except Exception as e:
        _log(log_fn, f'⚠️ 官方源下载失败，交由 Selenium Manager 兜底: {e}')

    return None


def resolve_chromedriver(preferred_path=None, log_fn=None, cache_dir=None, force_download=False):
    """
    智能解析与本机 Chrome 主版本一致的 chromedriver.exe。
    返回: (driver_path_or_None, chrome_bin_or_None, source_tag)
      source_tag: 'cache' | 'bundled' | 'auto-downloaded' | 'selenium-manager' | 'no-chrome'
    """
    chrome_bins = find_chrome_binary()
    chrome_bin = chrome_bins[0] if chrome_bins else None
    if not chrome_bin:
        fallback = preferred_path if (preferred_path and os.path.isfile(preferred_path)) else None
        return fallback, None, 'no-chrome'

    chrome_ver = get_chrome_version(chrome_bin)
    chrome_major = major_of(chrome_ver)
    cache_root = cache_dir or get_driver_cache_dir()

    if chrome_major is None:
        fallback = preferred_path if (preferred_path and os.path.isfile(preferred_path)) else None
        return fallback, chrome_bin, 'bundled' if fallback else 'selenium-manager'

    if not force_download:
        # ① 已下载过的同主版本驱动缓存
        cached_exe = os.path.join(cache_root, f'chrome-{chrome_major}', 'chromedriver.exe')
        if os.path.isfile(cached_exe):
            if major_of(get_chromedriver_version(cached_exe)) == chrome_major:
                return cached_exe, chrome_bin, 'cache'

        # ② 随包携带的静态 chromedriver.exe（主版本一致就秒复用，不联网）
        if preferred_path and os.path.isfile(preferred_path):
            pref_ver = get_chromedriver_version(preferred_path)
            pref_major = major_of(pref_ver)
            if pref_major == chrome_major:
                return preferred_path, chrome_bin, 'bundled'
            _log(log_fn, f'⚠️ 本机 Chrome ({chrome_ver}) 与内置驱动 ({pref_ver or "未知"}) 主版本不一致，正在自动适配…')
        else:
            _log(log_fn, f'🔍 检测到本机 Chrome {chrome_ver}，正在匹配对应驱动…')

    # ③ 自动热更拉取匹配主版本驱动
    dl = download_matching_chromedriver(chrome_major, cache_dir=cache_root, log_fn=log_fn, force=force_download)
    if dl and os.path.isfile(dl):
        return dl, chrome_bin, 'auto-downloaded'

    # ④ 最终交给 Selenium 4 内置 Selenium Manager 托管
    return None, chrome_bin, 'selenium-manager'


def _build_edge_options_from_chrome(chrome_options):
    from selenium.webdriver.edge.options import Options as EdgeOptions
    edge_opts = EdgeOptions()
    for arg in getattr(chrome_options, 'arguments', []) or []:
        # Chromium 通用命令行参数与 Edge 兼容，直接透传
        if not arg:
            continue
        edge_opts.add_argument(arg)
    exp = getattr(chrome_options, 'experimental_options', {}) or {}
    for k in ('excludeSwitches', 'useAutomationExtension'):
        if k in exp:
            try:
                edge_opts.add_experimental_option(k, exp[k])
            except Exception:
                pass
    try:
        edge_opts.page_load_strategy = getattr(chrome_options, 'page_load_strategy', 'normal')
    except Exception:
        pass
    edge_bins = find_edge_binary()
    if edge_bins:
        edge_opts.binary_location = edge_bins[0]
    return edge_opts


def _is_version_mismatch_error(exc):
    msg = str(exc or '').lower()
    keywords = (
        'sessionnotcreated',
        'session not created',
        'this version of chromedriver only supports',
        'unable to obtain driver',
        'chromedriver',
    )
    return any(k in msg for k in keywords)


def create_resilient_chrome_or_edge(chrome_options, preferred_driver=None, log_fn=None, edge_fallback=True):
    """
    弹性创建浏览器驱动：
      1. 用 resolve_chromedriver() 找匹配本机 Chrome 主版本的驱动
      2. 若启动时仍遇到 ChromeDriver 版本不匹配，自动强拉一次新版驱动并重试 Chrome
      3. 若本机未安装 Chrome 或 Chrome 仍启动失败且允许 Edge 兜底，则回退启动 Microsoft Edge
    返回: (driver, browser_kind, source_tag)  browser_kind 为 'chrome' | 'edge'
    """
    from selenium import webdriver
    from selenium.webdriver.chrome.service import Service as ChromeService

    cache_dir = get_driver_cache_dir()
    driver_path, chrome_bin, source = resolve_chromedriver(
        preferred_path=preferred_driver,
        log_fn=log_fn,
        cache_dir=cache_dir,
        force_download=False,
    )

    if chrome_bin and not getattr(chrome_options, 'binary_location', None):
        chrome_options.binary_location = chrome_bin

    if chrome_bin or source != 'no-chrome':
        for attempt in range(2):
            try:
                svc = (
                    ChromeService(executable_path=driver_path, log_output=subprocess.DEVNULL)
                    if driver_path
                    else ChromeService(log_output=subprocess.DEVNULL)
                )
                svc.creation_flags = CREATE_NO_WINDOW
                drv = webdriver.Chrome(service=svc, options=chrome_options)
                return drv, 'chrome', source
            except Exception as exc:
                if attempt == 0 and _is_version_mismatch_error(exc) and source in ('bundled', 'cache'):
                    _log(log_fn, f'⚠️ ChromeDriver 启动版本不匹配，正在自动重新拉取匹配驱动并重试…')
                    driver_path, chrome_bin, source = resolve_chromedriver(
                        preferred_path=preferred_driver,
                        log_fn=log_fn,
                        cache_dir=cache_dir,
                        force_download=True,
                    )
                    if chrome_bin:
                        chrome_options.binary_location = chrome_bin
                    continue
                _log(log_fn, f'⚠️ Chrome 启动失败（来源={source}）: {exc}')
                break

    if edge_fallback and find_edge_binary():
        from selenium.webdriver.edge.service import Service as EdgeService
        _log(log_fn, '🔄 切换为本机 Microsoft Edge 浏览器兜底…')
        edge_opts = _build_edge_options_from_chrome(chrome_options)
        esvc = EdgeService(log_output=subprocess.DEVNULL)
        esvc.creation_flags = CREATE_NO_WINDOW
        drv = webdriver.Edge(service=esvc, options=edge_opts)
        return drv, 'edge', 'edge-fallback'

    raise RuntimeError('无法启动 Chrome 或 Edge 浏览器（请确认系统已安装 Chrome 或 Edge）')
