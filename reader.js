/* ============================================================
   reader.js — EasyPub 内置阅读窗口（foliate-js 引擎）

   为什么自己写界面：foliate-js 只提供 API，不带 UI。这样配色 / 字号 /
   中文字体（宋体·黑体）才能完全跟随 EasyPub 的深色设计语言。

   引擎：node_modules/foliate-js/view.js 的 <foliate-view> 自定义元素。
   书本文件：直接 makeBook(绝对路径) —— 实测 Electron 的 file:// 页面
   可以 fetch 本地文件（见 tools/_reader_probe.js 的验证结论），
   所以不需要把书读成 Buffer 再塞过来。
   ============================================================ */
import { makeBook } from './node_modules/foliate-js/view.js';

/* ---------- i18n：复用弹窗页那套（i18n-popups.js 暴露 window.pageI18n） ---------- */
function T(s, fb) {
    try {
        if (window.pageI18n) return window.pageI18n.t(s, fb);
    } catch (e) {}
    return (fb != null) ? fb : s;
}

/* ---------- 设计令牌：正文配色（三套，存 localStorage） ---------- */
var THEMES = {
    dark:  { bg: '#0B0D10', panel: '#11151A', tx: '#C9CFD8', tx2: '#8D97A6', tx3: '#5A6472',
             line: '#232A33', acc: '#4FD1C5', sel: 'rgba(79,209,197,.22)', link: '#4FD1C5',
             // 标题金：深色底上要提亮才看得清（原书 Title 用的是 rgb(184,149,106)，太暗）
             title: '#E3C078', rule: 'rgba(227,192,120,.34)' },
    light: { bg: '#FFFFFF', panel: '#FFFFFF', tx: '#1F242B', tx2: '#5A6472', tx3: '#8D97A6',
             line: '#E3E7ED', acc: '#2AA79C', sel: 'rgba(42,167,156,.20)', link: '#0E7C72',
             title: '#9A6B1F', rule: 'rgba(154,107,31,.28)' },
    sepia: { bg: '#F4ECD8', panel: '#F4ECD8', tx: '#43382A', tx2: '#6E5F49', tx3: '#93836B',
             line: '#DFD3B8', acc: '#9A6B34', sel: 'rgba(154,107,52,.20)', link: '#8A5A22',
             title: '#8A5A22', rule: 'rgba(138,90,34,.30)' }
};

/* ---------- 中文字体：项目自带 Noto（统一放在 fonts/ 子目录） ---------- */
// ★ 路径必须带 fonts/ 前缀：下面的 fetch() 是相对 reader.html 所在目录解析的，
//   写成裸文件名就会去应用根目录找 → 404 → 静默回退成系统字体
//   （黑体选项曾经就是这样失效的）。theme.css / help.html 用的是
//   url('fonts/...')，两边现在一致了。
var FONTS = [
    { id: 'serif', name: '宋体 / 思源宋体', file: 'fonts/NotoSerifSC-VF.ttf', family: '"Noto Serif SC", "Songti SC", "SimSun", serif' },
    { id: 'sans',  name: '黑体 / 思源黑体', file: 'fonts/NotoSansSC-VF.ttf',  family: '"Noto Sans SC", "Microsoft YaHei UI", "Heiti SC", sans-serif' }
];
var fontCache = {};                 // id -> Blob，25MB 的字体只读一次

var STORE_UI = 'reader.ui';        // 界面偏好（主题/字体/字号/行距/翻页）
var STORE_POS = 'reader.pos.';     // 每本书的阅读位置（CFI）
var STORE_BM  = 'reader.bm.';      // 每本书的书签

var ui = {
    theme: 'dark', font: 'serif', size: 20, lh: 1.9, flow: 'paginated', align: 'start', margins: 'normal'
};
var view = null;                   // <foliate-view>
var book = null;
var bookPath = '';
var bookKey = '';
var bookTitle = '';
var progressMap = [];              // 章节起始百分比（用于大章节内定位）
var tocFlat = [];
var searchSeq = 0;                 // 搜索序号：新搜索作废旧的

/* ---------- 工具 ---------- */
function $(id) { return document.getElementById(id); }
function save(k, v) { try { localStorage.setItem(k, v); } catch (e) {} }
function load(k, d) { try { var v = localStorage.getItem(k); return v == null ? d : v; } catch (e) { return d; } }
function saveJSON(k, o) { save(k, JSON.stringify(o)); }
function loadJSON(k, d) { try { var v = localStorage.getItem(k); return v ? JSON.parse(v) : d; } catch (e) { return d; } }

/* 书里的样式分两层要和阅读器主题搏斗：
   ① 样式表里的 color / font-family —— 用注入的 !important 就能压住；
   ② ★ 更狠的一层：Book 生成的 EPUB 把 font-family 和 color 直接写成
      元素的行内样式，还带 !important：
        <p style="text-align:center;color:rgb(184,149,106) !important;…">
      行内 !important 的优先级高于任何样式表（包括我们注入的 !important），
      所以整本书的正文颜色和字体都改不动 —— 深色主题下就是「棕色/近黑小字
      贴在近黑背景上，完全看不清」。
      这里把行内样式里的 !important 抹掉，并直接删掉 font-family（避免书里
      的 "Songti SC"/"宋体" 抢先命中），剩下的交给注入样式表。 */
