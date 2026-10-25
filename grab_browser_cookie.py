# -*- coding: utf-8 -*-
r"""grab_browser_cookie.py —— **从你自己的浏览器本地读 cookie**（不走网络，Cloudflare 无从拦）

★★★ 2026-10-02（用户定稿）★★★
  用户原话：「我用脚本的不管怎么样都会被 cloudflare 拦，这样，我点刷新 cookie，
             你就弹出个可叉的 html，告诉用户让他打开浏览器……点到 re 从零开始的异世界生活
             第一卷第一章的正文页，然后你一直在**后台开脚本爬 cookie**」

为什么这条路能过 Cloudflare：
  · 之前用 Selenium 开 Edge —— 那是**自动化浏览器**，有机器人特征，站点一直拦。
  · 现在用**用户自己的浏览器**：真人点、真人过验证，Cloudflare 自然放行；
    脚本只做一件事 —— **在本地把浏览器已经存好的 cookie 读出来**，一个网络请求都不发。

怎么读（纯标准库 + ctypes，不依赖 pywin32）：
  · cookie 存在 SQLite：`<User Data>\<Profile>\Network\Cookies`
  · 值用 AES-256-GCM 加密，密钥在 `<User Data>\Local State` 的
    `os_crypt.encrypted_key`（base64，去掉 `DPAPI` 前缀后用当前用户的 DPAPI 解）
  · ★ 该库被浏览器**独占锁定**（实测 err=32，连 `FILE_SHARE_ALL` 都不行；
    也**没有** SeBackupPrivilege 可用 `robocopy /B`）
    ⇒ **必须等用户关掉浏览器**，脚本**轮询**到能读为止（这就是"后台一直爬"）。

用法：
  py grab_browser_cookie.py --cookie-file <userData>\linovelib-cookies.txt
  py grab_browser_cookie.py --timeout 900 --poll 3        # 一直等 15 分钟
输出：成功时 stdout 打一行 `@@COOKIE-OK {json}`；进度/诊断一律走 stderr。
"""
from __future__ import annotations

import argparse
import base64
import ctypes
import ctypes.wintypes as wt
import io
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time

# ★ 被 subprocess 拉起来时 stdout 是管道，Windows 按 GBK 编码 → 中文/emoji 会 UnicodeEncodeError
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

# 要取证的域名（子串匹配 host_key）
DOMAINS = ("bilinovel.com", "linovelib.com")
# 必须拿到的那一项 —— 没有它，站点的正文就是截断版
NEED = "cf_clearance"

BROWSERS = (
    ("Edge", "Microsoft", "Edge"),
    ("Chrome", "Google", "Chrome"),
)

