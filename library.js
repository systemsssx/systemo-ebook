/* ============================================================
   library.js — 书库板块逻辑
   ------------------------------------------------------------
   数据来自主进程：electronAPI.library.scan()
   后端是 Python 的 library_scan.py（EPUB 就是 zip，用标准库读）
   ============================================================ */
(function () {
    'use strict';

    var API = window.electronAPI && window.electronAPI.library;

    /* ---------- 演示数据 ----------
       只在"浏览器里直接打开 index.html"时启用（此时 window.electronAPI 不存在），
       用来单独调书库的界面。真实运行在 Electron 里，API 一定存在，这段不会执行。 */
    var S = {
        books: [],
        sel: {},          // path -> true
        multi: false,
        filter: 'all',
        q: '',
        cur: null,        // 详情弹窗当前编辑的对象
        loaded: false
    };

    var $ = function (id) { return document.getElementById(id); };

    /* ---------- 工具 ---------- */
    function fileUrl(p) {
        if (!p) return null;
        return 'file:///' + String(p).replace(/\\/g, '/').replace(/^\/+/, '');
    }

    function fmtSize(n) {
        if (!n) return '—';
        if (n < 1024) return n + ' B';
        if (n < 1048576) return (n / 1024).toFixed(0) + ' KB';
        return (n / 1048576).toFixed(1) + ' MB';
    }

    function fmtTime(ts) {
        if (!ts) return '—';
        var d = new Date(ts * 1000);
        var p = function (x) { return (x < 10 ? '0' : '') + x; };
        return (d.getMonth() + 1) + '-' + p(d.getDate()) + ' ' + p(d.getHours()) + ':' + p(d.getMinutes());
    }

    // 没封面时按书名生成一个稳定的渐变色，避免整片灰
    function placeholder(title) {
        var h = 0, s = title || '?';
        for (var i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) % 360;
        return 'linear-gradient(165deg, hsl(' + h + ',34%,34%), hsl(' + ((h + 28) % 360) + ',40%,13%))';
    }

    function esc(s) {
        return String(s == null ? '' : s)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;');
    }

    // ★ 修复：本文件是独立文件的严格模式 IIFE，拿不到 index.html 里的 T()，
    //   所以之前所有界面文案都写死中文，英文模式下会露出中文。
    //   这里走 i18n.js 暴露的全局入口（i18n.js 是 defer 加载，调用发生在运行时，安全）。
    function T(s) {
        return (window.__i18n && window.__i18n.t) ? window.__i18n.t(s) : s;
    }

    /* ---------- 数据 ---------- */
    function load(force) {
        if (!API) {
            console.warn('⚠️ library API 未连接');
            render();
            return;
        }
        if (S.loaded && !force) return;
        $('libCount').textContent = T('扫描中…');
        API.scan().then(function (r) {
            if (r && r.success) {
                S.books = r.books || [];
                S.loaded = true;
                // 清掉已经不存在的选中项
                var alive = {};
                S.books.forEach(function (b) { if (S.sel[b.path]) alive[b.path] = true; });
                S.sel = alive;
                console.log('📚 书库加载:', S.books.length, '项');
            } else {
                console.warn('⚠️ 书库扫描失败:', r && r.error);
                S.books = [];
            }
            render();
        }).catch(function (e) {
            console.warn('⚠️ 书库扫描异常:', e.message);
            S.books = [];
            render();
        });
    }

    function visible() {
        var q = S.q.trim().toLowerCase();
        return S.books.filter(function (b) {
            if (q && (b.title || '').toLowerCase().indexOf(q) < 0 &&
                (b.author || '').toLowerCase().indexOf(q) < 0 &&
                (b.file || '').toLowerCase().indexOf(q) < 0) {
                return false;
            }
            if (q) return true;
            return (b.title || '').toLowerCase().indexOf(q) >= 0 ||
                   (b.author || '').toLowerCase().indexOf(q) >= 0 ||
                   (b.file || '').toLowerCase().indexOf(q) >= 0;
        });
    }

    /* ---------- 用内置阅读器打开 ---------- */
    function toast(msg) {
        var t = $('libToast');
        if (!t) { console.log(msg); return; }
        t.textContent = msg;
        t.classList.add('show');
        clearTimeout(toast._t);
        toast._t = setTimeout(function () { t.classList.remove('show'); }, 3200);
    }
    function openInReader(path) {
        if (!window.electronAPI || !window.electronAPI.library ||
            !window.electronAPI.library.openReader) {
            toast('阅读器不可用（preload 未加载）');
            return;
        }
        // 记录一下：单击已经开过阅读器时，紧接着的那次 dblclick 不再重复开
        openInReader._last = path;
        openInReader._lastAt = Date.now();
        window.electronAPI.library.openReader(path).then(function (r) {
            if (!r || !r.success) toast((r && r.error) || '打开失败');
        }).catch(function (e) {
            toast('打开失败：' + (e && e.message ? e.message : e));
        });
    }

    /* ---------- 渲染 ---------- */
    function render() {
        var grid = $('libGrid');
        var list = visible();
        var total = S.books.length;

        $('libCount').innerHTML = total
            ? '<b>' + total + '</b> ' + T('本')
            : '0 ' + T('本');

        if (!total) {
            grid.innerHTML = '';
            $('libEmpty').style.display = 'flex';
            $('libEmpty').querySelector('.ie-t').textContent = T('书库是空的');
            $('libEmpty').querySelector('.ie-s').textContent =
                T('在「制作」里转换成功的 EPUB 会自动出现在这里');
            syncBulk();
            return;
        }
        if (!list.length) {
            grid.innerHTML = '';
            $('libEmpty').style.display = 'flex';
            $('libEmpty').querySelector('.ie-t').textContent = T('没有匹配的书');
            $('libEmpty').querySelector('.ie-s').textContent = T('换个关键词，或切回「全部」');
            syncBulk();
            return;
        }
        $('libEmpty').style.display = 'none';

        grid.innerHTML = list.map(function (b) {
            var cover = b.cover
                ? 'background-image:url(\'' + fileUrl(b.cover) + '\')'
                : 'background:' + placeholder(b.title);
            var selCls = S.sel[b.path] ? ' sel' : '';
            return '' +
                '<div class="lib-card' + selCls + '" data-path="' + esc(b.path) + '">' +
                  '<div class="lib-cover" style="' + cover + '">' +
                    '<div class="lib-chk">✓</div>' +

                    (b.cover ? '' : '<div class="ct">' + esc(b.title) + '</div>' +
                                    '<div class="ca">' + esc(b.author || '') + '</div>') +
                  '</div>' +
                  '<div class="lib-meta">' +
                    '<div class="n">' + esc(b.title) + '</div>' +
                    '<div class="s"><span>' + fmtSize(b.size) + '</span>' +
                    '<span>' + (b.author ? esc(b.author) : fmtTime(b.mtime)) + '</span></div>' +
                  '</div>' +
                '</div>';
        }).join('');

        // ★ 交互（用户 2026-09-23 定稿）：单击 = 阅读，右键 = 详情。
        //   以前是反的（单击开详情、双击才阅读），用户明确要求换过来。
        //   多选模式下单击仍然是勾选（否则没法多选），右键详情不受影响。
        Array.prototype.forEach.call(grid.querySelectorAll('.lib-card'), function (el) {
            el.addEventListener('click', function () {
                var path = el.dataset.path;
                if (S.multi) { toggleSel(path); return; }
                if (!path) return;
                // 只有 EPUB 能进阅读器；TXT 没有可渲染的版式，退回详情弹窗
                if (/\.epub$/i.test(path)) { openInReader(path); }
                else { openDetail(path); }
            });
            // 右键 → 详情弹窗（多选模式下也能用，方便单独改一本的元数据）
            el.addEventListener('contextmenu', function (e) {
                e.preventDefault();
                var path = el.dataset.path;
                if (!path) return;
                openDetail(path);
            });
            // 双击：保持"打开阅读器"，习惯双击的用户不会觉得变扭；
            // 但单击已经会开了，所以这里做个去重，避免开两次窗口
            el.addEventListener('dblclick', function () {
                var path = el.dataset.path;
                if (S.multi) return;
                if (!path || !/\.epub$/i.test(path)) return;
                if (openInReader._last === path && Date.now() - openInReader._lastAt < 1500) return;
                openInReader(path);
            });
        });

        syncBulk();
    }

    function syncBulk() {
        var n = Object.keys(S.sel).length;
        $('libSelNum').textContent = n;
        $('libBulk').classList.toggle('show', S.multi);

        var vis = visible();
        var allSel = vis.length > 0 && vis.every(function (b) { return S.sel[b.path]; });
        $('libSelectAll').textContent = allSel
            ? T('取消全选')
            : (T('全选') + ' ' + vis.length + ' ' + T('本'));

        ['libEdit', 'libSend', 'libReveal', 'libDel', 'libExport'].forEach(function (id) {
            $(id).disabled = n === 0;
        });
        $('libEdit').disabled = n !== 1;    // 只能编辑一本
    }

    function toggleSel(path, force) {
        var on = (force === undefined) ? !S.sel[path] : force;
        if (on) S.sel[path] = true; else delete S.sel[path];
        var el = document.querySelector('.lib-card[data-path="' + CSS.escape(path) + '"]');
        if (el) el.classList.toggle('sel', on);
        syncBulk();
    }

    /* ---------- 详情弹窗 ---------- */
    function openDetail(path) {
        var b = S.books.filter(function (x) { return x.path === path; })[0];
        if (!b) return;
        S.cur = b;

        var cov = $('lmCover');
        if (b.cover) {
            cov.innerHTML = '<img src="' + fileUrl(b.cover) + '" alt="">';
        } else {
            cov.innerHTML = T('无封面');
            cov.style.background = placeholder(b.title);
            cov.style.color = 'transparent';
        }

        $('lmTitle').value = b.title || '';
        $('lmAuthor').value = b.author || '';
        $('lmDesc').value = b.description || '';
        $('lmSize').textContent = fmtSize(b.size);
        $('lmTime').textContent = fmtTime(b.mtime);
        $('lmType').textContent = b.type.toUpperCase();
        $('lmChapters').textContent = '—';
        $('lmSave').disabled = (b.type !== 'epub');
        $('lmNote').textContent = (b.type === 'epub')
            ? T('改动会写回原 EPUB，不会重新转换')
            : T('TXT 没有元数据，只能改文件名');

        $('libModal').style.display = 'flex';
    }

    function closeDetail() {
        $('libModal').style.display = 'none';
        S.cur = null;
    }

    function saveDetail() {
        if (!S.cur || !API) return;
        var payload = {
            path: S.cur.path,
            title: $('lmTitle').value.trim(),
            author: $('lmAuthor').value.trim(),
            description: $('lmDesc').value
        };
        $('lmSave').disabled = true;
        $('lmNote').textContent = T('保存中…');
        API.update(payload).then(function (r) {
            $('lmSave').disabled = false;
            if (r && r.success) {
                S.cur.title = payload.title;
                S.cur.author = payload.author;
                S.cur.description = payload.description;
                $('lmNote').textContent = T('✅ 已保存');
                render();
                setTimeout(closeDetail, 550);
            } else {
                $('lmNote').textContent = '❌ ' + ((r && r.error) || T('保存失败'));
            }
        });
    }

    async function deletePaths(paths) {
        if (!API || !paths.length) return;
        if (!(await window.__uiConfirm(T('确定删除 ') + paths.length + T(' 个文件？') + '\n\n' +
                     T('这些文件会从 export 目录里真正删掉，不进回收站。'), { title: '删除文件', danger: true, okText: '删除' }))) return;
        API.remove(paths).then(function (r) {
            if (r && r.success) {
                console.log('📚 已删除', r.deleted.length, '项');
                paths.forEach(function (p) { delete S.sel[p]; });
                load(true);          // 强制重扫
            } else {
                alert(T('删除失败：') + ((r && r.error) || T('未知错误')));
            }
        });
    }


    /* ---------- 批量传输 ---------- */
    // 复用现有界面：书库只负责暂存清单 + 打开对应通道，传输逻辑不重写
    function openSend() {
        var paths = Object.keys(S.sel);
        if (!paths.length) return;
        $('sendCount').textContent = paths.length;
        $('libSendModal').style.display = 'flex';
    }

    function closeSend() {
        $('libSendModal').style.display = 'none';
    }

    function doSend(mode) {
        var paths = Object.keys(S.sel);
        var E = window.electronAPI;
        if (!paths.length || !E) return;

        // 没有 batch API（浏览器直开）就只提示
        if (!E.batch) {
            alert(T('批量传输需要在应用内使用'));
            return;
        }

        closeSend();

        // ① 暂存清单 —— 三个通道共用一份
        // ★ 2026-09-26：点「批量传输」= 一条新 sequence。先让主进程把上一次的
        //   清单和 sendqueue\ 整体重置，再写入本次勾选的书（顺序不能反）。
        var started = E.batch.newTask ? E.batch.newTask('library') : Promise.resolve();
        started.catch(function () {}).then(function () {
            E.batch.set(mode, paths).then(function () {
                if (mode === 'wifi') {
                    // ② WiFi：开现有的 WiFi 窗口，窗口会读到这份清单
                    if (E.wifi && E.wifi.openPage) {
                        E.wifi.openPage(paths);
                    }
                } else {
                    // ② Kindle / 苹果图书：跳到对应的现有页面，页面自己读清单
                    var pageId = (mode === 'kindle') ? 'page-kindle' : 'page-applebooks';
                    var nav = document.querySelector('.sidebar .nav-item[data-page="' + pageId + '"]');
                    if (nav) nav.click();
                }
            });
        });
    }


    /* ---------- 导出 ---------- */
    function exportPaths(paths) {
        if (!API || !paths.length) return;
        if (!API.exportFiles) { alert(T('导出需要在应用内使用')); return; }
        API.exportFiles(paths).then(function (r) {
            if (!r || r.canceled) return;
            if (r.success) {
                console.log('📤 已导出', r.count, '本 →', r.dir);
                var msg = T('已导出 ') + r.count + T(' 本到') + '\n' + r.dir;
                if (r.failed && r.failed.length) msg += '\n\n' + T('失败：') + r.failed.join('、');
                alert(msg);
            } else {
                alert(T('导出失败：') + ((r && r.error) || T('未知错误')));
            }
        });
    }

    /* ---------- 事件绑定 ---------- */
    function bind() {
        var search = $('libSearch');
        if (search) {
            search.addEventListener('input', function () {
                S.q = this.value; render();
            });
        }

        if ($('libRefresh')) $('libRefresh').addEventListener('click', function () { load(true); });

        /* ---------- 导入外部书籍 ---------- */
        // 走主进程的 library-import：系统文件框选书 → 复制进书库目录 → 重新扫描。
        // 外来 EPUB 不需要转译，foliate-js 是通用解析器；mobi/azw3/fb2/cbz 一并支持。
        if ($('libImport')) $('libImport').addEventListener('click', function () {
            if (!API || !API.importBooks) { toast('导入需要重启应用（preload 未加载新接口）'); return; }
            var btn = this;
            btn.disabled = true;
            API.importBooks().then(function (r) {
                btn.disabled = false;
                if (!r || !r.success) { toast('导入失败：' + ((r && r.error) || '未知错误')); return; }
                if (r.canceled) return;
                var n = (r.imported || []).length;
                var bad = (r.failed || []).length;
                var dup = (r.skipped || []).length;   // ★ 2026-09-27：内容已在书库里的
                if (!n && !bad) {
                    if (dup) { toast('这本已经在书库里了：' + r.skipped[0].existing); return; }
                    toast('没有可导入的文件'); return;
                }
                if (n) {
                    // 只把新进来的这几本加进列表并刷新，避免整库重扫
                    r.imported.forEach(function (it) {
                        S.books.unshift({
                            path: it.path, file: it.file, title: it.file.replace(/\.[^.]+$/, ''),
                            author: '', cover: '', size: 0, imported: true
                        });
                    });
                    toast('已导入 ' + n + ' 本' + (bad ? '，' + bad + ' 本失败' : '') + (dup ? '，' + dup + ' 本已在书库' : ''));
                } else {
                    toast('导入失败：' + (r.failed[0] && r.failed[0].error));
                }
                render();
                load(true);   // 让后端给出真实标题/封面/大小
            }).catch(function (e) {
                btn.disabled = false;
                toast('导入异常：' + e.message);
            });
        });

        if ($('libDirBtn')) $('libDirBtn').addEventListener('click', function () {
            if (API) API.reveal(null);
        });

        if ($('libMultiBtn')) $('libMultiBtn').addEventListener('click', function () {
            S.multi = !S.multi;
            this.classList.toggle('on', S.multi);
            // ★ 修复：原来直接写死中文（'退出多选' / '多选'），
            //   英文模式下点击后会变成中文。libary.js 是独立文件的严格模式 IIFE，
            //   拿不到 index.html 里的 T()，所以走 i18n.js 暴露的全局入口。
            var label = S.multi ? '退出多选' : '多选';
            this.textContent = (window.__i18n && window.__i18n.t)
                ? window.__i18n.t(label) : label;
            $('libGrid').classList.toggle('multiselect', S.multi);
            if (!S.multi) { S.sel = {}; render(); }
            syncBulk();
        });

        if ($('libSelectAll')) $('libSelectAll').addEventListener('click', function () {
            var vis = visible();
            var allSel = vis.length > 0 && vis.every(function (b) { return S.sel[b.path]; });
            vis.forEach(function (b) { toggleSel(b.path, !allSel); });
            render();
        });

        if ($('libEdit')) $('libEdit').addEventListener('click', function () {
            var k = Object.keys(S.sel);
            if (k.length === 1) openDetail(k[0]);
        });
        if ($('libReveal')) $('libReveal').addEventListener('click', function () {
            if (API) API.reveal(Object.keys(S.sel)[0]);
        });
        if ($('libExport')) $('libExport').addEventListener('click', function () {
            exportPaths(Object.keys(S.sel));
        });
        if ($('libDel')) $('libDel').addEventListener('click', function () {
            deletePaths(Object.keys(S.sel));
        });
        if ($('libSend')) $('libSend').addEventListener('click', openSend);

        // 批量传输弹窗
        if ($('libSendClose')) $('libSendClose').addEventListener('click', closeSend);
        // ★★★ 2026-10-02（用户要求）★★★ **弹窗不许点旁边/按 Escape 关掉，只能点关闭按钮。**
        //   原来这里挂了 `libSendScrim` / `libModalScrim` 的点击关闭，还有一段 Escape 关闭；
        //   用户报"会不小心点掉"，所以全部去掉 —— 只留各自的关闭/取消按钮
        //   （libSendClose / libModalClose / lmCancel）。
        //   （index.html 那边也有同样的清理，并在捕获阶段加了一道总闸。）
        Array.prototype.forEach.call(document.querySelectorAll('.send-opt'), function (el) {
            el.addEventListener('click', function () { doSend(el.dataset.m); });
        });

        // 弹窗
        if ($('libModalClose')) $('libModalClose').addEventListener('click', closeDetail);
        if ($('lmCancel')) $('lmCancel').addEventListener('click', closeDetail);
        if ($('lmSave')) $('lmSave').addEventListener('click', saveDetail);
        if ($('lmExport')) $('lmExport').addEventListener('click', function () {
            if (S.cur) exportPaths([S.cur.path]);
        });
        if ($('lmDelete')) $('lmDelete').addEventListener('click', function () {
            if (S.cur) { var p = S.cur.path; closeDetail(); deletePaths([p]); }
        });
        if ($('lmChangeCover')) $('lmChangeCover').addEventListener('click', function () {
            alert(T('换封面会走「制作 → 封面设置」的搜索 + 超分流程，后续接上。'));
        });

        // ★ 原来的 Escape 关弹窗已删（用户要求只能点关闭按钮）。
        //   上面两个遮罩的点击关闭同样已删。

        // 进入书库页时加载
        var page = $('page-library');
        if (page) {
            new MutationObserver(function () {
                if (page.classList.contains('active')) load(false);
            }).observe(page, { attributes: true, attributeFilter: ['class'] });
            if (page.classList.contains('active')) load(false);
        }
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', bind);
    } else {
        bind();
    }

    // 供调试
    window.__lib = { load: load, state: S };
})();