function cleanInlineStyle(style) {
    if (!style || typeof style !== 'string') return style;
    var s = style.replace(/!\s*important/gi, '');
    s = s.replace(/(^|;)\s*font-family\s*:[^;]*/gi, '$1');
    // 这几个「颜色类」属性不靠继承，必须单独删：书里给标题写
    // -webkit-text-fill-color 时，光改 color 是不生效的
    s = s.replace(/(^|;)\s*-(webkit|moz)-text-fill-color\s*:[^;]*/gi, '$1');
    s = s.replace(/(^|;)\s*(text-decoration-color|text-emphasis-color|caret-color|color)\s*:[^;]*/gi, '$1');
    return s.replace(/;\s*;/g, ';').replace(/^\s*;\s*|\s*;\s*$/g, '').trim();
}

/* 生成的书里，章标题下面那行「装饰线」是纯文本段落：
   <p style="…"><font color="#b8956a">────── ⋅ ──────</font></p>
   给它打上 .rd-deco，样式表里才能把 p{text-indent:2em} 对它归零、并与标题同心。 */
var DECO_RE = /^[\s\u00a0\-—–·⋅•‧·.。·=_*~～⌒〜ー―─━┄┅┈┉╌╍]{3,}$/;

function markDeco(root) {
    if (!root || !root.querySelectorAll) return;
    var ps = root.querySelectorAll('p, div, span, h2');
    for (var i = 0; i < ps.length; i++) {
        var el = ps[i];
        if (el.children && el.children.length > 1) continue;
        var txt = (el.textContent || '').replace(/[\s\u00a0]+/g, ' ').trim();
        if (!txt || txt.length > 60) continue;
        if (DECO_RE.test(txt)) el.classList.add('rd-deco');
    }
}

function rewriteInlineStyles(root) {
    if (!root || !root.querySelectorAll) return;
    var els = root.querySelectorAll('[style]');
    for (var i = 0; i < els.length; i++) {
        var el = els[i];
        var before = el.getAttribute('style') || '';
        var after = cleanInlineStyle(before);
        if (after) el.setAttribute('style', after);
        else el.removeAttribute('style');
    }
    markDeco(root);
}

/* 把用户选的中文字体压进书内样式表，并让主题颜色能生效 */
function rewriteBookCSS(css, family) {
    if (!css || typeof css !== 'string') return css;
    // 字体：整个 font-family 声明换成用户选的
    css = css.replace(/font-family\s*:[^;}]*/gi, 'font-family: ' + family);
    // 颜色：书内 color / background 常带 !important（Book 生成的书就是这样），
    // 抹掉 !important 后我们的注入样式表才压得住
    css = css.replace(/(color\s*:[^;}]*?)\s*!\s*important/gi, '$1');
    css = css.replace(/(background(-color)?\s*:[^;}]*?)\s*!\s*important/gi, '$1');
    return css;
}

