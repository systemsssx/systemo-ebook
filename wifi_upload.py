#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
WiFi 传书自动上传工具
用法：运行后输入网址，自动上传本脚本同目录下 download 文件夹里的所有文件
上传完成后，等待用户手动关闭浏览器，脚本检测到后自动结束进程。
"""

import os
import time
import sys
import os
import subprocess
import shutil
import io
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import NoSuchElementException, WebDriverException, InvalidSessionIdException

# ★ 兜底方案依赖（仿 main.py 的 handle_folder_dialog）
try:
    import pyautogui
    import win32gui
    import win32con
    from pywinauto import Desktop
    HAS_FALLBACK_DEPS = True
except ImportError as e:
    HAS_FALLBACK_DEPS = False
    print(f"⚠️ 兜底方案依赖缺失: {e}")

# ★ Windows 下 stdout 强制 utf-8（避免 Node 端 utf-8 解码 GBK 失败）
if sys.platform == 'win32':
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')
    except Exception:
        pass

# ================= 配置 =================
# 优先从命令行 --download-dir 读取（Node 端传入）
# 兜底：本脚本同目录的 download 文件夹（开发模式 & 用户未传参时用）
LOCAL_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "download")
for i, arg in enumerate(sys.argv[1:], 1):
    if arg == '--download-dir' and i < len(sys.argv):
        LOCAL_FOLDER = sys.argv[i + 1]
        break
    if arg.startswith('--download-dir='):
        LOCAL_FOLDER = arg.split('=', 1)[1]
        break
print(f"📁 WiFi 上传目录: {LOCAL_FOLDER}")

# ★ 批量上传：每本之间的固定等待（秒）。局域网传小文件够用，可用 --delay 覆盖。
UPLOAD_DELAY = 2.5
for i, arg in enumerate(sys.argv[1:], 1):
    if arg == '--delay' and i < len(sys.argv):
        try:
            UPLOAD_DELAY = float(sys.argv[i + 1])
        except (ValueError, IndexError):
            pass
        break
    if arg.startswith('--delay='):
        try:
            UPLOAD_DELAY = float(arg.split('=', 1)[1])
        except ValueError:
            pass
        break
print(f"⏱️ 每本之后等 {UPLOAD_DELAY:.1f} 秒并主动刷新页面（目标页面不会自己更新）")

# ★ 书库批量传输：由 Node 端传入文件清单 JSON，优先于扫目录
FILE_LIST = None
for i, arg in enumerate(sys.argv[1:], 1):
    if arg == '--file-list' and i < len(sys.argv):
        FILE_LIST = sys.argv[i + 1]
        break
    if arg.startswith('--file-list='):
        FILE_LIST = arg.split('=', 1)[1]
        break
if FILE_LIST:
    print(f"📋 使用指定文件清单: {FILE_LIST}")

# ★★★ 2026-10-05：支持 --url 直接传网址（以前只能靠 input() 交互，主进程写 stdin 一旦时序不对
#   就会卡在提示行；也给手动排查用）。有 --url 时不再询问。
URL_ARG = None
for i, arg in enumerate(sys.argv[1:], 1):
    if arg == '--url' and i < len(sys.argv):
        URL_ARG = sys.argv[i + 1]
        break
    if arg.startswith('--url='):
        URL_ARG = arg.split('=', 1)[1]
        break
if URL_ARG:
    print(f"🔗 网址来自 --url: {URL_ARG}")

# ★ 与 open_auth_page.py 同样的修复：
# 1. 手动找 chromedriver 路径（从 --chromedriver-path 参数 / 环境变量 / 同目录）
# 2. 手动找 Chrome 二进制路径（注册表 / 常见路径 / where chrome）
CHROMEDRIVER_PATH = None
for i, arg in enumerate(sys.argv[1:], 1):
    if arg == '--chromedriver-path' and i < len(sys.argv):
        CHROMEDRIVER_PATH = sys.argv[i + 1]
        break
    if arg.startswith('--chromedriver-path='):
        CHROMEDRIVER_PATH = arg.split('=', 1)[1]
        break

if not CHROMEDRIVER_PATH:
    CHROMEDRIVER_PATH = os.environ.get('CHROMEDRIVER_PATH')

if not CHROMEDRIVER_PATH:
    CHROMEDRIVER_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'chromedriver.exe')

print(f"📁 chromedriver 路径: {CHROMEDRIVER_PATH}")


def find_chrome_binary():
    """手动查找 Chrome 二进制路径（避免 0xC000007B）"""
    candidates = []

    # 1. 注册表
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
            except Exception:
                continue
    except ImportError:
        pass

    # 2. 常见路径
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
        # ★ 同 open_auth_page.py：`where` 输出是系统区域编码（GBK），必须在
        #   text=True 的同时钉死 encoding + errors='replace'，否则 PATH 里的中文路径
        #   会在 _readerthread 里抛 UnicodeDecodeError。
        result = subprocess.run(['where', 'chrome'], capture_output=True, text=True,
                                encoding='utf-8', errors='replace', timeout=3)
        for line in result.stdout.splitlines():
            line = line.strip()
            if line and os.path.exists(line):
                candidates.append(line)
    except Exception:
        pass

    # 4. Edge 兜底
    for p in [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    ]:
        if os.path.exists(p):
            candidates.append(p)

    # 去重
    seen, unique = set(), []
    for c in candidates:
        if c.lower() not in seen:
            seen.add(c.lower())
            unique.append(c)
    return unique


# =========================================

def create_driver():
    """创建 ChromeDriver 实例（参考 main.py 和 open_auth_page.py）"""
    chrome_options = Options()

    # ★ 关键修复：手动指定 Chrome 二进制路径
    chrome_paths = find_chrome_binary()
    if chrome_paths:
        chrome_options.binary_location = chrome_paths[0]
        print(f"📁 Chrome 二进制: {chrome_paths[0]}")
    else:
        print("⚠️ 没找到 Chrome 二进制路径，尝试用默认设置")

    chrome_options.add_argument('--disable-gpu')
    chrome_options.add_argument('--no-sandbox')
    chrome_options.add_argument('--window-size=1280,900')
    chrome_options.add_argument('--disable-dev-shm-usage')
    chrome_options.add_argument('--disable-blink-features=AutomationControlled')
    chrome_options.add_experimental_option('excludeSwitches', ['enable-automation'])
    chrome_options.add_experimental_option('useAutomationExtension', False)
    chrome_options.add_argument('--disable-features=VizDisplayCompositor')
    chrome_options.add_argument('--disable-web-security')
    chrome_options.add_argument('user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36')
    chrome_options.page_load_strategy = 'eager'

    # ★ 关键修复：显式指定 chromedriver 路径 + CREATE_NO_WINDOW 标志
    service = Service(CHROMEDRIVER_PATH)
    service.creation_flags = 0x08000000
    driver = webdriver.Chrome(service=service, options=chrome_options)

    # 隐藏自动化特征
    driver.execute_cdp_cmd('Page.addScriptToEvaluateOnNewDocument', {
        'source': '''
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            window.chrome = { runtime: {} };
        '''
    })
    return driver

def find_upload_element(driver):
    """查找上传输入框"""
    try:
        upload_input = driver.find_element(By.CSS_SELECTOR, 'input[type="file"]')
        return upload_input
    except NoSuchElementException:
        pass

    inputs = driver.find_elements(By.TAG_NAME, 'input')
    for inp in inputs:
        if inp.get_attribute('type') == 'file':
            return inp

    print("⚠️ 未找到上传输入框，请检查页面是否有 '点击上传' 或 '拖拽上传' 区域。")
    try:
        drop_zone = driver.find_element(By.CSS_SELECTOR, '[class*="drop"], [class*="upload"], [class*="drag"]')
        drop_zone.click()
        print("✅ 已点击拖拽区域，请在弹出的文件选择器中手动选择文件。")
    except:
        pass
    return None

def refresh_page(driver, delay, timeout=15):
    """等 delay 秒 → 主动刷新页面 → 等上传框重新出现。

    目标页面不会自己更新，必须靠刷新才能看到已上传列表、重置上传表单。

    返回刷新后重新找到的 input[type=file]；失败返回 None。
    """
    print(f"⏳ 等待 {delay:.1f} 秒…")
    time.sleep(delay)

    try:
        driver.refresh()
        print("   ↻ 已刷新页面")
    except Exception as e:
        print(f"   ⚠️ 刷新失败（浏览器可能已关闭）：{e}")
        return None

    # 等 DOM 重建 + 上传框回来
    started = time.time()
    while time.time() - started < timeout:
        try:
            el = driver.find_element(By.CSS_SELECTOR, 'input[type="file"]')
            if el:
                print(f"   ✓ 上传框已就绪（{time.time() - started:.1f}s）")
                return el
        except NoSuchElementException:
            pass
        except Exception:
            pass
        time.sleep(0.3)

    print(f"   ⚠️ 刷新后 {timeout} 秒内没等到上传框")
    return None


def upload_all_files(driver, folder_path):
    """批量上传：把文件夹里所有 .epub 一本一本连着传。

    目标页面不会自己更新已上传列表，所以节奏是：

        发第 1 本 → 等 UPLOAD_DELAY 秒 → 主动刷新页面 → 等上传框回来
                  → 发第 2 本 → …… 循环

    三个要点：
      · 刷新后必须重新查找 input[type=file]（旧元素已随 DOM 销毁）
      · 刷新失败或上传框不回来就中止，不硬撑
      · 某本 send_keys 失败只记一笔，继续下一本
    """
    # ★ 优先用 Node 端传来的文件清单（书库批量传输），否则扫目录
    listed = load_file_list(FILE_LIST) if FILE_LIST else None
    if listed is not None:
        if not listed:
            print("⚠️ 清单里没有可上传的 .epub 文件。")
            return False
        files = listed
        print(f"📚 按书库勾选，共 {len(files)} 本")
    else:
        if not os.path.isdir(folder_path):
            print(f"❌ 文件夹不存在: {folder_path}")
            return False
        files = sorted(
            os.path.join(folder_path, f) for f in os.listdir(folder_path)
            if os.path.isfile(os.path.join(folder_path, f))
            and f.lower().endswith('.epub')
        )
        if not files:
            print("⚠️ 文件夹内没有 .epub 文件可上传。")
            return False
        print(f"📚 扫描目录，共 {len(files)} 本")

    total = len(files)

    ok = 0
    for idx, file_path in enumerate(files, 1):
        name = os.path.basename(file_path)
        print(f"\n[{idx}/{total}] 📂 {name}")

        # 每轮重新找（最多等 10 秒）
        el = None
        for _ in range(20):
            try:
                el = driver.find_element(By.CSS_SELECTOR, 'input[type="file"]')
                if el:
                    break
            except NoSuchElementException:
                el = None
            except Exception:
                el = None
            time.sleep(0.5)

        if el is None:
            print(f"❌ [{idx}/{total}] 找不到上传框，中止（本次已提交 {ok} 本）")
            break

        try:
            el.send_keys(file_path)
            ok += 1
            print(f"✅ [{idx}/{total}] 已提交")
        except Exception as e:
            print(f"❌ [{idx}/{total}] 提交失败：{e}")
            continue

        if idx < total:
            # ★ 按需求：每传完一本，隔 UPLOAD_DELAY 秒主动刷新一次页面
            if refresh_page(driver, UPLOAD_DELAY) is None:
                print(f"❌ 刷新后无法继续，中止（本次已提交 {ok} 本）")
                break

    print(f"\n📦 批量结束：成功提交 {ok}/{total} 本")
    if ok < total:
        print("⚠️ 有未提交成功的，请在网页上核对")
    else:
        print("💡 请在网页上确认每一本都已收到")
    return ok > 0

def wait_for_browser_close(driver):
    """检测浏览器是否被手动关闭，参考 open_auth_page.py"""
    print("📌 上传完成，你可以手动关闭浏览器窗口，脚本将自动结束进程。")
    while True:
        try:
            # 尝试获取当前URL，如果浏览器已关闭会抛出异常
            driver.current_url
            time.sleep(2)  # 每2秒检测一次
        except (WebDriverException, InvalidSessionIdException):
            print("🔄 检测到浏览器已关闭。")
            break
        except Exception as e:
            print(f"⚠️ 检测过程中出现异常: {e}")
            time.sleep(2)

def load_file_list(json_path):
    """读书库传来的文件清单。返回存在的 .epub 绝对路径列表。"""
    import json as _json
    if not json_path or not os.path.isfile(json_path):
        return None
    try:
        with open(json_path, 'r', encoding='utf-8') as f:
            items = _json.load(f)
        out = []
        for p in items:
            if os.path.isfile(p) and p.lower().endswith('.epub'):
                out.append(p)
            else:
                print(f"⚠️ 跳过（不存在或不是 epub）: {p}")
        return out
    except Exception as e:
        print(f"⚠️ 读取文件清单失败：{e}")
        return None


def collect_epubs(folder_path):
    """兜底方案用：只返回 .epub 的绝对路径列表。

    ⚠️ 旧版叫 keep_only_epub，会 os.remove 掉目录里所有非 epub 文件 ——
       那是破坏性行为（用户下载目录里的 txt 会被删掉），已去掉。
       现在只"挑出 epub"，不动任何文件。
    """
    if not os.path.isdir(folder_path):
        return []
    return sorted(
        os.path.join(folder_path, f)
        for f in os.listdir(folder_path)
        if os.path.isfile(os.path.join(folder_path, f))
        and f.lower().endswith('.epub')
    )


def set_clipboard_text(text):
    """写入剪贴板（仿 main.py）"""
    try:
        import win32clipboard
        win32clipboard.OpenClipboard()
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardText(text, win32con.CF_UNICODETEXT)
        win32clipboard.CloseClipboard()
    except Exception as e:
        print(f"⚠️ 设置剪贴板失败: {e}")


def handle_folder_dialog_fallback(folder_path, timeout=10):
    """兜底方案：复用 main.py 的 handle_folder_dialog 逻辑
    调窗口位置/大小，再纯键盘填充，最后用坐标点击（无停顿）"""
    if not HAS_FALLBACK_DEPS:
        print("❌ 兜底方案依赖缺失，无法继续")
        return False
    print("⏳ 兜底方案：等待文件夹选择窗口出现...")
    try:
        dlg = Desktop(backend="win32").window(
            title_re=".*选择保存目录.*|.*选择文件夹.*|.*Select Folder.*|.*打开.*"
        )
        dlg.wait('visible', timeout=timeout)
        print(f"✅ 找到窗口: '{dlg.window_text()}'")
        # ★★ 调窗口位置到左上角 700x700（仿 main.py）★★
        dlg.move_window(x=0, y=0, width=700, height=700)
        print("✅ 窗口已调整至 (0,0)，尺寸 700x700")
    except Exception as e:
        print(f"❌ pywinauto 找不到窗口，尝试备用坐标 (650, 760): {e}")
        time.sleep(1)

    # 1. 聚焦地址栏
    pyautogui.hotkey('ctrl', 'l')
    # 2. 粘贴路径
    set_clipboard_text(folder_path)
    print(f"📋 已复制路径: {folder_path}")
    pyautogui.hotkey('ctrl', 'v')
    print("✅ 已粘贴路径")
    # 3. 回车进入路径
    pyautogui.press('enter')
    print("✅ 已发送 Enter")
    # 4. 单击 (506, 661) "选择"按钮（按 720p 缩 ×7/9 ≈ 0.778，再下移 70）
    pyautogui.click(506, 661)
    print("✅ 已单击 (506, 661)")
    return True


def fallback_upload(driver, url, folder_path):
    """兜底方案：点上传按钮 + 文件对话框"""
    print("⚠️ 主流方案未找到 input[type=file]，切换到兜底方案")
    try:
        # 1. 清理 download/，只留 epub
        collect_epubs(folder_path)

        # 2. 找"上传/选择文件"按钮
        candidates_xpath = [
            "//button[contains(translate(., 'UPLOAD选择上传导入文件导入'), '上传')]",
            "//button[contains(translate(., 'UPLOAD'), 'UPLOAD')]",
            "//input[@type='button'][@value]",
            "//a[contains(translate(., 'UPLOAD选择上传导入文件'), '上传')]",
        ]
        upload_btn = None
        for xp in candidates_xpath:
            try:
                btns = driver.find_elements(By.XPATH, xp)
                for b in btns:
                    if b.is_displayed() and b.is_enabled():
                        upload_btn = b
                        print(f"✅ 找到上传按钮: '{b.text.strip()}'")
                        break
                if upload_btn:
                    break
            except:
                continue
        if not upload_btn:
            print("❌ 兜底方案：找不到'上传'按钮")
            return False

        # 3. 点击按钮（触发系统文件选择对话框）
        upload_btn.click()
        print("✅ 已点击上传按钮，等待文件选择对话框...")

        # 4. 调窗口 + 键盘 + 鼠标（仿 main.py handle_folder_dialog）
        time.sleep(1)
        return handle_folder_dialog_fallback(folder_path, timeout=10)
    except Exception as e:
        print(f"❌ 兜底方案失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def wait_for_upload_input(driver, folder_path):
    """主流方案：永久等待 input[type=file]"""
    print(f"⏳ 主流方案：永久等待 input[type=file]...")
    while True:
        try:
            upload_input = driver.find_element(By.CSS_SELECTOR, 'input[type="file"]')
            if upload_input:
                print("✅ 找到 input[type=file]，走主流方案")
                ok = upload_all_files(driver, folder_path)
                return True if ok else None
        except NoSuchElementException:
            pass
        time.sleep(0.5)


def main():
    print("=" * 60)
    print("WiFi 传书自动上传工具")
    print("=" * 60)

    url = (URL_ARG or "").strip()
    if not url:
        try:
            url = input("请输入 WiFi 传书网址: ").strip()
        except EOFError:
            print("❌ 没有收到网址（既没有 --url，stdin 也断了）。请带 --url <网址> 再跑一次。")
            sys.exit(2)
    if not url:
        print("❌ 网址不能为空。")
        sys.exit(1)

    if not url.startswith(('http://', 'https://')):
        url = 'http://' + url

    print("🚀 正在启动浏览器...")
    driver = None
    try:
        driver = create_driver()
        print(f"🌐 正在访问: {url}")
        driver.get(url)

        # 等待页面加载完成
        time.sleep(3)

        # ★★★ 主流方案（永久等待）—— 找到 input[type=file] 就走，找不到就一直等 ★★★
        result = wait_for_upload_input(driver, LOCAL_FOLDER)
        success = result if result is not None else False

        if success:
            print("🎉 上传流程完成，请检查网页是否提示成功。")
        else:
            print("⚠️ 上传未成功，请检查浏览器中的页面状态。")

        # 等待用户手动关闭浏览器，检测到后自动退出
        wait_for_browser_close(driver)

    except Exception as e:
        print(f"❌ 发生错误: {e}")
        import traceback
        traceback.print_exc()
    finally:
        if driver:
            print("🔄 正在清理浏览器进程...")
            try:
                driver.quit()
            except:
                pass
            print("✅ 浏览器进程已清理。")

if __name__ == "__main__":
    main()