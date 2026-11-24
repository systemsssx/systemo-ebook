/* ==========================================================================
   folio-bridge.js — EasyPub V3 西洋古典羊皮卷生产级交互桥接脚本
   - 保留 100% 原版 Electron / IPC 业务流，不含任何示例或 mock 数据
   - 提供：双页对开分屏与书眉拖拽、欧洲烫金皮脊 SVG 封面、折叠抽屉、跨页拖书、
     通道发送桥接、封面书名同步、操作说明弹窗 + 「使用教程 ↗」直达帮手窗口
   ========================================================================== */

(function () {
    'use strict';

    const $ = (id) => document.getElementById(id);

    const PAGE_ROMAN = {
        'page-download': 'I',
        'page-books': 'II',
        'page-applebooks': 'III',
        'page-library': 'IV',
        'page-auto': 'V'
    };

    const PAGE_I18N_KEYS = {
        'page-download': 'nav.download',
        'page-books': 'nav.make',
        'page-applebooks': 'nav.send',
        'page-library': 'nav.library',
        'page-auto': 'nav.auto'
    };

    const PAGE_ZH_FALLBACK = {
        'nav.download': '下载',
        'nav.make': '制作',
        'nav.send': '传输',
        'nav.library': '书库',
        'nav.auto': '一键'
    };

    function getPageShort(pid) {
        const k = PAGE_I18N_KEYS[pid];
        if (!k) return '';
        const val = tr(k);
        return (val && val !== k) ? val : (PAGE_ZH_FALLBACK[k] || '');
    }

    function getPageName(pid) {
        const r = PAGE_ROMAN[pid];
        const s = getPageShort(pid);
        return (r && s) ? `${r} · ${s}` : s;
    }

    function clearTextSelection() {
        try {
            const sel = window.getSelection && window.getSelection();
            if (sel && sel.removeAllRanges) sel.removeAllRanges();
        } catch (_) {}
    }

    const ANTIQUE_LEATHER_PALETTES = [
        ['#473321', '#C8A872'],
        ['#333F2E', '#C4A673'],
        ['#3D3126', '#CFAF7A'],
        ['#4E3922', '#D4B47E'],
        ['#2E3830', '#C6A771']
    ];

    function escXml(str) {
        return String(str == null ? '' : str)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;');
    }

    function tr(key) {
        return (window.__i18n && typeof window.__i18n.t === 'function') ? window.__i18n.t(key) : key;
    }

    function setColophonStatus(text) {
        const el = $('statusText');
        if (el) el.textContent = text;
    }

    function pickLeatherPalette(seedStr) {
        const s = String(seedStr || 'EasyPub');
        let h = 0;
        for (let i = 0; i < s.length; i++) {
            h = ((h << 5) - h + s.charCodeAt(i)) | 0;
        }
        return ANTIQUE_LEATHER_PALETTES[Math.abs(h) % ANTIQUE_LEATHER_PALETTES.length];
    }

    /** 生成 18 世纪欧洲烫金皮脊精装书封 SVG Data URI */
    function makeClothCoverDataUri(title, sub, bgHex, accentHex) {
        const cleanTitle = String(title || 'Untitled').replace(/\s+/g, ' ').trim();
        const palette = pickLeatherPalette(cleanTitle);
        const bg = bgHex || palette[0];
        const accent = accentHex || palette[1];

        let line1 = cleanTitle;
        let line2 = '';
        if (cleanTitle.length > 6) {
            const cut = Math.min(6, Math.ceil(cleanTitle.length / 2));
            line1 = cleanTitle.slice(0, cut);
            line2 = cleanTitle.slice(cut, cut + 6);
        }
        const safeL1 = escXml(line1);
        const safeL2 = escXml(line2);
        const safeSub = escXml(String(sub || 'EPUB').slice(0, 14).toUpperCase());

        const titleMarkup = line2
            ? `<text x="98" y="64" text-anchor="middle" fill="${accent}" font-family="Cormorant Garamond, Noto Serif SC, Georgia, serif" font-size="14.5" font-weight="600" letter-spacing="1.2">${safeL1}</text>
               <text x="98" y="84" text-anchor="middle" fill="${accent}" font-family="Cormorant Garamond, Noto Serif SC, Georgia, serif" font-size="14.5" font-weight="600" letter-spacing="1.2">${safeL2}</text>`
            : `<text x="98" y="74" text-anchor="middle" fill="${accent}" font-family="Cormorant Garamond, Noto Serif SC, Georgia, serif" font-size="16" font-weight="600" letter-spacing="1.5">${safeL1}</text>`;

        const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="180" height="240" viewBox="0 0 180 240">
            <defs>
                <linearGradient id="covGrad" x1="0" y1="0" x2="1" y2="1">
                    <stop offset="0%" stop-color="${bg}"/>
                    <stop offset="100%" stop-color="#19110A"/>
                </linearGradient>
                <linearGradient id="spineGrad" x1="0" y1="0" x2="1" y2="0">
                    <stop offset="0%" stop-color="#140D07" stop-opacity="0.72"/>
                    <stop offset="68%" stop-color="#140D07" stop-opacity="0.06"/>
                    <stop offset="100%" stop-color="#000000" stop-opacity="0.32"/>
                </linearGradient>
            </defs>
            <rect width="180" height="240" fill="url(#covGrad)"/>
            <rect x="0" y="0" width="20" height="240" fill="url(#spineGrad)"/>
            <line x1="20" y1="0" x2="20" y2="240" stroke="${accent}" stroke-opacity="0.32" stroke-width="0.9"/>
            <g stroke="${accent}" stroke-opacity="0.52" stroke-width="1.2">
                <line x1="2" y1="44" x2="19" y2="44"/><line x1="2" y1="48" x2="19" y2="48"/>
                <line x1="2" y1="92" x2="19" y2="92"/><line x1="2" y1="96" x2="19" y2="96"/>
                <line x1="2" y1="142" x2="19" y2="142"/><line x1="2" y1="146" x2="19" y2="146"/>
                <line x1="2" y1="190" x2="19" y2="190"/><line x1="2" y1="194" x2="19" y2="194"/>
            </g>
            <rect x="28" y="14" width="140" height="212" rx="2" fill="none" stroke="${accent}" stroke-opacity="0.62" stroke-width="1.2"/>
            <rect x="33" y="19" width="130" height="202" rx="1" fill="none" stroke="${accent}" stroke-opacity="0.30" stroke-width="0.75"/>
            <g fill="none" stroke="${accent}" stroke-opacity="0.72" stroke-width="1">
                <path d="M35 31 V21 H45 M35 26 C38 26 40 24 40 21"/>
                <path d="M161 31 V21 H151 M161 26 C158 26 156 24 156 21"/>
                <path d="M35 209 V219 H45 M35 214 C38 214 40 216 40 219"/>
                <path d="M161 209 V219 H151 M161 214 C158 214 156 216 156 219"/>
            </g>
            ${titleMarkup}
            <line x1="62" y1="98" x2="134" y2="98" stroke="${accent}" stroke-opacity="0.45" stroke-width="0.8"/>
            <ellipse cx="98" cy="136" rx="24" ry="30" fill="none" stroke="${accent}" stroke-opacity="0.58" stroke-width="1.1"/>
            <ellipse cx="98" cy="136" rx="19" ry="25" fill="none" stroke="${accent}" stroke-opacity="0.28" stroke-width="0.75"/>
            <polygon points="98,123 103,136 98,149 93,136" fill="none" stroke="${accent}" stroke-opacity="0.75" stroke-width="1"/>
            <polygon points="87,136 98,131 109,136 98,141" fill="none" stroke="${accent}" stroke-opacity="0.55" stroke-width="0.85"/>
            <circle cx="98" cy="136" r="2.2" fill="${accent}" fill-opacity="0.82"/>
            <text x="98" y="198" text-anchor="middle" fill="${accent}" fill-opacity="0.86" font-family="Cinzel, Cormorant Garamond, Georgia, serif" font-size="8.5" letter-spacing="1.6">${safeSub}</text>
        </svg>`;
        return 'data:image/svg+xml;utf8,' + encodeURIComponent(svg);
    }
    window.__makeClothCoverDataUri = makeClothCoverDataUri;

    /* ======================================================================
       1. 双页对开分屏与书眉拖拽引擎
       ====================================================================== */
    const folioState = {
        mode: 'single',
        leftPageId: 'page-applebooks',
        rightPageId: 'page-books',
        activeSlot: 'left'
    };

    window.__folioManagedNav = true;
    window.__folioState = folioState;

    function normalizePageId(rawId) {
        if (rawId === 'page-kindle') {
            if (typeof window.__activateChannel === 'function') window.__activateChannel('kindle');
            return 'page-applebooks';
        }
        if (rawId === 'page-chapters' || rawId === 'page-cover') {
            return 'page-books';
        }
        if (rawId === 'page-help') {
            if (typeof window.__openStudioGuide === 'function') window.__openStudioGuide();
            return null;
        }
        return PAGE_ROMAN[rawId] ? rawId : 'page-applebooks';
    }

    function clearInlineMotionStyles(pageEl) {
        if (!pageEl) return;
        pageEl.style.display = '';
        pageEl.style.opacity = '';
        pageEl.style.transform = '';
        pageEl.style.transition = '';
        pageEl.style.pointerEvents = '';
        pageEl.style.visibility = '';
    }

    function mountPageIntoSlot(pageEl, slotEl) {
        if (!pageEl || !slotEl) return;
        if (pageEl.parentElement !== slotEl) {
            slotEl.appendChild(pageEl);
        }
        clearInlineMotionStyles(pageEl);
        pageEl.classList.add('active', 'is-entering');
        setTimeout(() => pageEl.classList.remove('is-entering'), 220);
    }

    function stashUnusedPages() {
        const stash = $('pageStash');
        if (!stash) return;
        document.querySelectorAll('.page').forEach((p) => {
            const keepLeft = p.id === folioState.leftPageId;
            const keepRight = folioState.mode === 'dual' && p.id === folioState.rightPageId;
            if (!keepLeft && !keepRight) {
                p.classList.remove('active', 'is-entering');
                clearInlineMotionStyles(p);
                if (p.parentElement !== stash) stash.appendChild(p);
            }
        });
    }

    function renderFolioLayout() {
        const leftSlot = $('folioLeft');
        const rightSlot = $('folioRight');
        const splitBtn = $('splitModeBtn');
        const splitBtnLabel = $('splitModeLabel');

        document.body.dataset.split = folioState.mode;

        if (folioState.mode === 'dual' && folioState.leftPageId === folioState.rightPageId) {
            const fallback = ['page-books', 'page-applebooks', 'page-library', 'page-download', 'page-auto']
                .find((id) => id !== folioState.leftPageId);
            folioState.rightPageId = fallback || 'page-books';
        }

        stashUnusedPages();
        mountPageIntoSlot($(folioState.leftPageId), leftSlot);

        if (folioState.mode === 'dual') {
            mountPageIntoSlot($(folioState.rightPageId), rightSlot);
        }

        if ($('slotBadgeLeftName')) {
            $('slotBadgeLeftName').textContent = 'FOLIO L · ' + getPageShort(folioState.leftPageId);
        }
        if ($('slotBadgeRightName')) {
            $('slotBadgeRightName').textContent = 'FOLIO R · ' + getPageShort(folioState.rightPageId);
        }
        if ($('closeLeftSlotBtn')) $('closeLeftSlotBtn').textContent = tr('关闭分屏');
        if ($('closeRightSlotBtn')) $('closeRightSlotBtn').textContent = tr('关闭分屏');

        const hlTag = document.querySelector('#dropHalfLeft .dh-tag');
        const hlSub = document.querySelector('#dropHalfLeft .dh-sub');
        const hrTag = document.querySelector('#dropHalfRight .dh-tag');
        const hrSub = document.querySelector('#dropHalfRight .dh-sub');
        if (hlTag) hlTag.textContent = tr('FOLIO L · 左页展开');
        if (hlSub) hlSub.textContent = tr('松手置入左书页');
        if (hrTag) hrTag.textContent = tr('FOLIO R · 右页展开');
        if (hrSub) hrSub.textContent = tr('松手置入右书页');

        document.querySelectorAll('.bookmark-tab[data-page]').forEach((tab) => {
            const pid = tab.dataset.page;
            tab.classList.remove('active', 'in-left', 'in-right');
            if (folioState.mode === 'single') {
                if (pid === folioState.leftPageId) tab.classList.add('active');
            } else {
                // Keep .active on both shown tabs so existing workflows checking .nav-item.active work in split view
                if (pid === folioState.leftPageId) tab.classList.add('active', 'in-left');
                if (pid === folioState.rightPageId) tab.classList.add('active', 'in-right');
            }
        });

        const crumb = $('resonaCrumb');
        if (crumb) {
            crumb.textContent = folioState.mode === 'single'
                ? (getPageName(folioState.leftPageId) || ('III · ' + getPageShort('page-applebooks')))
                : `${getPageName(folioState.leftPageId)} | ${getPageName(folioState.rightPageId)}`;
        }

        if (splitBtn && splitBtnLabel) {
            const isDual = folioState.mode === 'dual';
            splitBtn.classList.toggle('is-on', isDual);
            splitBtnLabel.textContent = tr(isDual ? '单页' : '分屏');
        }

        if (typeof window.__syncMakeSteps === 'function') {
            window.__syncMakeSteps();
        }
    }

    function switchPage(rawPageId, targetSide) {
        const pageId = normalizePageId(rawPageId);
        if (!pageId) return;

        if (folioState.mode === 'single') {
            folioState.leftPageId = pageId;
        } else {
            const side = targetSide || folioState.activeSlot || 'left';
            if (side === 'right') {
                if (folioState.leftPageId === pageId) {
                    folioState.leftPageId = folioState.rightPageId;
                }
                folioState.rightPageId = pageId;
            } else {
                if (folioState.rightPageId === pageId) {
                    folioState.rightPageId = folioState.leftPageId;
                }
                folioState.leftPageId = pageId;
            }
        }
        renderFolioLayout();
    }
    window.__folioSwitchPage = switchPage;

    function placePageOnSide(rawPageId, side) {
        const pageId = normalizePageId(rawPageId);
        if (!pageId) return;

        if (folioState.mode === 'single') {
            const prevPage = folioState.leftPageId;
            folioState.mode = 'dual';
            if (side === 'left') {
                folioState.leftPageId = pageId;
                folioState.rightPageId = (prevPage !== pageId) ? prevPage : 'page-applebooks';
            } else {
                folioState.rightPageId = pageId;
                folioState.leftPageId = (prevPage !== pageId) ? prevPage : 'page-books';
            }
        } else if (side === 'left') {
            if (folioState.rightPageId === pageId) {
                folioState.rightPageId = folioState.leftPageId;
            }
            folioState.leftPageId = pageId;
        } else {
            if (folioState.leftPageId === pageId) {
                folioState.leftPageId = folioState.rightPageId;
            }
            folioState.rightPageId = pageId;
        }

        folioState.activeSlot = side;
        renderFolioLayout();
        setColophonStatus(`${tr('双页分屏：')}${getPageName(folioState.leftPageId)} | ${getPageName(folioState.rightPageId)}`);
    }

    function initBookmarkDragAndSplitControls() {
        const stage = $('folioStage');
        const dropLayer = $('folioDropLayer');
        const halfLeft = $('dropHalfLeft');
        const halfRight = $('dropHalfRight');
        const leftSlot = $('folioLeft');
        const rightSlot = $('folioRight');

        if (leftSlot) leftSlot.addEventListener('pointerdown', () => { folioState.activeSlot = 'left'; }, { passive: true });
        if (rightSlot) rightSlot.addEventListener('pointerdown', () => { folioState.activeSlot = 'right'; }, { passive: true });

        $('splitModeBtn')?.addEventListener('click', () => {
            if (folioState.mode === 'single') {
                const cur = folioState.leftPageId;
                folioState.mode = 'dual';
                if (cur === 'page-applebooks') {
                    folioState.leftPageId = 'page-books';
                    folioState.rightPageId = 'page-applebooks';
                } else {
                    folioState.leftPageId = cur;
                    folioState.rightPageId = 'page-applebooks';
                }
                setColophonStatus(tr('双页分屏已展开'));
            } else {
                folioState.mode = 'single';
                setColophonStatus(tr('就绪'));
            }
            renderFolioLayout();
        });

        $('closeLeftSlotBtn')?.addEventListener('click', () => {
            folioState.mode = 'single';
            folioState.leftPageId = folioState.rightPageId;
            folioState.activeSlot = 'left';
            renderFolioLayout();
        });

        $('closeRightSlotBtn')?.addEventListener('click', () => {
            folioState.mode = 'single';
            folioState.activeSlot = 'left';
            renderFolioLayout();
        });

        let suppressNextTabClick = false;
        document.querySelectorAll('.bookmark-tab[data-page]').forEach((tab) => {
            tab.addEventListener('click', (e) => {
                if (suppressNextTabClick) {
                    e.stopImmediatePropagation();
                    e.preventDefault();
                }
            }, true);

            tab.addEventListener('pointerdown', (ev) => {
                if (ev.button !== 0) return;
                ev.preventDefault();
                clearTextSelection();
                const pageId = tab.dataset.page;
                const startX = ev.clientX;
                const startY = ev.clientY;
                let dragging = false;
                let ghost = null;
                let stageRect = null;
                let midX = 0;
                let hoverSide = null;
                let rafId = 0;
                let latestX = startX;
                let latestY = startY;

                function updateGhostFrame() {
                    rafId = 0;
                    if (!ghost) return;
                    ghost.style.transform = `translate3d(${latestX + 12}px, ${latestY + 12}px, 0)`;

                    if (stageRect &&
                        latestY >= stageRect.top - 12 &&
                        latestY <= stageRect.bottom &&
                        latestX >= stageRect.left &&
                        latestX <= stageRect.right) {
                        const nextSide = latestX < midX ? 'left' : 'right';
                        if (nextSide !== hoverSide) {
                            hoverSide = nextSide;
                            halfLeft?.classList.toggle('is-over', hoverSide === 'left');
                            halfRight?.classList.toggle('is-over', hoverSide === 'right');
                        }
                    } else if (hoverSide !== null) {
                        hoverSide = null;
                        halfLeft?.classList.remove('is-over');
                        halfRight?.classList.remove('is-over');
                    }
                }

                function onMove(moveEv) {
                    latestX = moveEv.clientX;
                    latestY = moveEv.clientY;
                    const dist = Math.hypot(latestX - startX, latestY - startY);

                    if (!dragging && dist >= 7) {
                        dragging = true;
                        document.body.classList.add('is-dragging-folio');
                        clearTextSelection();
                        tab.classList.add('is-dragging-src');
                        stageRect = stage ? stage.getBoundingClientRect() : null;
                        midX = stageRect ? (stageRect.left + stageRect.width / 2) : (window.innerWidth / 2);

                        dropLayer?.classList.add('is-active');
                        ghost = document.createElement('div');
                        ghost.className = 'drag-ghost-bookmark';
                        ghost.textContent = getPageName(pageId) || tab.textContent.trim();
                        document.body.appendChild(ghost);
                    }

                    if (dragging && !rafId) {
                        rafId = requestAnimationFrame(updateGhostFrame);
                    }
                }

                function onUp() {
                    window.removeEventListener('pointermove', onMove);
                    window.removeEventListener('pointerup', onUp);
                    window.removeEventListener('pointercancel', onUp);
                    if (rafId) cancelAnimationFrame(rafId);
                    document.body.classList.remove('is-dragging-folio');
                    clearTextSelection();

                    if (dragging) {
                        suppressNextTabClick = true;
                        setTimeout(() => { suppressNextTabClick = false; }, 0);
                        tab.classList.remove('is-dragging-src');
                        dropLayer?.classList.remove('is-active');
                        halfLeft?.classList.remove('is-over');
                        halfRight?.classList.remove('is-over');
                        if (ghost) ghost.remove();

                        if (hoverSide === 'left' || hoverSide === 'right') {
                            placePageOnSide(pageId, hoverSide);
                        }
                    }
                }

                window.addEventListener('pointermove', onMove, { passive: true });
                window.addEventListener('pointerup', onUp);
                window.addEventListener('pointercancel', onUp);
            });
        });
    }

    /* ======================================================================
       2. 短名折叠抽屉 & 通道发送桥接 & 封面文字同步
       ====================================================================== */
    function initFoldDrawersAndButtons() {
        document.querySelectorAll('.fold-trigger').forEach((btn) => {
            btn.addEventListener('click', () => {
                const panel = btn.closest('.fold-panel');
                if (!panel) return;
                const willOpen = !panel.classList.contains('is-open');
                panel.classList.toggle('is-open', willOpen);
                btn.setAttribute('aria-expanded', willOpen ? 'true' : 'false');
            });
        });

        document.querySelectorAll('.dest-send-btn[data-send-ch]').forEach((btn) => {
            btn.addEventListener('click', (e) => {
                e.stopPropagation();
                const ch = btn.dataset.sendCh;
                if (ch && typeof window.__activateChannel === 'function') {
                    window.__activateChannel(ch);
                }
                $('txSend')?.click();
            });
        });
    }

    function syncDefaultCoverLabels() {
        const titleEl = $('inputTitle');
        const authorEl = $('inputAuthor');
        const covTitle = $('defaultCoverTitle');
        const covAuthor = $('defaultCoverAuthor');
        if (covTitle) covTitle.textContent = (titleEl && titleEl.value.trim()) ? titleEl.value.trim() : tr('未命名书籍');
        if (covAuthor) covAuthor.textContent = (authorEl && authorEl.value.trim()) ? authorEl.value.trim() : tr('未知作者');
    }
    window.__syncDefaultCoverLabels = syncDefaultCoverLabels;

    function hookInputValueSync(el, cb) {
        if (!el || el.__folioValHooked) return;
        try {
            const proto = Object.getPrototypeOf(el);
            const desc = Object.getOwnPropertyDescriptor(proto, 'value');
            if (!desc || !desc.get || !desc.set) return;
            Object.defineProperty(el, 'value', {
                configurable: true,
                enumerable: desc.enumerable,
                get: function () { return desc.get.call(this); },
                set: function (v) {
                    const prev = desc.get.call(this);
                    desc.set.call(this, v);
                    if (prev !== desc.get.call(this)) cb();
                }
            });
            el.__folioValHooked = true;
        } catch (e) {}
    }

    function initCoverCaptionSync() {
        const titleEl = $('inputTitle');
        const authorEl = $('inputAuthor');
        hookInputValueSync(titleEl, syncDefaultCoverLabels);
        hookInputValueSync(authorEl, syncDefaultCoverLabels);
        titleEl?.addEventListener('input', syncDefaultCoverLabels);
        titleEl?.addEventListener('change', syncDefaultCoverLabels);
        authorEl?.addEventListener('input', syncDefaultCoverLabels);
        authorEl?.addEventListener('change', syncDefaultCoverLabels);
        document.addEventListener('languagechange', syncDefaultCoverLabels);

        const fileInfoEl = $('fileInfo');
        if (fileInfoEl && typeof MutationObserver !== 'undefined') {
            new MutationObserver(() => { syncDefaultCoverLabels(); }).observe(fileInfoEl, {
                childList: true,
                subtree: true,
                attributes: true,
                attributeFilter: ['style', 'class']
            });
        }
        syncDefaultCoverLabels();
    }

    /* ======================================================================
       3. 传输待传列表跨页拖书接收（支持书库拖书与本地拖入 EPUB）
       ====================================================================== */
    function initTxQueueDropTarget() {
        const queueCard = $('txQueueCard');
        if (!queueCard) return;

        queueCard.addEventListener('dragover', (e) => {
            e.preventDefault();
            queueCard.classList.add('is-drop-target');
        });

        queueCard.addEventListener('dragleave', (e) => {
            if (!e.relatedTarget || !queueCard.contains(e.relatedTarget)) {
                queueCard.classList.remove('is-drop-target');
            }
        });

        queueCard.addEventListener('drop', (e) => {
            e.preventDefault();
            queueCard.classList.remove('is-drop-target');
            document.body.classList.remove('is-dragging-folio');
            clearTextSelection();

            const rawBook = e.dataTransfer?.getData('application/x-easypub-book');
            if (rawBook) {
                try {
                    const parsed = JSON.parse(rawBook);
                    if (parsed && parsed.path && typeof window.__addBooksToTxQueue === 'function') {
                        window.__addBooksToTxQueue([parsed]);
                    }
                } catch (err) {
                    console.warn('跨页拖书解析失败:', err);
                }
                return;
            }

            const files = Array.from(e.dataTransfer?.files || []);
            const items = files
                .filter((f) => f && f.path && /\.(epub|txt|mobi|azw3|pdf)$/i.test(f.name || f.path))
                .map((f) => ({
                    path: f.path,
                    file: f.name || String(f.path).split(/[\\/]/).pop(),
                    title: (f.name || String(f.path).split(/[\\/]/).pop()).replace(/\.[^.]+$/, ''),
                    size: f.size || 0
                }));

            if (items.length && typeof window.__addBooksToTxQueue === 'function') {
                window.__addBooksToTxQueue(items);
            }
        });
    }

    /* ======================================================================
       4. V3 操作说明弹窗 + 「使用教程 ↗」直达完整 help.html 窗口
       ====================================================================== */
    function openStudioGuideModal() {
        const modal = $('studioGuideModal');
        const title = $('studioGuideTitle');
        const body = $('studioGuideBody');
        const tutBtn = $('studioGuideTutorial');
        if (!modal) return;

        if (title) title.textContent = tr('操作说明');
        if (body) {
            body.innerHTML = [
                `<p><b>1. ${tr('拖拽分屏')}：</b>${tr('按住顶栏任意栏目（如「II · 制作」），拖向下方左/右书页即可展开双页对开模式。')}</p>`,
                `<p><b>2. ${tr('跨页拖书')}：</b>${tr('分屏时可直接把「IV · 书库」的书籍封面拖入「III · 传输」待传列表。')}</p>`,
                `<p><b>3. ${tr('高效折叠')}：</b>${tr('常用功能全部外露，低频参数收纳在各页折叠条内。')}</p>`
            ].join('');
        }
        if (tutBtn) tutBtn.style.display = '';

        modal.style.display = 'flex';
        modal.style.visibility = 'visible';
        modal.style.pointerEvents = 'auto';
        modal.classList.add('show');
    }

    function closeStudioGuideModal() {
        const modal = $('studioGuideModal');
        if (!modal) return;
        modal.classList.remove('show');
        modal.style.display = '';
    }

    window.__openStudioGuide = openStudioGuideModal;

    function initStudioGuideModal() {
        $('studioGuideClose')?.addEventListener('click', closeStudioGuideModal);
        $('studioGuideTutorial')?.addEventListener('click', () => {
            closeStudioGuideModal();
            if (typeof window.__openHelp === 'function') {
                window.__openHelp();
            } else if (window.electronAPI?.applebooks?.openHelp) {
                window.electronAPI.applebooks.openHelp().catch((err) => {
                    console.warn('打开使用教程失败:', err);
                });
            }
        });
    }

    /* ======================================================================
       5. 初始化入口
       ====================================================================== */
    function bootFolioBridge() {
        initBookmarkDragAndSplitControls();
        initFoldDrawersAndButtons();
        initCoverCaptionSync();
        initTxQueueDropTarget();
        initStudioGuideModal();
        renderFolioLayout();
        document.addEventListener('dragstart', clearTextSelection);
        document.addEventListener('dragend', () => {
            document.body.classList.remove('is-dragging-folio');
            clearTextSelection();
        });
        document.addEventListener('drop', () => {
            document.body.classList.remove('is-dragging-folio');
            clearTextSelection();
        });
        document.addEventListener('languagechange', () => {
            renderFolioLayout();
            syncDefaultCoverLabels();
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', bootFolioBridge);
    } else {
        bootFolioBridge();
    }
})();
