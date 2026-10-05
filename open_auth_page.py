#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
脚本一：使用 ChromeDriver 自动打开百度授权页面。

★★ 2026-09-23 起：**只负责打开浏览器**，不再自动抓码、不再写 `auth_code.txt`。
   浏览器页面会显示授权码，用户自己复制 → 粘到授权窗口的输入框 → 点「提交授权」。
   （抓码代码还在文件里，但由 `GRAB_CODE_ENABLED = False` 这个总开关关掉，
     为 False 时绝不写任何文件、也绝不告诉界面「已交给授权窗口」。）

为什么撤掉自动抓码：授权码只有 10 分钟有效期、而且**只能用一次**。抓码只能靠
页面正则猜，可能抓到早就过期/被用过的串；一旦抓错，用户看到的就是
「我什么都没干它就报授权失败」，而且真正的失败原因（百度回的 invalid_grant）
被这层自动化掩盖了，无法归因。手动粘贴慢一点，但每一步都看得见。
"""

import re
import shutil
import tempfile
import time
import sys
import os
import subprocess
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.common.exceptions import WebDriverException, InvalidSessionIdException

# --- 解析 chromedriver 路径 ---
# ★ 修复空格路径 bug：优先从 sys.argv 读取 --chromedriver-path 参数
# 回退到环境变量 CHROMEDRIVER_PATH
# 最后回退到本脚本同目录
CHROME_DRIVER_PATH = None
for i, arg in enumerate(sys.argv[1:], 1):
    # ★ 修复 BUG：原来是 `i < len(sys.argv)` + 取 `sys.argv[i + 1]`，差一位。
    #   传 `--chromedriver-path` 而不带值时，i == len(sys.argv)-1 通过判断，
    #   但 sys.argv[i+1] 越界 → IndexError，整个脚本还没开始就崩了。
    if arg == '--chromedriver-path' and i + 1 < len(sys.argv):
        CHROME_DRIVER_PATH = sys.argv[i + 1]
        break
    if arg.startswith('--chromedriver-path='):
        CHROME_DRIVER_PATH = arg.split('=', 1)[1]
        break

if not CHROME_DRIVER_PATH:
    CHROME_DRIVER_PATH = os.environ.get('CHROMEDRIVER_PATH')

if not CHROME_DRIVER_PATH:
    CHROME_DRIVER_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'chromedriver.exe')

print(f"📁 chromedriver 路径: {CHROME_DRIVER_PATH}")


def find_chrome_binary():
    """
    ★ 修复 0xC000007B bug：手动查找 Chrome 二进制路径
    Selenium 不会自动找 Chrome，必须显式指定 binary_location
    """
    candidates = []

    # 1. 注册表查找（最常见）
    try:
        import winreg
        for reg_path in [
            r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe",
            r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe",
        ]:
            try:
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, reg_path) as key:
                    path, _ = winreg.QueryValueEx(key, "")
                    if path and os.path.exists(path):
                        candidates.append(path)
            except FileNotFoundError:
                continue
            except Exception:
                continue
    except ImportError:
        pass

    # 2. 常见安装路径
    common_paths = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%PROGRAMFILES%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%PROGRAMFILES(X86)%\Google\Chrome\Application\chrome.exe"),
        r"D:\Program Files\Google\Chrome\Application\chrome.exe",
        r"D:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    ]
    for p in common_paths:
        if p and os.path.exists(p):
            candidates.append(p)

    # 3. 用 where 命令查 PATH
    try:
        # ★ 必须钉死编码 + 容错：`where` 的输出是**系统区域编码**（本机 GBK），
        #   而 Python 的 text=True 在 UTF-8 模式（PYTHONUTF8/PYTHONIOENCODING）
        #   下按 UTF-8 严格解码 → 只要 PATH 里有一个中文路径，就会在
        #   _readerthread 里抛 UnicodeDecodeError: 'utf-8' codec can't decode byte 0xd0 …
        #   （实测报错文本正是「未知错误」这类中文被 GBK 编码出的 0xd0 开头字节）。
        #   同时开 errors='replace'：解不出来的字节替换掉即可，这里只要路径行，
        #   绝不能因为一个字节把整个授权流程带崩。
        result = subprocess.run(['where', 'chrome'], capture_output=True, text=True,
                                encoding='utf-8', errors='replace', timeout=3)
        for line in result.stdout.splitlines():
            line = line.strip()
            if line and os.path.exists(line):
                candidates.append(line)
    except Exception:
        pass

    # 4. Edge（最后兜底）
    edge_paths = [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    ]
    for p in edge_paths:
        if os.path.exists(p):
            candidates.append(p)

    # 去重保第一个
    seen = set()
    unique = []
    for c in candidates:
        if c.lower() not in seen:
            seen.add(c.lower())
            unique.append(c)
    return unique


# 固定的百度 OAuth 授权链接
AUTH_URL = "https://openapi.baidu.com/oauth/2.0/authorize?client_id=q8WE4EpCsau1oS0MplgMKNBn&response_type=code&redirect_uri=oob&scope=basic+netdisk"

# ★★★ 2026-09-23：**自动抓码已整个关掉**（用户要求）。
#   历史：这里会把页面上正则命中的串写进 auth_code.txt，Electron 侧轮询读取后
#   自动填进输入框并自动提交。问题：页面上的码可能早就过期/被用过，用户看到的是
#   「我什么都没干它就报授权失败」，还会掩盖真正的原因（invalid_grant 无法归因）。
#   现在改成纯手动：浏览器留着，用户自己复制页面上的授权码，粘进输入框提交。
#   代码保留在下面只为将来要恢复时有个参照，开关为 False 时**不写任何文件**。
GRAB_CODE_ENABLED = False

# 抓到的授权码写在这里，Electron 侧轮询读取（读完即删，保证只用一次）
CODE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'auth_code.txt')

# 从页面里认授权码：Baidu 给的是 32 位十六进制。
# 分两轮：
#   1) 带上下文的模式（最准）：授权码/authorization code/code= 后面那一串
#   2) 裸 32 位 hex 兜底：页面把码单独放在 <code>/<span> 里、前后没有关键字时用
#      （只认严格的 32 位 hex，所以不会误抓 client_id 之类）
CODE_PATTERNS = [
    r'授权码[^A-Za-z0-9]{1,16}?([A-Za-z0-9]{16,64})',
    r'authorization\s*code[^A-Za-z0-9]{1,16}?([A-Za-z0-9]{16,64})',
    # (?<![A-Za-z0-9_]) 是为了不让 client_id=... 里的 "id=" 被当成 code=
    r'(?<![A-Za-z0-9_])code\s*[=:]\s*["\']?([A-Za-z0-9]{16,64})',
]
BARE_CODE_PATTERN = r'(?<![0-9a-fA-F])([0-9a-fA-F]{32})(?![0-9a-fA-F])'


def extract_code(text):
    """从页面源码/URL 里抽出授权码；抽不到返回 None。"""
    if not text:
        return None
    for pat in CODE_PATTERNS:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            code = m.group(1)
            # 排掉明显的非授权码（比如 client_id 本身、scope 里的词）
            if code.lower() in ('basicnetdisk', 'oob'):
                continue
            # 后面跟着 & 的是 URL 参数名而不是值的情况也排掉
            return code
    m = re.search(BARE_CODE_PATTERN, text)
    return m.group(1) if m else None


def save_code(code):
    """把授权码写给 Electron 侧（UTF-8，覆盖写）。

    ★ 自动抓码已关掉（GRAB_CODE_ENABLED=False）—— 直接返回 False，不落地任何文件。
    """
    if not GRAB_CODE_ENABLED:
        return False
    try:
        with open(CODE_FILE, 'w', encoding='utf-8') as f:
            f.write(code)
        return True
    except Exception as e:
        print(f"⚠️ 授权码已抓到但写文件失败：{e}")
        return False


def open_browser():
    """启动 Chrome 并打开授权页面，等待浏览器被手动关闭"""
    driver = None
    # 供 except 分支/清理分支安全引用（原来只在这里赋值，异常时是未绑定名）
    profile_dir = None
    keep_profile = False
    try:
        if not os.path.exists(CHROME_DRIVER_PATH):
            print(f"❌ chromedriver.exe 不存在: {CHROME_DRIVER_PATH}")
            print("   → 浏览器无法启动。请确认 chromedriver.exe 与程序主体在同一目录。")
            # ★ 修复：原来直接 return（退出码 0），Electron 侧拿到 error=null，
            #   判定为「授权流程正常结束」，把失败静默吞掉。
            sys.exit(2)

        # ★ 关键修复：手动指定 Chrome 二进制路径，避免 0xC000007B
        chrome_options = Options()
        chrome_paths = find_chrome_binary()
        if chrome_paths:
            chrome_options.binary_location = chrome_paths[0]
            print(f"📁 Chrome 二进制: {chrome_paths[0]}")
        else:
            print("⚠️ 没找到 Chrome 二进制路径，尝试用默认设置")

        chrome_options.add_argument('--disable-gpu')
        chrome_options.add_argument('--no-sandbox')
        chrome_options.add_argument('--disable-dev-shm-usage')
        chrome_options.add_argument('--disable-blink-features=AutomationControlled')
        # ★ 每次授权都用一个全新的空配置目录（用户要求：「这个 chrome 页面有记忆，
        #   记住我昨天的登录信息，能不能变成一张白纸」）。
        #   Chrome 没有「启动时清掉已有 profile」的开关，唯一办法就是给它一个
        #   新建的空目录 —— 这样没有 Cookie、没有登录态、没有历史记录，
        #   打开授权页就是干净的登录页，用户可以登另一个百度账号。
        #   用完（脚本退出时）整个目录删掉，不留任何账号痕迹。
        #   想保留现场排查时加 --keep-profile。
        keep_profile = '--keep-profile' in sys.argv
        if keep_profile:
            profile_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.chrome_auth_profile')
            os.makedirs(profile_dir, exist_ok=True)
            print("📌 --keep-profile：复用已有的 .chrome_auth_profile（会带上上次的登录态）")
        else:
            # 顺手把历史遗留的那个记着登录信息的老配置目录删掉
            try:
                legacy = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.chrome_auth_profile')
                if os.path.isdir(legacy):
                    shutil.rmtree(legacy, ignore_errors=True)
                    print("🧹 已删除旧的授权用 Chrome 配置目录（里面有上次的登录信息）")
            except Exception as e:
                print(f"⚠️ 删除旧配置目录失败（忽略）: {e}")
            try:
                profile_dir = tempfile.mkdtemp(prefix='easypub_auth_')
                print("🆕 使用全新的空浏览器配置（没有登录信息，等同于一台干净电脑）")
            except Exception as e:
                print(f"⚠️ 无法创建临时配置目录（忽略，用默认配置）: {e}")
                profile_dir = None
        if profile_dir:
            chrome_options.add_argument(f'--user-data-dir={profile_dir}')
        chrome_options.add_experimental_option('excludeSwitches', ['enable-automation'])
        chrome_options.add_experimental_option('useAutomationExtension', False)

        # ★ 2026-09-23 新增：打开网络层日志。
        #   理由：授权码原本只靠"用正则从页面 HTML 里抠"来拿，抠错了完全没有痕迹 ——
        #   用户只看到一句「invalid code, expired or revoked」，谁也不知道那个码是
        #   从哪段文字里出来的、还是页面根本没给出真正的码。
        #   Network.* 事件会带上真实的请求 URL，Baidu 的 OAuth 回调里如果带 code
        #   就能原样看到。它同时也让我能打印「这个码是从页面 HTML 还是网络层来的」。
        chrome_options.set_capability('goog:loggingPrefs', {'performance': 'ALL'})

        # ★ 关键修复（2026-09-23，这条是真凶之一）：chromedriver 的 stderr 是
        #   **系统区域编码（这台机器是 GBK）**，而 Python 3 的 subprocess 按 UTF-8
        #   解码它 → 在 _readerthread 里抛
        #       UnicodeDecodeError: 'utf-8' codec can't decode byte 0xd0 …
        #   （实测报错文本就是中文「未知错误」之类被 GBK 编码出来的 0xd0 开头字节）
        #   结果：浏览器刚起来，授权进程就整个崩掉，退出码非 0，界面显示
        #   「Authorization process error」。旧注释以为 creation_flags 就能
        #   「不读输出」，其实子进程的 stdout/stderr 仍是 PIPE、仍会被解码。
        #   修法：把这两个流指向 DEVNULL —— 没有任何字节需要解码，就不会崩。
        class _QuietService(Service):
            def start(self):
                self.creation_flags = 0x08000000  # CREATE_NO_WINDOW
                self.start_error_message = (
                    'chromedriver 启动失败。请确认 chromedriver.exe 与 Chrome 版本匹配：' + str(CHROME_DRIVER_PATH)
                )
                self._terminate = None
                super(Service, self).start()
                self.process.stdout = open(os.devnull, 'rb')
                self.process.stderr = open(os.devnull, 'wb')
                # 让 Selenium 的收尾逻辑认为不需要再 terminate
                self._terminate = None

        service = _QuietService(CHROME_DRIVER_PATH)
        service.creation_flags = 0x08000000  # CREATE_NO_WINDOW
        # 创建 driver 时不 capture stdout（避免读 chromedriver 输出触发解码错误）
        driver = webdriver.Chrome(service=service, options=chrome_options)
        driver.get(AUTH_URL)
        print("✅ 浏览器已自动打开，请完成登录并授权。")
        print("🔎 授权成功后页面会显示一串【授权码】，请把它复制下来，")
        print("   粘回 EasyPub 的授权窗口后点「提交」。（授权码 10 分钟内有效，且只能用一次；")
        print("   复制完请尽快提交，不要把同一个码重复提交。）")
        print("📌 完成后可以关掉浏览器；关掉浏览器本脚本就会退出。")

        # ★ 轮询：① 检测浏览器关闭 ② 顺手把出现的授权码抓走
        #
        #   ★ 2026-09-23 从"瞎抓"改成"看见什么就说什么"：
        #     以前这里只在抓到码时打一行字，抓错了（或抓到旧码）完全没有痕迹，
        #     用户只能看到最后一句「invalid code, expired or revoked」，
        #     连"码是从哪个页面、哪段文字里抠出来的"都不知道。现在：
        #       · 每次 URL 变化都打印出来（能看到到底走到哪一步）
        #       · 抓到时打印「来源（URL/HTML）+ 模式编号 + 打码的码 + 页面地址」
        #     这样下一次失败时，日志本身就能说明问题。
        code_saved = False
        deadline = time.time() + 15 * 60
        last_url = ''
        _last_got = ''
        net_code = ''   # 网络层（Network.* 事件）里看到的授权码，最可信
        while True:
            # ★ 先消化网络层日志：OAuth 回调/接口请求里的 code 参数是原始值，
            #   不会像页面 HTML 那样被排版、转义、截断。查到就优先用它。
            try:
                for entry in driver.get_log('performance'):
                    msg = entry.get('message') or ''
                    if 'code=' not in msg:
                        continue
                    for m in re.finditer(r'code=([A-Za-z0-9]{16,64})', msg):
                        cand = m.group(1)
                        if cand.lower() in ('basicnetdisk', 'oob'):
                            continue
                        if cand != net_code:
                            net_code = cand
                            masked = cand[:6] + '…' + cand[-4:]
                            print(f"🛰️ 网络层看到一个授权码：{masked}（共 {len(cand)} 位）")
            except Exception:
                pass
            try:
                cur_url = driver.current_url or ''
                if cur_url != last_url:
                    print(f"🌐 页面地址变为：{cur_url[:160]}")
                    last_url = cur_url
                src = ''
                try:
                    src = driver.page_source or ''
                except Exception:
                    src = ''
                got = None
                origin = ''
                pat_no = 0
                # ① 最可信：网络层看到的 code 参数
                if net_code:
                    got, origin, pat_no = net_code, '网络层', 0
                # ② 其次：页面 HTML 里带上下文的模式
                if not got:
                    for i, pat in enumerate(CODE_PATTERNS, 1):
                        m = re.search(pat, src, re.IGNORECASE)
                        if m:
                            got, origin, pat_no = m.group(1), 'HTML', i
                            break
                # ③ 再次：页面里孤零零的 32 位 hex
                if not got:
                    m = re.search(BARE_CODE_PATTERN, src)
                    if m:
                        got, origin, pat_no = m.group(1), 'HTML(裸32位)', 4
                # ④ 最后：地址栏 URL
                if not got:
                    for i, pat in enumerate(CODE_PATTERNS, 1):
                        m = re.search(pat, cur_url, re.IGNORECASE)
                        if m:
                            got, origin, pat_no = m.group(1), 'URL', i
                            break
                if got:
                    masked = got[:6] + '…' + got[-4:] if len(got) > 12 else got
                    if not GRAB_CODE_ENABLED:
                        # ★ 自动抓码已关：**绝不写文件、绝不告诉界面「已交给授权窗口」**，
                        #   只在日志里把页码/位置报出来，方便用户对着浏览器找那个码。
                        if not code_saved:
                            code_saved = True   # 只提示一次，别刷屏
                            print(f"🎯 页面上有一个候选授权码：{masked}（共 {len(got)} 位，"
                                  f"来源={origin}，页面={cur_url[:90]}）")
                            print("   ⚠️ 自动抓码已关闭 —— 请你自己在浏览器里复制授权码，")
                            print("      粘到授权窗口的输入框，再点「提交授权」。")
                    elif not code_saved:
                        print(f"🎯 抓到一个候选授权码：{masked}（共 {len(got)} 位，"
                              f"来源={origin}，正则模式={pat_no}，页面={cur_url[:90]}）")
                        _last_got = got
                        if save_code(got):
                            code_saved = True
                            print("   已交给授权窗口，正在自动提交…")
                            print(f"   服务器时间：{time.strftime('%H:%M:%S')}"
                                  f"（授权码 10 分钟内有效，过期就得重新拿）")
                        else:
                            print("   ⚠️ 抓到了但保存失败，请手动复制粘贴。")
                    elif got != _last_got:
                        # 已经交过了：如果之后又出现**不同**的码，说明前面那个
                        # 可能是页面上的野字符串 —— 记下来，方便事后判断。
                        print(f"ℹ️ 页面上又出现另一个候选码：{masked}"
                              f"（来源={origin}，模式={pat_no}）—— 前一个可能抓错了")
                time.sleep(1.5)
            except (WebDriverException, InvalidSessionIdException):
                print("🔄 检测到浏览器已关闭。")
                break
            except Exception as e:
                print(f"⚠️ 检测过程中出现异常: {e}")
                time.sleep(1.5)
            if time.time() > deadline:
                print("⏰ 等待超过 15 分钟，脚本退出（浏览器仍在运行）。")
                break

        try:
            driver.quit()
        except:
            pass
        # ★ 白纸策略：临时配置目录里有这次登录产生的 Cookie / 历史记录，用完即删。
        if profile_dir and not keep_profile:
            try:
                shutil.rmtree(profile_dir, ignore_errors=True)
                print(f"🧹 已清理临时浏览器配置（登录信息不留档）：{profile_dir}")
            except Exception:
                pass
        print("✅ 浏览器已关闭，脚本退出。")

    except Exception as e:
        import traceback
        print(f"❌ 启动浏览器失败: {e}")
        print(f"   异常类型: {type(e).__name__}")
        print(f"   完整堆栈:")
        traceback.print_exc()
        print()
        print("排查建议:")
        print(f"  1. chromedriver.exe 路径: {CHROME_DRIVER_PATH}")
        print(f"  2. 该文件存在: {os.path.exists(CHROME_DRIVER_PATH)}")
        if os.path.exists(CHROME_DRIVER_PATH):
            print(f"  3. 文件大小: {os.path.getsize(CHROME_DRIVER_PATH)} 字节")
        print("  4. Chrome 浏览器是否已安装")
        print("  5. chromedriver 版本与 Chrome 版本是否匹配")
        # ★ 修复：原来无条件 input() 等回车，而 Electron 侧 execFile 不给 stdin 也不关闭
        #   → 失败路径永久阻塞。非交互时直接退出，让上面的诊断信息随 stdout 回传。
        if sys.stdin and sys.stdin.isatty():
            input("\n按回车退出...")
        else:
            print("\n（非交互运行，直接退出）")
        # ★ 修复：浏览器压根没起来（driver 未创建）时用非零退出码，
        #   否则 Electron 侧把这次失败当成「用户关掉了浏览器」的正常结束。
        sys.exit(3)

if __name__ == "__main__":
    open_browser()