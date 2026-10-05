/* ============================================================
   motion.js — 动效引擎（替代 animations.js）
   ------------------------------------------------------------
   对外 API 与旧版完全一致，index.html 里的 6 处调用无需改动：
       window.AnimationEngine.enabled            → boolean
       window.AnimationEngine.useGPU             → boolean
       window.AnimationEngine.pageTransition(old, next, ms)
       window.AnimationEngine.elasticScale(el, scale, ms)

   ★ 加载方式必须是同步 <script>，不能加 defer。
     旧版 animations.js 用了 defer，而 index.html 的内联脚本在解析到
     L1773 时就读取 window.AnimationEngine —— 那时 defer 脚本还没执行，
     取到 undefined，导致整套动画从来没生效过。

   本文件不操作 DOM 结构，只加/删 class 和过渡内联样式。
   ============================================================ */
(function () {
    'use strict';

    var REDUCED = false;
    try {
        REDUCED = window.matchMedia &&
                  window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    } catch (e) { /* 老环境忽略 */ }

    function AnimationEngine() {
        // 用户在系统里关掉动画时，直接走无动画分支
        this.enabled = !REDUCED;
        // 现代 Chromium 自己会做图层提升，不需要手工 translateZ(0)
        this.useGPU = true;
        this._scaleTimers = new WeakMap();
        this._pageTimers = new WeakMap();
    }

    /* ---------- 页面切换 ----------
       契约：index.html 在本方法返回后才切换 .active 类，
             所以这里只负责显示/隐藏与过渡，不碰 .active。 */
    AnimationEngine.prototype.pageTransition = function (oldPage, newPage, duration) {
        if (!newPage) return;
        duration = duration || 200;

        // 旧页面直接收起：交叉淡入会让两页叠加同时合成，更贵也更糊
        if (oldPage && oldPage !== newPage) {
            this._clearPage(oldPage);
            oldPage.classList.remove('mo-page-in');
            oldPage.style.display = 'none';
        }

        if (!this.enabled) {
            this._clearPage(newPage);
            newPage.style.display = 'flex';
            newPage.style.opacity = '1';
            return;
        }

        var self = this;
        this._clearPage(newPage);
        newPage.style.display = 'flex';
        newPage.style.opacity = '';
        // 强制回流，保证动画从头播放（元素刚从 display:none 出来）
        void newPage.offsetWidth;
        newPage.classList.add('mo-page-in');

        var prev = this._pageTimers.get(newPage);
        if (prev) clearTimeout(prev);
        this._pageTimers.set(newPage, setTimeout(function () {
            newPage.classList.remove('mo-page-in');
            self._clearPage(newPage);
        }, duration + 60));
    };

    /* ---------- 按压回弹 ----------
       只动 transform，走 GPU 合成。 */
    AnimationEngine.prototype.elasticScale = function (element, scale, duration) {
        if (!element || !this.enabled) return;
        scale = scale || 0.92;
        duration = duration || 350;

        var pressMs = Math.max(90, Math.round(duration * 0.35));
        var self = this;

        var prev = this._scaleTimers.get(element);
        if (prev) clearTimeout(prev);

        element.style.transition = 'transform ' + pressMs + 'ms cubic-bezier(0.4, 0, 0.2, 1)';
        element.style.transform = 'scale(' + scale + ')';

        this._scaleTimers.set(element, setTimeout(function () {
            element.style.transform = '';
            self._scaleTimers.set(element, setTimeout(function () {
                element.style.transition = '';
                self._scaleTimers.delete(element);
            }, pressMs + 30));
        }, pressMs));
    };

    /* ---------- 与旧版保持 API 兼容的空实现 ---------- */
    AnimationEngine.prototype.detectPerformance = function () { return; };
    AnimationEngine.prototype._remeasure = function () { return; };

    /* ---------- 内部：清掉遗留内联样式 ---------- */
    AnimationEngine.prototype._clearPage = function (el) {
        if (!el) return;
        el.style.opacity = '';
        el.style.transform = '';
        el.style.willChange = '';
    };

    window.AnimationEngine = new AnimationEngine();
})();