/* 注入到书里的正文样式（层级高于书内样式，用 !important 压住） */
function buildCSS() {
    var t = THEMES[ui.theme] || THEMES.dark;
    var f = FONTS.find(function (x) { return x.id === ui.font; }) || FONTS[0];
    var align = ui.align === 'justify' ? 'justify' : 'start';
    // 边距档位现在是「正文列左右各留多少」——因为整页底色已刷成主题色，
    // 留白只是让行长舒服，不会再出现「中间一条纸、两边空框」的割裂感。
    var pad = ui.margins === 'wide' ? '0 1.2%' : (ui.margins === 'narrow' ? '0 9%' : '0 4.5%');
    // ★ 字号必须同时落在 html 上：书里常写 html{font-size:87.5%}，只设 body 会被
    //   它按比例缩水（19px → 16.8px），并且 font-size 是可继承属性，挂在 html 上
    //   才能整体放大标题、行首装饰等所有相对字号。
    return [
        'html { color-scheme: ' + (ui.theme === 'dark' ? 'dark' : 'light') + ' !important;',
        '       font-size: ' + ui.size + 'px !important;',
        // ★ 分页器给每列补的「纸」用的是这个变量
        //   （paginator.js #replaceBackground 里读 html 的 --theme-bg-color，
        //    读不到就回退成书自己的背景色 = 白色），所以必须一起给它。
        '       --theme-bg-color: ' + t.bg + ' !important;',
        // 也让整页底色/文字色跟着主题
        '       background: ' + t.bg + ' !important; color: ' + t.tx + ' !important; }',
        // ★ 用户要的「填满」+「边框跟着主题变色」：
        //   分页器给正文列的宽度上限是 720px，窗口再宽也只用中间一块；与其硬去改
        //   分页器的列宽（会牵动分页计算），不如把**整页底色**刷成主题的纸张色，
        //   同时让正文不再被 max-width 挤窄 —— 于是「边距」和正文同色，看不出来。
        //   注意 body 的 max-width 必须写 !important：分页器 columnize() 里用
        //   setStylesImportant(body,{max-width:'none'}) 压制，我们这条要能压回去。
        'html, body { background: ' + t.bg + ' !important; color: ' + t.tx + ' !important; }',
        'body { font-size: ' + ui.size + 'px !important; max-width: none !important; margin: 0 !important;',
        '       padding: ' + pad + ' !important; }',
        'body, p, div, span, li, td, th, a, h1, h2, h3, h4, h5, h6, blockquote, dd, dt, em, i, strong, b, small, figcaption, aside, section, article {',
        '  font-family: ' + f.family + ' !important;',
        '  color: ' + t.tx + ' !important;',
        '  line-height: ' + ui.lh + ' !important;',
        '}',
        // 书里的 <span> 常自带 font-size/color，（行内 !important 已被抹掉）
        // 这里对正文里的元素统一接管字号，保证「字号」按钮的效果是真的
        'body p, body div, body span, body li, body dd, body blockquote, body td {',
        '  font-size: ' + ui.size + 'px !important; }',
        'h1, h2, h3, h4, h5, h6 { font-size: 1.28em !important; font-weight: 600 !important; color: ' + t.title + ' !important; }',
        'h1 { font-size: 1.5em !important; }',
        // ★ 章标题：Book 生成的书把章名写成 <p style="text-align:center;color:gold">，
        //   行内样式已被 cleanInlineStyle 抹掉，这里用「居中 + 金色 + 下压间距」把它
        //   恢复成标题的样子，并把 .chapter/.title/.heading 这类常见章名 class 一并纳入
        'p.chapter, p.title, p.chap, p.heading, p.chapter-title, p.chapterTitle, p.ct, div.chapter, div.title, div.heading, section > h1, section > h2 {',
        '  text-align: center !important;',
        '  color: ' + t.title + ' !important;',
        '  font-weight: 600 !important;',
        '  font-size: 1.34em !important;',
        '  line-height: 1.7 !important;',
        '  letter-spacing: .06em !important;',
        '  margin: 1.1em 0 1.5em !important;',
        '  text-indent: 0 !important;',
        '  border: 0 !important;',
        '}',
        // ★ 章标题下的「装饰线」在生成的书里其实是
        //   <p style="text-align:center"><font color="#b8956a">────── ⋅ ──────</font></p>
        //   （是文字，不是 <hr>）。它会被 p{text-indent:2em} 推歪、不再与标题同心，
        //   所以把这些「只由分界线字符组成的段落」标成 .rd-deco，单独归零缩进。
        'p.rd-deco, p.rd-deco font {',
        '  text-indent: 0 !important;',
        '  text-align: center !important;',
        '  margin: .2em 0 .4em !important;',
        '  padding: 0 !important;',
        '  letter-spacing: .18em !important;',
        '  font-size: .78em !important;',
        '  color: ' + t.rule + ' !important;',
        '  line-height: 1.2 !important;',
        '}',
        'p.rd-deco font { color: ' + t.rule + ' !important; }',
        // ★★ 2026-09-30（用户要求「线太短了，修」）：**不再强制 34% 居中**。
        //   我们生成的 EPUB 里，章标题下面那条装饰线就是 `<hr style="width:100%…">`（满宽金线）；
        //   而 cleanInlineStyle() 会把书里行内样式的 !important 抹掉，于是这里带 !important 的
        //   `width:34%; margin:1.6em auto` 一压 → 满宽金线变成一截居中的短横线（用户截图实锤）。
        //   现在只保留「用主题的线色、去掉默认立体边框」，**宽度/对齐交给书里自己的样式**
        //   （书里没写宽度时，hr 默认就是满宽）。
        'p + hr, hr + p { margin-top: 1.2em !important; }',
        'blockquote { border-left: 3px solid ' + t.rule + ' !important; padding-left: .9em !important;',
        '             color: ' + t.tx2 + ' !important; opacity: 1; }',
        'p { text-indent: 2em !important; }',
        'p, li, dd, blockquote, td { text-align: ' + align + ' !important; }',
        'a, a:visited { color: ' + t.link + ' !important; }',
        'img, svg, video { max-width: 100% !important; height: auto !important; }',
        '::selection { background: ' + t.sel + ' !important; }',
        'code, pre, kbd, samp { font-family: "Cascadia Mono", Consolas, monospace !important; background: ' + t.sel + '; }',
        // 书内常见的黑白反色/浅色背景（EasyPub 生成的书用了 light-dark()）
        '[style*="background"] { background: transparent !important; }'
    ].join('\n');
}

/* 把书页铺满阅读区。
   ★ foliate 的分页器默认 `--_max-inline-size:720px`（正文列硬上限）+ `--_margin:48px`
   （上下留白）+ `--_half-gap:3.5%`（左右外侧留白列），所以 1164px 的面板里正文只用
   中间 720px，两侧各约 220px 是分页器自己的「空框」——这就是用户说的
   「书籍页面只占中间一部分、边距太大」。
   覆盖三个变量就能真正铺满：
     - `--_max-inline-size` = 阅读区宽度（单列宽度上限）
     - `--_max-column-count-spread` = 1（★ 名字必须带 `-spread`！分页器读的是这个，
        只写 `--_max-column-count` 它不认，默认 spread=2，窗口一宽就悄悄拆成双栏）
     - `--_max-width` = max-inline-size × spread = 阅读区宽度
   必须用「像素」：分页器里 `Math.ceil(size / maxInlineSize)` 是直接做除法，写百分比
   会算成 NaN。`--_half-gap` 归零后左右外侧空列消失；正文左右的呼吸感改由
   `body{padding}`（边距档位）提供。 */
function applyPageWidth() {
    var pane = $('rdPane');
    if (!pane || !view) return;
    var pageW = Math.round(pane.getBoundingClientRect().width);
    if (!pageW) return;
    var st = view.style;
    st.setProperty('--_max-inline-size', pageW + 'px');
    st.setProperty('--_max-column-count-spread', '1');
    st.setProperty('--_max-width', pageW + 'px');
    st.setProperty('--_half-gap', '0px');
    st.setProperty('--_gap', '0px');
    st.setProperty('--_margin', ui.margins === 'wide' ? '14px' : (ui.margins === 'narrow' ? '56px' : '30px'));
}

