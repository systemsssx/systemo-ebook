/* ============================================================
   settings-ui.js — 把设置应用到界面，并在改动时保存
   ------------------------------------------------------------
   配置放在「功能所在的页面」而不是集中式设置页：
     · 标题最长   → 制作 · 章节划分
     · 超分倍数   → 制作 · 封面设置
     · 输出目录   → 制作页底部
     · WiFi 间隔  → 传输 · WiFi 传书
     · Kindle 邮箱 → 传输 · Kindle 传书（已有独立入口）

   设置本体存在 exe 同级的 settings.json（见 main.js）。
   ============================================================ */
(function () {
    'use strict';

    var API = window.electronAPI && window.electronAPI.settings;
    var $ = function (id) { return document.getElementById(id); };

    // ★ 初始化窗口：apply() 程序化给控件赋值也会触发 change，
    //   那不是用户操作，不该写盘。加载后 1.5 秒内忽略 change。
    var settingsReady = false;
    setTimeout(function () { settingsReady = true; }, 1500);

    // ★ 写入防抖：连续改动（比如点 number 输入的上下箭头）合并成一次写盘
    var pendingPatch = null, patchTimer = null;
    function setDebounced(patch, onDone) {
        pendingPatch = Object.assign(pendingPatch || {}, patch);
        if (patchTimer) clearTimeout(patchTimer);
        patchTimer = setTimeout(function () {
            var send = pendingPatch;
            pendingPatch = null;
            patchTimer = null;
            API.set(send).then(function (r) {
                if (typeof onDone === 'function') onDone(r);
            });
        }, 600);
    }

    function bindNumber(id, key, min, max, transform) {
        var el = $(id);
        if (!el || !API) return;
        el.addEventListener('change', function () {
            if (!settingsReady) return;
            var v = parseInt(this.value, 10);
            if (isNaN(v)) return;
            if (min != null && v < min) v = min;
            if (max != null && v > max) v = max;
            this.value = v;
            var patch = {};
            patch[key] = transform ? transform(v) : v;
            setDebounced(patch, function () {
                console.log('⚙️ 已保存', key, '=', patch[key]);
            });
        });
    }

    function bindSelect(id, key, transform) {
        var el = $(id);
        if (!el || !API) return;
        el.addEventListener('change', function () {
            if (!settingsReady) return;
            var patch = {};
            patch[key] = transform ? transform(this.value) : this.value;
            setDebounced(patch, function () {
                console.log('⚙️ 已保存', key, '=', patch[key]);
            });
        });
    }

    function apply(S) {
        // —— 章节划分：标题最长
        var mtl = $('maxTitleLength');
        if (mtl && S.maxTitleLength != null) mtl.value = S.maxTitleLength;

        // —— 封面设置：超分倍数
        var mag = $('coverMagnify');
        if (mag && S.coverMagnify != null) mag.value = String(S.coverMagnify);

        // —— 制作页底部：输出目录
        var disp = $('outDirDisplay');
        if (disp) {
            var d = (S.outputDir || '').trim();
            disp.textContent = d || '与源文件同目录';
            disp.title = d || '';
            disp.classList.toggle('is-default', !d);
        }

        // —— 传输 · 苹果图书：网盘目录 / 重名策略
        var rd = $('abRemoteDir');
        if (rd && S.baiduRemoteDir != null) rd.value = S.baiduRemoteDir;
        var od = $('abOnDup');
        if (od && S.baiduOnDup != null) od.value = S.baiduOnDup;

        // —— 侧栏：语言
        var lb = $('langZh'), le = $('langEn');
        if (lb && le) {
            var en = S.language === 'en';
            lb.classList.toggle('on', !en);
            le.classList.toggle('on', en);
        }
    }

    /* ---------- 输出目录 ---------- */
    function initOutDir() {
        var pick = $('outDirPick');
        var reset = $('outDirReset');
        if (!pick) return;

        pick.addEventListener('click', function () {
            if (!window.electronAPI || !window.electronAPI.pickDirectory) return;
            window.electronAPI.pickDirectory().then(function (r) {
                if (!r || !r.success) return;             // 用户取消
                return API.set({ outputDir: r.dir }).then(function (res) {
                    apply((res && res.settings) || {});
                    console.log('⚙️ 输出目录已设为', r.dir);
                });
            });
        });

        if (reset) {
            reset.addEventListener('click', function () {
                API.set({ outputDir: '' }).then(function (res) {
                    apply((res && res.settings) || {});
                    console.log('⚙️ 输出目录已恢复默认');
                });
            });
        }
    }

    /* ---------- 网盘设置 ---------- */
    function initBaidu() {
        var rd = $('abRemoteDir');
        if (rd && API) {
            rd.addEventListener('change', function () {
                if (!settingsReady) return;
                var v = this.value.trim() || '/epub_download_backup';
                this.value = v;
                API.set({ baiduRemoteDir: v });
            });
        }
        var od = $('abOnDup');
        if (od && API) {
            od.addEventListener('change', function () {
                if (!settingsReady) return;
                API.set({ baiduOnDup: this.value });
            });
        }
    }

    /* ---------- 语言开关 ---------- */
    function markLang(lang) {
        var wrap = $('langSwitch');
        if (!wrap) return;
        Array.prototype.forEach.call(wrap.querySelectorAll('div'), function (el) {
            el.classList.toggle('on', el.dataset.lang === lang);
        });
    }

    function initLang() {
        var wrap = $('langSwitch');
        if (!wrap || !API) return;
        // ★ 弹窗页（授权 / WiFi / Kindle / 指引）里也能切语言，
        //   主进程改完设置会广播 apply-language，主界面要跟着换，
        //   否则两边的语言会不一致。
        if (API.onLanguageChange) {
            API.onLanguageChange(function (lang) {
                if (window.__i18n) window.__i18n.setLang(lang);
                markLang(lang === 'en' ? 'en' : 'zh');
            });
        }
        Array.prototype.forEach.call(wrap.querySelectorAll('div'), function (el) {
            el.addEventListener('click', function () {
                var lang = el.dataset.lang;
                API.set({ language: lang }).then(function (res) {
                    apply((res && res.settings) || {});
                    if (window.__i18n) window.__i18n.setLang(lang);
                    markLang(lang);
                });
            });
        });
    }

    function init() {
        if (!API) {
            console.log('⚙️ 未连接主进程，跳过设置加载（浏览器直开时会这样）');
            return;
        }

        // 先接线（这样即使加载失败，改动也还能保存）
        bindNumber('maxTitleLength', 'maxTitleLength', 5, 500);
        // ★ coverMagnify 已从界面移除（超分倍数固定用默认值 4），这行留着是空操作
        //   bindSelect 内部已经做了 !el 判断，元素不存在时直接 return
        bindSelect('coverMagnify', 'coverMagnify', function (v) { return parseInt(v, 10); });
        initOutDir();
        initBaidu();
        initLang();

        API.get().then(function (r) {
            if (r && r.success) {
                apply(r.settings || {});
                if (window.__i18n && r.settings && r.settings.language) {
                    window.__i18n.setLang(r.settings.language);
                }
                console.log('⚙️ 设置已应用:', r.settings);
            }
        }).catch(function (e) {
            console.warn('⚠️ 读取设置失败:', e.message);
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }

    window.__settingsUI = { apply: apply };
})();