# ---------------------------------------------------------------- DPAPI
class _BLOB(ctypes.Structure):
    _fields_ = [("cbData", wt.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _dpapi_unprotect(data: bytes) -> bytes:
    """当前 Windows 用户的 DPAPI 解密（等价 win32crypt.CryptUnprotectData）。"""
    src = ctypes.create_string_buffer(data, len(data))
    blob_in = _BLOB(len(data), ctypes.cast(src, ctypes.POINTER(ctypes.c_char)))
    blob_out = _BLOB()
    if not ctypes.windll.crypt32.CryptUnprotectData(
            ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)):
        raise OSError("CryptUnprotectData 失败")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(blob_out.pbData)


def _master_key(user_data: str) -> bytes:
    with io.open(os.path.join(user_data, "Local State"), encoding="utf-8") as fh:
        state = json.load(fh)
    enc = base64.b64decode(state["os_crypt"]["encrypted_key"])
    if enc[:5] != b"DPAPI":
        raise OSError("encrypted_key 前缀不是 DPAPI")
    return _dpapi_unprotect(enc[5:])


def _decrypt(key: bytes, blob: bytes) -> str:
    """v10/v11 = AES-256-GCM（3 字节版本 + 12 字节 nonce + 密文 + 16 字节 tag）；老格式 = 纯 DPAPI。"""
    if blob[:3] in (b"v10", b"v11"):
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        return AESGCM(key).decrypt(blob[3:15], blob[15:], None).decode("utf-8", "replace")
    return _dpapi_unprotect(blob).decode("utf-8", "replace")


# ---------------------------------------------------------------- 读库
def _copy_db(src: str) -> str:
    """浏览器关掉后直接复制；还锁着就抛 OSError（err 32 = 还在跑）。"""
    fd, tmp = tempfile.mkstemp(prefix="_ck_", suffix=".db")
    os.close(fd)
    shutil.copy2(src, tmp)
    return tmp


def _profiles(user_data: str) -> list:
    out = []
    if not os.path.isdir(user_data):
        return out
    for name in sorted(os.listdir(user_data)):
        if name == "Default" or name.startswith("Profile "):
            db = os.path.join(user_data, name, "Network", "Cookies")
            if os.path.isfile(db):
                out.append((name, db))
    return out


def _read_cookies(user_data: str) -> tuple:
    """返回 (cookies:list[(host,name,value)], 锁着的库数)。锁着就跳过（等用户关浏览器）。"""
    try:
        key = _master_key(user_data)
    except Exception as exc:  # noqa: BLE001
        return [], 0, f"取密钥失败: {type(exc).__name__}: {exc}"
    found, locked = [], 0
    for _prof, db in _profiles(user_data):
        tmp = ""
        try:
            tmp = _copy_db(db)
        except OSError as exc:
            locked += 1
            continue
        try:
            con = sqlite3.connect(f"file:{tmp}?mode=ro", uri=True)
            like = " OR ".join(["host_key LIKE ?"] * len(DOMAINS))
            rows = con.execute(
                f"SELECT host_key, name, encrypted_value, expires_utc FROM cookies WHERE {like}",
                tuple(f"%{d}%" for d in DOMAINS)).fetchall()
            con.close()
            now_1601 = int((time.time() + 11644473600) * 1_000_000)   # Chromium: 1601 起微秒
            for host, name, blob, exp in rows:
                if exp and exp < now_1601:                            # 已过期
                    continue
                try:
                    val = _decrypt(key, blob)
                except Exception:  # noqa: BLE001
                    continue
                if val:
                    found.append((host, name, val))
        except Exception:  # noqa: BLE001
            pass
        finally:
            if tmp:
                try:
                    os.remove(tmp)
                except OSError:
                    pass
    return found, locked, ""


# ---------------------------------------------------------------- 合并写盘
def _merge(old_line: str, new_line: str) -> str:
    """非破坏性合并：同名项用新的覆盖，**其余一律保留**。

    ★ 照抄 refresh_cookie.py 的同名逻辑（那边注释更详细）：绝不能直接覆写 ——
      那会把用户从 F12 拷进来的原生登录态/其它票据一起冲掉。
    """
    old = {}
    for part in (old_line or "").split(";"):
        part = part.strip()
        if part and "=" in part:
            k, v = part.split("=", 1)
            old[k.strip()] = v.strip()
    new = {}
    for part in (new_line or "").split(";"):
        part = part.strip()
        if part and "=" in part:
            k, v = part.split("=", 1)
            new[k.strip()] = v.strip()
    old.update(new)
    return "; ".join(f"{k}={v}" for k, v in old.items())


def _write_cookie_file(path: str, line: str) -> None:
    """先写临时文件再改名：抓取进程随时可能读它，直接覆写会读到半截。"""
    d = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(d, exist_ok=True)
    tmp = path + ".tmp"
    with io.open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(line)
    os.replace(tmp, path)


def _browser_procs() -> dict:
    """数一下 Edge/Chrome 还剩几个进程。

    ★★★ 2026-10-02（用户实测踩到）：「我把浏览器关了啊」—— 但任务管理器里
      **msedge 还有 7 个、chrome 还有 7 个**。Chromium 关掉所有窗口后**仍会留后台进程**
      （"启动加速" / 后台模式 / 扩展），而 cookie 库就是被它们锁着。
      ⇒ 光报"库被占用"没用，得告诉用户**还剩几个**，他才知道没关干净。
    """
    out = {}
    for exe in ("msedge.exe", "chrome.exe"):
        try:
            r = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {exe}", "/NH"],
                               capture_output=True, text=True, timeout=10,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            out[exe.split(".")[0]] = (r.stdout or "").lower().count(exe)
        except Exception:  # noqa: BLE001
            out[exe.split(".")[0]] = -1
    return out


def grab(cookie_file: str, *, timeout: float = 900.0, poll: float = 3.0,
         need_fresh: bool = True) -> dict:
    """轮询读浏览器 cookie，拿到新鲜 cf_clearance 就写盘。返回结果 dict。"""
    started = time.time()
    deadline = started + max(30.0, timeout)
    old_line = ""
    try:
        with io.open(cookie_file, encoding="utf-8") as fh:
            old_line = fh.read().strip()
    except OSError:
        pass
    old_cf = ""
    m = re.search(r"cf_clearance=([^;\s]+)", old_line)
    if m:
        old_cf = m.group(1)

    announced = set()
    while time.time() < deadline:
        for label, *parts in BROWSERS:
            user_data = os.path.join(os.environ.get("LOCALAPPDATA", ""), *parts, "User Data")
            if not os.path.isdir(user_data):
                continue
            ck, locked, err = _read_cookies(user_data)
            if locked and ("lock", label) not in announced:
                announced.add(("lock", label))
                n = _browser_procs().get(label.lower(), -1)
                extra = (f"—— 任务管理器里 {label} 还剩 **{n} 个进程**，光关窗口不够，"
                         f"要**完全退出**（托盘里也退，或点弹窗里的"
                         f"「帮我强制关掉浏览器」）") if n and n > 0 else "—— 请**完全关掉**"
                print(f"   ⏳ {label} 的 cookie 库还被占用{extra}", file=sys.stderr)
            if err and ("err", label) not in announced:
                announced.add(("err", label))
                print(f"   ⚠️ {label}: {err}", file=sys.stderr)
            if not ck:
                continue
            print(f"   ✅ {label}：读到 {len(ck)} 条 {DOMAINS} 的 cookie"
                  f"（{', '.join(sorted({n for _h, n, _v in ck}))[:120]}）", file=sys.stderr)
            cf = next((v for _h, n, v in ck if n == NEED), "")
            if not cf:
                print(f"   ⚠️ {label} 里没有 {NEED} —— 请先在浏览器里打开站点并点到正文页",
                      file=sys.stderr)
                continue
            if need_fresh and old_cf and cf == old_cf:
                print(f"   ⏳ {label} 的 {NEED} 和现在这份一样（还是旧的）—— 请重新浏览一次",
                      file=sys.stderr)
                continue
            line = _merge(old_line, "; ".join(f"{n}={v}" for _h, n, v in ck))
            _write_cookie_file(cookie_file, line)
            return {"ok": True, "browser": label, "count": len(ck),
                    "cookieFile": cookie_file, "ms": int((time.time() - started) * 1000),
                    "names": sorted({n for _h, n, _v in ck})}
        time.sleep(poll)
    return {"ok": False, "error": f"等了 {timeout:.0f} 秒也没读到浏览器 cookie"
                                  f"（浏览器没关？或没打开过 bilinovel）",
            "ms": int((time.time() - started) * 1000)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="从浏览器本地读 cookie（不走网络，绕开 Cloudflare）")
    ap.add_argument("--cookie-file", required=True, help="写到哪里（与 linovelib.py 用同一个）")
    ap.add_argument("--timeout", type=float, default=900.0, help="轮询多久（默认 900 秒）")
    ap.add_argument("--poll", type=float, default=3.0, help="多久看一次（默认 3 秒）")
    ap.add_argument("--allow-old", action="store_true",
                    help="就算 cf_clearance 和现在这份一样也接受")
    a = ap.parse_args(argv)
    print(f"🔎 开始轮询浏览器 cookie（最多 {a.timeout:.0f} 秒，每 {a.poll:.0f} 秒看一次）…",
          file=sys.stderr)
    try:
        res = grab(a.cookie_file, timeout=a.timeout, poll=a.poll, need_fresh=not a.allow_old)
    except Exception as exc:  # noqa: BLE001
        print(f"❌ 异常: {type(exc).__name__}: {exc}", file=sys.stderr)
        res = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    if res.get("ok"):
        print("@@COOKIE-OK " + json.dumps(res, ensure_ascii=False), flush=True)
        return 0
    print("@@COOKIE-FAIL " + json.dumps(res, ensure_ascii=False), flush=True)
    return 1


if __name__ == "__main__":
    sys.exit(main())
