#!/usr/bin/env python
# -*- coding: utf-8 -*-

import sys
import io
import json
import os
import re
import subprocess
import tempfile
from datetime import datetime

# 强制 UTF-8 编码
sys.stdin = io.TextIOWrapper(sys.stdin.buffer, encoding='utf-8')
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')


def log(msg, level='INFO'):
    print(f'[{level}] {msg}', file=sys.stderr, flush=True)


def detect_encoding(file_path):
    with open(file_path, 'rb') as f:
        head = f.read(4)
    if head.startswith(b'\xef\xbb\xbf'):
        return 'utf-8-sig'
    elif head.startswith(b'\xff\xfe'):
        return 'utf-16le'
    elif head.startswith(b'\xfe\xff'):
        return 'utf-16be'
    encodings = ['utf-8', 'gbk', 'gb18030', 'big5', 'shift-jis']
    for enc in encodings:
        try:
            with open(file_path, 'r', encoding=enc) as f:
                f.read()
                return enc
        except:
            continue
    return 'utf-8'


def clean_text(text):
    if not text:
        return ''
    if not isinstance(text, str):
        text = str(text)
    text = re.sub(r'[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]', '', text)
    return text


# ★ 金色统一用 #b8956a（原 epub_style.css 里的 --color-title）
GOLD_HEX = '#b8956a'
GOLD_RGB = 'rgb(184,149,106)'


# ================================================================
# ★★★ 事后处理：注入内联样式 + 重新打包 ★★★
# ================================================================

def _finish_epub(epub_path, temp_dir):
    """收尾：给所有 XHTML 注入内联样式，再把 temp_dir 重新打包回 epub_path。

    ★ 抽成独立函数的原因（2026-09-30）：customize_toc() 有「找到目录页」/「没找到目录页」
      两个出口，**两个出口都必须走到这里**；原来「没找到目录页」那条是直接 `return`，
      会连样式注入和重新打包一起跳过（现在正常流程恒为「没有目录页」，所以这是致命路径）。
    """
    import shutil
    import zipfile

    inject_inline_styles(temp_dir)

    # ★ 修复两处 OCF 规范问题：
    #   1) mimetype 必须是 zip 里第一条且**不压缩**（ZIP_STORED），
    #      原来整包走 ZIP_DEFLATED，实测产出 6/6 的成品 mimetype 压缩方式=8，epubcheck 会报错。
    #   2) 原来直接 'w' 打开 epub_path，先截断原文件；写到一半崩溃 → 原 EPUB 全毁且无备份。
    #      改为写临时文件再 os.replace 原子替换（与 library_scan.py 的做法一致）。
    tmp_epub = epub_path + '.tmp'
    with zipfile.ZipFile(tmp_epub, 'w', zipfile.ZIP_DEFLATED) as zf:
        # 第一条：mimetype，固定 STORED
        zi = zipfile.ZipInfo('mimetype')
        zi.compress_type = zipfile.ZIP_STORED
        zf.writestr(zi, b'application/epub+zip')

        for root, dirs, files in os.walk(temp_dir):
            for file in files:
                file_path = os.path.join(root, file)
                arcname = os.path.relpath(file_path, temp_dir)
                arcname = arcname.replace(os.sep, '/')
                if arcname == 'mimetype':
                    continue          # 已作为第一条写入
                zf.write(file_path, arcname)

    os.replace(tmp_epub, epub_path)

    log('  ✅ EPUB 已重新打包（mimetype 未压缩，原子替换）')


def customize_toc(epub_path, chapters=None):
    """★ 2026-09-30 起：**书里不再有目录页**（用户：「不要目录（但要保留 title 页）」）。

    `generate_epub()` 已不再往 markdown 里写「# 目录 + 章节列表」那一段，所以 pandoc
    **不会产生目录章节页**；目录功能改由**阅读器自带的目录菜单**提供
    （pandoc 生成的 `nav.xhtml` / `toc.ncx` 照旧列出全部章节，一页不少）。

    函数名保留（它仍是「后处理」入口），下面「重写目录页」那一段只在**手上真有目录页**时
    才会跑（判据：某个 xhtml 的 `<h1>` 文本正好是「目录」/「CONTENTS」），正常流程不触发。
    """
    import zipfile
    import shutil
    import tempfile
    import re

    if not chapters:
        log('⚠️ chapters 为空，跳过自定义目录', 'WARN')
        return

    temp_dir = tempfile.mkdtemp(prefix='epub_toc_')
    try:
        with zipfile.ZipFile(epub_path, 'r') as z:
            z.extractall(temp_dir)

        toc_xhtml_path = None
        toc_filename = None
        for root, dirs, files in os.walk(temp_dir):
            for file in files:
                if not file.endswith('.xhtml'):
                    continue
                path = os.path.join(root, file)
                with open(path, 'r', encoding='utf-8') as f:
                    content = f.read()

                if '<h1' not in content:
                    continue
                h1_match = re.search(r'<h1[^>]*>(.*?)</h1>', content, re.DOTALL)
                if not h1_match:
                    continue
                h1_text = re.sub(r'<[^>]+>', '', h1_match.group(1)).strip()
                if h1_text in ('目录', 'CONTENTS'):
                    toc_xhtml_path = path
                    toc_filename = file
                    break
            if toc_xhtml_path:
                break

        # ★★ 2026-09-30 用户定稿：**书里不要目录页**（但保留扉页 title_page.xhtml）。
        #   保证方式在**源头**：generate_epub() 不再往 markdown 里写「# 目录」那一段，
        #   所以 pandoc 根本不会产生目录页；目录功能由阅读器自带的目录菜单提供
        #   （pandoc 生成的 nav.xhtml / toc.ncx 照旧列全部章节）。
        #
        #   原来这里会把「h1 文本 = 目录 / CONTENTS」的那一页**重写**成金色 CONTENTS 设计。
        #   现在**故意什么都不做**：
        #     · 不重写 —— 万一哪本小说的第 1 章章名就叫「目录」，重写就把正文换成假目录页；
        #     · 不删除 —— 同样理由，删了就是丢内容。
        #   真要再改「书里没有目录页」，请改 generate_epub()，别在这里加逻辑。
        if toc_xhtml_path:
            log(f'  ⚠️ 发现疑似目录页 {toc_filename}：按 2026-09-30 的决定不重写、不删除（源头已不生成）')
        else:
            log('  ℹ️ 没有目录页（正常：已不再生成目录页）')

        _finish_epub(epub_path, temp_dir)
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


