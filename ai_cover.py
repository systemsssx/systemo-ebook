#!/usr/bin/env python
# -*- coding: utf-8 -*-

import sys
import io
import json
import os
import base64
import time
import re
import random
import ssl
import urllib.parse
import urllib.request

if sys.platform == 'win32':
    sys.stdin = io.TextIOWrapper(sys.stdin.buffer, encoding='utf-8')
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

# ================================================================
# ★ 2026-09-29：DeepSeek 全套已删除。
#   原本这里是 _get_api_key() / __clean_ai_answer() / call_deepseek()，
#   用于“联网搜封面”时判断书籍类型（起点/番茄/晋江/轻小说）。
#   现在搜作者/搜封面由 novelmeta（meta_lookup.py）完成；
#   本脚本只保留 Selenium 兜底搜封面，类型直接由 --platform 传入。
#   ★ 不许恢复。
# ================================================================
# ================================================================
# ★★★ 类型判断 - 纯 AI + 上下文 + 关键词兜底 ★★★
# ================================================================

def guess_book_type_2step(title, author=''):
    """书籍类型判断（★ 2026-09-29：AI 判断已删除，只剩关键词兜底）。

    原实现先调 DeepSeek 判类型、失败再关键词兜底；DeepSeek 下线后只按关键词判断。
    ★ 调用方应当优先直接传 --platform（novelmeta 已经知道这本书来自哪个源），
      本函数只是最后的兜底。
    """
    print(f'📌 判断书籍类型（关键词兜底）: {title} (作者: {author})', file=sys.stderr)

    # ================================================================
    # ★★★ 关键词兜底（现在唯一的判断方式）★★★
    # ================================================================

    # 作者关键词兜底
    if author:
        author_lower = author.lower()
        qidian_authors = ['柳岸花又明', '爱潜水的乌贼', '天蚕土豆', '辰东', '唐家三少', '我吃西红柿', '猫腻']
        jj_authors = ['宋昭', '墨香铜臭', 'priest', '巫哲', '木苏里']
        zj_authors = ['东周公子南', '杀虫队队员']
        light_authors = ['结城弘', '雨森焚火', '川原砾', '渡航', '伏见司', '橘公司']
        
        for a in qidian_authors:
            if a in author:
                print(f'📖 作者兜底: {author} → 起点', file=sys.stderr)
                return 'web_novel_qidian'
        for a in jj_authors:
            if a in author:
                print(f'📖 作者兜底: {author} → 晋江', file=sys.stderr)
                return 'web_novel_jj'
        for a in zj_authors:
            if a in author:
                print(f'📖 作者兜底: {author} → 番茄', file=sys.stderr)
                return 'web_novel_zj'
        for a in light_authors:
            if a in author:
                print(f'📖 作者兜底: {author} → 轻小说', file=sys.stderr)
                return 'light_novel'

    # 书名关键词兜底
    title_lower = title.lower()
    
    # 晋江关键词
    jj_keywords = ['风月债', '纯爱', '耽美', '言情', '女频']
    for kw in jj_keywords:
        if kw in title:
            print(f'📖 书名兜底: {kw} → 晋江', file=sys.stderr)
            return 'web_novel_jj'

    # 起点关键词
    qidian_keywords = ['重生', '仙', '帝', '神', '魔', '武', '都市', '校花', '总裁', '兵王', '战神', '龙王', '赘婿', '系统', '穿越', '修仙', '玄幻']
    for kw in qidian_keywords:
        if kw in title:
            print(f'📖 书名兜底: {kw} → 起点', file=sys.stderr)
            return 'web_novel_qidian'

    # 番茄关键词
    zj_keywords = ['悬疑', '无限流', '脑洞', '惊悚', '恐怖', '灵异', '末日', '废土']
    for kw in zj_keywords:
        if kw in title:
            print(f'📖 书名兜底: {kw} → 番茄', file=sys.stderr)
            return 'web_novel_zj'

    # 轻小说关键词
    light_keywords = ['异世界', '转生', '史莱姆', '魔法', '冒险', '公会', '勇者', '魔王']
    for kw in light_keywords:
        if kw in title:
            print(f'📖 书名兜底: {kw} → 轻小说', file=sys.stderr)
            return 'light_novel'

    print(f'⚠️ 所有方法都失败，返回 unknown', file=sys.stderr)
    return 'unknown'


# ================================================================
# ★★★ Selenium 驱动（反爬增强版）★★★
# ================================================================

# ★★★ 2026-10-01（用户：「但是我获取封面不是可以静默吗」）★★★
#   create_driver() 原来**没有加 --headless** → 每次兜底搜封面都弹一个 Chrome 窗口出来。
#   默认改成**无头＝静默**；要排障就设环境变量 COVER_HEADLESS=0 把窗口放出来。
#   无头的风险是更易被反爬识别，但这个脚本本来就做了三重压制
#   （--disable-blink-features=AutomationControlled / excludeSwitches:[enable-automation] /
#    CDP 覆写 navigator.webdriver），而且**无头没拿到封面时会自动用有头再试一次**
#   （见 search_cover），所以默认静默是安全的。
HEADLESS = (os.environ.get('COVER_HEADLESS', '1').strip() != '0')

# ★ 2026-10-01：本次运行有没有撞上过**站点占位图**。
#   用途：区分"无头被反爬拦了"和"这个站压根没封面" —— 后者再做一次有头重试纯属浪费
#   （实测《败北女角太多了！》两次都拿到同一个 book-cover-no.svg，白跑 20 秒还弹了个窗口）。
_PLACEHOLDER_SEEN = False


