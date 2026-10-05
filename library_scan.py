#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
书库后端 —— 扫描 export 目录、读 EPUB 元数据与封面、改元数据、删除

为什么用 Python 而不是 Node：
    EPUB 就是一个 zip，Python 标准库自带 zipfile，零新增依赖。
    Node 侧要么加 adm-zip 之类的包，要么自己写 zip 解析，都不划算。

对外接口（与其它后端脚本一致：stdin 读 JSON，stdout 吐 JSON）：
    {"action": "scan",   "dir": "..."}
    {"action": "cover",  "path": "...", "cache_dir": "..."}
    {"action": "update", "path": "...", "title": "...", "author": "...", "description": "..."}
    {"action": "delete", "paths": ["...", "..."]}
"""

import io
import json
import os
import re
import shutil
import sys
import zipfile

if sys.platform == 'win32':
    try:
        sys.stdin = io.TextIOWrapper(sys.stdin.buffer, encoding='utf-8')
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')
    except Exception:
        pass

COVER_EXT = ('.jpg', '.jpeg', '.png', '.gif', '.webp')


def log(msg):
    print(msg, file=sys.stderr)


# ================================================================
# EPUB 解析
# ================================================================
def _find_opf(z):
    """从 META-INF/container.xml 找到 OPF 路径。"""
    try:
        xml = z.read('META-INF/container.xml').decode('utf-8', 'ignore')
        m = re.search(r'full-path\s*=\s*"([^"]+)"', xml)
        if m:
            return m.group(1)
    except KeyError:
        pass
    # 退路：直接在包里找 .opf
    for n in z.namelist():
        if n.lower().endswith('.opf'):
            return n
    return None


def _tag_text(xml, tag):
    """取 <dc:xxx> 或 <xxx> 的文本，容忍命名空间前缀。"""
    for pat in (r'<%s[^>]*>(.*?)</%s>' % (tag, tag),
                r'<dc:%s[^>]*>(.*?)</dc:%s>' % (tag, tag)):
        m = re.search(pat, xml, re.S | re.I)
        if m:
            v = re.sub(r'<[^>]+>', '', m.group(1))
            return v.strip()
    return ''


def _find_cover_href(z, opf_path, opf_xml):
    """找出封面图在包内的路径。"""
    base = os.path.dirname(opf_path)

    # ① EPUB3：manifest 里 properties="cover-image"
    m = re.search(r'<item\b[^>]*properties\s*=\s*"[^"]*cover-image[^"]*"[^>]*>', opf_xml, re.I)
    if not m:
        # 属性顺序可能相反，再扫一遍所有 item
        for it in re.findall(r'<item\b[^>]*>', opf_xml, re.I):
            if 'cover-image' in it:
                m = re.match(r'.*', it)
                break
    href = None
    if m:
        h = re.search(r'href\s*=\s*"([^"]+)"', m.group(0))
        if h:
            href = h.group(1)

    # ② EPUB2：<meta name="cover" content="ID"/> → manifest 里找该 id
    if not href:
        mc = re.search(r'<meta\b[^>]*name\s*=\s*"cover"[^>]*content\s*=\s*"([^"]+)"', opf_xml, re.I)
        if mc:
            cid = mc.group(1)
            mi = re.search(r'<item\b[^>]*id\s*=\s*"%s"[^>]*>' % re.escape(cid), opf_xml, re.I)
            if mi:
                h = re.search(r'href\s*=\s*"([^"]+)"', mi.group(0))
                if h:
                    href = h.group(1)

    # ③ 兜底：manifest 里第一个图片，名字里带 cover
    if not href:
        for it in re.findall(r'<item\b[^>]*>', opf_xml, re.I):
            h = re.search(r'href\s*=\s*"([^"]+)"', it)
            if h and h.group(1).lower().endswith(COVER_EXT) and 'cover' in h.group(1).lower():
                href = h.group(1)
                break

    if not href:
        return None
    return os.path.normpath(os.path.join(base, href)).replace('\\', '/')


def read_meta(path):
    """读一本 EPUB 的元数据 + 封面在包内的路径。"""
    info = {'title': '', 'author': '', 'description': '', 'language': '',
            'cover_href': None, 'opf_path': None, 'ok': False}
    try:
        with zipfile.ZipFile(path) as z:
            opf_path = _find_opf(z)
            if not opf_path:
                return info
            opf = z.read(opf_path).decode('utf-8', 'ignore')
            info['opf_path'] = opf_path
            info['title'] = _tag_text(opf, 'title')
            info['author'] = _tag_text(opf, 'creator')
            info['description'] = _tag_text(opf, 'description')
            info['language'] = _tag_text(opf, 'language')
            info['cover_href'] = _find_cover_href(z, opf_path, opf)
            info['ok'] = True
    except Exception as exc:                       # noqa: BLE001
        log('读取失败 %s: %s' % (os.path.basename(path), exc))
    return info


def extract_cover(path, cache_dir):
    """把封面图导出到缓存目录，返回缓存文件路径。"""
    info = read_meta(path)
    if not info['cover_href']:
        return None
    os.makedirs(cache_dir, exist_ok=True)
    stem = os.path.splitext(os.path.basename(path))[0]
    ext = os.path.splitext(info['cover_href'])[1].lower() or '.jpg'
    out = os.path.join(cache_dir, '%s%s' % (stem, ext))
    # 已缓存且比 EPUB 新就复用
    try:
        if os.path.exists(out) and os.path.getmtime(out) >= os.path.getmtime(path):
            return out
    except OSError:
        pass
    try:
        with zipfile.ZipFile(path) as z:
            data = z.read(info['cover_href'])
        with open(out, 'wb') as f:
            f.write(data)
        return out
    except Exception as exc:                       # noqa: BLE001
        log('导出封面失败 %s: %s' % (stem, exc))
        return None


# ================================================================
# 动作
# ================================================================
def do_scan(params):
    d = params.get('dir')
    if not d or not os.path.isdir(d):
        return {'success': False, 'error': '目录不存在: %s' % d}

    cache_dir = os.path.join(d, '.covers')
    books = []
    for name in sorted(os.listdir(d)):
        if name.startswith('.'):
            continue
        full = os.path.join(d, name)
        if not os.path.isfile(full):
            continue
        ext = os.path.splitext(name)[1].lower()
        # ★ 只收 EPUB：书库是成品书架，TXT 是原材料（在制作流程里）。
        #   三个传输通道也都只吃 EPUB，混着 TXT 会让用户选中却传不了。
        if ext != '.epub':
            continue
        try:
            st = os.stat(full)
        except OSError:
            continue

        item = {
            'file': name,
            'path': full,
            'type': ext.lstrip('.'),
            'size': st.st_size,
            'mtime': int(st.st_mtime),
            'title': os.path.splitext(name)[0],
            'author': '',
            'description': '',
            'language': '',
            'cover': None,
        }
        meta = read_meta(full)
        if meta['ok']:
            # ★★ 2026-09-30（用户要求）：**书库标题以文件名为准**。
            #   原因：轻小说按卷下载，文件名带卷号（`弹珠汽水瓶里的千岁同学 第 4 卷.epub`），
            #   而 dc:title 是**纯书名**（用户明确要求"书内标题不带卷名"）—— 于是书库里
            #   四卷全显示成同一个名字，看不出是哪一卷（用户截图实锤）。
            #   规则：OPF 标题**是文件名的子串**时用文件名（信息更全）；否则仍用 OPF 标题
            #   （文件名被手动改过、或跟书名无关时，别乱改）。
            _meta_title = (meta['title'] or '').strip()
            _stem_title = (item['title'] or '').strip()
            if _meta_title and _stem_title and _meta_title in _stem_title:
                item['title'] = _stem_title
            elif _meta_title:
                item['title'] = _meta_title
            item['author'] = meta['author']
            item['description'] = meta['description']
            item['language'] = meta['language']
        item['cover'] = extract_cover(full, cache_dir)
        books.append(item)

    log('扫描完成：%d 本' % len(books))
    return {'success': True, 'dir': d, 'books': books}


def do_cover(params):
    p = params.get('path')
    cache_dir = params.get('cache_dir') or os.path.join(os.path.dirname(p), '.covers')
    if not p or not os.path.isfile(p):
        return {'success': False, 'error': '文件不存在'}
    cover = extract_cover(p, cache_dir)
    return {'success': bool(cover), 'cover': cover}


def do_update(params):
    """改写 EPUB 里的 dc:title / dc:creator / dc:description。"""
    p = params.get('path')
    if not p or not os.path.isfile(p):
        return {'success': False, 'error': '文件不存在'}

    title = params.get('title')
    author = params.get('author')
    desc = params.get('description')

    try:
        with zipfile.ZipFile(p) as z:
            opf_path = _find_opf(z)
            if not opf_path:
                return {'success': False, 'error': '这个 EPUB 里找不到 OPF'}
            opf = z.read(opf_path).decode('utf-8', 'ignore')
            items = [(i, z.read(i.filename)) for i in z.infolist()]
    except Exception as exc:                       # noqa: BLE001
        return {'success': False, 'error': '打开失败: %s' % exc}

    def esc(s):
        return (s.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;'))

    def set_tag(xml, tag, value):
        """替换已有标签，没有就插到 </metadata> 前。"""
        pat = r'(<(%s|dc:%s)[^>]*>)(.*?)(</(%s|dc:%s)>)' % (tag, tag, tag, tag)
        if re.search(pat, xml, re.S | re.I):
            return re.sub(pat, lambda m: m.group(1) + esc(value) + m.group(4),
                          xml, count=1, flags=re.S | re.I)
        ins = '<dc:%s>%s</dc:%s>' % (tag, esc(value), tag)
        return re.sub(r'</metadata>', ins + '</metadata>', xml, count=1, flags=re.I)

    if title is not None:
        opf = set_tag(opf, 'title', title)
    if author is not None:
        opf = set_tag(opf, 'creator', author)
    if desc is not None:
        opf = set_tag(opf, 'description', desc)

    tmp = p + '.tmp'
    try:
        with zipfile.ZipFile(tmp, 'w', zipfile.ZIP_DEFLATED) as z:
            # mimetype 必须是第一个且不压缩
            if any(i.filename == 'mimetype' for i, _ in items):
                z.writestr(zipfile.ZipInfo('mimetype'), b'application/epub+zip',
                           compress_type=zipfile.ZIP_STORED)
            for info, data in items:
                if info.filename == 'mimetype':
                    continue
                if info.filename == opf_path:
                    data = opf.encode('utf-8')
                z.writestr(info, data)
        os.replace(tmp, p)
    except Exception as exc:                       # noqa: BLE001
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        return {'success': False, 'error': '写入失败: %s' % exc}

    log('元数据已更新：%s' % os.path.basename(p))
    return {'success': True, 'path': p}


def do_delete(params):
    paths = params.get('paths') or []
    deleted, failed = [], []
    for p in paths:
        try:
            if os.path.isfile(p):
                os.remove(p)
                deleted.append(p)
            else:
                failed.append(p)
        except Exception as exc:                   # noqa: BLE001
            log('删除失败 %s: %s' % (p, exc))
            failed.append(p)
    log('删除完成：成功 %d，失败 %d' % (len(deleted), len(failed)))
    return {'success': True, 'deleted': deleted, 'failed': failed}


ACTIONS = {
    'scan': do_scan,
    'cover': do_cover,
    'update': do_update,
    'delete': do_delete,
}


def main():
    try:
        raw = sys.stdin.read()
        if not raw:
            print(json.dumps({'success': False, 'error': '未收到输入数据'}))
            return
        raw = raw.lstrip('\ufeff').strip()
        params = json.loads(raw)
        action = params.get('action', 'scan')
        fn = ACTIONS.get(action)
        if not fn:
            print(json.dumps({'success': False, 'error': '未知动作: %s' % action}))
            return
        print(json.dumps(fn(params), ensure_ascii=False))
    except Exception as exc:                       # noqa: BLE001
        import traceback
        traceback.print_exc(file=sys.stderr)
        print(json.dumps({'success': False, 'error': str(exc)}))


if __name__ == '__main__':
    main()