# ================================================================
# ★★★ 给所有 XHTML 注入内联 !important style（完全替代 epub_style.css）★★★
# ================================================================

def inject_inline_styles(temp_dir):
    text_dir = os.path.join(temp_dir, 'EPUB', 'text')
    if not os.path.exists(text_dir):
        log('  ⚠️ 未找到 text 目录，跳过 inline style 注入', 'WARN')
        return

    # ============ 基础：h1 / p / body ============
    # ★★ 2026-09-30 用户定稿：**与轻小说 epub（linovelib.py）逐字一致** ★★
    #   用户原话：「把 epubgenerator 里的改成一摸一样的样式（不要目录（但要保留 title 页），左对齐，换行）」
    #   · **不写 font-family** → 用阅读器自己的首选字体（原来摁死宋体族）
    #   · 标题：金色 #b8956a · 24px · **不加粗（normal）**· **左对齐**· 上 3.5em / 下 0.1em
    #     （⚠️ 不能只删 bold：`<h1>` 默认就是粗体，必须显式 normal 才真的不粗）
    #   · 正文：16px · line-height 2.0 · 两端对齐 · 首行不缩进
    h1_style = (f'color:{GOLD_HEX} !important; '
                'font-size:24px !important; '
                'font-weight:normal !important; '
                'text-align:left !important; '
                'margin:0 !important; '
                'padding-top:3.5em !important; '
                'padding-bottom:0.1em !important; '
                'font-style:normal !important; '
                'text-decoration:none !important; '
                'border:none !important; '
                'background:none !important')

    p_style = ('color:#1e1e1e !important; '
               'font-size:16px !important; '
               'line-height:2.0 !important; '
               'text-indent:0 !important; '
               'margin:0.5em 0 !important; '
               'text-align:justify !important; '
               'font-style:normal !important; '
               'text-decoration:none !important; '
               'padding:0 !important')

    body_style = 'margin:0 !important'

    # ============ 特殊 h2 ============
    h2_class_styles = [
        ('preface-title-two',
         'font-size:22px !important; '
         'text-align:right !important; '
         'font-weight:normal !important; '
         'color:#cf181a !important; '
         'margin-bottom:3em !important;'),
        ('chapter-title-center',
         'font-weight:normal !important; '
         'font-size:22px !important; '
         'color:#478686 !important; '
         'text-align:center !important; '
         'border-bottom:dashed 1px #478686 !important; '
         'margin-bottom:2em !important; '
         'padding:0.3em 0 !important;'),
        ('feiye-zi',
         'text-align:right !important; '
         'font-size:16px !important; '
         'color:#2F4F4F !important; '
         'margin-top:30% !important; '
         'padding-right:0.5em !important; '
         'border-right:solid 2px #2F4F4F !important; '
         'padding-bottom:0.3em !important; '
         'font-weight:normal !important;'),
    ]

    # ============ 图片 ============
    img_default_style = 'width:100% !important;'
    formula_styles = [
        ('formula-1-5em', 'height:1.5em !important; vertical-align:middle !important;'),
        ('formula-2-5em', 'height:2.5em !important; vertical-align:middle !important;'),
        ('formula-1em',   'height:1em !important; vertical-align:middle !important;'),
        ('formula-2em',   'height:2em !important; vertical-align:middle !important;'),
    ]

    # ============ 图片容器 / 绕排 ============
    div_class_styles = [
        ('duokan-center60',     'width:60% !important; margin:1em auto !important; text-align:center !important;'),
        ('duokan-center30',     'width:30% !important; margin:1em auto !important; text-align:center !important;'),
        ('duokan-center',       'width:80% !important; margin:1em auto !important; text-align:center !important;'),
        ('duokan-float-left30', 'width:30% !important; float:left !important; margin-right:0.5em !important; margin-bottom:0.5em !important; text-align:center !important;'),
        ('duokan-float-left',   'width:50% !important; float:left !important; margin-right:0.5em !important; margin-bottom:0.5em !important; text-align:center !important;'),
        ('duokan-float-right30','width:30% !important; float:right !important; margin-left:0.5em !important; margin-bottom:0.5em !important; text-align:center !important;'),
        ('duokan-float-right',  'width:50% !important; float:right !important; margin-left:0.5em !important; margin-bottom:0.5em !important; text-align:center !important;'),
    ]

    # ============ 表格 ============
    table_style = 'font-size:14px !important; margin:1em auto 2em auto !important;'

    # ============ 音视频 ============
    av_style = 'vertical-align:middle !important;'
    video_shipin_style = 'width:100% !important; margin:1em auto !important; text-align:center !important;'

    # ============ 拼音 ============
    ruby_style = 'ruby-align:center !important; margin-right:0.5em !important;'
    rt_style = "font-family:'DK-SYMBOL','Symbol' !important; font-size:0.5em !important;"

    # ============ 不换行 ============
    nowrap_style = 'white-space:nowrap !important;'

    # ============ 辅助：把 new_style 合并进 tag 的 style 属性 ============
    def _add_style(tag, new_style):
        style_match = re.search(r'style="([^"]*)"', tag)
        if style_match:
            old_style = style_match.group(1)
            if '!important' in old_style:
                return tag
            return re.sub(r'style="[^"]*"', f'style="{new_style}; {old_style}"', tag, count=1)
        else:
            return tag.replace('>', f' style="{new_style}">', 1)

    # ============ 单文件注入逻辑 ============
    def _inject_one(xhtml_path, inject_p=True, inject_deco=True):
        with open(xhtml_path, 'r', encoding='utf-8') as f:
            html = f.read()
        original = html

        # 1. body
        def add_body_style(m):
            tag = m.group(0)
            if 'style=' in tag:
                return tag.replace('style="', f'style="{body_style}; ', 1)
            else:
                return tag.replace('>', f' style="{body_style}">', 1)

        html = re.sub(r'<body[^>]*>', add_body_style, html, count=1)

        # 2. h1
        def add_h1_style(m):
            return _add_style(m.group(0), h1_style)
        html = re.sub(r'<h1[^>]*>', add_h1_style, html)

        # 2.5 ★ 标题**在每个空格处断行**（2026-09-30 用户：「换行」；与轻小说 epub 同一做法）
        #      只动文字节点，标签（含 id / class）原样保留；全角空格 U+3000 同样当断点，&nbsp; 不算。
        #      例：「第一章 初入江湖」→「第一章<br/>初入江湖」；没有空格的标题保持一行。
        def break_title(m):
            open_tag, inner, close_tag = m.group(1), m.group(2), m.group(3)
            parts, pos = [], 0
            for t in re.finditer(r'<[^>]+>', inner):
                parts.append(re.sub(r'[ \u3000]+', '<br/>', inner[pos:t.start()]))
                parts.append(t.group(0))
                pos = t.end()
            parts.append(re.sub(r'[ \u3000]+', '<br/>', inner[pos:]))
            return open_tag + ''.join(parts).strip() + close_tag
        html = re.sub(r'(<h1[^>]*>)(.*?)(</h1>)', break_title, html, flags=re.DOTALL)

        # 3. h2（含特殊类）
        def add_h2_style(m):
            tag = m.group(0)
            style_match = re.search(r'style="([^"]*)"', tag)
            if style_match and '!important' in style_match.group(1):
                return tag
            class_match = re.search(r'class="([^"]*)"', tag)
            cls = class_match.group(1) if class_match else ''
            for hcls, hstyle in h2_class_styles:
                if hcls in cls:
                    return _add_style(tag, h1_style + ' ' + hstyle)
            return _add_style(tag, h1_style)
        html = re.sub(r'<h2[^>]*>', add_h2_style, html)

        # 3.5 装饰线：★ 2026-09-30 改成**一条满宽（100%）1px 金色实线**，紧贴标题下方
        #     （用户：「装饰线改成一条长线，放在标题下面，要最长」）
        #     原来是 `────── ⋅ ──────` 字符画，缺点：粗细/连续性**随阅读器字体变**；
        #     border 是画出来的线，换任何字体都一样。
        if inject_deco:
            deco_line = (
                '<hr style="'
                'border:none !important; '
                f'border-top:1px solid {GOLD_HEX} !important; '
                'height:0 !important; '
                'width:100% !important; '
                'margin:0.9em 0 4em 0 !important; '
                'padding:0 !important; '
                'background:none !important;'
                '"/>'
            )

            def insert_around_h1(m):
                inner = m.group(1)
                inner_text = re.sub(r'<[^>]+>', '', inner).strip()
                full_tag = m.group(0)
                if inner_text == 'CONTENTS':
                    return full_tag
                if 'class="title"' in full_tag:
                    return full_tag
                return full_tag + deco_line

            html = re.sub(r'<h1[^>]*>(.*?)</h1>', insert_around_h1, html, flags=re.DOTALL)

        # 4. p（含 nowrap 特殊类）
        if inject_p:
            def add_p_style(m):
                tag = m.group(0)
                style_match = re.search(r'style="([^"]*)"', tag)
                if style_match and '!important' in style_match.group(1):
                    return tag
                extra = ''
                if 'duokan-text-nowrap' in tag:
                    extra = ' ' + nowrap_style
                if style_match:
                    old_style = style_match.group(1)
                    return re.sub(r'style="[^"]*"', f'style="{p_style}{extra}; {old_style}"', tag, count=1)
                else:
                    return tag.replace('>', f' style="{p_style}{extra}">', 1)
            html = re.sub(r'<p(?:\s[^>]*)?>', add_p_style, html)

        # 5. img
        def add_img_style(m):
            tag = m.group(0)
            class_match = re.search(r'class="([^"]*)"', tag)
            cls = class_match.group(1) if class_match else ''
            for fcls, fstyle in formula_styles:
                if fcls in cls:
                    return _add_style(tag, fstyle)
            return _add_style(tag, img_default_style)
        html = re.sub(r'<img[^>]*?/?>', add_img_style, html)

        # 6. div（图片容器/绕排）
        def add_div_style(m):
            tag = m.group(0)
            class_match = re.search(r'class="([^"]*)"', tag)
            if not class_match:
                return tag
            cls = class_match.group(1)
            for dcls, dstyle in div_class_styles:
                if dcls in cls:
                    return _add_style(tag, dstyle)
            return tag
        html = re.sub(r'<div[^>]*>', add_div_style, html)

        # 7. table
        html = re.sub(r'<table[^>]*>', lambda m: _add_style(m.group(0), table_style), html)

        # 8. video
        def add_video_style(m):
            tag = m.group(0)
            class_match = re.search(r'class="([^"]*)"', tag)
            cls = class_match.group(1) if class_match else ''
            if 'shipin' in cls:
                return _add_style(tag, video_shipin_style + ' ' + av_style)
            return _add_style(tag, av_style)
        html = re.sub(r'<video[^>]*>', add_video_style, html)

        # 9. audio
        html = re.sub(r'<audio[^>]*>', lambda m: _add_style(m.group(0), av_style), html)

        # 10. ruby
        html = re.sub(r'<ruby[^>]*>', lambda m: _add_style(m.group(0), ruby_style), html)

        # 11. rt
        html = re.sub(r'<rt[^>]*>', lambda m: _add_style(m.group(0), rt_style), html)

        return (html != original), html

    # ============ 第一轮：EPUB/text/ 下所有章节 ============
    count = 0
    for fname in sorted(os.listdir(text_dir)):
        if not fname.endswith('.xhtml'):
            continue
        xhtml_path = os.path.join(text_dir, fname)
        changed, html = _inject_one(xhtml_path, inject_p=True, inject_deco=True)
        if changed:
            with open(xhtml_path, 'w', encoding='utf-8') as f:
                f.write(html)
            count += 1

    log(f'  ✅ 已给 {count} 个章节 XHTML 注入内联 !important style')

    # ============ 第二轮：扉页 title_page.xhtml ============
    title_page_candidates = [
        os.path.join(temp_dir, 'EPUB', 'title_page.xhtml'),
        os.path.join(temp_dir, 'EPUB', 'text', 'title_page.xhtml'),
        os.path.join(temp_dir, 'title_page.xhtml'),
    ]
    title_page_path = None
    for cand in title_page_candidates:
        if os.path.exists(cand):
            title_page_path = cand
            break

    if title_page_path:
        changed, html = _inject_one(title_page_path, inject_p=False, inject_deco=False)
        if changed:
            with open(title_page_path, 'w', encoding='utf-8') as f:
                f.write(html)
            log(f'  ✅ 扉页 {os.path.relpath(title_page_path, temp_dir)} 已注入 h1 样式（仅 h1，不动 p）')
        else:
            log(f'  ℹ️ 扉页 {os.path.relpath(title_page_path, temp_dir)} 无需修改')
    else:
        log('  ℹ️ 未找到 title_page.xhtml，跳过扉页处理')


