const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('electronAPI', {
    minimize: () => ipcRenderer.send('window-minimize'),
    maximize: () => ipcRenderer.send('window-maximize'),
    // ★ 2026-09-30 新增：真·全屏（F11 / 双击标题栏）。原来只有 maximize，没有全屏。
    fullscreen: () => ipcRenderer.send('window-fullscreen'),
    close: () => ipcRenderer.send('window-close'),

    selectFile: () => ipcRenderer.invoke('select-file'),
    readFile: (path) => ipcRenderer.invoke('read-file', path),
    fileDrop: (path) => ipcRenderer.invoke('file-drop', path),

    searchAuthor: (title) => ipcRenderer.invoke('search-author', title),
    searchCover: (title, author) => ipcRenderer.invoke('search-cover', title, author),
    // ★ 2026-09-29 novelmeta：一次拿回作者 + 封面 + 候选（多作者弹窗用）
    //   payload: { title, author?, want: 'author+cover'|'cover', sources? }
    metaLookup: (payload) => ipcRenderer.invoke('meta-lookup', payload),
    // ★ 精选推荐（只读）
    recommendList: (payload) => ipcRenderer.invoke('recommend-list', payload),
  coverCache: (payload) => ipcRenderer.invoke('cover-cache', payload),   // ★ 封面本地缓存
    recommendDetail: (payload) => ipcRenderer.invoke('recommend-detail', payload),
    // ★ 哔哩轻小说（零积分：抓公开章节+插图自己组装 EPUB）
    linovelibInfo: (payload) => ipcRenderer.invoke('linovelib-info', payload),
    linovelibGet: (payload) => ipcRenderer.invoke('linovelib-get', payload),
    onLinovelibProgress: (cb) => ipcRenderer.on('linovelib-progress', (e, d) => cb(d)),
    // ★ 2026-10-01：只续 Cookie（走 refresh_cookie.py，约 10 秒，会弹一次 Edge）。
    //   payload: { base? }；失败返回 { ok:false, error }。
    linovelibRefreshCookie: (payload) => ipcRenderer.invoke('linovelib-refresh-cookie', payload || {}),
    // ★★★ 2026-10-02（用户定稿）★★★ **从你自己的浏览器本地读 cookie** —— 一个网络请求都不发，
    //   所以 Cloudflare 拦不到。流程：界面弹窗告诉用户「去浏览器浏览到正文页，然后关掉浏览器」
    //   → 脚本在后台**轮询**读浏览器的 cookie 库（关掉后才读得了）→ 拿到 cf_clearance 就写盘。
    //   payload: { timeout? } 返回 { ok, browser?, count?, cookieFile?, error? }
    linovelibGrabBrowserCookie: (payload) => ipcRenderer.invoke('linovelib-grab-browser-cookie', payload || {}),
    // ★ 2026-10-02：强制结束 Edge/Chrome 进程。Chromium 关窗口后仍留后台进程，
    //   而 cookie 库被它们独占锁着 → 脚本读不到。给用户一个显式按钮（自己点才生效）。
    killBrowsers: () => ipcRenderer.invoke('kill-browsers'),
    // ★★★ 2026-10-02（最终方案）★★★
    //   开一个**应用内的 Electron 浏览器窗口**让用户点到正文页，
    //   然后由 Chromium 自己解密 cookie（`session.cookies.get()`）——
    //   绕开 Selenium 的机器人特征，也绕开 Edge/Chrome 的 App-Bound Encryption（v20，非管理员解不开）。
    linovelibOpenLoginWindow: (payload) => ipcRenderer.invoke('linovelib-open-login-window', payload || {}),
    // ★ 2026-10-02（用户要求）：**取消下载** —— 杀掉正在跑的抓取进程（连子进程树）。
    linovelibCancel: () => ipcRenderer.invoke('linovelib-cancel'),
    // ★ 2026-09-29 se 兜底找封面（番茄签名过期/源没封面时用）
    metaCoverFallback: (payload) => ipcRenderer.invoke('meta-cover-fallback', payload),

    previewChapters: (txtPath, customPattern, maxTitleLength, useAIFilter) =>
        ipcRenderer.invoke('preview-chapters', txtPath, customPattern, maxTitleLength, useAIFilter),

    convertTxtToEpub: (txtPath, options) => ipcRenderer.invoke('convert-txt-to-epub', txtPath, options),

    // 通用：获取任意文件的大小
    statFile: (filePath) => ipcRenderer.invoke('stat-file', filePath),

    path: {
        basename: (p) => require('path').basename(p),
        dirname: (p) => require('path').dirname(p),
        extname: (p) => require('path').extname(p),
        join: (...args) => require('path').join(...args)
    },
    fs: {
        // ★★★ 2026-10-01 修（用户实测截图里文件卡显示「? KB」）★★★
        //   原来直接 `return require('fs').statSync(p)` —— 那是 Node 的 **fs.Stats 类实例**
        //   （带 isFile()/isDirectory() 等方法），**contextBridge 克隆不了这种带方法的对象**，
        //   过桥时直接抛异常 → 渲染层 `catch(e) { sizeKB = '?' }` → 界面就显示「? KB」。
        //   这里改成返回**纯数据对象**，调用方照旧读 `stats.size`，一行都不用改。
        statSync: (p) => {
            const s = require('fs').statSync(p);
            return {
                size: s.size,
                mtimeMs: s.mtimeMs,
                birthtimeMs: s.birthtimeMs,
                isFile: s.isFile(),
                isDirectory: s.isDirectory()
            };
        },
        existsSync: (p) => require('fs').existsSync(p)
    },

    // ====== 下载小说（v2 增量，不动现有 API） ======
    // ★ 2026-10-01：第二参数 opts.pickLightNovel=true → 命中轻小说时不直接下，
    //   主进程回传 {needPick:true, novelId, title, author}，界面弹【轻小说】选卷窗。
    downloadNovel: (bookName, opts) => ipcRenderer.invoke('download-novel', bookName, opts || {}),
    // ★ 2026-09-30 修：这一行原来**被写在下面的 applebooks 对象内部**（缩进看着像同级，
    //   但花括号里就是子孙）→ `window.electronAPI.onDownloadLog` 实际是 undefined：
    //   ① index.html 里 `if (onDownloadLog) {...}` 整块不执行，novel_dl 的进度行永远更新不到小字；
    //   ② 控制台一直刷「preload 缺少 onDownloadLog」。现在提到顶层。
    onDownloadLog: (callback) => ipcRenderer.on('download-log', (event, data) => callback(data)),

    // ====== 苹果图书（v3 增量：百度网盘 + 授权） ======
    applebooks: {
        selectFiles: () => ipcRenderer.invoke('applebooks-select-files'),
        copyFiles: (filePaths) => ipcRenderer.invoke('applebooks-copy-files', filePaths),
        upload: (options) => ipcRenderer.invoke('applebooks-upload', options),
        authStatus: () => ipcRenderer.invoke('applebooks-auth-status'),
        refreshToken: () => ipcRenderer.invoke('applebooks-refresh-token'),
        // openAuth 已废弃（授权改成页面内弹窗，见 index.html 的 #abAuthModal）
        clearCode: () => ipcRenderer.invoke('applebooks-clear-code'),
        openHelp: () => ipcRenderer.invoke('applebooks-open-help'),
        openGuide: () => ipcRenderer.invoke('applebooks-open-guide'),
        startAuth: () => ipcRenderer.invoke('applebooks-start-auth'),
        submitAuthCode: (authCode) => ipcRenderer.invoke('applebooks-submit-auth-code', authCode),
        stopAuth: () => ipcRenderer.invoke('applebooks-stop-auth'),
        listFiles: () => ipcRenderer.invoke('applebooks-list-files'),
        clearDownload: () => ipcRenderer.invoke('applebooks-clear-download'),
        onUploadLog: (callback) => ipcRenderer.on('applebooks-upload-log', (event, data) => callback(data)),
        onAuthLog: (callback) => ipcRenderer.on('applebooks-auth-log', (event, data) => callback(data)),
        onAuthDone: (callback) => ipcRenderer.on('applebooks-auth-done', (event, data) => callback(data)),
        onAuthWindowClosed: (callback) => ipcRenderer.on('applebooks-auth-window-closed', () => callback()),
        onHelpWindowClosed: (callback) => ipcRenderer.on('help-window-closed', () => callback())
    },

    // ====== WiFi 传书（v4 增量） ======
    wifi: {
        openPage: (files) => ipcRenderer.invoke('wifi-open-page', files),
        copyFiles: (filePaths) => ipcRenderer.invoke('wifi-copy-files', filePaths),
        // ★ 主窗口侧也订阅 WiFi 传书日志/结果 —— 原来只发给 wifi 小窗口，
        //   所以「发送」页 WiFi 通道点完之后一个字都看不到。
        onLog: (callback) => ipcRenderer.on('wifi-upload-log', (event, data) => callback(data)),
        onUploadDone: (callback) => ipcRenderer.on('wifi-upload-done', (event, data) => callback(data)),
        onPageClosed: (callback) => ipcRenderer.on('wifi-page-closed', () => callback()),
        // ★ 2026-09-30：WiFi 弹窗需要（原 wifi-page 窗口的 wifiAPI 搬过来）
        startUpload: (url) => ipcRenderer.invoke('wifi-upload', url),
        stopUpload: () => ipcRenderer.invoke('wifi-stop'),
        getUrls: () => ipcRenderer.invoke('wifi-get-urls'),
        getSettings: () => ipcRenderer.invoke('settings-get'),
        setSettings: (patch) => ipcRenderer.invoke('settings-set', patch),
        // ★ 一次性订阅：触发一次后自动移除监听（一键模式用）
        onPageClosedOnce: (callback) => {
            const handler = () => {
                ipcRenderer.removeListener('wifi-page-closed', handler);
                callback();
            };
            ipcRenderer.on('wifi-page-closed', handler);
        }
    },

    // ====== Send to Kindle（Amazon 官方通道，2026-09-30 取代 SMTP 邮箱） ======
    // ★ 主进程请求打开某个页面内弹窗（2026-09-30）
    onOpenModal: (cb) => ipcRenderer.on('ui-open-modal', (e, which) => cb(which)),

    stk: {
        // ★ 2026-09-30 v2：授权改成**页面内弹窗**；登录由主进程驱动弹窗里的 webview
        login: (payload) => ipcRenderer.invoke('stk-login', payload || {}),
        logout: () => ipcRenderer.invoke('stk-logout'),   // ★ 清空登录信息（换账号）
        onState: (callback) => ipcRenderer.on('stk-auth-state', (event, data) => callback(data)),
        status: () => ipcRenderer.invoke('stk-status'),           // 是否已授权
        upload: (filePath) => ipcRenderer.invoke('stk-upload', { file: filePath }),
        uploadBatch: (filePaths) => ipcRenderer.invoke('stk-upload-batch', { files: filePaths }),
        uploadNewest: () => ipcRenderer.invoke('stk-upload-newest'),
        onLog: (callback) => ipcRenderer.on('stk-upload-log', (event, data) => callback(data)),
    },
    // ====== Kindle 传书（SMTP 推送） ======
    // ★ 2026-09-30 重构：Kindle 不再走 SMTP 邮箱 —— 配置窗口/收发信全部删除，只留操作指引。
    kindle: {
        openHelp: () => ipcRenderer.invoke('kindle-open-help'),
        copyFile: (srcPath) => ipcRenderer.invoke('kindle-copy-file', srcPath),
        selectFiles: () => ipcRenderer.invoke('applebooks-select-files'), // 复用苹果图书的选择器
        onPageClosed: (callback) => ipcRenderer.on('kindle-page-closed', () => callback()),
        // 配置窗口的日志（测试连接时回传）
        onConfigLog: (callback) => ipcRenderer.on('kindle-upload-log', (event, data) => callback(data)),
        // 主窗口的发送日志
        onSendLog: (callback) => ipcRenderer.on('kindle-send-log', (event, data) => callback(data)),
        onSendDone: (callback) => ipcRenderer.on('kindle-send-done', (event, data) => callback(data))
    },

    // ====== 传输通道各自的状态检测（三合一传输页） ======
    //   applebooks → 百度授权 / wifi → 本地传书服务+局域网地址 / kindle → SMTP 邮箱配置
    //   ★ 三个通道各查各的，不共用百度网盘那套结论。
    tx: {
        channelStatus: (channel) => ipcRenderer.invoke('tx-channel-status', channel)
    },

    // ====== 书库批量传输：三通道共用的待传清单 ======
    batch: {
        set: (target, files) => ipcRenderer.invoke('batch-set', target, files),
        get: (target) => ipcRenderer.invoke('batch-get', target),
        clear: () => ipcRenderer.invoke('batch-clear'),
        // ★ 2026-09-26：开始一条新任务 —— 主进程会清空待传清单 + 整体重建 sendqueue\
        newTask: (reason) => ipcRenderer.invoke('batch-new-task', reason),
        // 主进程确认重置完成后广播过来，界面收到就把队列清空
        onTaskReset: (callback) => ipcRenderer.on('transfer-task-reset', (event, data) => callback(data))
    },

    // ====== 全局设置（落盘到 exe 同级的 settings.json） ======
    settings: {
        get: () => ipcRenderer.invoke('settings-get'),
        set: (patch) => ipcRenderer.invoke('settings-set', patch),
        // 弹窗页（授权 / WiFi / Kindle / 指引）里切了语言时，主进程会广播过来，
        // 主窗口必须跟着换，否则两边的语言会不一致
        onLanguageChange: (callback) => ipcRenderer.on('apply-language', (event, lang) => callback(lang))
    },

    pickDirectory: () => ipcRenderer.invoke('pick-directory'),

    // ====== ★ 2026-09-29：DeepSeek API key 通道已删除 ======
    //   「搜作者 / 搜封面」改由 novelmeta（meta-lookup）完成，不需要任何 key。
    //   不许恢复 apiKey.get/set —— 主进程那侧对应的 ipcMain.handle 也已删除。

    // ====== 书库（扫描 export 目录 / 改元数据 / 删除） ======
    library: {
        scan: () => ipcRenderer.invoke('library-scan'),
        update: (payload) => ipcRenderer.invoke('library-update', payload),
        remove: (paths) => ipcRenderer.invoke('library-delete', paths),
        reveal: (target) => ipcRenderer.invoke('library-reveal', target),
        exportFiles: (paths) => ipcRenderer.invoke('library-export', paths),
        getDir: () => ipcRenderer.invoke('library-dir'),
        importBooks: () => ipcRenderer.invoke('library-import'),
        // 双击书本 → 用内置阅读窗口打开
        openReader: (filePath) => ipcRenderer.invoke('reader-open', filePath)
    }
});