def create_driver():
    """创建 Selenium 驱动（反爬增强）。默认无头（HEADLESS=True，见上）。"""
    try:
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options
        from selenium.webdriver.chrome.service import Service
    except ImportError:
        print('⚠️ 请安装依赖: pip install selenium', file=sys.stderr)
        return None

    # ★ 2026-09-25：以前这里无条件 `ChromeDriverManager().install()`（联网现下 driver）。
    #   开发机上 ~\.wdm 早有缓存，所以「npmstart 能找封面」；打包版一到别人电脑上就
    #   可能下不动、被墙、或版本与对方 Chrome 不匹配 → 表现为「找不到封面」。
    #   现在优先用调用方（main.js）通过 CHROMEDRIVER_PATH 传进来的随包 chromedriver.exe，
    #   取不到才回落到 webdriver_manager 现下。诊断一律走 stderr（main.js 记进
    #   COVER-STDERR）；**绝不能用以 '{' 开头的行** —— main.js 靠「第一条以 { 开头的行」
    #   解析封面的结果 JSON。
    _env_driver = (os.environ.get('CHROMEDRIVER_PATH') or '').strip()
    _env_ok = bool(_env_driver) and os.path.isfile(_env_driver)
    _wdm_dir = os.path.join(os.path.expanduser('~'), '.wdm')
    _chosen = None
    _source = 'none'

    if _env_ok:
        _chosen = _env_driver
        _source = 'env:CHROMEDRIVER_PATH'
    else:
        try:
            from webdriver_manager.chrome import ChromeDriverManager
            _chosen = ChromeDriverManager().install()
            _source = 'webdriver_manager(现下)'
        except Exception as _e:
            print('COVER-DRIVER ' + json.dumps({
                'source': 'none',
                'env': _env_driver,
                'env_exists': _env_ok,
                'wdm_dir': _wdm_dir,
                'wdm_dir_exists': os.path.isdir(_wdm_dir),
                'error': str(_e)[:300],
            }, ensure_ascii=False), file=sys.stderr)

    print('COVER-DRIVER ' + json.dumps({
        'source': _source,
        'chosen': _chosen,
        'env': _env_driver,
        'env_exists': _env_ok,
        'wdm_dir': _wdm_dir,
        'wdm_dir_exists': os.path.isdir(_wdm_dir),
        'frozen': bool(getattr(sys, 'frozen', False)),
        'executable': sys.executable,
        'chrome_paths': [p for p in [
            os.path.join(os.environ.get('PROGRAMFILES', ''), 'Google', 'Chrome', 'Application', 'chrome.exe'),
            os.path.join(os.environ.get('PROGRAMFILES(X86)', ''), 'Google', 'Chrome', 'Application', 'chrome.exe'),
            os.path.join(os.environ.get('PROGRAMFILES', ''), 'Microsoft', 'Edge', 'Application', 'msedge.exe'),
        ] if p and os.path.isfile(p)],
    }, ensure_ascii=False), file=sys.stderr)

    if not _chosen:
        print('⚠️ 找不到可用的 chromedriver：CHROMEDRIVER_PATH 指向的文件不存在，'
              'webdriver_manager 也没能提供。', file=sys.stderr)
        return None

    chrome_options = Options()
    # ★ 2026-10-01：默认无头（静默，不弹窗口）—— 用户：「获取封面不是可以静默吗」
    if HEADLESS:
        chrome_options.add_argument('--headless=new')
        print('🕶 无头模式（静默，不弹窗口）', file=sys.stderr)
    # 反爬：隐藏自动化特征
    chrome_options.add_argument('--disable-gpu')
    chrome_options.add_argument('--no-sandbox')
    chrome_options.add_argument('--window-size=1280,900')
    chrome_options.add_argument('--disable-blink-features=AutomationControlled')
    chrome_options.add_experimental_option('excludeSwitches', ['enable-automation'])
    chrome_options.add_experimental_option('useAutomationExtension', False)
    # 反爬：禁用一些可能导致被检测的特性
    chrome_options.add_argument('--disable-features=VizDisplayCompositor')
    chrome_options.add_argument('--disable-web-security')
    chrome_options.add_argument('--disable-features=IsolateOrigins,site-per-process')
    # 设置真实 User-Agent
    chrome_options.add_argument('user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36')
    chrome_options.page_load_strategy = 'eager'

    try:
        service = Service(_chosen)
        driver = webdriver.Chrome(service=service, options=chrome_options)
    except Exception as e:
        print(f'⚠️ ChromeDriver 初始化失败（driver={_chosen}, 来源={_source}）: {e}', file=sys.stderr)
        return None

    # 反爬：执行CDP命令隐藏webdriver
    driver.execute_cdp_cmd('Page.addScriptToEvaluateOnNewDocument', {
        'source': '''
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            window.chrome = { runtime: {} };
        '''
    })
    return driver


# ================================================================
# ★★★ 哔哩轻小说全套模块 ★★★
# ================================================================

def convert_chinese_to_number(chinese):
    """中文数字转阿拉伯数字"""
    mapping = {
        '一': '1', '二': '2', '三': '3', '四': '4', '五': '5',
        '六': '6', '七': '7', '八': '8', '九': '9', '十': '10',
        '百': '100', '千': '1000', '万': '10000'
    }
    if chinese in mapping:
        return mapping[chinese]
    return chinese