# ================================================================
# ★★★ 清理章节内容中的干扰行 ★★★
# ================================================================

def clean_chapter_content(content):
    lines = content.split('\n')
    cleaned = []

    for line in lines:
        line_clean = line.strip()
        if not line_clean:
            continue

        if re.search(r'第[一二三四五六七八九十百千零0-9]+卷\s*[~—\-]\s*第[一二三四五六七八九十百千零0-9]+败', line_clean):
            continue
        if re.search(r'第[一二三四五六七八九十百千零0-9]+卷\s*[~—\-]第[一二三四五六七八九十百千零0-9]+败', line_clean):
            continue
        if re.search(r'第[一二三四五六七八九十百千零0-9]+卷\s*Intermission', line_clean, re.IGNORECASE):
            continue
        if re.search(r'^Intermission', line_clean, re.IGNORECASE):
            continue
        if re.search(r'第[一二三四五六七八九十百千零0-9]+卷\s*[后记|特典|插图|尾声|序]', line_clean):
            continue
        if re.match(r'^(后记|特典|插图|尾声|序)$', line_clean):
            continue
        if re.search(r'特典', line_clean) and len(line_clean) < 30:
            continue

        cleaned.append(line_clean)

    return '\n'.join(cleaned)


# ================================================================
# 分章正则
# ================================================================
    """AI 智能过滤章节标题（★ 分批 + 详细日志版本，V3 优化规则）"""
    if not candidates or len(candidates) <= 2:
        log('候选标题≤2，跳过 AI 过滤', 'INFO')
        return candidates

    if len(candidates) > max_titles:
        candidates_subset = candidates[:max_titles]
        log(f'候选标题过多({len(candidates)})，截取前{max_titles}个进行AI过滤')
    else:
        candidates_subset = candidates

    candidate_list = []
    for i, c in enumerate(candidates_subset):
        line_idx = c['line']
        start = max(0, line_idx - context_lines)
        end = min(len(lines), line_idx + context_lines + 1)
        context = lines[start:end]
        context_with_marker = []
        for j, line in enumerate(context):
            actual_line_num = start + j
            prefix = '>>> ' if actual_line_num == line_idx else '    '
            context_with_marker.append(f"{prefix}{line.strip()}")
        context_str = '\n'.join(context_with_marker)
        candidate_list.append({
            'index': i + 1,
            'title': c['title'],
            'line': line_idx + 1,
            'context': context_str
        })

    BATCH_SIZE = 2000
    all_keep_indices = []
    use_detailed = context_lines > 0
    total_batches = (len(candidate_list) + BATCH_SIZE - 1) // BATCH_SIZE

    if total_batches > 1:
        log(f'📊 候选共 {len(candidate_list)} 条，将分 {total_batches} 批调用 AI')

    for batch_idx in range(total_batches):
        start_idx = batch_idx * BATCH_SIZE
        end_idx = min(start_idx + BATCH_SIZE, len(candidate_list))
        batch = candidate_list[start_idx:end_idx]

        batch_for_prompt = [
            {'index': j+1, 'title': c['title'], 'line': c['line'], 'context': c['context']}
            for j, c in enumerate(batch)
        ]

        prompt_lines = [f"请分析以下从小说《{title}》中提取的候选章节标题，判断哪些是真正的章节标题。", ""]

        if use_detailed:
            prompt_lines.extend([
                "【判断规则】",
                "1. ★ 章节标题必须是【独立成行】：标题行前面必须是空行（或文件开头），后面也必须是空行（或文件末尾）。",
                "   如果标题行的上一行或下一行【紧接着是非空的正文内容】（即标题被正文包围，前后没有空行分隔），这是【正文里的章节序号字眼】，不是真章节，必须丢弃。",
                "2. 真正的章节标题通常具有以下特征：",
                "   - 简短（一般 ≤ 20 个字）",
                "   - 独立成行，前后有空行或分隔符",
                "   - 格式为：第X章、第X卷、Chapter X、序言、前言、后记、附录等",
                "   - 不包含对话、叙事描述、动作描写",
                "3. 正文中的干扰项（必须丢弃）：",
                "   - 以数字+空格开头（如 '10 '、'3 '），后面跟叙述内容",
                "   - 包含对话标记（如「」、『》、\"\"、''）",
                "   - 前后文是连贯的段落，不是独立标题行",
                "   - 标题行上下都是正文 → 这是正文里的章节序号字眼，丢弃",
                "   - 标题前后是乱码/页眉页脚 → 丢弃",
                "4. ★ 去重规则：如果多个候选标题【完全相同】（包括标点、空格、字符大小写），只保留行号最小的第一条，后面的同标题视为重复并丢弃。",
                "5. ★ 序号连续性：如果相邻保留的章节序号出现大幅跳号（如缺了 50+ 个连续序号），说明可能漏掉了一些章节标题。请回头检查这些跳号区间内的【被丢弃的候选标题】，如果它们满足规则1-2（独立成行+简短），应该【保留】。",
                "【候选标题列表（带上下文，>>> 标记当前候选行）】",
            ])
            for cand in batch_for_prompt:
                prompt_lines.append(f"{cand['index']}. 行号 {cand['line']}，标题：\"{cand['title']}\"")
                prompt_lines.append("   上下文：")
                for line in cand['context'].split('\n'):
                    prompt_lines.append(f"       {line}")
                prompt_lines.append("")
        else:
            prompt_lines.extend([
                "【判断规则】",
                "1. ★ 章节标题必须【独立成行】（前/后有空行或文件边界）。标题被正文包围=正文里的章节序号字眼，必须丢弃。",
                "2. 真章节标题：简短（≤20字）、格式为第X章/第X卷/Chapter X/序言/后记等",
                "3. 干扰项（丢弃）：以数字+空格开头、含对话标记、前后文连贯叙述、上下都是正文",
                "4. ★ 去重：完全相同的标题只保留行号最小的第一条，后面同标题丢弃",
                "5. ★ 序号连续性：相邻序号大幅跳号（如缺50+个）→ 回头检查被丢弃的候选，独立成行的应该保留",
                "",
                "【候选标题列表】",
            ])
            for cand in batch_for_prompt:
                prompt_lines.append(f"{cand['index']}. \"{cand['title']}\" (行号: {cand['line']})")
            prompt_lines.append("")

        prompt_lines.extend([
            "【任务】",
            "请返回一个JSON数组，只包含真正是章节标题的序号（从1开始）。",
            "示例：[1, 3, 5, 7]",
            "只返回JSON数组，不要有其他内容。",
        ])
        prompt = "\n".join(prompt_lines)

        if total_batches > 1:
            log(f'  📤 第 {batch_idx+1}/{total_batches} 批：{len(batch)} 条候选（prompt={len(prompt)} 字符）')

        result = call_deepseek(prompt)
        if not result:
            log(f'  ⚠️ 第 {batch_idx+1} 批 AI 调用失败，该批保留全部候选', 'WARN')
            for j in range(len(batch)):
                all_keep_indices.append(start_idx + j)
            continue

        try:
            result = re.sub(r'```json\s*', '', result)
            result = re.sub(r'```\s*', '', result)
            json_match = re.search(r'\[[0-9,\s]+\]', result)
            if json_match:
                result = json_match.group(0)
            indices = json.loads(result.strip())
            if not isinstance(indices, list):
                log(f'  ⚠️ 第 {batch_idx+1} 批 AI 返回格式错误，该批保留全部候选', 'WARN')
                for j in range(len(batch)):
                    all_keep_indices.append(start_idx + j)
                continue

            batch_keep = [i - 1 for i in indices if 1 <= i <= len(batch)]
            log(f'  ✅ 第 {batch_idx+1} 批 AI 过滤：{len(batch)} → {len(batch_keep)} 条')

            batch_kept_set = set(batch_keep)
            for j, c in enumerate(batch):
                if j not in batch_kept_set:
                    log(f'     🗑️ 丢弃: "{c["title"]}" (行 {c["line"]})')

            for j in batch_keep:
                all_keep_indices.append(start_idx + j)

        except Exception as e:
            log(f'  ⚠️ 第 {batch_idx+1} 批 AI 结果解析失败: {e}，该批保留全部候选', 'WARN')
            for j in range(len(batch)):
                all_keep_indices.append(start_idx + j)

    all_keep_indices = sorted(set(all_keep_indices))
    log(f'📊 AI 过滤总结果：{len(candidates_subset)} → {len(all_keep_indices)} (保留 {len(all_keep_indices)/max(len(candidates_subset),1)*100:.1f}%)')

    if len(all_keep_indices) < 2:
        log('AI 过滤后标题太少，使用所有候选', 'WARN')
        return candidates

    kept_set = set(all_keep_indices)
    dropped_titles = [(c['title'], c['line']) for i, c in enumerate(candidates_subset) if i not in kept_set]
    if dropped_titles:
        log(f'🗑️ AI 共丢弃 {len(dropped_titles)} 个标题（前 20 个）：')
        for t, line in dropped_titles[:20]:
            log(f'   - "{t}" (行 {line})')
        if len(dropped_titles) > 20:
            log(f'   ... 还有 {len(dropped_titles) - 20} 个')

    return [candidates[i] for i in all_keep_indices]


