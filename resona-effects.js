/*
 * 界面交互与有限动效。
 * 业务脚本负责真实状态；本文件只同步外观、键盘操作和装饰效果。
 * 使用原生动画时间线，不建立常驻帧循环；隐藏、失焦或减少动态效果时停止动画。
 */
(function () {
    'use strict';

    // 集中维护页面名称、控件入口和弹窗配置，避免在事件处理中散落选择器。
    const PAGE_NAMES = {
        'page-books': '制作',
        'page-download': '下载',
        'page-auto': '一键',
        'page-applebooks': '传输',
        'page-library': '书库'
    };
    const EFFECT_CONTROLS = [
        '.tool-submit', '#convertBtn', '#txSend', '#autoTxtBtn',
        '.library-toolbar button', '.ac-btn', '.lm-b',
        '.channel-actions button', '#txAddLocal'
    ].join(',');
    const NUMBER_FIELDS = {
        maxTitleLength: '标题最长',
        wfDelay: '刷新间隔（秒）',
        newChapterLine: '章节起始行'
    };
    const DIALOG_SELECTOR = '.ac-modal,.lib-modal,#chapterEditorOverlay';
    const DIALOG_CONFIGS = {
        wifiModal: ['LOCAL NETWORK / DEVICE TRANSFER', 'wfClose'],
        stkAuthModal: ['KINDLE / ACCOUNT ACCESS', 'stkCancel'],
        abAuthModal: ['BAIDU DRIVE / AUTHORIZATION', 'abClose'],
        autoChoiceModal: ['YOUR BOOK IS READY / NEXT STEP', 'autoChoiceCancel'],
        lnModal: ['BOOK DISCOVERY / SELECT EDITION', 'lnClose'],
        recDownModal: ['DOWNLOAD / BOOK SELECTION', 'recDownCancel'],
        recInfoModal: ['LIBRARY / BOOK DETAILS', 'recInfoClose'],
        jxModal: ['BOOK TOOLS / OPTIONS', 'jxCancel'],
        metaChoiceModal: ['BOOK METADATA / SELECT EDITION', 'metaChoiceCancel'],
        grabCookieModal: ['BROWSER / AUTHORIZATION', 'grabCookieClose'],
        __uiConfirmModal: ['SYSTEMO / PLEASE CONFIRM', '__uiConfirmNo']
    };
    const DEFAULT_DIALOG_CONFIG = ['SYSTEMO / BOOK STUDIO', null];
    const ENTER_EASING = 'cubic-bezier(.16,.84,.24,1)';

    const motionPreference = matchMedia('(prefers-reduced-motion: reduce)');
    const activeAnimations = new Set();
    let syncQueued = false;
    let previousPage = null;

    function translate(key) {
        return window.__i18n ? window.__i18n.t(key) : key;
    }

    function canAnimate() {
        return !motionPreference.matches && !document.hidden &&
            !document.body.classList.contains('effect-paused');
    }

    // 完成、取消和无法播放三条路径都负责清理临时节点。
    function animate(element, frames, options, cleanup) {
        if (!element || !canAnimate() || !element.animate) {
            if (cleanup) cleanup();
            return;
        }
        const animation = element.animate(frames, options);
        activeAnimations.add(animation);
        const finish = () => {
            activeAnimations.delete(animation);
            if (cleanup) cleanup();
        };
        animation.finished.then(finish, finish);
        return animation;
    }

    function toggleClass(element, className, enabled) {
        if (element.classList.contains(className) !== enabled) {
            element.classList.toggle(className, enabled);
        }
    }

    function updateCoverCopy() {
        const title = document.getElementById('defaultCoverTitle');
        const author = document.getElementById('defaultCoverAuthor');
        const titleInput = document.getElementById('inputTitle');
        const authorInput = document.getElementById('inputAuthor');
        const titleText = titleInput && titleInput.value.trim() || translate('待命名的故事');
        const authorText = authorInput && authorInput.value.trim() || translate('作者未填写');

        if (title && title.textContent !== titleText) title.textContent = titleText;
        if (author && author.textContent !== authorText) author.textContent = authorText;
    }

    // 读取业务维护的 on/active 状态，只更新对应外观，不触发业务点击。
    function syncPageState() {
        syncQueued = false;
        document.querySelectorAll('.make-step').forEach(step => {
            toggleClass(step, 'current', step.classList.contains('on'));
        });
        document.querySelectorAll('.channel').forEach(channel => {
            const selected = channel.classList.contains('on');
            toggleClass(channel, 'selected', selected);
            if (channel.getAttribute('aria-selected') !== String(selected)) {
                channel.setAttribute('aria-selected', String(selected));
            }
            const tabIndex = selected ? 0 : -1;
            if (channel.tabIndex !== tabIndex) channel.tabIndex = tabIndex;
        });

        const page = document.querySelector('.page.active');
        const breadcrumb = document.getElementById('resonaCrumb');
        const pageName = translate(page && PAGE_NAMES[page.id] || '');
        if (breadcrumb && breadcrumb.textContent !== pageName) {
            breadcrumb.textContent = pageName;
        }
        if (!page || page === previousPage) return;

        previousPage = page;
        page.querySelectorAll('.studio-compose,.studio-artifact,.studio-flow').forEach((element, index) => {
            animate(element, [
                { opacity: 0, translate: '0 12px' },
                { opacity: 1, translate: '0 0' }
            ], { duration: 320, delay: index * 65, easing: ENTER_EASING });
        });
        updateCoverCopy();
    }

    function schedulePageSync() {
        if (syncQueued) return;
        syncQueued = true;
        queueMicrotask(syncPageState);
    }

    function pauseEffects() {
        document.body.classList.add('effect-paused');
        activeAnimations.forEach(animation => animation.cancel());
        activeAnimations.clear();
    }

    function resumeEffects() {
        document.body.classList.remove('effect-paused');
    }

    function createNumberStep(input, caption, direction) {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'number-step-button';
        button.textContent = direction < 0 ? '−' : '+';
        const updateLabel = () => {
            const action = translate(direction < 0 ? '减少' : '增加');
            button.setAttribute('aria-label', action + ' ' + translate(caption));
        };
        updateLabel();
        document.addEventListener('languagechange', updateLabel);
        button.addEventListener('click', () => {
            if (input.disabled || input.readOnly) return;
            const previous = input.value;
            if (direction < 0) input.stepDown();
            else input.stepUp();
            if (input.value === previous) return;
            input.dispatchEvent(new Event('input', { bubbles: true }));
            input.dispatchEvent(new Event('change', { bubbles: true }));
        });
        return button;
    }

    function initNumberFields() {
        // 包装原输入框而不替换节点，保留 ID、范围校验和已有监听器。
        Object.entries(NUMBER_FIELDS).forEach(([id, caption]) => {
            const input = document.getElementById(id);
            if (!input || input.closest('.number-field')) return;
            const group = document.createElement('span');
            group.className = 'number-field';
            input.before(group);
            group.appendChild(input);
            const label = document.querySelector('label[for="' + id + '"]');
            if (!input.hasAttribute('aria-label') && !label) input.setAttribute('aria-label', caption);
            group.prepend(createNumberStep(input, caption, -1));
            group.appendChild(createNumberStep(input, caption, 1));
        });
    }

    function decorateDialog(modal, card) {
        const title = card.querySelector('.ac-title');
        if (!title) return;
        const [caption, closeId] = DIALOG_CONFIGS[modal.id] || DEFAULT_DIALOG_CONFIG;
        const kicker = document.createElement('div');
        kicker.className = 'dialog-kicker';
        kicker.textContent = caption;
        title.before(kicker);

        const closeButton = closeId && modal.querySelector('#' + closeId);
        if (!closeButton) return;
        const dismiss = document.createElement('button');
        dismiss.className = 'dialog-dismiss';
        dismiss.type = 'button';
        dismiss.textContent = '×';
        dismiss.setAttribute('aria-label', '关闭');
        // 委托原关闭按钮，保留取消任务、结束等待等业务收尾。
        dismiss.addEventListener('click', () => closeButton.click());
        card.appendChild(dismiss);
    }

    function observeDialogVisibility(modal, card) {
        let visible = false;
        const refresh = () => {
            const style = getComputedStyle(modal);
            const nextVisible = !modal.hidden && style.display !== 'none' && style.visibility !== 'hidden';
            if (nextVisible && !visible) {
                animate(card, [
                    { opacity: 0, translate: '0 14px', scale: '.985' },
                    { opacity: 1, translate: '0 0', scale: '1' }
                ], { duration: 280, easing: ENTER_EASING });
            }
            visible = nextVisible;
        };
        new MutationObserver(refresh).observe(modal, {
            attributes: true,
            attributeFilter: ['style', 'class', 'hidden']
        });
        refresh();
    }

    function initDialogs() {
        const registered = new WeakSet();
        const register = modal => {
            if (registered.has(modal)) return;
            registered.add(modal);
            const card = modal.querySelector('.ac-card,.lm-card,.ce-card');
            if (!card) return;
            decorateDialog(modal, card);
            observeDialogVisibility(modal, card);
        };
        document.querySelectorAll(DIALOG_SELECTOR).forEach(register);
        // 确认框会按需插入 body，只监听顶层新增节点，避免遍历整个界面的变化。
        new MutationObserver(records => {
            records.forEach(record => record.addedNodes.forEach(node => {
                if (node.nodeType !== 1) return;
                if (node.matches(DIALOG_SELECTOR)) register(node);
                node.querySelectorAll(DIALOG_SELECTOR).forEach(register);
            }));
        }).observe(document.body, { childList: true });
        const delayInput = document.getElementById('wfDelay');
        if (delayInput) {
            (delayInput.closest('.number-field') || delayInput).parentElement.classList.add('dialog-compact-settings');
        }
    }

    function initPageNavigation() {
        const channels = [...document.querySelectorAll('#txChannels .channel')];
        const channelList = document.getElementById('txChannels');
        if (channelList) {
            channelList.setAttribute('role', 'tablist');
            channelList.setAttribute('aria-label', '传输方式');
        }
        channels.forEach(channel => {
            channel.setAttribute('role', 'tab');
            channel.tabIndex = 0;
            channel.addEventListener('keydown', event => {
                const index = channels.indexOf(channel);
                let next = null;
                if (event.key === 'ArrowRight') next = channels[(index + 1) % channels.length];
                if (event.key === 'ArrowLeft') next = channels[(index + channels.length - 1) % channels.length];
                if (event.key === 'Home') next = channels[0];
                if (event.key === 'End') next = channels[channels.length - 1];
                if (event.key === 'Enter' || event.key === ' ') next = channel;
                if (!next) return;
                event.preventDefault();
                next.focus();
                next.click();
            });
        });
        const observer = new MutationObserver(schedulePageSync);
        document.querySelectorAll('.page,.make-step,.channel').forEach(element => {
            observer.observe(element, { attributes: true, attributeFilter: ['class'] });
        });
    }

    function showControlPulse(event) {
        const button = event.target.closest && event.target.closest(EFFECT_CONTROLS);
        if (!button || button.disabled || !canAnimate()) return;
        const rect = button.getBoundingClientRect();
        const pulse = document.createElement('span');
        pulse.className = 'fx-pulse';
        pulse.setAttribute('aria-hidden', 'true');
        pulse.style.left = (event.clientX - rect.left) + 'px';
        pulse.style.top = (event.clientY - rect.top) + 'px';
        button.appendChild(pulse);
        animate(pulse, [
            { transform: 'scale(.1)', opacity: .8 },
            { transform: 'scale(4)', opacity: 0 }
        ], { duration: 480, easing: 'cubic-bezier(.2,.8,.2,1)' }, () => pulse.remove());
    }

    function initControlEffects() {
        document.querySelectorAll(EFFECT_CONTROLS).forEach(button => button.classList.add('fx-control'));
        document.addEventListener('pointerover', event => {
            const button = event.target.closest && event.target.closest(EFFECT_CONTROLS);
            if (button && !button.classList.contains('fx-control')) button.classList.add('fx-control');
        }, { passive: true });
        document.addEventListener('pointerdown', showControlPulse, { passive: true });
    }

    function initLifecycle() {
        document.addEventListener('visibilitychange', () => document.hidden ? pauseEffects() : resumeEffects());
        window.addEventListener('blur', pauseEffects);
        window.addEventListener('focus', resumeEffects);
        motionPreference.addEventListener('change', () => {
            if (motionPreference.matches) pauseEffects();
            else resumeEffects();
        });
        document.addEventListener('languagechange', () => {
            schedulePageSync();
            updateCoverCopy();
        });
        ['inputTitle', 'inputAuthor'].forEach(id => {
            const input = document.getElementById(id);
            if (!input) return;
            input.addEventListener('input', updateCoverCopy);
            input.addEventListener('change', updateCoverCopy);
        });
    }

    function init() {
        document.body.dataset.fxMode = 'software-safe';
        const legacyCanvas = document.getElementById('field');
        if (legacyCanvas) legacyCanvas.remove();
        initPageNavigation();
        initControlEffects();
        initLifecycle();
        initNumberFields();
        initDialogs();
        syncPageState();
        // 保留现有诊断入口，供手动排查残留动画。
        window.__resonaEffects = {
            get activeCount() { return activeAnimations.size; },
            mode: 'software-safe'
        };
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init, { once: true });
    } else {
        init();
    }
})();