/* 按需加载字体：宋体有 25MB，只有用户真的选了才读，避免打开书就卡住。
   ★ 坑（实测过）：FontFace 对象是「某个 document 的」——在主文档里 new FontFace，
   再塞进书 iframe 的 document.fonts 是无效的（iframe 里永远是系统回退，canvas 量
   中文字宽全等 = 拿到同一个回退字体）。正确做法：在书所在的 document 里用
   blob: URL 建 FontFace 再 add，blob 与该 document 同源，能真正解码。 */
function ensureFont(id) {
    var f = FONTS.find(function (x) { return x.id === id; });
    if (!f) return Promise.resolve();
    var name = f.family.split(',')[0].replace(/"/g, '');
    var dv = null;
    try {
        dv = view && view.renderer && typeof view.renderer.getContents === 'function'
            ? view.renderer.getContents()[0] : null;
    } catch (e) {}
    var doc = dv && dv.doc ? dv.doc : null;
    if (!doc || !doc.fonts || !doc.fonts.add || doc.__rdFont === id) return Promise.resolve();
    doc.__rdFont = id;                                  // 同一 document 同一字体只做一次
    var prev = fontCache[id];
    var reading = prev || fetch(f.file).then(function (r) {
        if (!r.ok) throw new Error('font http ' + r.status);
        return r.blob();
    }).then(function (b) {
        fontCache[id] = b;
        return b;
    });
    return reading.then(function (b) {
        if (!b) return null;
        var url = URL.createObjectURL(b);
        var ff = new doc.defaultView.FontFace(name, 'url("' + url + '")');
        return ff.load().then(function (loaded) {
            doc.fonts.add(loaded);
            // 字宽变了（回退字体 → Noto），分页要重排一次，否则每页行数还是旧的
            try { view.renderer.expand && view.renderer.expand(); } catch (e) {}
        });
    }).catch(function (e) {
        doc.__rdFont = '';
        console.warn('[reader] 字体加载失败', id, e && e.message);
    });
}

/* 把样式与字体一起刷进书里 */
function refreshStyle() {
    applyPageWidth();                       // ← 每次重排前先按阅读区实际宽度重设列宽
    if (!view || !view.renderer || !view.renderer.setStyles) return;
    try { view.renderer.setAttribute('flow', ui.flow); } catch (e) {}
    view.renderer.setStyles(buildCSS());
    ensureFont(ui.font);
}

/* ---------- 翻页：空格 / 方向键 / 点半屏 ---------- */
/* foliate-view 自己的点击区在 shadow DOM 里，外面拿不到；书页还是 iframe，
   父文档的 click 也收不到 iframe 里的点击。所以两边都挂：
   ① 父文档（点到页边留白）；② 每章 load 后在 iframe 的 document 上再挂一份
   （点到正文文字上）。
   点击区：2026-09-24 用户要求放大 —— 现在是「右半屏前进、左半屏后退」
   （原来是右 22% / 左 22%，中间 56% 留给划词，用户嫌范围太小）。
   放大后中间不再天然避开划词，所以加了 isDragClick / hasSelection 两道闸：
   按下到抬起位移超过 6px、或者页面上有非折叠选区时，一律不翻页。 */
/* 翻页锁：view.next()/prev() 是 async 的，一次翻页要等分页器重排完。
   不加锁连按时，后一次会基于「还没更新的 lastLocation」算位置，实测会原地打转
   （0.000695 → 0.000117 → 0.000695 → 0.001273）。这里把翻页串行化，最多排 3 步。
   2026-09-24：补上「排队的步数要真的执行」。原来 pageQueue 只写不读（没有出队逻辑），
   翻页期间的点按会被静默丢掉，表现是「点了没反应」。 */
var pageBusy = false, pageQueue = 0;

function drainQueue() {
    pageBusy = false;
    if (pageQueue > 0) { pageQueue--; turn(1); }
    else if (pageQueue < 0) { pageQueue++; turn(-1); }
}

function doTurn(dir) {
    var fn = dir > 0 ? view.next : view.prev;
    if (typeof fn !== 'function') { pageBusy = false; return; }
    try {
        var p = fn.call(view);
        if (!p || typeof p.then !== 'function') { pageBusy = false; return; }
        p.then(drainQueue, drainQueue);
    } catch (e) { pageBusy = false; }
}
function turn(dir) {
    if (!view) return;
    if (pageBusy) { pageQueue = Math.max(-3, Math.min(3, pageQueue + dir)); return; }
    pageBusy = true;
    doTurn(dir);
}
function pageNext() { turn(1); }
function pagePrev() { turn(-1); }

/* ---------- 点击翻页的分区与误判防护 ---------- */
var _rdDown = { x: 0, y: 0 };
function markDown(x, y) { _rdDown.x = x; _rdDown.y = y; }
function isDragClick(e) {
    return Math.abs(e.clientX - _rdDown.x) > 6 || Math.abs(e.clientY - _rdDown.y) > 6;
}
function hasSelection(doc) {
    try {
        var w = (doc && (doc.defaultView || doc)) || window;
        var sel = w.getSelection ? w.getSelection() : null;
        return !!(sel && sel.rangeCount && !sel.isCollapsed && String(sel).trim());
    } catch (err) { return false; }
}
/* 右半边 → 前进(+1)、左半边 → 后退(-1)
   left/width 既能传元素矩形（rdPane），也能传 iframe 的 0 / innerWidth */
function zoneDir(x, left, width) {
    if (!width) return 0;
    var f = (x - left) / width;
    if (f > 0.5) return 1;
    if (f < 0.5) return -1;
    return 0;
}

function paneClick(e) {
    if (e.defaultPrevented) return;
    var pane = $('rdPane');
    if (!pane) return;
    var box = pane.getBoundingClientRect();
    if (!box.width) return;
    if (isDragClick(e) || hasSelection(window)) return;   // 划词/拖拽结束的那一下不算翻页
    var dir = zoneDir(e.clientX, box.left, box.width);
    if (dir > 0) { e.stopPropagation(); pageNext(); }
    else if (dir < 0) { e.stopPropagation(); pagePrev(); }
}

function attachFramePaging(doc) {
    if (!doc || doc.__rdPageBound) return;
    doc.__rdPageBound = true;
    // 记下按下的位置：用来区分「点击」和「拖拽 / 划词」
    doc.addEventListener('mousedown', function (e) { markDown(e.clientX, e.clientY); }, true);
    doc.addEventListener('click', function (e) {
        if (e.defaultPrevented) return;
        var t = e.target;
        // 链接、按钮、图片上的点击留给书本身
        while (t && t.nodeType === 1) {
            var tag = t.tagName;
            if (tag === 'A' || tag === 'BUTTON' || tag === 'INPUT' || tag === 'LABEL' ||
                tag === 'IMG' || tag === 'SVG' || tag === 'VIDEO' || tag === 'AUDIO' || tag === 'SELECT') return;
            t = t.parentElement;
        }
        if (e.button !== 0) return;
        if (isDragClick(e) || hasSelection(doc)) return;   // 划词/拖拽结束的那一下不算翻页
        var win = doc.defaultView || window;
        /* 注意：这里 e.clientX 是 **iframe 自己文档里**的坐标，不是阅读区坐标。
           分页器会把整节内容铺成一块比阅读区宽得多的画布，再用位移把当前页挪进可视区：
           实测 iframe innerWidth=2165 而阅读区只有 1164，阅读区 x=838 的点在 iframe 里是 x=1879。
           直接拿它和 innerWidth 分半，算出来的是「在这块大画布上的位置」，不是「在屏幕上的左右半」，
           所以点中间会乱跳（同一位置在不同分页偏移下会算出相反方向）。
           必须先加回 iframe 在视口里的 left，换算成视口坐标，再按阅读区分半。 */
        var fe = win.frameElement;
        var px = e.clientX + (fe ? fe.getBoundingClientRect().left : 0);
        var pane = $('rdPane');
        var box = pane ? pane.getBoundingClientRect() : null;
        var dir = (box && box.width) ? zoneDir(px, box.left, box.width)
                                     : zoneDir(e.clientX, 0, win.innerWidth || 1);
        if (dir > 0) pageNext();
        else if (dir < 0) pagePrev();
    }, true);
}

/* ---------- 界面偏好 ---------- */
function loadUI() {
    var s = loadJSON(STORE_UI, null);
    if (s && typeof s === 'object') {
        for (var k in ui) if (s[k] !== undefined) ui[k] = s[k];
    }
}
function saveUI() {
    saveJSON(STORE_UI, ui);
    document.documentElement.setAttribute('data-theme', ui.theme);
    document.documentElement.setAttribute('data-font', ui.font);
    Array.prototype.forEach.call(document.querySelectorAll('[data-set]'), function (el) {
        var g = el.getAttribute('data-set');
        var v = el.getAttribute('data-val');
        el.classList.toggle('on', String(ui[g]) === v);
    });
    $('rdSizeVal').textContent = ui.size + 'px';
    $('rdLhVal').textContent = ui.lh.toFixed(1);
    save(STORE_UI, JSON.stringify(ui));
}

/* ---------- 目录 ---------- */
function flattenTOC(items, depth, out) {
    (items || []).forEach(function (it) {
        out.push({ label: (it.label || '').trim() || '—', href: it.href, depth: depth, sub: !!it.subitems });
        if (it.subitems) flattenTOC(it.subitems, depth + 1, out);
    });
}
function renderTOC() {
    var box = $('rdTocList');
    box.innerHTML = tocFlat.map(function (it, i) {
        return '<div class="rd-toc-i d' + Math.min(it.depth, 3) + '" data-i="' + i + '" data-href="' +
            String(it.href || '').replace(/"/g, '&quot;') + '" title="' + String(it.label).replace(/"/g, '&quot;') + '">' +
            '<span class="rd-toc-t">' + it.label.replace(/[<>&]/g, function (c) {
                return c === '<' ? '&lt;' : (c === '>' ? '&gt;' : '&amp;');
            }) + '</span></div>';
    }).join('');
    Array.prototype.forEach.call(box.querySelectorAll('.rd-toc-i'), function (el) {
        el.addEventListener('click', function () {
            var href = el.getAttribute('data-href');
            if (href) view.goTo(href);
            highlightTOC(href);
            if (window.innerWidth < 860) toggleTOC(false);
        });
    });
    $('rdTocCount').textContent = tocFlat.length + ' ' + T('章');
}
function highlightTOC(href) {
    Array.prototype.forEach.call(document.querySelectorAll('.rd-toc-i'), function (el) {
        var t = el.getAttribute('data-href');
        el.classList.toggle('cur', !!t && !!href && (t === href || t.split('#')[0] === String(href).split('#')[0]));
    });
}

/* ---------- 书签 ---------- */
function getBookmarks() { return loadJSON(STORE_BM + bookKey, []); }
function renderBookmarks() {
    var list = getBookmarks();
    var box = $('rdBmList');
    if (!list.length) {
        box.innerHTML = '<div class="rd-empty">' + T('还没有书签。读到想记住的地方，点顶部 ⭐ 添加。') + '</div>';
        return;
    }
    box.innerHTML = list.map(function (b, i) {
        return '<div class="rd-bm-i" data-i="' + i + '">' +
            '<span class="rd-bm-t">' + String(b.label || b.cfi).replace(/[<>&]/g, function (c) {
                return c === '<' ? '&lt;' : (c === '>' ? '&gt;' : '&amp;');
            }) + '</span>' +
            '<span class="rd-bm-x" data-del="' + i + '" title="' + T('删除') + '">✕</span></div>';
    }).join('');
    Array.prototype.forEach.call(box.querySelectorAll('.rd-bm-i'), function (el) {
        el.addEventListener('click', function (e) {
            if (e.target && e.target.getAttribute('data-del') != null) return;
            var b = getBookmarks()[+el.getAttribute('data-i')];
            if (b && b.cfi) view.goTo(b.cfi);
        });
    });
    Array.prototype.forEach.call(box.querySelectorAll('[data-del]'), function (el) {
        el.addEventListener('click', function (e) {
            e.stopPropagation();
            var list2 = getBookmarks();
            list2.splice(+el.getAttribute('data-del'), 1);
            saveJSON(STORE_BM + bookKey, list2);
            renderBookmarks();
        });
    });
}

/* ---------- 搜索 ---------- */
/* view.search() 是「异步生成器」，必须 for await 消费：
     yield { progress }                        —— 进度
     yield { index, subitems:[{cfi,excerpt}] } —— 该章命中
     yield 'done'
   excerpt 是 {pre, match, post} 对象，不是字符串。 */
function esc(s) {
    return String(s == null ? '' : s).replace(/[<>&]/g, function (c) {
        return c === '<' ? '&lt;' : (c === '>' ? '&gt;' : '&amp;');
    });
}
function renderSearchResults(results, kw) {
    var box = $('rdSrList');
    if (!results.length) {
        box.innerHTML = '<div class="rd-empty">' + T('没有找到匹配的内容') + '</div>';
        return;
    }
    box.innerHTML = results.map(function (r, i) {
        var x = r.excerpt || {};
        var line = (x.pre || '') + '<em>' + esc(x.match || kw) + '</em>' + (x.post || '');
        return '<div class="rd-sr-i" data-i="' + i + '">' +
            '<div class="rd-sr-l">' + esc(r.label) + '</div>' +
            '<div class="rd-sr-x">' + line + '</div></div>';
    }).join('');
    Array.prototype.forEach.call(box.querySelectorAll('.rd-sr-i'), function (el) {
        el.addEventListener('click', function () {
            var r = results[+el.getAttribute('data-i')];
            if (r && r.cfi) view.goTo(r.cfi);
        });
    });
}
async function runSearch(kw) {
    if (!kw || !book || !view) return;
    var box = $('rdSrList');
    box.innerHTML = '<div class="rd-empty">' + T('搜索中…') + '</div>';
    var results = [];
    var searchId = ++searchSeq;
    try {
        for await (var r of view.search({ query: kw })) {
            if (searchId !== searchSeq) return;             // 用户又搜了新的，这次作废
            if (r === 'done') break;
            if (r.progress != null) {
                $('rdSrProg').textContent = Math.round(r.progress * 100) + '%';
                continue;
            }
            if (r.subitems) {
                var sec = (book.sections && book.sections[r.index]) || {};
                var label = r.label
                    || (tocFlat.find(function (x) { return x.href && sec.id && x.href.split('#')[0] === sec.id; }) || {}).label
                    || (T('第 %n 章').replace('%n', r.index + 1));
                r.subitems.forEach(function (s) {
                    results.push({ cfi: s.cfi, excerpt: s.excerpt, label: label });
                });
            } else if (r.cfi) {
                results.push({ cfi: r.cfi, excerpt: r.excerpt, label: '' });
            }
        }
    } catch (e) {
        if (searchId === searchSeq) box.innerHTML = '<div class="rd-empty">' + esc(T('搜索出错：') + (e && e.message ? e.message : e)) + '</div>';
        $('rdSrProg').textContent = '';
        return;
    }
    if (searchId !== searchSeq) return;
    $('rdSrProg').textContent = results.length ? (results.length + ' ' + T('处')) : '';
    renderSearchResults(results, kw);
}

/* ---------- 进度 ---------- */
function updateProgress(d) {
    d = d || {};
    var pct = Math.round((d.fraction || 0) * 100);
    if ($('rdPct')) $('rdPct').textContent = pct + '%';
    if ($('rdBar')) $('rdBar').style.width = pct + '%';
    if (d.tocItem && d.tocItem.href) highlightTOC(d.tocItem.href);
    var label = (d.tocItem && d.tocItem.label) || '';
    if ($('rdChap')) $('rdChap').textContent = label;
    if (d.cfi) save(STORE_POS + bookKey, d.cfi);
}

/* ---------- 侧栏 ---------- */
function setOn(id, on) { var el = $(id); if (el) el.classList.toggle('on', !!on); }
function toggleTOC(on) {
    var s = $('rdToc');
    if (!s) return;
    var v = (on === undefined) ? !s.classList.contains('show') : on;
    s.classList.toggle('show', v);
    setOn('btnToc', v);
}
function toggleSearch(on) {
    var p = $('rdSrPanel');
    if (!p) return;
    var v = (on === undefined) ? !p.classList.contains('show') : on;
    p.classList.toggle('show', v);
    setOn('btnSearch', v);
    if (v) setTimeout(function () { var i = $('rdSrInput'); if (i) i.focus(); }, 30);
}
function showTab(name) {
    Array.prototype.forEach.call(document.querySelectorAll('.rd-tab'), function (el) {
        el.classList.toggle('on', el.getAttribute('data-tab') === name);
    });
    var tl = $('rdTocList'), bl = $('rdBmList'), sp = $('rdSrPanel');
    if (tl) tl.style.display = (name === 'toc') ? '' : 'none';
    if (bl) bl.style.display = (name === 'bm') ? '' : 'none';
    if (sp) sp.classList.toggle('show', name === 'sr');
    setOn('btnSearch', name === 'sr');
    if (name === 'sr') setTimeout(function () { var i = $('rdSrInput'); if (i) i.focus(); }, 30);
}

/* ---------- 打开一本书 ---------- */
async function openBook(path) {
    bookPath = path;
    bookKey = path;
    $('rdLoading').style.display = 'flex';
    $('rdLoadingTxt').textContent = T('正在解析书籍…');
    try {
        book = await makeBook(path);
    } catch (e) {
        $('rdLoadingTxt').textContent = T('打开失败：') + (e && e.message ? e.message : e);
        return;
    }
    bookTitle = (book.metadata && book.metadata.title) || path.split(/[\\/]/).pop().replace(/\.epub$/i, '');
    var au = book.metadata && book.metadata.author;
    if (Array.isArray(au)) au = au.join('、');
    else if (au && typeof au === 'object') au = au.name || '';
    var author = au || '';
    $('rdTitle').textContent = bookTitle;
    $('rdTitle').setAttribute('title', bookTitle);
    $('rdAuthor').textContent = author;
    document.title = bookTitle + ' · EasyPub';

    view = document.createElement('foliate-view');
    view.id = 'rdv';
    $('rdPane').appendChild(view);
    applyPageWidth();                       // ← 挂上 DOM 后立刻按阅读区实际宽度设列宽

    // 书内样式：CSS 换字体 + 抹掉 color/background 的 !important；
    // HTML 抹掉行内样式的 !important 与 font-family（否则主题色压不住，见 cleanInlineStyle）
    try {
        book.transformTarget.addEventListener('data', function (ev) {
            var d = ev.detail;
            if (!d || !d.data) return;
            var isCSS = d.type === 'text/css' || /\.css(\?|$)/.test(d.name || '');
            var isHTML = d.type === 'application/xhtml+xml' || /\.x?html?(\?|$)/.test(d.name || '');
            if (isCSS) {
                var f = FONTS.find(function (x) { return x.id === ui.font; }) || FONTS[0];
                d.data = rewriteBookCSS(d.data, f.family);
            } else if (isHTML && typeof d.data === 'string') {
                d.data = d.data.replace(/(<[^>]*\sstyle\s*=\s*)(["'])([\s\S]*?)\2/gi, function (m, pre, q, inner) {
                    var cleaned = cleanInlineStyle(inner);
                    return cleaned ? pre + q + cleaned + q : '';
                });
            }
        });
    } catch (e) {}

    view.addEventListener('relocate', function (e) { updateProgress(e.detail || {}); });
    // 章节换页 / 每次 load 后都要重新清洗行内样式（分页器会重建 iframe）
    view.addEventListener('load', function (e) {
        try {
            var dv = (e && e.detail && e.detail.doc);
            if (!dv && view.renderer && view.renderer.getContents) {
                var c = view.renderer.getContents()[0];
                dv = c && c.doc;
            }
            if (dv) { rewriteInlineStyles(dv); attachFramePaging(dv); }
        } catch (err) {}
        try { refreshStyle(); } catch (err) {}
    });
    view.addEventListener('error', function (e) {
        console.log('reader view error', e && e.detail);
    });

    await view.open(book);
    view.renderer.setAttribute('flow', ui.flow);

    var last = load(STORE_POS + bookKey, '');
    try {
        await view.init({ lastLocation: last || undefined, showTextStart: !last });
    } catch (e) {
        try { await view.init({ showTextStart: true }); } catch (e2) {}
    }
    refreshStyle();

    tocFlat = [];
    flattenTOC(book.toc, 0, tocFlat);
    renderTOC();
    renderBookmarks();
    $('rdLoading').style.display = 'none';
    // 打开后立刻把当前位置写进目录高亮
    try {
        var d = view.lastLocation;
        if (d) updateProgress(d);
    } catch (e) {}
}

/* ---------- 事件绑定 ---------- */
/* ★ 一定要用 $on 这类带空值保护的绑定：以前每个 addEventListener 都直接写，
   只要有一个 id 拼错 / 元素被删，整条 boot() 就抛异常中断 —— 表现是
   「书打不开、一直停在 正在解析书籍…」，而且看起来毫无报错线索。 */
function $on(id, evt, fn) {
    var el = $(id);
    if (!el) { console.warn('[reader] 缺少元素 #' + id + '，事件未绑定'); return null; }
    el.addEventListener(evt, fn);
    return el;
}
/* 工具条横向滚动：窗口窄的时候右边几个按钮会被推出可视区（用户 2026-09-24 反馈
   「有些功能被挡住了」）。Windows 鼠标滚轮只产生 deltaY，默认不会让 overflow-x
   容器横滚，所以这里手动映射；细滚动条在 reader.html 的 .rd-tools::-webkit-scrollbar。 */
function bindToolsWheel() {
    var bar = document.querySelector('.rd-tools');
    if (!bar) return;
    bar.addEventListener('wheel', function (e) {
        if (bar.scrollWidth <= bar.clientWidth + 1) return;   // 没溢出就不抢滚轮
        var d = Math.abs(e.deltaY) >= Math.abs(e.deltaX) ? e.deltaY : e.deltaX;
        if (!d) return;
        e.preventDefault();
        bar.scrollLeft += d;
    }, { passive: false });
}

function bindUI() {
    $on('btnToc', 'click', function () { toggleTOC(); showTab('toc'); });
    $on('btnBm', 'click', function () { toggleTOC(true); showTab('bm'); });
    $on('btnSearch', 'click', function () { toggleSearch(); });
    $on('rdTocClose', 'click', function () { toggleTOC(false); });
    $on('rdSrClose', 'click', function () { toggleSearch(false); });
    Array.prototype.forEach.call(document.querySelectorAll('.rd-tab'), function (el) {
        el.addEventListener('click', function () { showTab(el.getAttribute('data-tab')); });
    });

    $on('btnPrev', 'click', function () { pagePrev(); });
    $on('btnNext', 'click', function () { pageNext(); });
    $on('rdPane', 'click', paneClick);
    // 父文档里按下的位置（iframe 文档里有自己的一份，见 attachFramePaging）
    window.addEventListener('mousedown', function (e) { markDown(e.clientX, e.clientY); }, true);
    bindToolsWheel();

    $on('btnMinus', 'click', function () {
        ui.size = Math.max(12, ui.size - 1); saveUI(); refreshStyle();
    });
    $on('btnPlus', 'click', function () {
        ui.size = Math.min(36, ui.size + 1); saveUI(); refreshStyle();
    });
    $on('rdLh', 'input', function () {
        ui.lh = +this.value; saveUI(); refreshStyle();
    });

    Array.prototype.forEach.call(document.querySelectorAll('[data-set]'), function (el) {
        el.addEventListener('click', function () {
            var g = el.getAttribute('data-set');
            var v = el.getAttribute('data-val');
            ui[g] = (g === 'size' || g === 'lh') ? Number(v) : v;
            saveUI(); refreshStyle();
        });
    });

    $on('btnAddBm', 'click', function () {
        var d = null;
        try { d = view.lastLocation; } catch (e) {}
        if (!d || !d.cfi) return;
        var list = getBookmarks();
        var label = (d.tocItem && d.tocItem.label) || T('第 %n 处').replace('%n', list.length + 1);
        list.unshift({ cfi: d.cfi, label: label, t: Date.now() });
        saveJSON(STORE_BM + bookKey, list);
        renderBookmarks();
        toggleTOC(true); showTab('bm');
    });

    $on('rdSrGo', 'click', function () { runSearch($('rdSrInput').value.trim()); });
    $on('rdSrInput', 'keydown', function (e) {
        if (e.key === 'Enter') runSearch(this.value.trim());
    });

    $on('btnMin', 'click', function () { window.readerAPI && window.readerAPI.minimize(); });
    $on('btnMax', 'click', function () { window.readerAPI && window.readerAPI.maximize(); });
    $on('btnClose', 'click', function () { window.readerAPI && window.readerAPI.close(); });

    document.addEventListener('keydown', function (e) {
        if (e.target && /input|textarea/i.test(e.target.tagName)) return;
        if (e.ctrlKey || e.altKey || e.metaKey) return;      // 别抢 Ctrl/Alt 组合键
        if (e.key === 'Escape') { toggleSearch(false); toggleTOC(false); }
        if (e.key === 'ArrowLeft') view && view.goLeft();
        if (e.key === 'ArrowRight') view && view.goRight();
        if (e.key === 'PageUp' || (e.key === ' ' && e.shiftKey)) { e.preventDefault(); pagePrev(); return; }
        if (e.key === 'PageDown' || e.key === ' ') { e.preventDefault(); pageNext(); return; }
        if (e.key === '=' || e.key === '+') { ui.size = Math.min(36, ui.size + 1); saveUI(); refreshStyle(); }
        if (e.key === '-') { ui.size = Math.max(12, ui.size - 1); saveUI(); refreshStyle(); }
    });

    // 窗口尺寸变化 → 重新排版
    var rt = null;
    window.addEventListener('resize', function () {
        clearTimeout(rt);
        rt = setTimeout(function () {
            applyPageWidth();
            try { view && view.renderer && view.renderer.expand && view.renderer.expand(); } catch (e) {}
            try { refreshStyle(); } catch (e) {}   // 让分页器按新宽度重建「纸」的背景色
        }, 150);
    });
}

/* ---------- 启动 ---------- */
function boot() {
    var q = new URLSearchParams(location.search);
    var file = q.get('file') || '';
    loadUI();
    document.documentElement.setAttribute('data-theme', ui.theme);
    document.documentElement.setAttribute('data-font', ui.font);
    bindUI();
    saveUI();
    if (window.pageI18n) {
        window.pageI18n.apply();
        window.pageI18n.onChange(function () {
            window.pageI18n.apply();
            renderTOC();
            renderBookmarks();
        });
    }
    if (!file) {
        $('rdLoadingTxt').textContent = T('没有指定书籍文件');
        return;
    }
    openBook(file);
}

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
} else {
    boot();
}