# ================================================================
# 分章正则
# ================================================================

DEFAULT_CHAPTER_PATTERN = (
    r'(?i)(?:序言|前言|序|后记|附录|'
    r'第[一二三四五六七八九十百千零贰叁肆伍陆柒捌玖拾]+卷|'
    r'第[0-9]+卷|'
    r'第[一二三四五六七八九十百千零贰叁肆伍陆柒捌玖拾]+章|'
    r'第[0-9]+章|'
    r'(?:chapter|chap|ch|ch\.)\s*[一二三四五六七八九十百千零贰叁肆伍陆柒捌玖拾0-9]+)'
)


def split_chapters_by_regex(lines, regex_pattern=None, chapter_markers=None, max_title_length=25, use_ai_filter=False, book_title=''):
    chapters = []

    if chapter_markers and len(chapter_markers) > 0:
        log(f'使用用户标记分章，共 {len(chapter_markers)} 个标记')
        sorted_markers = sorted(chapter_markers, key=lambda x: x.get('line', 0))
        total_lines = len(lines)

        for idx, marker in enumerate(sorted_markers):
            start_line = marker.get('line', 0) - 1
            title = marker.get('title', f'第{idx+1}章')

            if idx + 1 < len(sorted_markers):
                end_line = sorted_markers[idx + 1].get('line', 0) - 1
            else:
                end_line = total_lines

            # ★ 修复：content_lines 原来只在 if 分支赋值。
            #   marker 越界时：首个越界 → UnboundLocalError；后续越界 →
            #   残留上一章的值，本章正文被替换成别章内容（静默产出错书）。
            content_lines = []
            if 0 <= start_line < total_lines:
                content_lines = lines[start_line:end_line]
                content = '\n'.join(content_lines)
            else:
                log(f'⚠️ 章节标记越界已跳过：{title}（行号 {start_line + 1} 超出 {total_lines}）', 'WARN')
                content = ''

            if content_lines and content_lines[0].strip() == title:
                content = '\n'.join(content_lines[1:])

            content = clean_chapter_content(content)

            chapters.append({
                'title': title,
                'content': content.strip(),
                'line': start_line + 1
            })
        return chapters

    if regex_pattern is None:
        regex_pattern = DEFAULT_CHAPTER_PATTERN
        log(f'使用默认正则规则')
    else:
        log(f'使用自定义正则: {regex_pattern[:80]}...')

    try:
        pattern = re.compile(regex_pattern, re.IGNORECASE)
        log(f'✅ 正则编译成功')
    except Exception as e:
        log(f'⚠️ 正则编译失败: {e}，使用默认规则', 'WARN')
        pattern = re.compile(DEFAULT_CHAPTER_PATTERN, re.IGNORECASE)

    strict_pattern = re.compile(
        r'(?i)(?:'
        r'序言|前言|序|后记|附录'
        r'|第[一二三四五六七八九十百千零贰叁肆伍陆柒捌玖拾0-9]+(?:章|回|节|卷)'
        r'|(?:chapter|chap)\s*[一二三四五六七八九十百千零贰叁肆伍陆柒捌玖拾0-9]+'
        r')'
        r'(?=\s|$)'
    )

    matched_lines = []
    length_filtered_count = 0
    for i, line in enumerate(lines):
        line_clean = line.strip()
        if not line_clean:
            continue
        if len(line_clean) > max_title_length:
            length_filtered_count += 1
            continue
        if not strict_pattern.match(line_clean):
            continue
        matched_lines.append({
            'title': line_clean,
            'line': i
        })

    log(f'严格匹配到 {len(matched_lines)} 个章节标题（必须严格开头，且 ≤{max_title_length}字）')
    if length_filtered_count > 0:
        log(f'🛡️ 字数过滤：丢弃 {length_filtered_count} 个超过 {max_title_length} 字的候选（长段一定是正文）')

    deduped = []
    seen_titles = set()
    for m in matched_lines:
        title_key = m['title'].strip()
        if title_key not in seen_titles:
            seen_titles.add(title_key)
            deduped.append(m)
        else:
            log(f'🛡️ Python 硬规则去重：删除重复 "{m["title"]}" (行 {m["line"]})')
    if len(deduped) < len(matched_lines):
        log(f'🛡️ Python 去重：移除 {len(matched_lines) - len(deduped)} 个重复章节')
    matched_lines = deduped

    if not matched_lines:
        log('没有匹配到任何章节，返回全文', 'WARN')
        return [{
            'title': '全文',
            'content': '\n'.join(lines),
            'line': 0
        }]

    total_lines = len(lines)
    for idx, marker in enumerate(matched_lines):
        start_line = marker['line']
        title = marker['title']

        if idx + 1 < len(matched_lines):
            end_line = matched_lines[idx + 1]['line']
        else:
            end_line = total_lines

        content_lines = lines[start_line + 1:end_line]
        content = '\n'.join(content_lines)

        if content_lines and content_lines[0].strip() == title:
            content = '\n'.join(content_lines[1:])

        content = clean_chapter_content(content)

        chapters.append({
            'title': title,
            'content': content.strip(),
            'line': start_line + 1
        })

    return chapters