def download_cover(cover_url):
    """下载封面转 base64（智能 Referer + GIF过滤）"""
    global _PLACEHOLDER_SEEN          # ★ 声明必须在函数体最前（同一函数里只能声明一次）
    try:
        if not cover_url:
            return None

        # ★ 提前过滤 .gif（URL 路径或参数里的）
        if cover_url.lower().endswith('.gif') or '/gif/' in cover_url.lower():
            print(f'⚠️ 跳过 GIF 动图: {cover_url}', file=sys.stderr)
            return None

        print(f'📤 下载封面: {cover_url}', file=sys.stderr)

        import requests

        if 'bilinovel' in cover_url or 'readpai' in cover_url:
            referer = 'https://www.bilinovel.com/'
        elif 'fanqie' in cover_url or 'fqnovelpic' in cover_url:
            referer = 'https://fanqienovel.com/'
        elif 'qidian' in cover_url:
            referer = 'https://www.qidian.com/'
        elif 'jjwxc' in cover_url or 'static.jjwxc.net' in cover_url:
            referer = 'https://www.jjwxc.net/'
        else:
            referer = 'https://www.google.com/'

        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36',
            'Referer': referer,
            'Accept': 'image/webp,image/apng,image/*,*/*;q=0.8',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive'
        }

        session = requests.Session()
        session.headers.update(headers)

        response = session.get(cover_url, timeout=30)

        if response.status_code != 200:
            print(f'⚠️ 下载失败: HTTP {response.status_code}', file=sys.stderr)
            return None

        content_type = response.headers.get('Content-Type', '')

        # ★ 检查 Content-Type 是不是 GIF
        if 'gif' in content_type.lower():
            print(f'⚠️ 跳过 GIF 动图 (Content-Type: {content_type})', file=sys.stderr)
            return None

        if 'image' not in content_type:
            print(f'⚠️ 返回的不是图片: {content_type}', file=sys.stderr)
            return None

        image_data = response.content
        if not image_data:
            print(f'⚠️ 下载的数据为空', file=sys.stderr)
            return None

        # ★★★ 2026-10-01（用户：「修」）★★★
        #   实测：无头兜底搜《败北女角太多了！》拿回来的是
        #     data:image/svg+xml;base64,…   解码后**只有 312 字节**
        #   —— 那是站点的**占位图**（/images/book-cover-no.svg 一类），
        #   可它被当成封面返回、`ok:true`；而 main.js 只校验 `^data:image/` 前缀，
        #   `data:image/svg+xml;…` 照样过检 → **占位图会被嵌进 EPUB 当封面**。
        #   在这里挡掉最省事：所有分支（哔哩/起点/番茄/晋江）都走 download_cover。
        #     ① 矢量图不是封面（真封面全是 JPEG/PNG/WebP 位图）；
        #     ② 小于 3KB 的基本都是占位/图标（真封面实测 20~300KB）。
        #   挡掉之后返回 None → search_cover 会**自动用有头模式再试一次**，
        #   两头都拿不到才如实报"没找到封面"，而不是塞一张占位图进去。
        if 'svg' in content_type.lower():
            print(f'⚠️ 跳过占位矢量图 (Content-Type: {content_type}): {cover_url}', file=sys.stderr)
            _PLACEHOLDER_SEEN = True
            return None
        if len(image_data) < 3000:
            print(f'⚠️ 跳过过小图（{len(image_data)} 字节，基本是占位图）: {cover_url}', file=sys.stderr)
            _PLACEHOLDER_SEEN = True
            return None

        base64_data = base64.b64encode(image_data).decode('utf-8')
        print(f'✅ 封面下载成功，大小: {len(image_data)} bytes', file=sys.stderr)
        return f'data:{content_type};base64,{base64_data}'

    except ImportError:
        print(f'⚠️ 请安装 requests: pip install requests', file=sys.stderr)
        return None
    except Exception as e:
        print(f'⚠️ 下载封面失败: {e}', file=sys.stderr)
        return None


