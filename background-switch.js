/* 主窗口背景切换：独立管理外观偏好，不参与业务设置和传输状态。 */
(function () {
    'use strict';

    const STORAGE_KEY = 'systemo.background-mode';
    const SIMPLE_MODE = 'simple';
    const SCENE_MODE = 'scene';
    let currentMode = SCENE_MODE;

    function translate(key) {
        return window.__i18n ? window.__i18n.t(key) : key;
    }

    function normalizeMode(value) {
        return value === SIMPLE_MODE ? SIMPLE_MODE : SCENE_MODE;
    }

    function updateButton() {
        const button = document.getElementById('backgroundSwitch');
        if (!button) return;
        const isSimple = currentMode === SIMPLE_MODE;
        button.setAttribute('aria-checked', String(isSimple));
        button.setAttribute('aria-label', translate('简洁背景'));
        button.title = translate(isSimple ? '切换为场景背景' : '切换为简洁背景');
    }

    function applyMode(mode) {
        currentMode = normalizeMode(mode);
        document.body.dataset.backgroundMode = currentMode;
        updateButton();
    }

    function toggleBackground() {
        applyMode(currentMode === SIMPLE_MODE ? SCENE_MODE : SIMPLE_MODE);
        // 存储不可用时仍允许当前窗口切换，不让偏好保存中断界面操作。
        try {
            localStorage.setItem(STORAGE_KEY, currentMode);
        } catch (error) {
            console.warn('背景偏好保存失败：', error.message);
        }
    }

    // 脚本位于页面末尾，尽早恢复偏好，减少首次绘制时的背景闪换。
    try {
        currentMode = normalizeMode(localStorage.getItem(STORAGE_KEY));
    } catch (error) {
        console.warn('背景偏好读取失败：', error.message);
    }
    applyMode(currentMode);

    const button = document.getElementById('backgroundSwitch');
    if (button) button.addEventListener('click', toggleBackground);
    document.addEventListener('languagechange', updateButton);
    window.addEventListener('storage', function (event) {
        if (event.key === STORAGE_KEY) applyMode(event.newValue);
    });
})();