# ================================================================
# ★★★ 查找 Pandoc ★★★
# ================================================================

def _bundled_pandoc_candidates():
    """
    随包分发的免安装 Pandoc（pandoc-3.10.2/pandoc.exe）。

    命中这里，用户就不需要自己装 Pandoc：
      * 环境变量 EASYPUB_PANDOC 手动指定（最高优先级）
      * 开发环境：脚本同级的 pandoc-3.10.2/
      * 打包环境：resources/pandoc-3.10.2/（后端 exe 的上一级）
    """
    candidates = []

    env_path = os.environ.get('EASYPUB_PANDOC')
    if env_path:
        candidates.append(env_path)

    here = os.path.dirname(os.path.abspath(__file__))
    roots = [here, os.path.dirname(here)]

    if getattr(sys, 'frozen', False):
        # <resources>/backend/easypub-backend.exe → <resources>/
        exe_dir = os.path.dirname(os.path.abspath(sys.executable))
        roots += [exe_dir, os.path.dirname(exe_dir)]

    meipass = getattr(sys, '_MEIPASS', None)
    if meipass:
        roots += [meipass, os.path.dirname(meipass)]

    for root in roots:
        candidates.append(os.path.join(root, 'pandoc-3.10.2', 'pandoc.exe'))
        candidates.append(os.path.join(root, 'pandoc', 'pandoc.exe'))

    seen = set()
    out = []
    for c in candidates:
        c = os.path.normpath(c)
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out