def search_cover_bilinovel(title, author=''):
    """哔哩轻小说搜索"""
    print(f'📤 哔哩轻小说搜索: {title}', file=sys.stderr)

    driver = None
    try:
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.common.exceptions import TimeoutException, NoSuchElementException
        from selenium.webdriver.common.keys import Keys

        driver = create_driver()
        if not driver:
            return None

        driver.set_page_load_timeout(15)

        main_title = title
        volume = None

        volume_patterns = [
            r'第([一二三四五六七八九十百千零\d]+)卷',
            r'第([一二三四五六七八九十百千零\d]+)冊',
            r'([一二三四五六七八九十百千零\d]+)卷',
            r'([一二三四五六七八九十百千零\d]+)册',
            r'第([一二三四五六七八九十百千零\d]+)话',
            r'第([一二三四五六七八九十百千零\d]+)話'
        ]
        for pattern in volume_patterns:
            match = re.search(pattern, title)
            if match:
                volume = match.group(1)
                main_title = title[:match.start()].strip()
                break

        if volume:
            print(f'📤 主书名: {main_title}, 卷号: {volume}', file=sys.stderr)

        print(f'📤 打开搜索页...', file=sys.stderr)
        try:
            driver.get('https://www.bilinovel.com/search.html')
        except Exception as e:
            print(f'⚠️ 搜索页加载超时: {e}', file=sys.stderr)
        time.sleep(2)

        search_input = None
        try:
            search_input = WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, 'input[type="text"], input[placeholder*="书名"], input[placeholder*="关键词"]'))
            )
        except TimeoutException:
            print('⚠️ 未找到搜索框', file=sys.stderr)
            driver.quit()
            return None

        print(f'📤 输入主书名: {main_title}', file=sys.stderr)
        search_input.clear()
        for char in main_title:
            search_input.send_keys(char)
            time.sleep(random.uniform(0.05, 0.1))
        time.sleep(0.5)
        search_input.send_keys(Keys.RETURN)
        print('📤 搜索提交', file=sys.stderr)
        time.sleep(2)

        try:
            WebDriverWait(driver, 8).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, 'a[href*="/book/"], a[href*="/novel/"]'))
            )
            first_link = driver.find_element(By.CSS_SELECTOR, 'a[href*="/book/"], a[href*="/novel/"]')
            result_url = first_link.get_attribute('href')
            if result_url:
                print(f'📤 打开第一个结果: {result_url}', file=sys.stderr)
                driver.execute_script(f"window.location.href = '{result_url}';")
                time.sleep(2)
            else:
                print('⚠️ 结果链接无效', file=sys.stderr)
                driver.quit()
                return None
        except TimeoutException:
            print('⚠️ 无搜索结果', file=sys.stderr)
            driver.quit()
            return None

        # 分卷查找
        if volume:
            print(f'📤 查找第 {volume} 卷...', file=sys.stderr)
            driver.execute_script("window.scrollTo(0, 300);")
            time.sleep(1)

            volume_found = False
            try:
                all_links = driver.find_elements(By.TAG_NAME, 'a')
                for link in all_links:
                    try:
                        link_text = link.text
                        href = link.get_attribute('href')
                        if not href:
                            continue
                        if re.search(rf'第{volume}卷', link_text) or re.search(rf'第{volume}冊', link_text):
                            print(f'📤 找到分卷链接: {link_text} -> {href}', file=sys.stderr)
                            driver.execute_script(f"window.location.href = '{href}';")
                            time.sleep(2)
                            volume_found = True
                            break
                    except:
                        continue
            except:
                pass

            if not volume_found:
                try:
                    volume_link = driver.execute_script(f"""
                        var links = document.querySelectorAll('a');
                        for (var i = 0; i < links.length; i++) {{
                            var text = links[i].textContent || '';
                            var href = links[i].getAttribute('href');
                            if (href && text && text.indexOf('第{volume}卷') !== -1) {{
                                return href;
                            }}
                        }}
                        return null;
                    """)
                    if volume_link:
                        print(f'📤 JS 找到分卷链接: {volume_link}', file=sys.stderr)
                        driver.execute_script(f"window.location.href = '{volume_link}';")
                        time.sleep(2)
                        volume_found = True
                except:
                    pass

            if not volume_found:
                volume_num = convert_chinese_to_number(volume)
                if volume_num:
                    try:
                        volume_link = driver.execute_script(f"""
                            var links = document.querySelectorAll('a');
                            for (var i = 0; i < links.length; i++) {{
                                var text = links[i].textContent || '';
                                var href = links[i].getAttribute('href');
                                if (href && text && text.indexOf('{volume_num}') !== -1) {{
                                    return href;
                                }}
                            }}
                            return null;
                        """)
                        if volume_link:
                            print(f'📤 JS 找到分卷链接(数字): {volume_link}', file=sys.stderr)
                            driver.execute_script(f"window.location.href = '{volume_link}';")
                            time.sleep(2)
                            volume_found = True
                    except:
                        pass

        # ★★★ 滚动到封面位置（600px）★★★
        print(f'📤 滚动到封面位置（600px）...', file=sys.stderr)
        try:
            driver.execute_script("window.scrollTo(0, 0);")
            time.sleep(1)
            driver.execute_script("window.scrollTo(0, 600);")
            time.sleep(1.5)
            driver.execute_script("window.scrollTo(0, 250);")
            time.sleep(1)
        except Exception as se:
            print(f'⚠️ 滚动失败: {se}', file=sys.stderr)

        # 提取封面
        cover_url = None
        try:
            cover_url = driver.execute_script("""
                var imgs = document.querySelectorAll('img');
                var maxArea = 0;
                var maxImg = null;
                for (var i = 0; i < imgs.length; i++) {
                    var rect = imgs[i].getBoundingClientRect();
                    var area = rect.width * rect.height;
                    var top = rect.top;
                    var src = imgs[i].src || '';
                    // ★ 过滤掉动图、logo、icon、广告图
                    if (src.toLowerCase().endsWith('.gif')) continue;
                    if (src.indexOf('logo') !== -1) continue;
                    if (src.indexOf('icon') !== -1) continue;
                    if (src.indexOf('avatar') !== -1) continue;
                    if (src.indexOf('ad') !== -1) continue;
                    if (area > 5000 && top > 50 && top < 700) {
                        if (area > maxArea) {
                            maxArea = area;
                            maxImg = imgs[i];
                        }
                    }
                }
                if (!maxImg) {
                    for (var i = 0; i < imgs.length; i++) {
                        var rect = imgs[i].getBoundingClientRect();
                        var area = rect.width * rect.height;
                        var src = imgs[i].src || '';
                        // ★ 同样过滤动图
                        if (src.toLowerCase().endsWith('.gif')) continue;
                        if (src.indexOf('logo') !== -1) continue;
                        if (src.indexOf('icon') !== -1) continue;
                        if (src.indexOf('avatar') !== -1) continue;
                        if (src.indexOf('ad') !== -1) continue;
                        if (area > 8000 && area > maxArea) {
                            maxArea = area;
                            maxImg = imgs[i];
                        }
                    }
                }
                return maxImg ? maxImg.src : null;
            """)
            if cover_url and 'logo' not in cover_url.lower() and 'icon' not in cover_url.lower():
                print(f'✅ JS 提取封面', file=sys.stderr)
        except Exception as e:
            print(f'⚠️ JS 提取失败: {e}', file=sys.stderr)

        if not cover_url:
            selectors = [
                '.book-cover img', '.cover-img img', '.detail-cover img',
                '.book-info img', '.novel-cover img', '.book-detail-cover img',
                'img[src*="cover"]', 'img[src*="book"]', '.book-cover-box img'
            ]
            for selector in selectors:
                try:
                    img_element = driver.find_element(By.CSS_SELECTOR, selector)
                    if img_element:
                        src = img_element.get_attribute('src')
                        # ★ CSS 选择器也过滤动图
                        if src and 'logo' not in src.lower() and 'icon' not in src.lower() and not src.lower().endswith('.gif'):
                            cover_url = src
                            print(f'✅ 选择器找到封面', file=sys.stderr)
                            break
                except Exception:
                    continue

        driver.quit()

        if cover_url:
            return download_cover(cover_url)
        return None

    except Exception as e:
        print(f'⚠️ 哔哩搜索失败: {e}', file=sys.stderr)
        if driver:
            driver.quit()
        return None


# ================================================================
# ★★★ 起点搜索 ★★★
# ================================================================

