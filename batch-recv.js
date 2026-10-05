/* ============================================================
   batch-recv.js — 通道页面接收书库的批量清单
   ------------------------------------------------------------
   书库的批量传输只做两件事：暂存清单 + 打开对应界面。
   真正的传输逻辑不重写，由这里让【原有界面】读到清单并执行：

     Kindle 页   → 读清单，用原有 kindle.send（已支持数组）批量发送
     苹果图书页  → 读清单，先用原有 copyFiles 复制到下载目录，
                   之后用户点原来的「上传」按钮走原有网盘流程

   WiFi 是独立窗口，由 wifi-page.js 自己处理，不在这里。
   ============================================================ */
(function () {
    'use strict';

    var E = window.electronAPI;
    if (!E || !E.batch) {
        console.log('📚 batch API 不可用，跳过（浏览器直开时会这样）');
        return;
    }

    var $ = function (id) { return document.getElementById(id); };

    /* ---------- 通用：在页面里插一个"本次 N 本"徽标 ---------- */
    function badge(host, count, text) {
        if (!host) return null;
        var old = host.querySelector('.batch-badge');
        if (old) old.remove();
        var el = document.createElement('div');
        el.className = 'batch-badge';
        el.innerHTML = '<span>📚 来自书库：本次将' + text + ' <b>' + count + '</b> 本</span>';
        host.appendChild(el);
        return el;
    }

    /* ================================================================
       Kindle：切到该页时读清单，点「传输」批量发送
       ================================================================ */
    var kindleFiles = [];

    function takeKindle() {
        E.batch.get('kindle').then(function (r) {
            if (!r || !r.count) return;
            kindleFiles = r.files || [];

            var page = $('page-kindle');
            var right = page && page.querySelector('.books-right');
            badge(right, r.count, '发送到 Kindle');

            // 让原有的单文件流程别插手：克隆按钮会摘掉它身上所有监听器
            var btn = $('kdUploadBtn');
            if (!btn) return;
            var fresh = btn.cloneNode(true);
            btn.parentNode.replaceChild(fresh, btn);

            fresh.addEventListener('click', function () {
                if (!kindleFiles.length) return;
                fresh.disabled = true;
                fresh.textContent = '推送中…';
                console.log('📚 Kindle 批量发送', kindleFiles.length, '本');
                E.kindle.send(kindleFiles).then(function (res) {
                    fresh.disabled = false;
                    fresh.textContent = '传输';
                    if (res && res.success) {
                        console.log('📚 Kindle 批量完成：成功', res.ok, '/', res.total);
                        kindleFiles = [];
                        E.batch.clear();
                        var b = document.querySelector('#page-kindle .batch-badge');
                        if (b) b.remove();
                    } else {
                        console.warn('⚠️ Kindle 批量失败:', res && res.error);
                    }
                });
            });
        });
    }

    /* ================================================================
       苹果图书：切到该页时先把清单复制进下载目录，之后走原有上传流程
       ================================================================ */
    function takeAppleBooks() {
        E.batch.get('applebooks').then(function (r) {
            if (!r || !r.count) return;
            var files = r.files || [];

            var page = $('page-applebooks');
            var right = page && page.querySelector('.books-right');
            var el = badge(right, files.length, '上传');

            console.log('📚 苹果图书：复制', files.length, '本到下载目录');
            E.applebooks.copyFiles(files).then(function (res) {
                if (el) {
                    var ok = res && res.success;
                    el.innerHTML = ok
                        ? '<span>📚 已从书库复制 <b>' + files.length + '</b> 本 · 点「上传」走网盘流程</span>'
                        : '<span>⚠️ 复制失败：' + ((res && res.error) || '未知错误') + '</span>';
                }
            });
        });
    }

    /* ---------- 监听页面切换 ---------- */
    function watch(pageId, fn) {
        var page = $(pageId);
        if (!page) return;
        var last = page.classList.contains('active');
        new MutationObserver(function () {
            var now = page.classList.contains('active');
            if (now && !last) fn();          // 只在"变成激活"的那一下触发
            last = now;
        }).observe(page, { attributes: true, attributeFilter: ['class'] });
    }

    watch('page-kindle', takeKindle);
    watch('page-applebooks', takeAppleBooks);

    console.log('📚 batch-recv 已就绪');
})();