def find_pandoc():
    # ① 随包分发的免安装 Pandoc（打包后走这里，用户无需自己安装）
    for p in _bundled_pandoc_candidates():
        if os.path.isfile(p):
            return p

    # ② 退路：系统 PATH / 常见安装目录（开发机、或用户自己装过 Pandoc）
    try:
        result = subprocess.run(['pandoc', '--version'], capture_output=True, timeout=5)
        if result.returncode == 0:
            return 'pandoc'
    except:
        pass

    try:
        result = subprocess.run(['where', 'pandoc'], capture_output=True, timeout=5,
                                encoding='utf-8', errors='ignore')
        if result.returncode == 0:
            for line in result.stdout.split('\n'):
                p = line.strip()
                if p and os.path.exists(p):
                    return p
    except:
        pass

    userprofile = os.environ.get('USERPROFILE', 'C:\\Users\\Default')
    paths = [
        os.path.join(userprofile, 'AppData', 'Local', 'Pandoc', 'pandoc.exe'),
        'C:\\Program Files\\Pandoc\\pandoc.exe',
        'C:\\Program Files (x86)\\Pandoc\\pandoc.exe',
        'D:\\Program Files\\Pandoc\\pandoc.exe',
        'C:\\Pandoc\\pandoc.exe',
    ]
    for p in paths:
        if os.path.exists(p):
            return p
    return None