def search_cover_qidian(title, author=''):
    """起点中文网搜索"""
    print(f'📤 起点中文网搜索: {title}', file=sys.stderr)

    driver = None
    try:
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.common.exceptions import TimeoutException
        from selenium.webdriver.common.keys import Keys

        driver = create_driver()
        if not driver:
            return None

        driver.set_page_load_timeout(15)

        encoded_title = urllib.parse.quote(title)
        search_url = f'https://www.qidian.com/search?kw={encoded_title}'
        print(f'📤 打开起点搜索页: {search_url}', file=sys.stderr)
        driver.get(search_url)
        print(f'✅ 起点搜索页加载完成', file=sys.stderr)
        time.sleep(3)

        try:
            print(f'📤 查找第一个结果...', file=sys.stderr)

            selectors = [
                '.result-item .book-info .book-name a',
                '.result-item .book-title a',
                '.res-item .book-info a',
                '.search-result .book-info .book-name a',
                '.book-item .book-info .book-name a'
            ]

            first_link = None
            for selector in selectors:
                try:
                    first_link = driver.find_element(By.CSS_SELECTOR, selector)
                    if first_link:
                        break
                except Exception:
                    continue

            if not first_link:
                all_links = driver.find_elements(By.CSS_SELECTOR, 'a[href*="/book/"]')
                for link in all_links:
                    href = link.get_attribute('href')
                    if '/book/' in href and '?' not in href and 'search' not in href:
                        first_link = link
                        break

            if first_link:
                href = first_link.get_attribute('href')
                if href:
                    print(f'📤 打开第一个结果: {href}', file=sys.stderr)
                    driver.get(href)
                    print(f'✅ 详情页加载完成', file=sys.stderr)
                    time.sleep(3)
                else:
                    print('⚠️ 链接无效', file=sys.stderr)
                    driver.quit()
                    return None
            else:
                print('⚠️ 未找到结果链接', file=sys.stderr)
                driver.quit()
                return None

        except Exception as e:
            print(f'⚠️ 点击结果失败: {e}', file=sys.stderr)
            try:
                html = driver.page_source
                pattern = r'https://www\.qidian\.com/book/\d+/'
                matches = re.findall(pattern, html)
                if matches:
                    href = matches[0]
                    print(f'📤 从源码提取链接: {href}', file=sys.stderr)
                    driver.get(href)
                    print(f'✅ 详情页加载完成', file=sys.stderr)
                    time.sleep(3)
                else:
                    print('⚠️ 未找到任何书籍链接', file=sys.stderr)
                    driver.quit()
                    return None
            except Exception as e2:
                print(f'⚠️ 方法2失败: {e2}', file=sys.stderr)
                driver.quit()
                return None

        print(f'📤 等待图片加载...', file=sys.stderr)
        time.sleep(2)

        print(f'📤 获取封面...', file=sys.stderr)
        cover_url = None

        try:
            cover_url = driver.execute_script("""
                var imgs = document.querySelectorAll('img');
                var maxArea = 0;
                var maxImg = null;
                for (var i = 0; i < imgs.length; i++) {
                    var rect = imgs[i].getBoundingClientRect();
                    var area = rect.width * rect.height;
                    if (area > 10000 && rect.top > 50 && rect.top < 600) {
                        if (area > maxArea) {
                            maxArea = area;
                            maxImg = imgs[i];
                        }
                    }
                }
                if (!maxImg) {
                    for (var i = 0; i < imgs.length; i++) {
                        var rect = imgs[i].getBoundingClientRect();
                        var area = rect.width * rect.height;
                        if (area > 20000 && area > maxArea) {
                            maxArea = area;
                            maxImg = imgs[i];
                        }
                    }
                }
                return maxImg ? maxImg.src : null;
            """)
            if cover_url and 'logo' not in cover_url.lower() and 'icon' not in cover_url.lower():
                print(f'✅ JS 提取封面: {cover_url}', file=sys.stderr)
        except Exception as e:
            print(f'⚠️ JS 提取失败: {e}', file=sys.stderr)

        if not cover_url:
            selectors = [
                '.book-img img',
                '.pic img',
                '.cover img',
                '.book-cover img',
                '.detail-cover img',
                'img[src*="qidian"]',
                'img[src*="bookcover"]',
                '.book-info .book-img img'
            ]
            for selector in selectors:
                try:
                    img_element = driver.find_element(By.CSS_SELECTOR, selector)
                    if img_element:
                        src = img_element.get_attribute('src')
                        if src and 'logo' not in src.lower() and 'icon' not in src.lower():
                            cover_url = src
                            print(f'✅ CSS选择器提取: {cover_url}', file=sys.stderr)
                            break
                except Exception:
                    continue

        if not cover_url:
            print(f'📤 尝试从源码提取...', file=sys.stderr)
            html = driver.page_source
            patterns = [
                r'https?://[^\s"\']+\.(jpg|jpeg|png|webp)[^\s"\']*',
                r'https?://[^\s"\']*qidian[^\s"\']+\.(jpg|jpeg|png|webp)',
                r'https?://[^\s"\']*bookcover[^\s"\']+\.(jpg|jpeg|png|webp)',
                r'https?://[^\s"\']*img[^\s"\']+\.(jpg|jpeg|png|webp)'
            ]
            for pattern in patterns:
                matches = re.findall(pattern, html, re.IGNORECASE)
                if matches:
                    for url in matches:
                        if 'logo' not in url.lower() and 'icon' not in url.lower() and 'avatar' not in url.lower():
                            if 'qidian' in url.lower() or 'bookcover' in url.lower() or 'img' in url.lower():
                                cover_url = url
                                print(f'✅ 从源码提取封面: {cover_url}', file=sys.stderr)
                                break
                    if cover_url:
                        break

        driver.quit()

        if cover_url:
            if cover_url.startswith('//'):
                cover_url = 'https:' + cover_url
            elif cover_url.startswith('/'):
                cover_url = 'https://www.qidian.com' + cover_url

            print(f'📤 下载起点封面: {cover_url}', file=sys.stderr)
            return download_cover(cover_url)
        else:
            print(f'⚠️ 未找到起点封面', file=sys.stderr)
        return None

    except Exception as e:
        print(f'⚠️ 起点搜索失败: {e}', file=sys.stderr)
        if driver:
            driver.quit()
        return None


# ================================================================
# ★★★ 番茄搜索 ★★★
# ================================================================

def search_cover_fanqie(title, author=''):
    """番茄小说搜索"""
    print(f'📤 番茄小说搜索: {title}', file=sys.stderr)

    driver = None
    try:
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.common.exceptions import TimeoutException
        from selenium.webdriver.common.keys import Keys

        driver = create_driver()
        if not driver:
            return None

        driver.set_page_load_timeout(15)

        print(f'📤 打开番茄首页: https://fanqienovel.com/', file=sys.stderr)
        driver.get('https://fanqienovel.com/')
        print(f'✅ 番茄首页加载完成', file=sys.stderr)
        time.sleep(3)

        print(f'📤 定位搜索框...', file=sys.stderr)
        search_input = None

        try:
            search_input = driver.find_element(By.CSS_SELECTOR, 'input[placeholder*="搜索"], input[placeholder*="书名"]')
        except Exception:
            try:
                search_input = driver.find_element(By.CSS_SELECTOR, 'input[type="text"]')
            except Exception:
                try:
                    search_input = driver.find_element(By.NAME, 'q')
                except Exception:
                    try:
                        search_input = driver.find_element(By.CSS_SELECTOR, '.search-input, .search-box input')
                    except Exception:
                        pass

        if not search_input:
            print(f'⚠️ 未找到搜索框', file=sys.stderr)
            driver.quit()
            return None

        print(f'📤 输入书名: {title}', file=sys.stderr)
        search_input.clear()
        for char in title:
            search_input.send_keys(char)
            time.sleep(random.uniform(0.05, 0.1))
        time.sleep(0.5)
        search_input.send_keys(Keys.RETURN)
        print(f'📤 搜索提交', file=sys.stderr)
        time.sleep(3)

        cover_url = None

        try:
            print(f'📤 等待搜索结果...', file=sys.stderr)
            WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.TAG_NAME, 'body'))
            )
            time.sleep(3)

            print(f'📤 查找结果链接...', file=sys.stderr)

            result = driver.execute_script("""
                var links = document.querySelectorAll('a');
                var bookLink = null;
                var bookImg = null;

                for (var i = 0; i < links.length; i++) {
                    var href = links[i].getAttribute('href');
                    if (href && href.indexOf('/book/') !== -1) {
                        bookLink = href;
                        var img = links[i].querySelector('img');
                        if (img) {
                            bookImg = img.src;
                        } else {
                            var parent = links[i].parentElement;
                            if (parent) {
                                var siblingImg = parent.querySelector('img');
                                if (siblingImg) {
                                    bookImg = siblingImg.src;
                                }
                            }
                        }
                        break;
                    }
                }

                return {
                    link: bookLink,
                    img: bookImg
                };
            """)

            print(f'📤 JS 结果: link={result.get("link")}, img={result.get("img")[:50] if result.get("img") else "None"}...', file=sys.stderr)
            if result and result.get('link'):
                href = result['link']
                print(f'📤 进入详情页: {href}', file=sys.stderr)
                driver.get(href)
                time.sleep(3)

                img_selectors = [
                    '.book-cover img',
                    '.cover img',
                    'img[src*="cover"]',
                    'img[src*="book"]'
                ]
                for selector in img_selectors:
                    try:
                        img = driver.find_element(By.CSS_SELECTOR, selector)
                        if img:
                            src = img.get_attribute('src')
                            if src and 'logo' not in src.lower() and 'icon' not in src.lower():
                                cover_url = src
                                print(f'✅ 详情页找到封面: {cover_url}', file=sys.stderr)
                                break
                    except Exception:
                        continue

            if not cover_url:
                print(f'📤 从当前页面提取图片...', file=sys.stderr)

                cover_url = driver.execute_script("""
                    var imgs = document.querySelectorAll('img');
                    var maxArea = 0;
                    var maxImg = null;
                    for (var i = 0; i < imgs.length; i++) {
                        var rect = imgs[i].getBoundingClientRect();
                        var area = rect.width * rect.height;
                        var src = imgs[i].src || '';
                        if (area > 10000 && src.indexOf('logo') === -1 && src.indexOf('avatar') === -1) {
                            if (area > maxArea) {
                                maxArea = area;
                                maxImg = imgs[i];
                            }
                        }
                    }
                    return maxImg ? maxImg.src : null;
                """)

                if cover_url:
                    print(f'✅ 从当前页面提取封面: {cover_url}', file=sys.stderr)

        except TimeoutException:
            print(f'⚠️ 等待超时', file=sys.stderr)
        except Exception as e:
            print(f'⚠️ 搜索失败: {e}', file=sys.stderr)

        if not cover_url:
            print(f'📤 尝试从源码提取...', file=sys.stderr)
            html = driver.page_source
            patterns = [
                r'https?://[^\s"\']+\.(jpg|jpeg|png|webp)[^\s"\']*',
                r'https?://[^\s"\']*fanqie[^\s"\']+\.(jpg|jpeg|png|webp)',
                r'https?://[^\s"\']*cover[^\s"\']+\.(jpg|jpeg|png|webp)'
            ]
            for pattern in patterns:
                matches = re.findall(pattern, html, re.IGNORECASE)
                if matches:
                    for url in matches:
                        if 'logo' not in url.lower() and 'icon' not in url.lower() and 'avatar' not in url.lower():
                            cover_url = url
                            print(f'✅ 从源码提取封面: {cover_url}', file=sys.stderr)
                            break
                    if cover_url:
                        break

        driver.quit()

        if cover_url:
            return download_cover(cover_url)
        else:
            print(f'⚠️ 未找到番茄封面', file=sys.stderr)
        return None

    except Exception as e:
        print(f'⚠️ 番茄搜索失败: {e}', file=sys.stderr)
        if driver:
            driver.quit()
        return None


# ================================================================
# ★★★ 晋江搜索（视觉识别 + 反爬）★★★
# ================================================================