# ================================================================
# ★★★ 生成 EPUB（完全弃用外部 CSS） ★★★
# ================================================================

def generate_epub(title, author, chapters, output_path, cover_path=None, options=None):
    if options is None:
        options = {}

    log(f'========== 开始生成 EPUB ==========')
    log(f'书名: {title}, 作者: {author}, 章节数: {len(chapters)}')

    pandoc_path = find_pandoc()
    if not pandoc_path:
        log('❌ 未找到 Pandoc，请安装: https://pandoc.org/installing.html', 'ERROR')
        return False

    log(f'✅ 使用 Pandoc: {pandoc_path}')

    temp_md = None
    try:
        fd, temp_md = tempfile.mkstemp(suffix='.md', text=True)
        os.close(fd)

        with open(temp_md, 'w', encoding='utf-8') as f:
            f.write('---\n')
            f.write(f'title: "{title}"\n')
            f.write(f'author: "{author}"\n')
            f.write(f'date: "{datetime.now().strftime("%Y-%m-%d")}"\n')
            f.write('lang: zh-CN\n')
            f.write('---\n\n')

            # ★★ 2026-09-30 用户定稿：**不再生成目录页**（「不要目录（但要保留 title 页）」）。
            #   原来这里会写「# 目录 + 章节列表 + {.toc-list} + \pagebreak」，pandoc 据此生成
            #   一个 ch001.xhtml 目录页（再由 customize_toc 重写成金色 CONTENTS）。
            #   现在整段删掉 → 书里根本没有目录页；扉页 title_page.xhtml 由 YAML 元数据生成，
            #   **照旧保留**；阅读器的「目录」菜单走 pandoc 生成的 nav.xhtml / toc.ncx，一页不少。
            for idx, ch in enumerate(chapters):
                ch_title = ch['title']
                ch_content = ch['content']
                anchor = f'ch-{idx + 1}'

                f.write(f'# {ch_title} {{#{anchor}}}\n\n')

                paragraphs = ch_content.split('\n')
                for para in paragraphs:
                    if para.strip():
                        f.write(f'{para}\n\n')

        log(f'📤 临时文件: {temp_md}')

        cmd = [
            pandoc_path,
            temp_md,
            '-o', output_path,
            '--wrap=preserve',
            '--metadata', 'lang=zh-CN',
            '--epub-chapter-level=1',
        ]

        if cover_path and os.path.exists(cover_path):
            cmd.append(f'--epub-cover-image={cover_path}')
            log(f'📤 使用封面: {cover_path}')

        log(f'📤 执行命令: {" ".join(cmd)}')
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=10000,
            encoding='utf-8',
            errors='ignore'
        )

        if result.returncode != 0:
            log(f'❌ Pandoc 返回码: {result.returncode}', 'ERROR')
            log(f'❌ stderr: {result.stderr}', 'ERROR')
            log(f'❌ stdout: {result.stdout}', 'ERROR')
            return False

        log(f'✅ EPUB 生成成功，开始注入内联样式（与轻小说 epub 同一套）+ 重新打包...')
        customize_toc(output_path, chapters=chapters)
        log(f'✅ 处理完成: {output_path}')
        return True

    except subprocess.TimeoutExpired:
        log('❌ Pandoc 超时（10000秒）', 'ERROR')
        return False
    except Exception as e:
        log(f'❌ 生成失败: {e}', 'ERROR')
        import traceback
        traceback.print_exc(file=sys.stderr)
        return False
    finally:
        if temp_md and os.path.exists(temp_md):
            try:
                os.unlink(temp_md)
            except:
                pass