def search_cover_jjwxc(title, author=''):
    """晋江文学城搜索（视觉识别 + 反爬策略）"""
    print(f'📤 晋江文学城搜索: {title}', file=sys.stderr)
    print('=' * 60, file=sys.stderr)

    driver = None
    try:
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.common.exceptions import TimeoutException
        import random
        import urllib.parse

        driver = create_driver()
        if not driver:
            return None

        # ================================================================
        # 1. 反爬：先访问首页获取Cookie
        # ================================================================
        print(f'📤 访问晋江首页获取Cookie...', file=sys.stderr)
        driver.get('https://www.jjwxc.net/')
        time.sleep(random.uniform(2, 4))
        print(f'✅ 首页加载完成', file=sys.stderr)

        # ================================================================
        # 2. ★★★ 构造搜索URL（书名 + 作者，GBK编码）★★★
        # ================================================================
        # 如果有作者，用"书名 作者"组合搜索
        if author:
            search_query = f"{title} {author}"
        else:
            search_query = title

        try:
            gbk_query = search_query.encode('gbk', errors='ignore')
            encoded_query = urllib.parse.quote(gbk_query)
        except Exception:
            encoded_query = urllib.parse.quote(search_query)

        search_url = f'https://www.jjwxc.net/search.php?kw={encoded_query}&t=1&version=1'
        print(f'📤 打开搜索链接: {search_url}', file=sys.stderr)
        driver.get(search_url)
        print(f'✅ 搜索页加载完成', file=sys.stderr)
        time.sleep(random.uniform(3, 5))

        # ================================================================
        # 3. 反爬：随机滚动，模拟人类行为
        # ================================================================
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight * 0.3);")
        time.sleep(random.uniform(1, 2))
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight * 0.6);")
        time.sleep(random.uniform(1, 2))

        # ================================================================
        # 4. ★★★ 视觉识别：查找红字加粗的书名（优先）★★★
        # ================================================================
        print(f'📤 视觉识别红字加粗的书名...', file=sys.stderr)

        result = driver.execute_script("""
            var searchTitle = arguments[0];
            var target = null;
            
            // 方法1：查找 <b><font color="red"> 红字加粗
            var redBold = document.querySelectorAll('b font[color="red"], font[color="red"] b, b > font[color="red"]');
            for (var i = 0; i < redBold.length; i++) {
                var el = redBold[i];
                var text = el.textContent.trim();
                var parentA = el.closest('a');
                if (parentA && parentA.href && parentA.href.indexOf('onebook.php?novelid=') !== -1) {
                    // 优先匹配包含搜索词的
                    if (text.indexOf(searchTitle) !== -1) {
                        target = {
                            href: parentA.href,
                            text: text
                        };
                        break;
                    }
                }
            }
            
            // 方法2：如果方法1没找到，查找所有红色文字（不一定加粗）
            if (!target) {
                var allRed = document.querySelectorAll('font[color="red"], [style*="color:red"], [style*="color:#"]');
                for (var i = 0; i < allRed.length; i++) {
                    var el = allRed[i];
                    var text = el.textContent.trim();
                    if (text.indexOf(searchTitle) !== -1) {
                        var parentA = el.closest('a');
                        if (parentA && parentA.href && parentA.href.indexOf('onebook.php?novelid=') !== -1) {
                            target = {
                                href: parentA.href,
                                text: text
                            };
                            break;
                        }
                    }
                }
            }
            
            // 方法3：暴力遍历所有 a 标签，找文本包含搜索词的（兜底）
            if (!target) {
                var allLinks = document.querySelectorAll('a[href*="onebook.php?novelid="]');
                for (var i = 0; i < allLinks.length; i++) {
                    var href = allLinks[i].getAttribute('href');
                    var text = allLinks[i].textContent.trim();
                    // 排除导航菜单
                    if (href && href.indexOf('/fenzhan/') === -1 && href.indexOf('/channel/') === -1) {
                        if (text.indexOf(searchTitle) !== -1) {
                            target = {
                                href: href,
                                text: text
                            };
                            break;
                        }
                    }
                }
            }
            
            return target;
        """, title)

        # ================================================================
        # 5. 如果视觉识别失败，尝试从页面源码正则提取（兜底）
        # ================================================================
        if not result:
            print('⚠️ 视觉识别未找到，尝试正则提取...', file=sys.stderr)
            page_source = driver.page_source
            pattern = r'<a[^>]+href="([^"]*onebook\.php\?novelid=[^"]+)"[^>]*>([^<]*(?:' + re.escape(title) + r')[^<]*)</a>'
            matches = re.findall(pattern, page_source, re.IGNORECASE)
            if matches:
                href, text = matches[0]
                href = href.replace('&amp;', '&')
                result = {'href': href, 'text': text.strip()}
                print(f'✅ 正则提取到: "{text}"', file=sys.stderr)

        if not result:
            print('❌ 未找到书名链接', file=sys.stderr)
            driver.quit()
            return None

        href = result.get('href')
        text = result.get('text')
        print(f'✅ 找到书名: "{text}"', file=sys.stderr)
        print(f'📤 链接: {href}', file=sys.stderr)

        if href.startswith('//'):
            href = 'https:' + href
        elif href.startswith('/'):
            href = 'https://www.jjwxc.net' + href
        elif not href.startswith('http'):
            href = 'https://www.jjwxc.net/' + href.lstrip('/')

        print(f'📤 修复后链接: {href}', file=sys.stderr)

        # ================================================================
        # 6. 打开详情页（反爬：加延时）
        # ================================================================
        print(f'📤 打开详情页: {href}', file=sys.stderr)
        driver.get(href)
        time.sleep(random.uniform(2, 4))

        if '404' in driver.title or '不存在' in driver.page_source:
            print('❌ 详情页 404', file=sys.stderr)
            driver.quit()
            return None

        # ================================================================
        # 7. 提取封面
        # ================================================================
        print(f'📤 提取封面...', file=sys.stderr)
        cover_url = driver.execute_script("""
            var imgs = document.querySelectorAll('img');
            var maxArea = 0;
            var maxImg = null;
            for (var i = 0; i < imgs.length; i++) {
                var rect = imgs[i].getBoundingClientRect();
                var area = rect.width * rect.height;
                var src = imgs[i].src || '';
                if (area > 5000 && src.indexOf('logo') === -1 && src.indexOf('icon') === -1) {
                    if (area > maxArea) {
                        maxArea = area;
                        maxImg = imgs[i];
                    }
                }
            }
            return maxImg ? maxImg.src : null;
        """)

        driver.quit()

        if cover_url:
            print(f'✅ 找到封面: {cover_url}', file=sys.stderr)
            return download_cover(cover_url)
        else:
            print('❌ 未找到封面', file=sys.stderr)
            return None

    except Exception as e:
        print(f'⚠️ 晋江搜索失败: {e}', file=sys.stderr)
        import traceback
        traceback.print_exc(file=sys.stderr)
        if driver:
            driver.quit()
        return None


# ================================================================
# ★★★ 其他（暂不支持）★★★
# ================================================================

def search_cover_other(title, author=''):
    """其他 - 直接返回失败"""
    print(f'❌ 暂不支持该平台: {title}', file=sys.stderr)
    return None


# ================================================================
# ★★★ 主入口 ★★★
# ================================================================

_PLATFORM_ALIASES = {
    'fanqie': 'web_novel_zj', 'zj': 'web_novel_zj', '番茄': 'web_novel_zj',
    'qidian': 'web_novel_qidian', 'qd': 'web_novel_qidian', '起点': 'web_novel_qidian',
    'jj': 'web_novel_jj', 'jjwxc': 'web_novel_jj', '晋江': 'web_novel_jj',
    'bilinovel': 'light_novel', 'light': 'light_novel', 'linovelib': 'light_novel',
}


def _dispatch_cover(title, author, platform):
    """按平台分派到具体分支（search_cover 的实体；抽出来是为了能重试一遍）。"""
    book_type = _PLATFORM_ALIASES.get((platform or '').strip().lower(), '')
    if book_type:
        print(f'📌 平台由调用方指定: {platform} -> {book_type}', file=sys.stderr)
    else:
        book_type = guess_book_type_2step(title, author)
        print(f'📌 book_type（关键词兜底） = {book_type}', file=sys.stderr)

    if book_type == 'light_novel':
        print(f'📤 走轻小说分支 -> 哔哩轻小说', file=sys.stderr)
        return search_cover_bilinovel(title, author)
    elif book_type == 'web_novel_qidian':
        print(f'📤 走起点分支 -> 起点中文网', file=sys.stderr)
        return search_cover_qidian(title, author)
    elif book_type == 'web_novel_zj':
        print(f'📤 走番茄分支 -> 番茄小说', file=sys.stderr)
        return search_cover_fanqie(title, author)
    elif book_type == 'web_novel_jj':
        print(f'📤 走晋江分支 -> 晋江文学城', file=sys.stderr)
        return search_cover_jjwxc(title, author)
    else:
        print(f'⚠️ 未知类型: {book_type}', file=sys.stderr)
        return None


def search_cover(title, author='', platform=''):
    """主入口：按平台直连搜索（★ 2026-09-29 起不再需要 AI 判断类型）。

    platform 由调用方给出 —— novelmeta 已经知道这本书来自哪个源：
        fanqie | qidian | jj | bilinovel（别名见 _PLATFORM_ALIASES）
    传空则退回关键词兜底 guess_book_type_2step()。

    ★★★ 2026-10-01（用户：「但是我获取封面不是可以静默吗」）★★★
      **默认无头＝静默，不弹浏览器窗口。**
      但"无头拿不到封面"是真实存在的风险（反爬对无头更严），所以这里留了一手：
      **无头没拿到 → 自动用有头模式再试一次**（那次会弹窗口，但只在无头失败时发生）。
      想直接放窗口排障：环境变量 `COVER_HEADLESS=0`。
    """
    global HEADLESS
    print(f'🔍 ===== 搜索封面主入口 =====', file=sys.stderr)
    print(f'🔍 搜索封面: {title}', file=sys.stderr)
    print(f'📌 作者: {author}', file=sys.stderr)

    result = _dispatch_cover(title, author, platform)
    if result or not HEADLESS:
        return result
    # ★ 撞到的是站点占位图 → 说明"这个站就是没封面"，不是无头被拦，
    #   再做有头重试纯属浪费（实测白跑 20 秒 + 弹一个没用的窗口）。
    if _PLACEHOLDER_SEEN:
        print('ℹ️ 拿到的是站点占位图（这个站就是没封面）→ 不做有头重试', file=sys.stderr)
        return None
    # 无头空手而归 → 有头再试一次（这次会让用户看到窗口，属必要的兜底）
    print('⚠️ 无头模式没拿到封面 → 改用有头模式再试一次（会弹窗口）', file=sys.stderr)
    HEADLESS = False
    return _dispatch_cover(title, author, platform)


def _emit(payload):
    """统一输出一行 JSON（stdout 只放结果，诊断一律走 stderr）。"""
    print(json.dumps(payload, ensure_ascii=False))


def _result_payload(result):
    """把 download_cover() 返回的 data URI 包成统一结构。

    ★ 同时保留 `success`（旧契约）与 `ok`（新契约）：
      主进程的 meta-cover-fallback 读 ok，老的调用方读 success。
    `cover_url` 留空是刻意的 —— 具体命中的 URL 在 stderr 的 COVER-* / 📤 行里，
      这里不重复造字段（data URI 已经能直接用）。
    """
    if not result:
        return {'ok': False, 'success': False, 'cover_data': None,
                'cover_url': '', 'file': '', 'driver': '', 'error': '未找到封面'}
    return {'ok': True, 'success': True, 'cover_data': result,
            'cover_url': '', 'file': '', 'driver': '', 'error': None}


if __name__ == '__main__':
    # ★ 2026-09-29 新增用法（供 EasyPub 的 meta-cover-fallback 调用）：
    #     python ai_cover.py --fallback-search "<书名>" "<作者>" [--platform fanqie|qidian|jj|bilinovel]
    #   场景：novelmeta 拿到了元数据但封面下载失败（例如番茄的字节签名已过期），
    #         于是用真实浏览器打开详情页现取 img.src 再下载。
    #   旧的两种用法（位置参数 / stdin JSON）保持可用。
    argv = sys.argv[1:]
    platform = ''
    if '--platform' in argv:
        i = argv.index('--platform')
        platform = argv[i + 1] if i + 1 < len(argv) else ''
        del argv[i:i + 2]
    argv = [a for a in argv if a != '--fallback-search']

    try:
        if argv:
            title = argv[0]
            author = argv[1] if len(argv) > 1 else ''
            _emit(_result_payload(search_cover(title, author, platform)))
            sys.exit(0)

        input_data = sys.stdin.read().strip()
        if input_data:
            try:
                params = json.loads(input_data)
                title = params.get('title', '')
                author = params.get('author', '')
                platform = params.get('platform', platform) or platform
            except Exception:
                title = input_data
                author = ''

            if title:
                _emit(_result_payload(search_cover(title, author, platform)))
                sys.exit(0)

        _emit({'ok': False, 'success': False, 'cover_data': None,
               'cover_url': '', 'file': '', 'driver': '', 'error': '未收到输入'})

    except Exception as e:
        _emit({'ok': False, 'success': False, 'cover_data': None,
               'cover_url': '', 'file': '', 'driver': '', 'error': str(e)})