# ================================================================
# ★★★ 兼容 main.js 调用的函数名 ★★★
# ================================================================

def generate_epub_with_pandoc(title, author, chapters, output_path, cover_path=None):
    return generate_epub(title, author, chapters, output_path, cover_path, None)


# ================================================================
# 主入口
# ================================================================

if __name__ == '__main__':
    print('[DEBUG] ===== Python 脚本启动 =====', file=sys.stderr, flush=True)
    log('🚀 Python 脚本已启动', 'DEBUG')

    try:
        input_data = sys.stdin.read()
        log(f'📥 收到原始数据长度: {len(input_data)} 字符', 'DEBUG')

        if not input_data:
            log('⚠️ 没有收到任何输入数据', 'WARN')
            print(json.dumps({'success': False, 'error': '没有收到输入数据'}))
            sys.exit(1)

        params = json.loads(input_data)
        log(f'📥 解析到的参数: {list(params.keys())}', 'DEBUG')

        title = params.get('title', '未命名')
        author = params.get('author', '未知作者')
        txt_path = params.get('txt_path')
        output_path = params.get('output_path', 'output.epub')
        cover_path = params.get('cover_path', None)

        regex_pattern = params.get('regex_pattern', None)
        chapter_markers = params.get('chapter_markers', [])
        custom_pattern = params.get('custom_pattern', None)
        max_title_length = params.get('max_title_length', 25)
        use_ai_filter = params.get('use_ai_filter', True)
        preview_only = params.get('preview_only', False)
        generate_epub_flag = params.get('generate_epub', False)

        log(f'📥 cover_path = {repr(cover_path)}', 'DEBUG')
        log(f'📥 generate_epub_flag = {generate_epub_flag}', 'DEBUG')
        log(f'📥 preview_only = {preview_only}', 'DEBUG')

        if not txt_path or not os.path.exists(txt_path):
            print(json.dumps({'success': False, 'error': f'TXT 文件不存在: {txt_path}'}))
            sys.exit(1)

        encoding = detect_encoding(txt_path)
        with open(txt_path, 'r', encoding=encoding) as f:
            content = f.read()
        lines = content.split('\n')

        if custom_pattern:
            regex_pattern = custom_pattern
            log(f'使用用户自定义规则: {custom_pattern}')

        log('📤 开始分章...', 'DEBUG')
        chapters = split_chapters_by_regex(
            lines,
            regex_pattern,
            chapter_markers,
            max_title_length,
            use_ai_filter,
            title
        )

        if not chapters:
            print(json.dumps({'success': False, 'error': '没有检测到章节'}))
            sys.exit(1)

        if preview_only:
            log('📤 preview_only=True，返回章节列表', 'DEBUG')
            print(json.dumps({
                'success': True,
                'chapter_count': len(chapters),
                'chapters': chapters
            }))
            sys.exit(0)

        if not generate_epub_flag:
            log('📤 generate_epub_flag=False，返回章节列表', 'DEBUG')
            print(json.dumps({
                'success': True,
                'chapter_count': len(chapters),
                'chapters': chapters
            }))
            sys.exit(0)

        log('📤 开始生成 EPUB...', 'DEBUG')
        success = generate_epub(title, author, chapters, output_path, cover_path, None)

        if success:
            print(json.dumps({
                'success': True,
                'output_path': output_path,
                'chapter_count': len(chapters),
                'chapters': chapters
            }))
        else:
            print(json.dumps({'success': False, 'error': 'Pandoc 生成失败，请确保已安装 Pandoc'}))

    except json.JSONDecodeError as e:
        log(f'⚠️ JSON 解析失败: {e}', 'ERROR')
        print(json.dumps({'success': False, 'error': f'JSON 解析失败: {str(e)}'}))
    except Exception as e:
        import traceback
        traceback.print_exc(file=sys.stderr)
        print(json.dumps({
            'success': False,
            'error': str(e)
        }))