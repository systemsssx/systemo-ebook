const { app, BrowserWindow, ipcMain, dialog, shell, screen, net, session } = require('electron');
const path = require('path');
const fs = require('fs');
const os = require('os');
const crypto = require('crypto');   // ★ 2026-09-27：书库内容去重（sha256 指纹）
const { exec, execSync, execFile } = require('child_process');

// ================================================================
// ★★★ 最优先：让所有日志写不出去也不能把主进程搞崩 ★★★
// ----------------------------------------------------------------
// 背景（2026-09-22 深夜踩到的真坑，crash.log 里刷了 15 条）：
//   主进程的 stdout/stderr 一旦变成**断掉的管道**（父进程/终端被关掉、
//   管道读端被回收、被 IDE 接管后断开……），任何一句 console.log 都会
//   抛 `Error: EPIPE: broken pipe, write`。
//   而它是**未捕获异常** → 触发全局兜底 → 兜底里第一行又是 console.error
//   → 再抛一次 → **自击循环**，用户看到的就是一个接一个的
//   「EasyPub 出错了 / uncaughtException / EPIPE」弹窗，关一个弹一个。
//
//   crash.log 里的原始堆栈（真正的第一条，不是兜底循环的那条）：
//     at console.log (node:internal/console/constructor:380:26)
//     at C:\work\epubui\EasyPub-New\main.js:2177:37      ← 书库后端 stderr
//     at ChildProcess.exithandler (node:internal/child_process:411:7)
//     at C:\work\epubui\EasyPub-New\main.js:2201:33      ← 「找到 N 项」
//     at async WebContents.<anonymous>
//
//   也就是说：只是"打印一行日志"失败而已，跟业务逻辑毫无关系，
//   却能把整个应用搞成弹窗地狱。日志写不出去必须**静默降级**。
// ================================================================
(function initStdioGuards() {
    const swallow = (stream, name) => {
        if (!stream || typeof stream.on !== 'function') return;
        stream.on('error', (e) => {
            if (e && (e.code === 'EPIPE' || e.code === 'ERR_STREAM_DESTROYED')) {
                stream.__epipeDead = true; // 记下来，后面直接跳过写，不再靠异常兜
                return;                    // 静默吞掉：日志没了可以接受，崩掉不行
            }
            // 其它流错误也不能让主进程挂
            try { if (!stream.destroyed) stream.write(`[stdio ${name} error] ${e && e.message}\n`); } catch (_) {}
        });
    };
    swallow(process.stdout, 'stdout');
    swallow(process.stderr, 'stderr');

    // 双保险：即使 stream 错误没冒出来，写日志本身也不允许抛
    const guard = (fn) => function (...args) {
        try { if (!this || !this.__epipeDead) fn.apply(console, args); } catch (e) { /* 日志失败一律忽略 */ }
    };
    console.log = guard(console.log);
    console.info = guard(console.info || console.log);
    console.warn = guard(console.warn);
    console.error = guard(console.error);
})();

// ================================================================
// ★ 全局异常兜底
// ----------------------------------------------------------------
// 没有这层兜底时，主进程里任何未捕获异常/未处理 Promise 都会**静默退出**，
// 用户看到的就是"窗口一闪就没了、什么提示都没有"。
// 这里把错误落到 crash.log 并弹窗，便于定位（crash.log 就在本文件同级）。
//
// ⚠️ 兜底自身必须"绝对不能再抛"（否则就是上面说的自击循环）：
//    写日志包 try、弹窗包 try，并且**限流**——同一秒里炸 100 次也只弹一次。
//    EPIPE 这类"基础设施故障"直接记日志、不弹窗，因为它没有可操作性，
//    弹出来只是骚扰（用户唯一能做的就是关掉它，然后它再弹）。
// ================================================================
// ★★★ 2026-09-30（同类 bug：打包后路径假设失效）：
//   崩溃日志原来写 `path.join(__dirname, 'crash.log')` —— 打包后 `__dirname` 是
//   **app.asar 内部的虚拟路径（只读）**，而且两处写入都包着 try/catch ⇒ **静默失败**，
//   等于"崩溃日志根本不存在"（真出问题时最需要它）。
//   与 download/export/logs 统一口径：一律写 **EasyPub.exe 同级目录**（开发时=项目根）。
const RUN_ROOT = app.isPackaged ? path.dirname(process.execPath) : __dirname;
const CRASH_LOG = path.join(RUN_ROOT, 'crash.log');
let lastFatalKey = '';
let lastFatalAt = 0;
function reportFatal(where, err) {
    const msg = (err && err.message) || String(err);
    const line = `\n[${new Date().toISOString()}] ${where}\n${(err && err.stack) || err}\n`;
    try { console.error(line); } catch (e) { /* 日志写不出去就算了 */ }
    try { fs.appendFileSync(CRASH_LOG, line, 'utf-8'); } catch (e) { /* 磁盘不可写就算了 */ }

    // 基础设施类故障（管道断了 / 流被销毁 / 窗口早关了）：只记日志，不打扰用户
    if (/EPIPE|broken pipe|ERR_STREAM_DESTROYED|Object has been destroyed/i.test(msg)) return;

    // 同一类错误 5 秒内只弹一次，避免"关一个弹一个"
    const key = where + '|' + msg;
    const now = Date.now();
    if (key === lastFatalKey && now - lastFatalAt < 5000) return;
    lastFatalKey = key;
    lastFatalAt = now;

    try {
        const text = `${where}\n\n${msg}\n\n详情见：${CRASH_LOG}`;
        if (app && app.isReady && app.isReady()) {
            dialog.showErrorBox('EasyPub 出错了', text);
        } else if (app) {
            app.once('ready', () => {
                try { dialog.showErrorBox('EasyPub 启动失败', text); } catch (e) {}
            });
        }
    } catch (e) { /* 弹窗失败也不能再抛 */ }
}
process.on('uncaughtException', (err) => { try { reportFatal('uncaughtException', err); } catch (e) {} });
process.on('unhandledRejection', (reason) => { try { reportFatal('unhandledRejection', reason); } catch (e) {} });


// ================================================================
// ★★★ 统一的"中转站" download 目录（EXE 同级，直接可写）★★★
// ================================================================
// 完全用 __dirname 同级目录，砍掉 userData/AppData 那套虚拟路径
//   开发模式：E:\work\epubui\EasyPub-New\download\
//   打包模式：dist\win-unpacked\download\
//   用户可读可写，文件路径和 exe 同级，Python 能直接访问
//
// 关键：打包后 __dirname 指向 app.asar 内部虚拟路径（不可写！）
// 必须用 process.execPath 拿到真正的 EasyPub.exe 所在目录（win-unpacked/）
//
// ★ 防御：若 Electron 没以正常模式启动（例如环境变量 ELECTRON_RUN_AS_NODE=1
//   让它退化成纯 Node），require('electron') 返回的是**路径字符串**而不是
//   app 对象，此时 app.isPackaged 会对 undefined 取属性，报出一句
//   "Cannot read properties of undefined (reading 'isPackaged')" —— 完全看不出
//   真实原因。这里提前判断并给出可操作的提示。
if (!app || typeof app.isPackaged !== 'boolean') {
    console.error('');
    console.error('  ============================================================');
    console.error('  EasyPub 启动失败：Electron 没有在正常模式下运行。');
    console.error('  ============================================================');
    console.error('  最常见原因：环境变量 ELECTRON_RUN_AS_NODE 被设置了。');
    console.error('  它会让 Electron 退化成纯 Node，拿不到 app 对象。');
    console.error('');
    console.error('  解决办法（cmd 里执行，注意引号不能省）：');
    console.error('      set "ELECTRON_RUN_AS_NODE="');
    console.error('      npm start');
    console.error('');
    console.error('  ★ 引号很重要：写成  set ELECTRON_RUN_AS_NODE= && npm start');
    console.error('    会让变量变成"一个空格"而不是被删除，等于没清掉。');
    console.error('');
    try {
        fs.appendFileSync(
            path.join(RUN_ROOT, 'crash.log'),
            `\n[${new Date().toISOString()}] ELECTRON_RUN_AS_NODE 导致启动失败\n`,
            'utf-8');
    } catch (e) { /* 忽略 */ }
    process.exit(1);
}

// ★ 2026-09-30：APP_ROOT 与上面的 RUN_ROOT 是同一个东西（打包=exe 同级、开发=项目根），
//   统一成一个常量，避免以后两处改歪。
const APP_ROOT = RUN_ROOT;
const DOWNLOAD_DIR = path.join(APP_ROOT, 'download');
try { fs.mkdirSync(DOWNLOAD_DIR, { recursive: true }); } catch (e) {}
console.log(`[download dir] ${DOWNLOAD_DIR}`);

// ================================================================
// ★★★ 待传目录 sendqueue（2026-09-23） ★★★
// ================================================================
// 用户原话：「能不能和lim一样不要一个文件一个文件传，而是直接同步整个文件夹」。
// 看过 lim\upload_folder_cmd.py 与 lim\main.js 之后的结论：lim 并没有更快的协议，
// 它的做法是「固定的持久目录 + copy 进去 + bypy syncup 整个目录」——
// 云端已有的书（同名同大小）bypy 直接判 SAME，一个字节都不传；
// 只有新书才真正走网络（bypy 单文件 = 一次整文件 POST ≈116 KB/s，
// 这是百度对第三方 app key 的单连接限速，换 lim 也一样慢）。
// 所以这里照 lim 的思路：固定的 sendqueue\。
// ★ 2026-09-26 更正：下面这句「传过的书不删，下次增量跳过」**已作废** ——
//   现在的口径是「每次任务开始整体重置 + 每次发送前只清本通道」（见 beginNewTask）。
const SENDQUEUE_DIR = path.join(APP_ROOT, 'sendqueue');
try { fs.mkdirSync(SENDQUEUE_DIR, { recursive: true }); } catch (e) {}
console.log(`[sendqueue dir] ${SENDQUEUE_DIR}`);

// 正在跑的上传子进程（退出时要带走，否则 bypy 会被留成孤儿进程）
let activeUploadChild = null;

// 清理旧版本留下的一次性上传临时目录（%TEMP%\easypub-upload-*）。
// 旧实现每次上传都 mkdtemp 一个，进程被杀时从不清理，实测累积了 8 个。
try {
    const tmp = os.tmpdir();
    let n = 0;
    for (const f of fs.readdirSync(tmp)) {
        if (!/^easypub-upload-/.test(f)) continue;
        try { fs.rmSync(path.join(tmp, f), { recursive: true, force: true }); n++; } catch (_) {}
    }
    if (n) console.log(`🧹 清理遗留的上传临时目录：${n} 个`);
} catch (_) {}

// ================================================================
// ★★★ 上传日志落盘（2026-09-23 用户要求） ★★★
// ================================================================
// 用户原话：「顺便你后台可以写日志，你后台自己看哪有问题」。
// 以前上传出问题只能靠界面现象口头复述，bypy 的每一行输出都随窗口一起消失。
// 现在把上传链路的完整证据写进 APP_ROOT\logs\upload-YYYY-MM-DD.log：
//   START   上传开始（参数、文件数、待传路径）
//   OUT     bypy stdout 原文（每个数据块）
//   ERR     bypy stderr 原文（每个数据块）
//   RESULT  结论（退出码/错误/输出尾部/耗时）
//   ABORT   上传前就中止（本地目录为空等）
//   QUIT    应用退出（用来确认「上传还没返回，窗口就被关了」）
const LOG_DIR = path.join(APP_ROOT, 'logs');
function uploadLogPath() {
    const d = new Date();
    const p2 = (n) => String(n).padStart(2, '0');
    return path.join(LOG_DIR, 'upload-' + d.getFullYear() + '-' + p2(d.getMonth() + 1) + '-' + p2(d.getDate()) + '.log');
}
function logUpload(tag, text) {
    try {
        fs.mkdirSync(LOG_DIR, { recursive: true });
        fs.appendFileSync(uploadLogPath(),
            '[' + new Date().toISOString() + '] ' + tag + ' ' + String(text == null ? '' : text) + '\n');
    } catch (_) {}
}

// ================================================================
// ★★★ 统一暂存：三条通道都「先复制到临时文件夹、再对临时文件夹传」（2026-09-25 用户要求）★★★
// ================================================================
// 用户原话：「把所有传输的逻辑都改成『先复制到临时文件夹、再对临时文件夹传输』」，
// 并明确「三种路径（苹果/百度、WiFi、Kindle）都是」。
//
// 以前三条通道各拿各的目录：队列走 sendqueue\，一键/旧流程把 download\ 直接交给
// 传输端，WiFi 甚至让脚本自己 os.listdir(download\)。两个真实的坑：
//   1. 日志里看不出到底传的是哪个目录（"上传失败"时无法判断是不是源目录不对）；
//   2. download\ 里混着 .covers\ 子目录，而 upload_folder_cmd.py 的 _iter_local_files
//      是递归 os.walk → 封面图片也被当书传上网盘。
// 现在统一：三条通道都先把「本次要传的书」复制进暂存目录（每次清空），传输端只看它。
// 日志里的 STAGE 行同时记「原来的来源文件」和「实际暂存的目录 + 文件清单」，
// 所以即使这次失败本来就是源目录造成的，日志也拿得出证据。
//
// 暂存目录分配：苹果/百度用 sendqueue\ 根目录（远端目录是**平铺**的，
// remoteDir + 相对路径 → 带上子目录会把书传到网盘的子文件夹里）；
// WiFi / Kindle 只吃绝对路径，不关心目录名，所以用子目录，互不干扰。
const STAGE_SUBDIRS = { wifi: '_wifi', kindle: '_kindle' };

function stageDirFor(channel) {
    const sub = STAGE_SUBDIRS[channel];
    return sub ? path.join(SENDQUEUE_DIR, sub) : SENDQUEUE_DIR;
}

function wipeStageDir(dir, channel) {
    // 只清这个通道自己的暂存内容。苹果/百度用根目录，所以要跳过另外两个通道的子目录。
    let entries = [];
    try { entries = fs.readdirSync(dir); } catch (e) { entries = []; }
    for (const name of entries) {
        if (!STAGE_SUBDIRS[channel] && (name === '_wifi' || name === '_kindle')) continue;
        try { fs.rmSync(path.join(dir, name), { recursive: true, force: true }); } catch (e) {}
    }
    fs.mkdirSync(dir, { recursive: true });
}

// 一键/旧流程没有显式清单时的来源：download\ 顶层的书（不递归，避免带上 .covers\）
function listDownloadBooks() {
    try {
        return fs.readdirSync(DOWNLOAD_DIR)
            .filter((f) => /\.(epub|mobi|azw3|pdf|txt)$/i.test(f))
            .map((f) => path.join(DOWNLOAD_DIR, f))
            .filter((p) => { try { return fs.statSync(p).isFile(); } catch (e) { return false; } });
    } catch (e) {
        return [];
    }
}

// ★ 2026-09-26：按「真实路径」给待传清单去重。
//   以前只按文件名去重，于是同一本书以 download\X.epub 和 export\X.epub 两个路径
//   同时进清单时会被当成两本，暂存目录里就出现 X.epub + X_1.epub（内容一模一样）——
//   这正是用户看到的「传输文件夹里两个重复 epub」。
function dedupeByRealPath(list) {
    const seen = new Set();
    const out = [];
    for (const p of list) {
        let key;
        try { key = fs.realpathSync(p); } catch (e) { key = path.resolve(p); }
        key = key.toLowerCase();
        if (seen.has(key)) continue;
        seen.add(key);
        out.push(p);
    }
    return out;
}

// 把要传的文件复制进暂存目录。返回 { ok, dir, files, reason, srcCount }
function stageTransferFiles(srcPaths, channel) {
    const given = (Array.isArray(srcPaths) ? srcPaths : []).filter((p) => typeof p === 'string' && p);
    const isFile = (p) => { try { return fs.statSync(p).isFile(); } catch (e) { return false; } };
    const existing = given.filter(isFile);
    const unique = dedupeByRealPath(existing);
    let sources = unique;
    let reason = 'explicit-paths';
    if (!sources.length) {
        sources = dedupeByRealPath(listDownloadBooks());
        reason = given.length ? 'explicit-paths-missing→download' : 'download-fallback';
    }
    // ★ 2026-09-27：暂存目录里**只放 epub**（用户要求）。
    //   一键流程的 download\ 里除了 epub 还躺着一份 .txt，以前两个都会被复制进
    //   sendqueue\ 一起上传 —— 现在非 epub 一律不进暂存目录。
    const isEpub = (p) => /\.epub$/i.test(p);
    const nonEpubIgnored = sources.filter((p) => !isEpub(p)).map((p) => path.basename(p));
    if (nonEpubIgnored.length) {
        sources = sources.filter(isEpub);
        console.log('⏭️ 暂存只收 epub，已忽略:', nonEpubIgnored.join('、'));
    }
    const dir = stageDirFor(channel);
    const busy = {
        applebooks: !!activeUploadChild,
        wifi: !!currentWifiProcess,
        kindle: !!currentKindleProcess,
    }[channel];
    const meta = {
        channel,
        reason,
        srcDir: sources.length ? path.dirname(sources[0]) : null,
        srcCount: sources.length,
        dupDropped: existing.length - unique.length,
        stageDir: dir,
        // ★ 专门用来钉「一键里点的是苹果、最后却发 Kindle」：处理器叫谁 + 队列属于谁。
        pendingBatchTarget: pendingBatch ? pendingBatch.target : null,
        pendingBatchCount: pendingBatch && pendingBatch.files ? pendingBatch.files.length : 0,
        concurrentTransferRunning: !!busy,
        files: [],
        sources,
        missing: given.filter((p) => !isFile(p)),
        nonEpubIgnored,   // ★ 2026-09-27：被挡在暂存目录外的非 epub 文件
    };
    if (!sources.length) {
        const why = nonEpubIgnored.length
            ? '只支持传输 EPUB 文件（已忽略 ' + nonEpubIgnored.length + ' 个非 epub：' + nonEpubIgnored.join('、') + '）'
            : '没有找到要传输的文件';
        logUpload('STAGE', JSON.stringify(Object.assign({ ok: false, why }, meta)));
        return { ok: false, dir, files: [], reason, srcCount: 0, error: why };
    }
    try {
        wipeStageDir(dir, channel);
        const used = new Set();
        for (const src of sources) {
            const ext = path.extname(src);
            const base = path.basename(src, ext);
            let name = path.basename(src);
            let n = 1;
            while (used.has(name.toLowerCase())) { name = `${base}_${n}${ext}`; n++; }
            used.add(name.toLowerCase());
            const dst = path.join(dir, name);
            fs.copyFileSync(src, dst);
            let size = null;
            try { size = fs.statSync(dst).size; } catch (e) {}
            meta.files.push({ name, size, from: src });
        }
    } catch (e) {
        logUpload('STAGE', JSON.stringify(Object.assign({ ok: false, why: '复制失败: ' + e.message }, meta)));
        return { ok: false, dir, files: [], reason, srcCount: sources.length, error: '准备待传文件失败：' + e.message };
    }
    const out = meta.files.map((f) => path.join(dir, f.name));
    logUpload('STAGE', JSON.stringify(Object.assign({ ok: true }, meta)));
    console.log('🗂️ 统一暂存:', channel, reason, '→', dir, out.length, '个文件');
    return { ok: true, dir, files: out, reason, srcCount: sources.length };
}

// ================================================================
// ★★★ 统一解析 Python 脚本路径（兼容开发模式 + 打包后） ★★★
// ================================================================
// 打包后 .py 文件在 resources/app.asar.unpacked/（因 asarUnpack 规则）
// 开发模式：APP_ROOT 就是项目根
function getPythonScript(name) {
    if (app.isPackaged) {
        return path.join(process.resourcesPath, 'app.asar.unpacked', name);
    }
    return path.join(APP_ROOT, name);
}

// ================================================================
// ★★★ 随包分发的 Python 后端（PyInstaller 冻结产物，无需用户装 Python）★★★
// ================================================================
// 打包后路径：resources/backend/easypub-backend.exe
// （由 package.json 的 extraResources 从 dist-backend/easypub-backend 拷进去）
// 开发环境默认不用它，除非显式设置 EASYPUB_BACKEND 指过去。
function findBundledBackend() {
    const candidates = [];
    if (process.env.EASYPUB_BACKEND) {
        candidates.push(process.env.EASYPUB_BACKEND);
    }
    if (app.isPackaged) {
        candidates.push(path.join(process.resourcesPath, 'backend', 'easypub-backend.exe'));
    }
    for (const p of candidates) {
        if (p && fs.existsSync(p)) {
            console.log(`📦 使用随包 Python 后端: ${p}`);
            return p;
        }
    }
    return null;
}

// ================================================================
// ★★★ 自动查找 Python（支持任意用户名/路径，不再写死） ★★★
// ================================================================
function findPython() {
    // 优先级 0: 随包分发的后端（打包后默认走这里，用户无需装 Python）
    const bundled = findBundledBackend();
    if (bundled) {
        return bundled;
    }
    // 优先级 1: 环境变量 PYTHON_PATH（用户可手动指定）
    if (process.env.PYTHON_PATH && fs.existsSync(process.env.PYTHON_PATH)) {
        return process.env.PYTHON_PATH;
    }
    // 优先级 2: 常见安装路径（覆盖大多数用户）
    // ★ 2026-09-30：原来这里写死了 `C:\Users\<用户名>\AppData\Local\Programs\Python\…`
    //   —— 既泄露开发者用户名，在别人机器上也必然找不到。改成按 LOCALAPPDATA 动态拼。
    const _pyLocal = process.env.LOCALAPPDATA || path.join(os.homedir(), 'AppData', 'Local');
    const candidates = [
        path.join(_pyLocal, 'Programs', 'Python', 'Python314', 'python.exe'),
        path.join(_pyLocal, 'Programs', 'Python', 'Python313', 'python.exe'),
        path.join(_pyLocal, 'Programs', 'Python', 'Python312', 'python.exe'),
        'C:\\Python314\\python.exe',
        'C:\\Python313\\python.exe',
        'C:\\Python312\\python.exe',
        'C:\\Python311\\python.exe',
        'C:\\Program Files\\Python314\\python.exe',
        'C:\\Program Files\\Python313\\python.exe',
        'C:\\Program Files\\Python312\\python.exe',
        'C:\\Program Files (x86)\\Python314\\python.exe',
        'C:\\Program Files (x86)\\Python313\\python.exe',
        'C:\\Program Files (x86)\\Python312\\python.exe',
    ];
    // 优先级 3: PATH 环境变量里的 python / python3
    try {
        const stdout = execSync('where python 2>nul', { encoding: 'utf-8', timeout: 3000 });
        const pyPaths = stdout.split('\n').map(s => s.trim()).filter(Boolean);
        candidates.push(...pyPaths);
    } catch (e) {}
    try {
        const stdout = execSync('where python3 2>nul', { encoding: 'utf-8', timeout: 3000 });
        const pyPaths = stdout.split('\n').map(s => s.trim()).filter(Boolean);
        candidates.push(...pyPaths);
    } catch (e) {}

    for (const p of candidates) {
        if (p && fs.existsSync(p)) {
            console.log(`🐍 自动找到 Python: ${p}`);
            return p;
        }
    }
    // 找不到，返回默认（最终由 fs.existsSync 校验）
    return 'python';
}

// ================================================================
// ★★★ 自动导出工具（独立模块，不参与软件运行，永不清理） ★★★
// 用途：每次下载的 txt / 转换生成的 epub 自动副本到导出目录
// 特点：
//   - 导出目录放在 EXE 同级（用户可读可写，和打包目录在一起）
//   - 路径：dist\win-unpacked\export\  或  E:\work\epubui\EasyPub-New\export\
//   - 永不清空（和软件内 download/ 的清理逻辑完全隔离）
//   - 不影响软件的任何流程（只是异步复制）
//   - 失败也不抛出，只在 stderr 打日志
// ================================================================
const EXPORT_DIR = path.join(APP_ROOT, 'export');

// ================================================================
// ★★★ 全局设置：落盘到 exe 同级的 settings.json ★★★
// ================================================================
// 为什么不用 localStorage：
//   · 它属于 Electron 的 userData 目录，跟软件本体分离，用户重装就丢
//   · 主进程（这里）读不到，Python 后端也读不到
// 放在 APP_ROOT（exe 同级）跟现有的 kindle_config.json 保持一致。
const SETTINGS_PATH = path.join(APP_ROOT, 'settings.json');

const SETTINGS_DEFAULTS = {
    language: 'zh',                      // 界面语言 zh | en
    outputDir: '',                       // 成品输出目录；空 = 用默认的 export
    coverMagnify: 4,                     // 封面超分倍数；0 = 不超分
    maxTitleLength: 25,                  // 章节标题最长字数
    wifiDelay: 2.5,                      // WiFi 传书：每本之后等几秒再刷新页面
    kindleDelay: 1.5,                    // Kindle 推送：每本之间的间隔（秒）
    baiduRemoteDir: '/epub_download_backup',
    baiduOnDup: 'overwrite',             // overwrite | skip | prompt
    // ★ 2026-10-01：轻小说源（哔哩轻小说 → linovelib.py）的两个开关。
    //   ★ 注意：这两个值**只是给"下载小说"那条兜底路径**用的（novel_dl.py → linovelib.py）；
    //     【轻小说】弹窗那条路走的是 linovelibGet，开关由弹窗里的勾选框当场给（见 index.html）。
    linovelibAutoCookie: true,           // cf_clearance 过期自动续期（弹一次 Edge，约 10 秒）
    linovelibBase: ''                    // 站点域名；空 = 脚本默认 https://www.linovelib.com
    // ★ 2026-09-29：apiKey 字段已删除（DeepSeek 全套下线，改由 novelmeta 直连）
};

let SETTINGS = Object.assign({}, SETTINGS_DEFAULTS);
// 上次保存设置的错误（装机目录无写权限时会上报给渲染层提示用户）
let lastSettingsError = null;

function loadSettings() {
    try {
        if (fs.existsSync(SETTINGS_PATH)) {
            const raw = JSON.parse(fs.readFileSync(SETTINGS_PATH, 'utf-8'));
            SETTINGS = Object.assign({}, SETTINGS_DEFAULTS, raw);
            console.log('⚙️ 已读取 settings.json');
        } else {
            console.log('⚙️ 没有 settings.json，使用默认设置');
        }
    } catch (e) {
        console.warn('⚠️ settings.json 读取失败，回退默认值:', e.message);
        SETTINGS = Object.assign({}, SETTINGS_DEFAULTS);
    }
    return SETTINGS;
}

function saveSettings(patch) {
    SETTINGS = Object.assign({}, SETTINGS, patch || {});
    try {
        // ★ 修复：原来直接 writeFileSync 覆盖，进程在写入中途被杀（关机/任务管理器结束）
        //   会留下半个 JSON，用户所有设置静默丢失。
        //   改为「写临时文件 → rename 原子替换」，rename 在同一分区是原子的。
        const tmp = SETTINGS_PATH + '.tmp';
        fs.writeFileSync(tmp, JSON.stringify(SETTINGS, null, 2), 'utf-8');
        fs.renameSync(tmp, SETTINGS_PATH);
        console.log('⚙️ 设置已保存');
    } catch (e) {
        console.warn('⚠️ settings.json 写入失败:', e.message);
        // 装机目录无写权限（如 C:\Program Files）时是典型场景，让上层能提示用户
        lastSettingsError = e.message;
    }
    return SETTINGS;
}

// 成品输出目录：设置里填了就用它，否则用默认的 export
function getExportDir() {
    const d = (SETTINGS.outputDir || '').trim();
    return d || EXPORT_DIR;
}

// ★ 安全修复：路径白名单。
//   本项目有若干 IPC 通道直接把渲染层传来的路径交给 fs 使用（读文件、删文件、
//   改 epub 元数据）。渲染层一旦被注入（例如恶意 TXT 的章节标题），这些通道
//   就是任意文件读取/删除。这里统一限制在「书库导出目录 + 下载中转目录」之内。
function isPathInside(parent, child) {
    try {
        const rel = path.relative(path.resolve(parent), path.resolve(child));
        // 空字符串 = 就是该目录本身；不以 .. 开头且不是绝对路径 = 确实在里面
        return rel === '' || (!rel.startsWith('..') && !path.isAbsolute(rel));
    } catch (e) {
        return false;
    }
}

function assertPathAllowed(target, what) {
    const p = String(target || '').trim();
    if (!p) throw new Error(`${what}：路径为空`);
    const allowed = [getExportDir(), DOWNLOAD_DIR];
    if (allowed.some(dir => isPathInside(dir, p))) return p;
    throw new Error(`${what}：拒绝访问书库与下载目录之外的路径 —— ${p}`);
}

loadSettings();

// 设置读写 IPC
ipcMain.handle('settings-get', async () => {
    return { success: true, settings: SETTINGS, path: SETTINGS_PATH };
});

ipcMain.handle('settings-set', async (event, patch) => {
    // ★ 修复：原来无论写入成功与否都回 success:true，用户以为保存成功、实际每次失败
    //   （典型场景：程序装在 C:\Program Files，普通用户无写权限）。
    lastSettingsError = null;
    const next = saveSettings(patch);
    if (lastSettingsError) {
        return { success: false, error: lastSettingsError, settings: next, path: SETTINGS_PATH };
    }
    // ★ 界面语言改动要广播给所有窗口：主窗口切到中文/英文后，
    //   已经打开的授权页 / WiFi 页 / Kindle 页 / 指引页要同步跟随，
    //   否则它们会停在旧语言（这些弹窗页是独立文档，拿不到主窗口的 i18n 状态）。
    if (patch && patch.language != null) {
        const lang = next && next.language ? next.language : patch.language;
        try {
            BrowserWindow.getAllWindows().forEach((w) => {
                try {
                    if (w && !w.isDestroyed() && w.webContents && !w.webContents.isDestroyed()) {
                        w.webContents.send('apply-language', lang);
                    }
                } catch (e) {}
            });
        } catch (e) {}
    }
    return { success: true, settings: next, path: SETTINGS_PATH };
});

try { fs.mkdirSync(getExportDir(), { recursive: true }); } catch (e) {}
console.log(`[export dir] ${EXPORT_DIR}`);
// ★ 2026-09-27：启动时把书库里内容重复的副本清一次（同一本书只留一本）
try { pruneDuplicateExports(); } catch (e) { console.warn('⚠️ 书库去重跳过:', e.message); }

// ================================================================
// ★ 2026-09-29：DeepSeek API key 全套已按用户要求删除。
// ----------------------------------------------------------------
// 原来这里放着 getApiKey() / buildPythonEnv() 里的 DEEPSEEK_API_KEY 注入 /
// api-key-get / api-key-set —— 供 ai_author.py 与 ai_cover.py 走 DeepSeek。
// 现在「搜作者 / 搜封面」由 novelmeta（meta_lookup.py）直连公开接口完成，
// 不需要任何 key；ai_cover.py 只保留 Selenium 兜底搜封面这一件事。
// ★ 不许恢复：不要重新引入 key 输入框、授权弹窗或 key 注入。
// ================================================================
function buildPythonEnv(extra) {
    return Object.assign({}, process.env, extra || {});
}


/**
 * 自动导出单个文件到 EXPORT_DIR
 * - 同名文件：内容相同则跳过，不同则覆盖（不再产生 _1/_2 副本）
 * - 复制失败绝不抛出（静默返回 false）
 */
function exportFileToExportDir(srcPath, options = {}) {
    try {
        if (!srcPath || !fs.existsSync(srcPath)) return false;

        // ★ 确保导出目录存在（不参与软件任何清理逻辑）
        if (!fs.existsSync(getExportDir())) {
            fs.mkdirSync(getExportDir(), { recursive: true });
            console.log(`📦 创建导出目录：${getExportDir()}`);
        }

        const fileName = path.basename(srcPath);
        const targetPath = path.join(getExportDir(), fileName);
        const label = options.label || '文件';

        // ★★★ 同名文件：内容一样就跳过，否则覆盖（不再产生 _1/_2 副本）
        if (fs.existsSync(targetPath)) {
            if (sameFileContent(srcPath, targetPath)) {
                console.log(`📦 ${label}内容未变，跳过：${targetPath}`);
                return true;
            }
            console.log(`📦 ${label}同名已存在，覆盖：${targetPath}`);
        }

        // ★ 先写临时文件再改名：中途失败也不会在导出目录里留下半个文件
        const tmpPath = targetPath + '.tmp';
        try {
            fs.copyFileSync(srcPath, tmpPath);
            fs.renameSync(tmpPath, targetPath);
        } catch (e) {
            try { fs.unlinkSync(tmpPath); } catch (e2) {}
            throw e;
        }
        console.log(`📦 ${label}已导出：${targetPath}`);
        // ★ 2026-09-27：同一本书（内容逐字节相同）在书库里只留一本
        pruneDuplicatesOf(targetPath);
        return true;
    } catch (e) {
        // ★ 失败绝不抛出，不影响主流程
        console.warn(`⚠️ 导出失败（已忽略）：${e.message}`);
        return false;
    }
}

// ★ 2026-09-26：判断两个文件内容是否相同。
//   先比大小快速排除（9MB 的书不用白读两遍），大小一样再逐字节比。
//   给「同名覆盖」当短路条件用：内容没变就不必重新复制。
function sameFileContent(a, b) {
    try {
        const sa = fs.statSync(a);
        const sb = fs.statSync(b);
        if (sa.size !== sb.size) return false;
        return fs.readFileSync(a).equals(fs.readFileSync(b));
    } catch (e) {
        return false;
    }
}

// ================================================================
// ★★★ 2026-09-27：书库（export\）内容去重 ★★★
// ----------------------------------------------------------------
// 用户要求：「相同 epub 只能有一个」。
// 一本 EPUB 的重复是这么攒出来的：
//   · 重名 → 派生 书名_1.epub、书名_2.epub（导入 / 导出 / 转换各写各的）
//   · 同一本书从 download\ 和 export\ 两个路径进来
//   · 重新转换一次，又按原名写回一份
// 打开书库就是好几本一模一样的书。现在的规矩：
//   内容逐字节相同 → 全库只留一本，留名字最「正」的那本
//   （没有 _1/_2 后缀 > 名字短的 > 先来后到）。
// 只在「内容确实相同」时才删，txt 与 epub 之类永远不会误判。
// ================================================================
const EXPORT_LIB_EXT = /\.(epub|mobi|azw3|azw|fb2|fbz|cbz|pdf|txt)$/i;

function listExportFiles() {
    try {
        return fs.readdirSync(getExportDir())
            .filter((f) => EXPORT_LIB_EXT.test(f))
            .map((f) => path.join(getExportDir(), f))
            .filter((p) => { try { return fs.statSync(p).isFile(); } catch (e) { return false; } });
    } catch (e) {
        return [];
    }
}

// 内容指纹：长度 + sha256。每个文件只读一次，比两两逐字节比快得多
function fileFingerprint(p) {
    try {
        const buf = fs.readFileSync(p);
        return buf.length + ':' + crypto.createHash('sha256').update(buf).digest('hex');
    } catch (e) {
        return null;
    }
}

// 名字更「正」的排前面：没有 _1/_2 后缀的优先，其次名字短的
function exportNameRank(p) {
    const base = path.basename(p, path.extname(p));
    return (/_\d+$/.test(base) ? 100000 : 0) + base.length;
}

// export\ 里有没有「内容与 srcPath 完全相同」的那一本？（excludePath 用来排除自己）
function findSameContentInExport(srcPath, excludePath) {
    const sig = fileFingerprint(srcPath);
    if (!sig) return null;
    const ex = excludePath ? path.resolve(excludePath).toLowerCase() : null;
    for (const p of listExportFiles()) {
        if (ex && path.resolve(p).toLowerCase() === ex) continue;
        if (fileFingerprint(p) === sig) return p;
    }
    return null;
}

// 刚写进来一本：把库里与它内容相同、但名字更差的副本删掉（同一本书只留一本）
function pruneDuplicatesOf(targetPath) {
    const sig = fileFingerprint(targetPath);
    if (!sig) return [];
    const removed = [];
    for (const p of listExportFiles()) {
        if (path.resolve(p).toLowerCase() === path.resolve(targetPath).toLowerCase()) continue;
        if (fileFingerprint(p) !== sig) continue;
        if (exportNameRank(p) < exportNameRank(targetPath)) {
            // 老名字更正规 → 留老的，把刚写进来的这份删掉
            try {
                fs.unlinkSync(targetPath);
                removed.push(path.basename(targetPath));
                console.log(`🧹 书库去重：${path.basename(targetPath)} 与已有的 ${path.basename(p)} 内容相同，已删除新副本`);
            } catch (e) {
                console.warn('⚠️ 去重删除失败:', targetPath, e.message);
            }
            return removed;
        }
        try {
            fs.unlinkSync(p);
            removed.push(path.basename(p));
            console.log(`🧹 书库去重：删除重复的 ${path.basename(p)}（内容与 ${path.basename(targetPath)} 完全相同）`);
        } catch (e) {
            console.warn('⚠️ 去重删除失败:', p, e.message);
        }
    }
    return removed;
}

// 全库去重：启动时跑一次，把历史上攒下的 _1/_2 副本清掉
function pruneDuplicateExports() {
    const groups = new Map();
    for (const p of listExportFiles()) {
        const sig = fileFingerprint(p);
        if (!sig) continue;
        if (!groups.has(sig)) groups.set(sig, []);
        groups.get(sig).push(p);
    }
    const removed = [];
    for (const list of groups.values()) {
        if (list.length < 2) continue;
        const sorted = list.slice().sort((a, b) => exportNameRank(a) - exportNameRank(b));
        const keeper = sorted[0];
        for (let i = 1; i < sorted.length; i++) {
            try {
                fs.unlinkSync(sorted[i]);
                removed.push({ removed: path.basename(sorted[i]), kept: path.basename(keeper) });
            } catch (e) {
                console.warn('⚠️ 书库去重删除失败:', sorted[i], e.message);
            }
        }
    }
    if (removed.length) {
        console.log(`🧹 书库去重：清掉 ${removed.length} 个重复副本`);
        removed.forEach((r) => console.log(`    ${r.removed}（与 ${r.kept} 内容相同）`));
    }
    return removed;
}

/**
 * 批量导出：传入 txt 路径 + epub 路径，一键复制到导出目录
 * - 任一文件不存在就跳过那个文件（不报错）
 * - 不阻塞主流程
 */
function exportDownloadedFiles(txtPath, epubPath) {
    if (txtPath && fs.existsSync(txtPath)) {
        exportFileToExportDir(txtPath, { label: 'TXT' });
    }
    if (epubPath && fs.existsSync(epubPath)) {
        exportFileToExportDir(epubPath, { label: 'EPUB' });
    }
}

// ★★★ 优化（动画流畅度）：启用 GPU 硬件加速 ★★★
// RTX 5060 完全支持 GPU 加速，关掉 GPU 导致所有动画用 CPU 软渲染 → 慢、不灵动
// 如果未来出现 GPU 进程崩溃，再把这 3 行打开（注释符号去掉即可）
// app.commandLine.appendSwitch('disable-gpu');
// app.commandLine.appendSwitch('disable-software-rasterizer');
// ★ 2026-09-23：这台机器上 GPU 进程一起来就崩（日志 gpu_process_host.cc(991)
//   GPU process exited unexpectedly: exit_code=-1073740791），崩了之后窗口会被
//   关掉 —— app.on('window-all-closed') 打印「🧹 退出前清理 download/」并
//   app.quit()，表现就是「应用自己退了 / 得重开一次」。以前是启动时手动加
//   --disable-gpu 规避，这里直接关掉硬件加速，省得每次都要记得加参数。
app.disableHardwareAcceleration();

let mainWindow;

function createWindow() {
    // 屏幕自适应：用主屏 workAreaSize（DIP，已扣掉任务栏）判断装不装得下 1350x920。
    // 装得下就用 1350x920；装不下就缩到比可用区略小（上限 1200x700），
    // 避免窗口被任务栏或屏幕边缘裁掉。注意这里不能用物理分辨率判——
    // 1080p 屏在 125% 缩放下 workAreaSize 只有约 1536x864。
    const wa = screen.getPrimaryDisplay().workAreaSize;
    const FIT = 40;                                 // 小尺寸时两边各留约 20px
    let winW, winH, sizeTier;
    if (wa.width >= 1350 && wa.height >= 920) {
        winW = 1350; winH = 920; sizeTier = 'big';
    } else {
        winW = Math.min(1200, Math.max(900, wa.width - FIT));
        winH = Math.min(700, Math.max(520, wa.height - FIT));
        sizeTier = 'small';
    }
    console.log('[display] workAreaSize=' + wa.width + 'x' + wa.height +
        ' scaleFactor=' + screen.getPrimaryDisplay().scaleFactor +
        ' -> window=' + winW + 'x' + winH + ' (' + sizeTier + ')');

    mainWindow = new BrowserWindow({
        width: winW,
        height: winH,
        minWidth: Math.min(1180, winW),
        minHeight: Math.min(620, winH),
        frame: false,
        transparent: false,
        backgroundColor: '#0B0D10',
        webPreferences: {
            preload: path.join(__dirname, 'preload.js'),
            contextIsolation: true,
            nodeIntegration: false,
            webviewTag: true,          // ★ 2026-09-30：授权弹窗里内嵌验证码网页要用
        },
        icon: path.join(__dirname, 'icon.ico')
    });

    mainWindow.loadFile('index.html');

    // ★ 2026-09-30：**把渲染层的 console 也写进 logs\upload-*.log**。
    //   排查"界面没反应/点了没弹窗"时，渲染进程的报错是唯一线索，而它在打包版里根本看不到
    //   （没有 devtools、没有控制台）。这里只记 warning/error + 含 LN/轻小说 字样的行，避免刷屏。
    try {
        mainWindow.webContents.on('console-message', (event, level, message, line, sourceId) => {
            const lv = ['LOG', 'WARN', 'ERROR', 'DEBUG'][level] || ('L' + level);
            const msg = String(message || '');
            if (level >= 1 || /LN|ln-|轻小说|linovelib/i.test(msg)) {
                try {
                    logUpload('UI-' + lv, msg.slice(0, 400)
                        + (sourceId ? ' @' + String(sourceId).split(/[\\/]/).pop() + ':' + line : ''));
                } catch (e) {}
            }
        });
    } catch (e) {}
    // mainWindow.webContents.openDevTools();

    mainWindow.on('closed', () => {
        mainWindow = null;
    });
}

app.whenReady().then(() => {
    createWindow();
});

app.on('window-all-closed', () => {
    // ★ 2026-09-23：先把正在跑的上传子进程带走（bypy 是 python 的子进程，
    //   _killTree 用 taskkill /T 整棵杀），否则窗口关掉后它继续在后台传，
    //   日志也没有结论（以前日志末尾停在 START 就是这么来的）。
    if (activeUploadChild) {
        try {
            console.log('🧹 退出前终止未完成的上传进程');
            logUpload('QUIT', '窗口关闭时仍在执行上传 → 已终止 bypy 子进程');
            _killTree(activeUploadChild);
        } catch (_) {}
        activeUploadChild = null;
    }
    // ★ 退出程序前清空 download 目录
    try {
        const downloadDir = DOWNLOAD_DIR;
        if (fs.existsSync(downloadDir)) {
            const files = fs.readdirSync(downloadDir);
            let count = 0;
            for (const f of files) {
                const fp = path.join(downloadDir, f);
                try {
                    if (fs.statSync(fp).isFile()) {
                        fs.unlinkSync(fp);
                        count++;
                    }
                } catch (e) {}
            }
            console.log(`🧹 退出前清理 download/：已删除 ${count} 个文件`);
            // ★ 2026-09-23：退出也记一笔。上传没返回就退出（用户提前关窗/进程被杀）时，
            //   日志末尾停在哪一步一眼就能看出来。
            logUpload('QUIT', `窗口全部关闭 → 退出应用（清理 download/ 删除 ${count} 个文件）`);
        }
    } catch (e) {
        console.log('⚠️ 退出清理失败：', e.message);
    }
    if (process.platform !== 'darwin') {
        app.quit();
    }
});

// ================================================================
// 窗口控制
// ================================================================

// ★★★ 2026-09-30：窗口控制加**留痕**（用户报「窗口无法最小化和全屏」）。
//   以前这三个处理器一个字都不记，所以"点了没反应"完全没法定位 —— 现在每次动作都写一行
//   `WIN-CTRL`：有这行 = 渲染层点了、主进程收到了（问题在窗口本身）；没有 = 点击根本没到主进程
//   （多半是渲染层事件没绑上 / 被别的元素盖住）。
//   `_manualMax` / `_restoreBounds`：给"maximize() 不生效"的兜底用（见下面 window-maximize）。
let _manualMax = false;
let _restoreBounds = null;

ipcMain.on('window-minimize', () => {
    if (!mainWindow) { logUpload('WIN-CTRL', JSON.stringify({ action: 'minimize', hasWindow: false })); return; }
    mainWindow.minimize();
    // 记**动作之后**的真实状态，日志自证（minimized:true = 系统确实最小化了）
    setTimeout(() => {
        try {
            logUpload('WIN-CTRL', JSON.stringify({
                action: 'minimize', minimized: mainWindow.isMinimized(),
                visible: mainWindow.isVisible(), focused: mainWindow.isFocused(),
            }));
        } catch (e) {}
    }, 250);
});

ipcMain.on('window-maximize', () => {
    if (!mainWindow) { logUpload('WIN-CTRL', JSON.stringify({ action: 'maximize', hasWindow: false })); return; }
    const wasMax = mainWindow.isMaximized() || _manualMax;
    if (wasMax) {
        if (mainWindow.isMaximized()) mainWindow.unmaximize();
        if (_manualMax) {
            try { if (_restoreBounds) mainWindow.setBounds(_restoreBounds); } catch (e) {}
            _manualMax = false;
        }
        setTimeout(() => {
            try { logUpload('WIN-CTRL', JSON.stringify({ action: 'restore', nowMaximized: mainWindow.isMaximized(), manual: _manualMax })); } catch (e) {}
        }, 200);
        return;
    }
    try { _restoreBounds = mainWindow.getBounds(); } catch (e) { _restoreBounds = null; }
    mainWindow.maximize();
    // ★★★ 2026-09-30：**兜底**。实测无边框窗口在 Windows 上偶发 `maximize()` 不生效
    //   （日志实锤：WIN-CTRL {"wasMaximized":false,"nowMaximized":false}）——
    //   等 180ms 再看一次，仍没最大化就**手动铺满工作区**（视觉结果一致，并记 manual:true 以便还原）。
    setTimeout(() => {
        try {
            if (!mainWindow.isMaximized()) {
                const wa = screen.getPrimaryDisplay().workArea;
                mainWindow.setBounds({ x: wa.x, y: wa.y, width: wa.width, height: wa.height });
                _manualMax = true;
            }
            logUpload('WIN-CTRL', JSON.stringify({
                action: 'maximize', nowMaximized: mainWindow.isMaximized(), manual: _manualMax,
                bounds: mainWindow.getBounds(),
            }));
        } catch (e) {}
    }, 180);
});

// ★★★ 2026-09-30 新增：**真正的全屏**（原来只有"最大化"，压根没有全屏 —— 用户要的 F11 那种）。
//   `setFullScreen(true)` 会盖住任务栏；`maximize()` 不会。两者互不干扰：
//   进全屏前先记下是否已最大化，退出全屏时恢复原状。
ipcMain.on('window-fullscreen', () => {
    if (!mainWindow) return;
    const to = !mainWindow.isFullScreen();
    logUpload('WIN-CTRL', JSON.stringify({ action: 'fullscreen', to, wasMaximized: mainWindow.isMaximized() }));
    mainWindow.setFullScreen(to);
});

ipcMain.on('window-close', () => {
    if (mainWindow) mainWindow.close();
});

// ================================================================
// 文件操作
// ================================================================

ipcMain.handle('select-file', async () => {
    const result = await dialog.showOpenDialog(mainWindow, {
        properties: ['openFile'],
        filters: [{ name: '文本文件', extensions: ['txt'] }]
    });
    if (!result.canceled && result.filePaths.length > 0) {
        return result.filePaths[0];
    }
    return null;
});

ipcMain.handle('read-file', async (event, filePath) => {
    if (!filePath || typeof filePath !== 'string') {
        throw new Error('文件路径无效');
    }
    // ★ 安全修复：原来只校验"存在/是文件"，不校验在哪 —— 可读取任意文件。
    //   注意：本通道目前全项目无调用方，收紧不会影响任何现有功能。
    filePath = assertPathAllowed(filePath, '读取文件');
    if (!fs.existsSync(filePath)) {
        throw new Error(`文件不存在: ${filePath}`);
    }
    const stats = fs.statSync(filePath);
    if (!stats.isFile()) {
        throw new Error(`路径不是文件: ${filePath}`);
    }
    const buffer = fs.readFileSync(filePath);
    return {
        success: true,
        buffer: buffer,
        name: path.basename(filePath),
        size: stats.size
    };
});

ipcMain.handle('file-drop', async (event, filePath) => {
    if (fs.existsSync(filePath)) {
        const stats = fs.statSync(filePath);
        if (stats.isFile()) {
            return { success: true, path: filePath, name: path.basename(filePath), size: stats.size };
        }
    }
    return { success: false, error: '文件不存在或不是文件' };
});

// ================================================================
// 编码转换工具
// ================================================================

function callEncodingConverter(inputPath, outputPath) {
    return new Promise((resolve) => {
        const pythonScript = getPythonScript('encoding_converter.py');
        const pythonPath = findPython();

        if (!fs.existsSync(pythonScript)) {
            resolve({ success: true, output_path: inputPath, note: '跳过转换' });
            return;
        }

        const inputData = JSON.stringify({
            input_path: inputPath,
            output_path: outputPath || null
        });

        const python = exec(`"${pythonPath}" -X utf8 "${pythonScript}"`, {
            maxBuffer: 1024 * 1024 * 50
        }, (error, stdout) => {
            if (error) {
                resolve({ success: true, output_path: inputPath, note: '转换失败，使用原文件' });
                return;
            }
            try {
                const result = JSON.parse(stdout);
                resolve(result);
            } catch (e) {
                resolve({ success: true, output_path: inputPath, note: '解析失败，使用原文件' });
            }
        });

        python.stdin.write(inputData);
        python.stdin.end();
    });
}

// ================================================================
// AI 搜索作者
// ================================================================

// ================================================================
// ★ 2026-09-29 se 兜底找封面（ai_cover.py 去 AI 化后的唯一用途）
//   触发条件（在 runMetaLookup 里自动判断）：
//     · 元数据查到了，但没有任何封面（封面字段为空），或
//     · 封面下载失败（novelmeta.cover 抛 CoverError，例如番茄的字节签名已过期）
//   为什么需要它：番茄这类站的封面 URL 带时效签名，离线拿不到；直接用真实浏览器
//   打开详情页现取 img.src 最可靠。番茄没有封面时它也无能为力（会返回失败）。
//   ★ 不需要任何 API key；复用随包 chromedriver。
// ================================================================
const META_SOURCE_TO_PLATFORM = {
    fanqie: 'fanqie',      // 番茄
    jjwxc: 'jj',           // 晋江
    bilinovel: 'bilinovel',// 哔哩轻小说
    qidian: 'qidian',      // 起点（当前没有起点源，留着以后用）
    weread: '',            // 微信读书：走关键词兜底
};

async function runCoverFallback(payload = {}) {
    const title = String(payload.title || '').trim();
    const author = String(payload.author || '').trim();
    const platform = String(payload.platform || '').trim();
    if (!title) return { ok: false, error: '书名不能为空' };

    const t0 = Date.now();
    const script = getPythonScript('ai_cover.py');
    const pythonPath = findPython();
    if (!fs.existsSync(script) || !fs.existsSync(pythonPath)) {
        return { ok: false, error: 'Python 环境不存在' };
    }
    const coverDriver = app.isPackaged
        ? path.join(process.resourcesPath, 'chromedriver.exe')
        : path.join(__dirname, 'chromedriver.exe');

    const args = ['-X', 'utf8', script, '--fallback-search', title];
    if (author) args.push(author);
    if (platform) args.push('--platform', platform);
    logUpload('COVER-FALLBACK-START', JSON.stringify({
        argv: args, chromedriver: coverDriver, chromedriverExists: fs.existsSync(coverDriver),
    }));

    try {
        const data = await new Promise((resolve, reject) => {
            execFile(pythonPath, args, {
                maxBuffer: 1024 * 1024 * 50,
                encoding: 'utf-8',
                windowsHide: true,
                timeout: 90000,
                env: buildPythonEnv({
                    PYTHONIOENCODING: 'utf-8',
                    // ★ 2026-09-25 起 ai_cover.py 优先用随包 chromedriver，找不到才回落 wdm
                    CHROMEDRIVER_PATH: coverDriver,
                    // ★ 2026-10-01（用户：「但是我获取封面不是可以静默吗」）：
                    //   兜底搜封面**默认无头＝静默**，不再弹浏览器窗口。
                    //   ai_cover.py 是"无头失败自动用有头再试一次"，所以这里给 1 是安全的；
                    //   要排障就设 COVER_HEADLESS=0 让窗口出来。
                    COVER_HEADLESS: process.env.COVER_HEADLESS || '1'
                })
            }, (error, stdout, stderr) => {
                if (stderr) console.log('🐍 兜底搜封面 stderr:', String(stderr).trim().slice(-800));
                if (error) { reject(error); return; }
                try {
                    let jsonLine = '';
                    for (const line of String(stdout || '').split('\n')) {
                        const t = line.trim();
                        if (t.startsWith('{')) { jsonLine = t; break; }
                    }
                    if (!jsonLine) { reject(new Error('未找到 JSON 输出')); return; }
                    resolve(JSON.parse(jsonLine));
                } catch (e) { reject(e); }
            });
        });
        logUpload('COVER-FALLBACK-RESULT', JSON.stringify({
            ok: !!data.ok, ms: Date.now() - t0, error: data.error || null,
        }));
        return data;
    } catch (e) {
        logUpload('COVER-FALLBACK-ERROR', JSON.stringify({
            message: String(e.message || '').slice(0, 300), ms: Date.now() - t0,
        }));
        return { ok: false, error: e.message || '兜底找封面失败' };
    }
}

ipcMain.handle('meta-cover-fallback', async (event, payload) => runCoverFallback(payload || {}));

// ================================================================
// ★ 2026-09-29 novelmeta 元数据查询（作者 + 封面一次拿回）
//   渲染层两种用法：
//     · want='author+cover'（只填了书名）→ 要作者也要封面
//     · want='cover'（书名+作者都填了）  → 只要封面，作者只作消歧提示
//   返回 meta_lookup.py 的原始 JSON，含 candidates（供多作者选择弹窗）。
//   ★ 不需要任何 API key。
// ================================================================
async function runMetaLookup(payload = {}) {
    const title = String(payload.title || '').trim();
    const author = String(payload.author || '').trim();
    const want = payload.want === 'cover' ? 'cover' : 'author+cover';
    // ★★★ 2026-09-30（用户实测「为啥我连封面和作者的按钮都点不动」）：
    //   这里原来是 `if (!title || title.length < 2) return` —— **一个字书名（《岛》）被静默挡掉**，
    //   而且**在 META-ARGS 那行日志之前**就返回了，所以日志里一片空白、看着像按钮坏了。
    //   渲染层同一道门槛已删；这里只拦空书名，并且**拦了也留日志**。
    if (!title) {
        logUpload('META-SKIP', JSON.stringify({ reason: '书名为空', want }));
        return { ok: false, error: '书名不能为空', want };
    }
    const t0 = Date.now();
    const script = getPythonScript('meta_lookup.py');
    const pythonPath = findPython();
    if (!fs.existsSync(script) || !fs.existsSync(pythonPath)) {
        logUpload('META-ERROR', JSON.stringify({ error: 'Python 环境不存在', script, pythonPath }));
        return { ok: false, error: 'Python 环境不存在', want };
    }
    const args = ['-X', 'utf8', script, '--title', title, '--want', want, '--json'];
    if (author) args.push('--author', author);
    if (payload.sources) args.push('--source', String(payload.sources));
    logUpload('META-ARGS', JSON.stringify({ argv: args }));

    try {
        const data = await new Promise((resolve, reject) => {
            execFile(pythonPath, args, {
                maxBuffer: 1024 * 1024 * 50,
                encoding: 'utf-8',
                windowsHide: true,
                timeout: 45000,
                // novelmeta 纯标准库、不需要 key；只给包路径 + IPv4-only
                env: buildPythonEnv({
                    PYTHONIOENCODING: 'utf-8',
                    NOVELMETA_HOME: APP_ROOT,
                    NOVELMETA_IPV4_ONLY: '1'
                })
            }, (error, stdout, stderr) => {
                if (stderr) console.log('🐍 meta stderr:', String(stderr).trim().slice(-800));
                if (error) { reject(error); return; }
                try {
                    let jsonLine = '';
                    for (const line of String(stdout || '').split('\n')) {
                        const t = line.trim();
                        if (t.startsWith('{')) { jsonLine = t; break; }
                    }
                    if (!jsonLine) { reject(new Error('未找到 JSON 输出')); return; }
                    resolve(JSON.parse(jsonLine));
                } catch (e) { reject(e); }
            });
        });
        // ★ 有元数据但没封面 → 自动走 se 兜底（番茄签名过期 / 源本身不带封面）
        // ★★★ 2026-10-01：这个条件必须和**渲染层的弹窗条件对齐**，否则两头都不动 ★★★
        //   用户实测「搜不出封面」的死结就是这么来的：
        //     · meta_lookup 返回 ok=true / has_cover=false / ambiguous=true
        //       （书名不够硬时它**故意扣住封面**不下，见 meta_lookup.py:428 的 `if confident`）；
        //     · 渲染层 searchCover 的判据是 `result.ambiguous && !author` ——
        //       作者框有值时**不弹候选窗**；
        //     · 主进程这里如果只写 `!data.ambiguous`，ambiguous=true 时也**不兜底**；
        //   → 没人去拿封面，直接报「未找到封面」。
        //   现在改成"**只有界面确实会弹窗让用户重选时**才不抢跑"：判据与渲染层一致
        //   （歧义 且 没填作者）。作者已填 → 界面不会弹窗 → 兜底该跑就跑。
        const _willShowPicker = !!data.ambiguous && !author;
        if (data && data.ok && !data.has_cover && !_willShowPicker) {
            // ★★★ 2026-09-30（用户实测）：**兜底要用"用户输的书名"**，不能用 `data.title` ——
            //   后者是"最接近的候选"，匹配度不够时它可能**是另一本书**：
            //   实测查《诡异游戏入侵，氪金十亿当鬼王》→ 候选《诡异游戏》(0.9185，未达 0.95)
            //   → 兜底却拿「诡异游戏」去百度图片搜 → 当然搜不到（用户看着就是"兜底也没用"）。
            //   规则：匹配够硬（≥0.95）才用命中书名（它是规范写法），否则一律用用户原书名。
            const _hard = Number(data.title_match || 0) >= 0.95;
            const _fbTitle = (_hard && data.title) ? data.title : title;
            const _fbAuthor = (data.author || '').trim() || author;
            logUpload('COVER-FALLBACK-INPUT', JSON.stringify({
                useTitle: _fbTitle, matchedTitle: data.title || null,
                titleMatch: data.title_match || null, hard: _hard,
            }));
            const fb = await runCoverFallback({
                title: _fbTitle,
                author: _fbAuthor,
                platform: META_SOURCE_TO_PLATFORM[data.source] || '',
            });
            if (fb && fb.ok && fb.cover_data) {
                data.cover_data = fb.cover_data;
                data.has_cover = true;
                data.cover_fallback = true;
                logUpload('META-COVER-FALLBACK', JSON.stringify({ source: data.source, ok: true }));
            } else {
                data.cover_fallback = false;
                data.cover_fallback_error = (fb && fb.error) || '兜底未找到封面';
                logUpload('META-COVER-FALLBACK', JSON.stringify({
                    source: data.source, ok: false, error: data.cover_fallback_error,
                }));
            }
        }

        // ★ 2026-09-29：自检 + 可观测 —— 不留"半成功"状态。
        //   浏览器对 data: URI 不做嗅探：前缀不是 data:image/ 就一定渲染不出来，
        //   这种情况宁可当"没封面"（走失败小字），也不要让界面停在"✅ 已获取封面但没图"。
        // ★★★ 2026-10-01：**再加两道**（实测无头兜底搜回来的 data:image/svg+xml
        //   解码只有 312 字节 —— 那是站点占位图，`^data:image/` 拦不住，会被当封面嵌进 EPUB）：
        //     · 矢量图（svg）不是封面，真封面全是位图；
        //     · 极小的图（base64 长度 < 4000 ≈ 解码后 3KB）基本是占位/图标。
        //   ai_cover.py 里已经先挡了一道，这里是**第二道**，防止别的路径漏进来。
        const _cd = data && data.cover_data ? String(data.cover_data) : '';
        if (_cd && !/^data:image\//.test(_cd)) {
            logUpload('META-COVER-REJECT', JSON.stringify({
                head: _cd.slice(0, 40), len: _cd.length,
            }));
            data.cover_reject = 'data URI 前缀不是 data:image/';
            data.cover_data = null;
            data.has_cover = false;
        } else if (_cd && (/^data:image\/svg/i.test(_cd) || _cd.length < 4000)) {
            logUpload('META-COVER-REJECT', JSON.stringify({
                head: _cd.slice(0, 40), len: _cd.length,
                reason: /^data:image\/svg/i.test(_cd) ? 'svg 占位图' : '图太小（占位）',
            }));
            data.cover_reject = /^data:image\/svg/i.test(_cd) ? 'svg 占位图，不是真封面' : '图片过小，八成是占位图';
            data.cover_data = null;
            data.has_cover = false;
        }
        if (data) {
            logUpload('META-COVER-APPLY', JSON.stringify({
                has_uri: !!data.cover_data,
                head: data.cover_data ? String(data.cover_data).slice(0, 30) : null,
                len: data.cover_data ? String(data.cover_data).length : 0,
                source: data.source || null,
                fallback: !!data.cover_fallback,
            }));
        }

        logUpload('META-RESULT', JSON.stringify({
            ok: !!data.ok, source: data.source || null, ms: Date.now() - t0,
            has_author: !!data.has_author, has_cover: !!data.has_cover,
            ambiguous: !!data.ambiguous, candidates: (data.candidates || []).length,
        }));
        return data;
    } catch (e) {
        logUpload('META-ERROR', JSON.stringify({ message: String(e.message || '').slice(0, 300), ms: Date.now() - t0 }));
        return { ok: false, error: e.message || '查询失败', want };
    }
}

// ── 精选推荐：推荐池（只读）────────────────────────────────────
//   数据来源：微信读书 /web/category/list（主，免登录）+ 番茄 book_list/v0（备选）
//   ★ 只读：不碰 download-novel / meta-lookup / 书库相关任何契约。
//   ★ rank_pool.py 的 stdout 里混着日志，只有以 "@@RANK " 开头的那一行才是数据
//     （与项目现有的 @@PROGRESS 协议同一风格）。
async function runRecommendList(payload = {}) {
    const batch = Math.max(1, parseInt(payload.batch, 10) || 1);
    // ★ 2026-09-30：上限 20 → 200（轻小说栏位一次要 5 页 = 150 条；原来被截到 20 条，第2页起空白）
    const count = Math.max(1, Math.min(200, parseInt(payload.count, 10) || 10));
    const script = getPythonScript('rank_pool.py');
    const pythonPath = findPython();
    if (!fs.existsSync(script) || !fs.existsSync(pythonPath)) {
        return { ok: false, error: 'Python 环境不存在', items: [] };
    }
    // ★ 2026-09-30：支持栏位 —— source='linovelib' 时走哔哩轻小说榜单（--sort 选榜）
    const src = payload.source === 'linovelib' ? 'linovelib' : 'all';
    const args = ['-X', 'utf8', script, '--json', '--pick', String(count),
                  '--batch', String(batch), '--source', src];
    if (src === 'linovelib') {
        const pages = Math.max(1, Math.min(5, parseInt(payload.pages, 10) || 1));
        args.push('--sort', String(payload.sort || 'monthvisit'), '--pages', String(pages));
    } else {
        args.push('--enrich');
    }
    logUpload('RANK-ARGS', JSON.stringify({ argv: args }));
    const t0 = Date.now();
    try {
        const stdout = await new Promise((resolve, reject) => {
            execFile(pythonPath, args, {
                maxBuffer: 1024 * 1024 * 64,
                encoding: 'utf-8',
                windowsHide: true,
                timeout: 180000,
            }, (err, out, errOut) => {
                if (err && !out) { reject(err); return; }
                resolve(String(out || '') + String(errOut || ''));
            });
        });
        const line = stdout.split(/\r?\n/).filter(l => l.indexOf('@@RANK ') === 0).pop();
        if (!line) {
            logUpload('RANK-NODATA', JSON.stringify({ tail: stdout.slice(-400) }));
            return { ok: false, error: '推荐池没有返回数据', items: [] };
        }
        const data = JSON.parse(line.slice('@@RANK '.length));
        data.ms = Date.now() - t0;
        logUpload('RANK-OK', JSON.stringify({ pool: data.pool, picked: data.picked, ms: data.ms }));
        return data;
    } catch (exc) {
        const msg = String((exc && exc.message) || exc);
        logUpload('RANK-ERROR', JSON.stringify({ error: msg }));
        return { ok: false, error: msg, items: [] };
    }
}

// 详情弹窗：条目本身已带 intro/tags/rating/free，这里只做「缺简介时按书名补一次」的兜底。
async function runRecommendDetail(payload = {}) {
    const item = (payload && typeof payload === 'object') ? payload : {};
    const out = {
        ok: true,
        title: String(item.title || ''),
        author: String(item.author || ''),
        source: String(item.source || ''),
        intro: String(item.intro || ''),
        tags: Array.isArray(item.tags) ? item.tags : [],
        rating: item.rating || null,
        rating_count: item.rating_count || null,
        free: !!item.free,
        max_free_chapter: item.max_free_chapter || 0,
        publish_time: item.publish_time || '',
        finished: item.finished,
        url: String(item.url || ''),
    };
    if (!out.intro && out.title) {
        try {
            const meta = await runMetaLookup({ title: out.title, author: out.author, want: 'author+cover' });
            if (meta && meta.ok && meta.intro) { out.intro = meta.intro; }
        } catch (e) { /* 兜底失败不影响详情展示 */ }
    }
    return out;
}

ipcMain.handle('meta-lookup', async (event, payload) => runMetaLookup(payload || {}));
ipcMain.handle('recommend-list', async (event, payload) => runRecommendList(payload || {}));

// ══════════════════════════════════════════════════════════════════════
// ★ 2026-09-30：推荐封面缓存（封面图的「慢」是真瓶颈 —— 站点 CDN 单张 0.9~2.8s）
//   主进程并发下载（默认 8）→ 落 userData/rec-covers/<sha1>.jpg → 返回 file:// 路径；
//   命中缓存直接返回。渲染层拿不到路径时才回退直连，所以不会白屏。
// ══════════════════════════════════════════════════════════════════════
const COVER_DIR = path.join(app.getPath('userData'), 'rec-covers');
const _coverJobs = new Map();      // file → Promise<path|null>（同一张图只下一次）
const _coverWait = [];             // 等待队列 [{job, resolve}]
let _coverActive = 0;

function _coverPump() {
    while (_coverActive < 8 && _coverWait.length) {
        const it = _coverWait.shift();
        _coverActive += 1;
        _coverFetch(it.job)
            .then((r) => it.resolve(r))
            .catch(() => it.resolve(null))
            .then(() => { _coverActive -= 1; _coverPump(); });
    }
}
function _coverFetch(job) {
    return new Promise((resolve) => {
        let host = '';
        try { host = new URL(job.url).origin; } catch (e) { host = ''; }
        let done = false;
        let timer = null;
        const fin = (v) => {
            if (done) { return; }
            done = true;
            if (timer) { clearTimeout(timer); }
            resolve(v);
        };
        let req;
        try { req = net.request({ method: 'GET', url: job.url }); } catch (e) { fin(null); return; }
        // ★ 2026-09-30 修：Electron 的 net.ClientRequest **没有 setTimeout()** ——
        //   原来在这里调它 → 抛 TypeError → Promise reject → 每张封面都「瞬间失败」
        //   （实测日志 350/350 ok:false，时间戳挤在 5ms 内）。改成手动定时器。
        try {
            req.setHeader('Referer', host + '/');
            req.setHeader('User-Agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) ' +
                'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36');
        } catch (e) { /* 头设置失败不致命 */ }
        timer = setTimeout(() => { try { req.abort(); } catch (e) {} fin(null); }, 25000);
        const chunks = [];
        req.on('response', (res) => {
            if (res.statusCode !== 200) { try { req.abort(); } catch (e) {} fin(null); return; }
            res.on('data', (c) => chunks.push(c));
            res.on('end', () => {
                const buf = Buffer.concat(chunks);
                if (buf.length < 512) { fin(null); return; }        // 空壳/占位图
                try { fs.writeFileSync(job.file, buf); fin(job.file); }
                catch (e) { fin(null); }
            });
            res.on('error', () => fin(null));
        });
        req.on('error', () => fin(null));
        req.on('abort', () => fin(null));
        try { req.end(); } catch (e) { fin(null); }
    });
}

function coverCached(url) {
    try { fs.mkdirSync(COVER_DIR, { recursive: true }); } catch (e) {}
    const ext = (url.split('?')[0].match(/\.[a-z0-9]{3,4}$/i) || ['.jpg'])[0];
    const file = path.join(COVER_DIR, crypto.createHash('sha1').update(url).digest('hex') + ext);
    if (fs.existsSync(file) && fs.statSync(file).size > 512) {
        return Promise.resolve(file);
    }
    if (_coverJobs.has(file)) { return _coverJobs.get(file); }
    const p = new Promise((resolve) => { _coverWait.push({ job: { url, file }, resolve }); _coverPump(); });
    _coverJobs.set(file, p);
    p.then(() => _coverJobs.delete(file));
    return p;
}

ipcMain.handle('cover-cache', async (event, payload) => {
    const url = String((payload && payload.url) || '').trim();
    if (!/^https?:\/\//i.test(url)) { return { ok: false, error: 'url 非法' }; }
    const got = await coverCached(url);
    if (!got) { logUpload('COVER-CACHE', JSON.stringify({ url: url.slice(-32), ok: false }));
        return { ok: false }; }
    return { ok: true, path: 'file:///' + got.replace(/\\/g, '/') };
});
// ── 哔哩轻小说（linovelib.com）：零积分路线 ────────────────────────────
//   ★ 不走站的 /download 接口（那要积分+登录）：改抓**公开章节页 + 插图**，
//     自己组装 EPUB → 零积分、免登录、带插图。实测依据见 linovelib.py 文档头。
//   ★ 只读：不碰 download-novel / 书库契约；产物是 EPUB，直接进书库。
//
// ★★★ 2026-10-01（把"模块化"后的 linovelib.py + 新增的 refresh_cookie.py 接回来）★★★
//   两个**必须由主进程指路**的目录 —— 都跟打包后的可写性有关：
//     · cookies.txt：cf_clearance（Cloudflare 通行证）落盘的地方。脚本默认写在
//       `<脚本目录>/cookies.txt`，而打包后脚本目录是 resources\app.asar.unpacked\
//       （perMachine 安装 = C:\Program Files\…）→ 普通用户**写不进去**，
//       Selenium 续期会以 PermissionError 失败；而且这个目录升级时会被整体覆盖。
//     · _ln_tmp：插图先下到这里再由 build_epub 读进 EPUB。同理不可写 →
//       os.makedirs 直接抛 PermissionError，**整本书在"下插图"这一步崩掉**。
//   现在统一指到 userData（app.getPath('userData')，一定有写权限、升级不丢）。
const LN_COOKIE_FILE = path.join(app.getPath('userData'), 'linovelib-cookies.txt');
const LN_WORK_DIR = path.join(app.getPath('userData'), 'linovelib-tmp');

// 开发模式下老位置（脚本目录）里可能已经有手工拷的 cookies.txt → 首次自动搬过来，
// 免得用户升上来发现"昨天还好好的，今天要重新拷 Cookie"。
function migrateLegacyLinovelibCookie() {
    try {
        if (fs.existsSync(LN_COOKIE_FILE)) { return; }
        const legacy = path.join(APP_ROOT, 'cookies.txt');
        if (!fs.existsSync(legacy)) { return; }
        fs.mkdirSync(path.dirname(LN_COOKIE_FILE), { recursive: true });
        fs.copyFileSync(legacy, LN_COOKIE_FILE);
        console.log(`🍪 已把旧的 cookies.txt 搬到 userData：${LN_COOKIE_FILE}`);
    } catch (e) { /* 搬不动就算了，脚本会当没有 Cookie 跑（只是正文可能被截断） */ }
}

// ★★★ 2026-10-02（用户要求）：「刷新 cookie 也要杀掉前一个任务，然后点刷新 cookie 重新开」
//   杀掉所有还在跑的「抓取 / 取 Cookie」Python 进程（linovelib.py / refresh_cookie.py）。
//   为什么要杀：
//     · 同一时刻两个抓取进程会**互相拖累**，实测把站点磨到全站 403（各跑了 4.6 / 2.9 分钟）；
//     · 两个 Edge 会话会互相抢窗口 → `NoSuchWindowException`（日志里实测到过）。
//   只杀命令行里带这两个脚本名的 python/easypub-backend，**不碰别的 python**（bypy/超分等）。
// ★ 当前在跑的 linovelib 子进程（用于"新任务开始前先杀掉上一个"）。
//   ★ 只按**句柄**杀，不按命令行匹配杀 —— 后者会把同一次操作里的兄弟进程一起杀掉（实测踩到）。
let _lnActiveChild = null;

// ★★★ 2026-10-02（用户要求）：「刷新 cookie 也要杀掉前一个任务，然后点刷新 cookie 重新开」
//   ⚠️ 这个函数**已经不在 runLinovelib 里调用了** —— 它是按命令行匹配杀"所有"匹配进程，
//      实测会把同一次点击引发的 `--info`/`--get` 互相杀掉（甚至杀掉刚起来的自己），
//      表现为**零输出退出**、界面报 `Command failed`、连分卷信息都看不到。
//      现在改成上面那句 `_lnActiveChild.kill()`（精确到句柄）。
//      留着这个函数只给"清理遗留"用，**别在正常流程里调**。
function killStaleLinovelibProcs(selfScriptLower) {
    const { execFileSync } = require('child_process');
    let killed = 0;
    try {
        // ★ 别用 `Get-CimInstance -Filter "Name='a' or Name='b'"` —— 从 Node 传进 PowerShell 时
        //   引号会被 CreateProcess 吃掉，PowerShell 报 `positional parameter cannot be found that
        //   accepts argument 'or'`（实测踩到）。改成**不用 -Filter、在 Where-Object 里筛**。
        // ★ 也**别用 wmic** —— 新版 Windows 已经把它移除了（实测 CommandNotFoundException）。
        const ps = "Get-CimInstance Win32_Process | Where-Object { "
            + "($_.Name -eq 'python.exe' -or $_.Name -eq 'easypub-backend.exe') "
            + "-and $_.CommandLine -match 'linovelib[.]py|refresh_cookie[.]py' } "
            + "| ForEach-Object { $_.ProcessId }";
        const out = execFileSync('powershell.exe',
            ['-NoProfile', '-NonInteractive', '-Command', ps],
            { encoding: 'utf-8', timeout: 15000, windowsHide: true });
        const pids = String(out || '').split(/\r?\n/).map(s => s.trim()).filter(s => /^\d+$/.test(s));
        for (const pid of pids) {
            try { process.kill(Number(pid)); killed += 1; } catch (e) { /* 已经没了就跳过 */ }
        }
    } catch (e) {
        // PowerShell 不可用 / 权限不足都不该影响主流程，如实返回 0
    }
    return killed;
}

async function runLinovelib(payload = {}, event = null) {
    // ★★★ 2026-09-30 修：本函数的签名里**原先没有 event**，但下面用 `event.sender.send(...)`
    //   转发 @@PROGRESS/@@INFO/脚本提示 —— 而外层是 `try { … } catch (e) {}`，
    //   于是每次都抛 ReferenceError 又被**静默吞掉**：
    //     · 轻小说弹窗的进度条/状态行从来没动过（用户："进度条一点都不符还误导人"）；
    //     · 「下载小说」那行状态小字也不会被轻小说下载更新（用户实测"卡在那句不动"）。
    //   现在把 event 显式传进来，并用 _send() 统一做存在性 + 未销毁检查。
    const _send = (channel, data) => {
        try {
            if (event && event.sender && !event.sender.isDestroyed()) { event.sender.send(channel, data); }
        } catch (e) { /* 界面没了就丢掉，不影响下载 */ }
    };
    const target = String(payload.target || '').trim();
    // ★ 2026-10-01：多了一个 mode='refresh' —— 只续 Cookie（对应 linovelib.py --refresh-cookie），
    //   给界面上的「刷新 Cookie」按钮用；它不需要 target。
    const mode = payload.mode === 'get' ? 'get' : (payload.mode === 'refresh' ? 'refresh' : 'info');
    if (mode !== 'refresh' && !target) { return { ok: false, error: '请填入书的网址或书号', volumes: [] }; }
    const script = getPythonScript('linovelib.py');
    const pythonPath = findPython();
    if (!fs.existsSync(script) || !fs.existsSync(pythonPath)) {
        return { ok: false, error: 'Python 环境不存在', volumes: [] };
    }
    migrateLegacyLinovelibCookie();
    // ★★★ 2026-10-02 修正（用户报「连分卷信息都看不到了」）★★★
    //   我上一版在这里调 `killStaleLinovelibProcs()` —— 它是**按命令行**匹配
    //   `linovelib.py` 去杀**所有**匹配进程。后果：一次点击引发的 `--info` / `--get`
    //   会互相杀，甚至杀掉刚起来的那个自己 → 进程**零输出**退出 →
    //   `LN-ERROR {"error":"Command failed: …python.exe … linovelib.py 2013 --info…"}`
    //   （日志实测：18:03:39/41/45 三次 `--info` 全部瞬间空输出死掉，用户就"看不到分卷信息"了）。
    //   现在改成**只终止本应用上一次启动的那个子进程**（精确到句柄），
    //   既满足"重试前先杀掉前一个"，又不会误伤同一次操作里的兄弟进程。
    if (_lnActiveChild && !_lnActiveChild.killed) {
        try { _lnActiveChild.kill(); console.log('🧹 已终止上一次还在跑的 linovelib 进程（新任务开始前先清旧的）'); } catch (e) {}
        _lnActiveChild = null;
    }
    // ★ 可选域名：脚本默认 https://www.linovelib.com；被限流/整站空壳时可切
    //   https://www.bilinovel.com（实测该域**必须**带 cf_clearance，linovelib.com 不用）。
    const base = String(payload.base || '').trim();
    const args = ['-X', 'utf8', script];
    if (mode === 'refresh') {
        // ★★★ 2026-10-02：**必须把书号传下去**。取 Cookie 现在只有一条路 ——
        //   打开 https://www.bilinovel.com/novel/<书号>/catalog 让用户点第一章，
        //   所以脚本必须知道是哪本书；不传的话它会兜到写死的目录页（以前就是这么错的）。
        const rt = String(payload.target || '').trim();
        args.push('--refresh-cookie', '--json');
        if (rt) { args.push('--novel-id', rt); }
    } else {
        args.push(target, mode === 'get' ? '--get' : '--info', '--json');
    }
    // ★ Cookie 文件始终显式传：脚本内部"续期写哪个文件就读哪个文件"（_COOKIE_FILE），
    //   不传的话它会用脚本目录 —— 打包后不可写。
    args.push('--cookie-file', LN_COOKIE_FILE);
    if (base) { args.push('--base', base); }
    if (mode === 'get') {
        // ★ 2026-09-30：先落到 download\linovelib 当中转，**成功后由 exportFileToExportDir()
        //   搬进书库 export\** —— 复用它的「同名同内容跳过 / 内容变了覆盖 / 去重复本」逻辑，
        //   也保证书库里不会出现半成品。（用户要求：轻小说路径直接出 EPUB，不走 TXT）
        const outDir = String(payload.outDir || path.join(DOWNLOAD_DIR, 'linovelib'));
        args.push('--out', outDir);
        const vols = Array.isArray(payload.vols) ? payload.vols.filter(v => Number(v) > 0) : [];
        if (vols.length) { args.push('--vol'); vols.forEach(v => args.push(String(v))); }
        // ★ 2026-10-01：cf_clearance 过期时自动用 Selenium+Edge 续期（弹一次 Edge 约 10 秒）。
        //   以前这一步在界面上**根本没法触发** —— 证一过期就只会报「正文被站方截断」，
        //   用户只能自己去浏览器 F12 重拷 Cookie。
        if (payload.autoCookie) { args.push('--auto-cookie'); }
        if (Number(payload.workers) > 0) {
            args.push('--workers', String(Math.max(1, Math.min(32, Number(payload.workers)))));
        }
    }
    logUpload('LN-ARGS', JSON.stringify({ argv: args }));
    const t0 = Date.now();
    try {
        const stdout = await new Promise((resolve, reject) => {
            const child = execFile(pythonPath, args, {
                maxBuffer: 1024 * 1024 * 64, encoding: 'utf-8', windowsHide: true, timeout: 30 * 60 * 1000,
                // ★ 插图中转目录也指到可写处（脚本会先探测，写不了才往下退）
                env: buildPythonEnv({ LINOVELIB_WORK_DIR: LN_WORK_DIR }),
            }, (err, out, errOut) => {
                _lnActiveChild = null;                    // ★ 跑完了，清掉句柄
                if (err && !out) { reject(err); return; }
                resolve(String(out || '') + String(errOut || ''));
            });
            _lnActiveChild = child;                       // ★ 记下句柄：下次调用按它杀，别按命令行匹配
            if (child && child.stdout) {
                child.stdout.on('data', (buf) => {
                    String(buf).split(/\r?\n/).forEach((line) => {
                        if (!line.trim()) { return; }
                        if (line.startsWith('@@PROGRESS ')) {
                            try { _send('linovelib-progress', JSON.parse(line.slice(11))); } catch (e) {}
                        } else if (line.startsWith('@@NEEDCLICK ')) {
                            // ★★★ 2026-10-02（用户定稿「cookie 只保留这一条路径」）★★★
                            //   轻小说被站方锁住（「需要足夠的權限」= 要登录）时，linovelib.py
                            //   会打开 https://www.bilinovel.com/novel/<书号>/catalog 并等人点第一章。
                            //   这里把那条 @@NEEDCLICK 转成 stage='needclick' 送界面 → 弹窗提醒用户。
                            //   ★ 脚本那边**同步在等**（refresh_cookie.py 轮询 drv.current_url），
                            //     所以这个事件必须实时送到，不能等进程结束。
                            try {
                                const nc = JSON.parse(line.slice('@@NEEDCLICK '.length));
                                logUpload('LN-NEEDCLICK', JSON.stringify(nc));
                                _send('linovelib-progress', Object.assign({ stage: 'needclick' }, nc));
                            } catch (e) {}
                        } else if (line.startsWith('@@INFO ')) {
                            try { _send('linovelib-info', JSON.parse(line.slice(7))); } catch (e) {}
                        } else {
                            logUpload('LN', line);
                            // ★ 2026-09-30：脚本的关键提示也送到界面（限流重试 / 分卷 / 插图进度）——
                            //   否则下载期间用户只看到进度条，分不清「在重试」还是「卡死了」，
                            //   实测就被误当成"下不来"、中途关了窗口（关窗＝杀掉抓取）。
                            // ★ 2026-10-01：补上新脚本新增的那几条（否则续期/绕过分卷/限流这些
                            //   最需要被看见的状态恰恰看不到）：
                            //     ℹ️ 书页没有分卷 → 绕过分卷走目录页
                            //     🔄 cf_clearance 超龄 → 自动续期…   ✅ Cookie 已自动续期
                            //     [卷1] …   ⚠️ 有 N 章没抓到…
                            if (/↩️|🖼|📖|🔎|ℹ️|🔄|⚠️|✅|插图下载成功|\[\s*卷|Cookie/.test(line)) {
                                _send('linovelib-progress', { stage: 'note', text: line.trim() });
                            }
                        }
                    });
                });
            }
            // ★ 2026-09-30：**stderr 也进日志**。linovelib.py 的「①/② 走到哪一步」「索引未缓存→
            //   跳过现爬」这些诊断行都打在 stderr；以前只转 stdout，所以「按书名查询卡住」时
            //   logs\upload-*.log 里什么都看不到（用户实测三次都只有一行「按书名解析…」）。
            // ★ 2026-10-01：**并且转给界面**。这些行正是"按书名解析走到第几步""自动续期在跑"这类
            //   用户最想看的进度；只落日志等于白写（界面 15 秒后只会机械地报「仍在查询…」）。
            if (child && child.stderr) {
                child.stderr.on('data', (buf) => {
                    String(buf).split(/\r?\n/).forEach((line) => {
                        if (!line.trim()) { return; }
                        logUpload('LN-ERR', line);
                        _send('linovelib-progress', { stage: 'note', text: line.trim() });
                    });
                });
            }
        });
        // ★ 2026-09-30 修：结果行有两种 —— `--info` 是**裸 JSON**，`--get` 是 **`@@RESULT {...}`**
        //   （见 linovelib.py 文件头的协议注释；脚本两种都算"末行 JSON"，但这里原来只认裸 `{`）。
        //   后果（用户实测）：整卷抓完、EPUB 已写进 download\linovelib\，却因为解析不到结果被判
        //   `{success:false, error:'没有返回数据'}` → 不进书库、界面报失败。
        const last = stdout.split(/\r?\n/)
            .map(l => l.trim())
            .filter(l => l.startsWith('@@RESULT ') || l.startsWith('{'))
            .pop();
        const lastJson = last ? (last.startsWith('@@RESULT ') ? last.slice('@@RESULT '.length) : last) : '';
        const res = lastJson ? JSON.parse(lastJson) : { success: false, error: '没有返回数据' };
        res.ok = !!res.success;
        res.ms = Date.now() - t0;
        // ★★ 2026-09-30：**产出的 EPUB 直接进书库 export\**（用户要求，全程不碰 TXT）★★
        //   linovelib.py 只负责把书组装好、写到中转目录；落书库这件事由主进程用现成的
        //   exportFileToExportDir() 做（同名去重 / 原子替换 / 去重复本都在里面）。
        if (res.ok && res.file) {
            const destPath = path.join(getExportDir(), path.basename(res.file));
            const exported = exportFileToExportDir(res.file, { label: 'EPUB' });
            res.exported = exported ? destPath : null;
            res.fileName = path.basename(destPath);
            logUpload('LN-EXPORT', JSON.stringify({ from: res.file, to: res.exported, ok: !!exported }));
            console.log(exported
                ? `📚 轻小说已进书库：${destPath}`
                : `⚠️ 轻小说进书库失败（中转文件仍在）：${res.file}`);
        }
        logUpload('LN-DONE', JSON.stringify({ ok: res.ok, chapters: res.chapters, images: res.images,
                                              cover: res.cover || '', exported: !!res.exported, ms: res.ms }));
        return res;
    } catch (exc) {
        const msg = String((exc && exc.message) || exc);
        logUpload('LN-ERROR', JSON.stringify({ error: msg }));
        return { ok: false, error: msg, volumes: [] };
    }
}

ipcMain.handle('linovelib-info', async (event, payload) => runLinovelib({ ...(payload || {}), mode: 'info' }, event));
// ★★★★ 阉割版（2026-10-02，用户要求）★★★★
//   **保留完整 UI，删掉下载执行。**
//   界面一行都不删：推荐、轻小说「查询/选卷」、搜作者/搜封面、制作、书库、上传……全部照常。
//   只有"真正会下载"的 IPC 在这里被拦死 —— 统一返回 notAvailable，
//   渲染层拿到这个标志就弹「因相关法律法规规定，该部分暂不开放使用」。
//   （查询分卷走的是 linovelib-info，**不在**拦截名单里。）
function linovelibBlocked(ipc) {
    try { logUpload('LN-BLOCKED', JSON.stringify({ ipc: ipc })); } catch (e) {}
    return { success: false, ok: false, notAvailable: true, error: '该部分暂不开放使用' };
}

ipcMain.handle('linovelib-get', async () => linovelibBlocked('linovelib-get'));
// ★ 2026-10-01：新增「只续 Cookie」通道 —— 走 refresh_cookie.py（linovelib.py --refresh-cookie），
//   给界面上的「刷新 Cookie」按钮用（不需要书号，约 10 秒，会弹一次 Edge）。
ipcMain.handle('linovelib-refresh-cookie', async () => linovelibBlocked('linovelib-refresh-cookie'));

// ================================================================
// ★★★ 2026-10-02（用户定稿）：**从你自己的浏览器本地读 cookie** ★★★
//   用户原话：「我用脚本的不管怎么样都会被 cloudflare 拦，这样，我点刷新 cookie，
//             你就弹出个可叉的 html，告诉用户让他打开浏览器……点到 re 从零开始的异世界生活
//             第一卷第一章的正文页，然后你一直在**后台开脚本爬 cookie**」
//   为什么能过 Cloudflare：**一个网络请求都不发** —— 只是把浏览器已经存好的 cookie
//   从本地 SQLite 里读出来（DPAPI + AES-GCM 解密，同一 Windows 用户可解）。
//   ★ 库被浏览器独占锁定 → 必须等用户**关掉浏览器**，脚本轮询到能读为止。
// ================================================================
async function runGrabBrowserCookie(payload = {}, event = null) {
    const script = getPythonScript('grab_browser_cookie.py');
    const pythonPath = findPython();
    if (!fs.existsSync(script) || !fs.existsSync(pythonPath)) {
        return { ok: false, error: 'Python 环境不存在' };
    }
    migrateLegacyLinovelibCookie();
    const timeout = Math.max(60, Math.min(3600, Number(payload.timeout) || 900));
    const args = ['-X', 'utf8', script, '--cookie-file', LN_COOKIE_FILE,
                  '--timeout', String(timeout), '--poll', '3'];
    logUpload('GRABCOOKIE-ARGS', JSON.stringify({ argv: args, timeout }));
    const _send = (data) => {
        try { if (event && event.sender && !event.sender.isDestroyed()) { event.sender.send('linovelib-progress', data); } } catch (e) {}
    };
    const t0 = Date.now();
    try {
        const out = await new Promise((resolve, reject) => {
            const child = execFile(pythonPath, args, {
                maxBuffer: 1024 * 1024 * 32, encoding: 'utf-8', windowsHide: true,
                timeout: (timeout + 120) * 1000,
                env: buildPythonEnv({ PYTHONIOENCODING: 'utf-8' }),
            }, (err, stdout, stderr) => {
                if (err && !stdout) { reject(err); return; }
                resolve(String(stdout || '') + '\n' + String(stderr || ''));
            });
            const _relay = (buf) => {
                String(buf).split(/\r?\n/).forEach((line) => {
                    const t = line.trim();
                    if (!t) { return; }
                    logUpload('GRABCOOKIE', t);
                    if (!t.startsWith('@@')) { _send({ stage: 'note', text: t }); }
                });
            };
            if (child && child.stdout) { child.stdout.on('data', _relay); }
            if (child && child.stderr) { child.stderr.on('data', _relay); }
        });
        const last = out.split(/\r?\n/).map(s => s.trim())
            .filter(l => l.startsWith('@@COOKIE-OK ') || l.startsWith('@@COOKIE-FAIL ')).pop();
        const res = last
            ? JSON.parse(last.slice(last.indexOf(' ') + 1))
            : { ok: false, error: '没有拿到结果行' };
        res.ms = Date.now() - t0;
        logUpload('GRABCOOKIE-RESULT', JSON.stringify({ ok: !!res.ok, browser: res.browser || null, count: res.count || 0, ms: res.ms }));
        if (res.ok) { _send({ stage: 'note', text: `✅ 已从 ${res.browser} 读到 cookie（${res.count} 项）` }); }
        return res;
    } catch (e) {
        logUpload('GRABCOOKIE-ERROR', JSON.stringify({ message: String(e.message || '').slice(0, 300) }));
        return { ok: false, error: e.message || '读浏览器 cookie 失败' };
    }
}
ipcMain.handle('linovelib-grab-browser-cookie', async () => linovelibBlocked('linovelib-grab-browser-cookie'));

// ================================================================================
// ★★★ 2026-10-02（最终方案）★★★ **用应用自己的 Electron 窗口登录，让 Chromium 自己解密 cookie**
//
//  为什么把前面两条路都废掉了：
//   · Selenium 开 Edge —— 自动化浏览器有机器人特征，站点一直拦。
//   · 从**外部**读 Edge/Chrome 的 cookie 库 —— 实测被 **App-Bound Encryption** 堵死：
//       cookie 密文前缀全是 `v20`，`Local State` 里多了 `app_bound_encrypted_key`（`APPB`，
//       flag=1 = 需要提权/浏览器身份），偏移 4/8/12 全部 `CryptUnprotectData 失败`，
//       连老的 `encrypted_key` 也一起被包了。**非管理员账号在原理上解不开**。
//
//  现在这条：**我们自己就是 Chromium** ——
//   ① 开一个应用内的 BrowserWindow（persist 分区，登录态会留着）打开 bilinovel；
//   ② 你在里面正常浏览、点到**正文页**（真人操作，Cloudflare 放行；弹验证码你也能直接点）；
//   ③ `win.webContents.session.cookies.get()` —— **由 Chromium 自己解密**，
//      再没有 ABE / 驱动 / 文件锁的问题；
//   ④ 拿到 cf_clearance 就写进 cookie 文件，窗口自动关。
//
//  比"让用户关掉整个浏览器"友好得多：不用关浏览器、不用管理员、不用装驱动。
// ================================================================================
let _lnLoginWin = null;

async function runLoginWindow(payload = {}, event = null) {
    const timeout = Math.max(60, Math.min(3600, Number(payload.timeout) || 900));
    const PART = 'persist:linovelib';        // 持久分区：下次不用重新过验证
    const _send = (data) => {
        try { if (event && event.sender && !event.sender.isDestroyed()) { event.sender.send('linovelib-progress', data); } } catch (e) {}
    };
    if (_lnLoginWin && !_lnLoginWin.isDestroyed()) {
        try { _lnLoginWin.focus(); } catch (e) {}
        return { ok: false, error: '登录窗口已经开着了 —— 请在那个窗口里点到正文页' };
    }
    migrateLegacyLinovelibCookie();
    logUpload('LOGINWIN-OPEN', JSON.stringify({ partition: PART, timeout }));
    // ★★★ 2026-10-02（用户报「你那个生成的浏览器窗口会挡住提示」）★★★
    //   默认居中会**正好压在主页面的提示弹窗上**，用户看不到"请点到正文页"。
    //   实测这台机器：屏幕工作区 2560×1392，主窗口 1350×920 **居中**（x≈605）——
    //   主窗口右边只剩 ~1190px，放不下 1180 宽的窗口，硬放就会压住弹窗。
    //   所以：**右边够宽就并排；不够就缩到右下角**（离居中的弹窗最远，几乎不重叠）。
    let wx, wy, WW = 1180, WH = 860;
    try {
        const wa = screen.getPrimaryDisplay().workArea;
        const mw = (typeof mainWindow !== 'undefined' && mainWindow && !mainWindow.isDestroyed())
            ? mainWindow.getBounds() : null;
        const rightSpace = mw ? (wa.x + wa.width) - (mw.x + mw.width) - 12 : 0;
        if (mw && rightSpace >= 900) {
            WW = Math.min(1180, rightSpace);              // ① 右边够宽 → 并排
            wx = mw.x + mw.width + 12; wy = mw.y;
        } else {
            WW = 980; WH = 820;                            // ② 不够 → 右下角，别压弹窗
            wx = Math.max(wa.x, wa.x + wa.width - WW - 8);
            wy = Math.max(wa.y, wa.y + wa.height - WH - 8);
        }
    } catch (e) { wx = undefined; wy = undefined; }        // 算不出来就让 Electron 居中
    const win = new BrowserWindow({
        width: WW, height: WH, ...(wx === undefined ? {} : { x: wx, y: wy }),
        title: '点到《正文页》—— 脚本会自动取 Cookie（不用关这个窗口）',
        autoHideMenuBar: true,
        webPreferences: { partition: PART, contextIsolation: true, nodeIntegration: false }
    });
    _lnLoginWin = win;
    // ★ 2026-10-02：**UA 对齐已撤回**（用户实测「其实没问题」）。
    //   原本这里给窗口设成 linovelib.py 那串纯 Chrome UA（想解决 cf_clearance 的 UA 绑定），
    //   但实测默认 UA 就能正常取到证并下载，所以**恢复 Electron 默认 UA**，不再改。
    //   （留个记号：如果哪天出现「证明明是新的、下两页就变 ~530 字截断版」，
    //     第一个要怀疑的就是这里的 UA 与 linovelib.py 不一致。）
    win.loadURL('https://www.bilinovel.com/');
    _send({ stage: 'note', text: '🌐 已打开登录窗口 —— 请在里面点到**正文页**（看得见正文那一页）' });

    const t0 = Date.now();
    try {
        const res = await new Promise((resolve) => {
            let done = false;
            const finish = (r) => { if (!done) { done = true; resolve(r); } };
            // ★ 每 2 秒看一次 cookie（Chromium 自己解密，不需要任何外部库）
            const timer = setInterval(async () => {
                if (win.isDestroyed()) { clearInterval(timer); finish({ ok: false, error: '登录窗口被关掉了（关之前请先点到正文页）' }); return; }
                if (Date.now() - t0 > timeout * 1000) { clearInterval(timer); finish({ ok: false, error: `等了 ${timeout} 秒还没拿到 cf_clearance —— 请在窗口里点到正文页` }); return; }
                try {
                    const sess = win.webContents.session;
                    const all = await sess.cookies.get({});
                    const mine = all.filter(c => /bilinovel\.com|linovelib\.com/.test(c.domain || ''));
                    if (!mine.length) { return; }
                    const cf = mine.find(c => c.name === 'cf_clearance');
                    if (!cf) {
                        _send({ stage: 'note', text: `⏳ 已看到 ${mine.length} 项 cookie，但还没有 cf_clearance —— 继续点到正文页…` });
                        return;
                    }
                    // ★ 判据：有 cf_clearance + 当前页是章节页（正文页）—— 证明这张证能读正文
                    let url = '';
                    try { url = win.webContents.getURL() || ''; } catch (e) {}
                    const isChapter = /\/novel\/\d+\/\d+\.html/.test(url);
                    if (!isChapter) {
                        _send({ stage: 'note', text: `⏳ 拿到 cf_clearance 了，但当前页还不是正文页（${url.slice(0, 70)}）—— 请点进正文页` });
                        return;
                    }
                    const line = mine.map(c => `${c.name}=${c.value}`).join('; ');
                    fs.mkdirSync(path.dirname(LN_COOKIE_FILE), { recursive: true });
                    fs.writeFileSync(LN_COOKIE_FILE + '.tmp', line, 'utf-8');
                    fs.renameSync(LN_COOKIE_FILE + '.tmp', LN_COOKIE_FILE);
                    clearInterval(timer);
                    logUpload('LOGINWIN-OK', JSON.stringify({ count: mine.length, url: url.slice(0, 120), ms: Date.now() - t0 }));
                    finish({ ok: true, count: mine.length, cookieFile: LN_COOKIE_FILE, url, ms: Date.now() - t0 });
                } catch (e) { /* 下一轮再试 */ }
            }, 2000);
            win.on('closed', () => { clearInterval(timer); finish({ ok: false, error: '登录窗口被关掉了（关之前请先点到正文页）' }); });
        });
        if (res.ok) { _send({ stage: 'note', text: `✅ 已取到 cookie（${res.count} 项，含 cf_clearance）` }); }
        return res;
    } finally {
        try { if (!win.isDestroyed()) { win.close(); } } catch (e) {}
        _lnLoginWin = null;
    }
}
ipcMain.handle('linovelib-open-login-window', async () => linovelibBlocked('linovelib-open-login-window'));

// ★★★ 2026-10-02（用户要求）：「现在把下载也设一个取消键，点取消可以杀掉进程」★★★
//   杀掉 `_lnActiveChild`（**精确到句柄**，不是按命令行匹配 —— 那个坑今天踩过：
//   按命令行匹配会把同一次操作的兄弟进程一起杀掉，表现为"零输出秒死"）。
//   ★ 用 `taskkill /F /T` **连整棵子进程树**一起杀：Python 可能拉起了别的子进程
//     （比如取 Cookie 时的 Edge / msedgedriver），只杀父进程会留下孤儿。
ipcMain.handle('linovelib-cancel', async () => {
    const child = _lnActiveChild;
    if (!child || child.killed) {
        logUpload('LN-CANCEL', JSON.stringify({ ok: false, reason: '没有在跑的任务' }));
        return { ok: false, error: '现在没有在跑的下载任务' };
    }
    const pid = child.pid;
    logUpload('LN-CANCEL', JSON.stringify({ ok: true, pid }));
    let treeOk = false;
    try {
        await new Promise((resolve) => {
            execFile('taskkill', ['/F', '/T', '/PID', String(pid)], { windowsHide: true },
                () => resolve());
        });
        treeOk = true;
    } catch (e) { /* 下面还会兜一层 child.kill() */ }
    try { child.kill(); } catch (e) {}
    _lnActiveChild = null;
    return { ok: true, pid, tree: treeOk };
});

// ★★★ 2026-10-02（用户实测踩到）★★★
//   「我把浏览器关了啊」—— 但任务管理器里 msedge 还有 7 个、chrome 还有 7 个。
//   Chromium 关掉所有窗口后**仍会留后台进程**（启动加速/后台模式/扩展），
//   而 cookie 库就是被它们锁着 → 脚本永远读不到。
//   给界面一个显式按钮（用户自己点，明确同意），强制结束浏览器进程。
ipcMain.handle('kill-browsers', async () => {
    const kill = (img) => new Promise((resolve) => {
        execFile('taskkill', ['/IM', img, '/F'], { windowsHide: true }, (err, out) => {
            resolve({ img, ok: !err, out: String(out || '').trim().slice(0, 300) });
        });
    });
    try {
        const results = [await kill('msedge.exe'), await kill('chrome.exe')];
        const killed = results.filter(r => r.ok).map(r => r.img);
        logUpload('KILL-BROWSERS', JSON.stringify(results));
        return { ok: true, killed, results };
    } catch (e) {
        return { ok: false, error: String(e.message || e) };
    }
});
ipcMain.handle('recommend-detail', async (event, payload) => runRecommendDetail(payload || {}));

ipcMain.handle('search-author', async (event, title) => {
    // ★ 2026-09-30：一个字的书名（《岛》）以前被 `length < 2` 静默挡掉 → 只拦空书名。
    if (!title) {
        logUpload('AUTHOR-SKIP', JSON.stringify({ reason: '书名为空' }));
        return null;
    }

    console.log('🔍 开始搜索作者:', title);

    // ★ 2026-09-29：改用 novelmeta（meta_lookup.py）查作者，**不再需要任何 API key**。
    //   纯标准库 + 公开接口，失败只体现在返回值里，不拉起任何需要授权的服务。

    try {
        const pythonScript = getPythonScript('meta_lookup.py');
        const pythonPath = findPython();

        if (fs.existsSync(pythonScript) && fs.existsSync(pythonPath)) {
            const result = await new Promise((resolve, reject) => {
                // ★ 安全修复：改用 execFile + argv 数组。
                //   原来把 title 拼进 exec 的命令串，title 里的 " 和 & 可跳出引号执行任意命令。
                const python = execFile(pythonPath,
                    ['-X', 'utf8', pythonScript, '--title', title, '--want', 'author+cover', '--json'],
                    {
                        maxBuffer: 1024 * 1024 * 50,
                        encoding: 'utf-8',
                        windowsHide: true,
                        timeout: 30000,
                        // ★ novelmeta 不需要任何 key；只注入包路径 + IPv4-only
                        //   （本机 IPv6 无出口，开启后 weread 8.40s -> 0.24s）
                        env: buildPythonEnv({
                            PYTHONIOENCODING: 'utf-8',
                            NOVELMETA_HOME: APP_ROOT,
                            NOVELMETA_IPV4_ONLY: '1'
                        })
                    },
                    (error, stdout, stderr) => {
                        if (stderr) {
                            console.log('🐍 Python stderr:', stderr);
                        }
                        if (error) {
                            reject(error);
                            return;
                        }
                        try {
                            const lines = stdout.split('\n');
                            let jsonLine = '';
                            for (const line of lines) {
                                const trimmed = line.trim();
                                if (trimmed.startsWith('{') || trimmed.startsWith('[')) {
                                    jsonLine = trimmed;
                                    break;
                                }
                            }
                            if (!jsonLine) {
                                reject(new Error('未找到 JSON 输出'));
                                return;
                            }
                            const data = JSON.parse(jsonLine);
                            resolve(data);
                        } catch (e) {
                            reject(e);
                        }
                    }
                );
            });

            if (result && result.ok && result.author) {
                console.log('✅ 搜索到作者:', result.author, '（来源', result.source, '）');
                return result.author;
            }
        }
    } catch (e) {
        console.warn('⚠️ AI 搜索作者失败:', e.message);
    }
    return null;
});

// ================================================================
// AI 搜索封面
// ================================================================

ipcMain.handle('search-cover', async (event, title, author) => {
    // ★ 2026-09-30：同上 —— 一个字书名不再被拦（《岛》），只拦空书名。
    if (!title) {
        logUpload('COVER-SKIP', JSON.stringify({ reason: '书名为空' }));
        return { success: false, error: '书名不能为空' };
    }

    // ★ 封面搜索诊断日志（2026-09-24）：用户报「找封面的功能损坏了」。
    //   排查结论：ai_cover.py 与 8 份存档逐字节相同（SHA256 D34AA47F…AB84），没被改坏；
    //   真因是 fanqienovel.com 首页 renderer 超时（Timed out receiving message from
    //   renderer: 14.3s）→ 脚本按设计返回 {"success":false,"error":"未找到封面"}，
    //   界面显示「❌ 未找到封面，请手动上传」。属站点/网络抖动，所以时好时坏。
    //   2026-09-24 深夜复测：连着两次都是 ~30 秒成功拿到 12747 字节封面（同一本书）。
    //   ★ 这套流程有一个 60 秒硬上限（下面 execFile 的 timeout + 65 秒兜底计时器），
    //     冷启动/站点慢时会被砍掉并报「Command failed」——真要报「损坏」多半是这个。
    //   这条日志把 argv / 耗时 / stderr 尾部留证，下次不用再盲猜。tag 一律 COVER-*。
    const coverT0 = Date.now();
    console.log('🔍 开始搜索封面:', title, author);

    // ★ 2026-09-29：改用 novelmeta（meta_lookup.py）找封面，**不再需要 DeepSeek API key**。
    //   原来的 ai_cover.py「联网搜封面」已退居为兜底（见 ipcMain.handle('meta-cover-fallback')）。

    try {
        const pythonScript = getPythonScript('meta_lookup.py');
        const pythonPath = findPython();
        // ★ 2026-09-25 诊断（用户报「cover 在 npmstart 成功、打包后不成功」）：
        //   ai_cover.py 的 create_driver() 一直靠 webdriver_manager **现下** chromedriver
        //   （缓存在 ~\.wdm）。开发机上缓存早就在，打包版到新电脑上可能下不动、或被墙、
        //   或版本与对方 Chrome 不匹配。所以：①把随包的 chromedriver.exe 用
        //   CHROMEDRIVER_PATH 传下去（ai_cover.py 已改成优先用它）②把「实际用了哪个
        //   driver」由 ai_cover.py 打 COVER-DRIVER 行，留给后台日志对号。
        const coverDriver = app.isPackaged
            ? path.join(process.resourcesPath, 'chromedriver.exe')
            : path.join(__dirname, 'chromedriver.exe');
        logUpload('COVER-START', JSON.stringify({
            title, author: author || '', python: pythonPath,
            frozen: app.isPackaged, appRoot: APP_ROOT, resourcesPath: process.resourcesPath,
            chromedriver: coverDriver, chromedriverExists: fs.existsSync(coverDriver),
            aCP: process.env.ACP || null, PYTHONIOENCODING: 'utf-8',
        }));

        if (!fs.existsSync(pythonScript) || !fs.existsSync(pythonPath)) {
            logUpload('COVER-RESULT', JSON.stringify({ ok: false, error: 'Python 环境不存在', ms: Date.now() - coverT0 }));
            return { success: false, error: 'Python 环境不存在' };
        }

        // ★ 安全修复：execFile + argv 数组。原来把 title/author 拼进 exec 命令串，
        //   输入里的 " 和 & 可跳出引号执行任意命令。
        // ★ 2026-09-24：补 '-X','utf8'（必须排在脚本路径前）+ PYTHONIOENCODING=utf-8，
        //   与 main.js 里其余十几处 Python 调用保持一致。注意这不是 bug 修复：
        //   ai_cover.py:16-19 自己就把 stdin/stdout/stderr 包成了 utf-8，所以编码从来
        //   没出过问题（A/B 实测：带不带 -X utf8 都是 ~30 秒返回同一张封面）。
        //   本机 ACP=936，唯一的差别是文件系统编码/默认 open() 编码也走 utf-8。
        const coverArgs = ['-X', 'utf8', pythonScript, '--title', title, '--want', 'cover', '--json'];
        if (author) coverArgs.push('--author', author);
        logUpload('COVER-ARGS', JSON.stringify({
            argv: coverArgs,
            scriptExists: fs.existsSync(pythonScript),
            timeoutMs: 60000,
            note: '封面搜索有 60 秒硬超时（含冷启动），超时会报 Command failed',
        }));

        const result = await new Promise((resolve, reject) => {
            const python = execFile(pythonPath, coverArgs,
                {
                    maxBuffer: 1024 * 1024 * 50,
                    encoding: 'utf-8',
                    windowsHide: true,
                    timeout: 60000,
                    // ★ 2026-09-29：novelmeta 不需要 key（原 DEEPSEEK_API_KEY 注入已删除）
                    env: buildPythonEnv({
                        PYTHONIOENCODING: 'utf-8',
                        NOVELMETA_HOME: APP_ROOT,
                        NOVELMETA_IPV4_ONLY: '1',
                        // ★ 2026-09-25 保留：随包 chromedriver 路径给随后的 se 兜底用
                        //   （meta-cover-fallback → ai_cover.py --fallback-search）
                        CHROMEDRIVER_PATH: coverDriver
                    })
                },
                (error, stdout, stderr) => {
                    if (stderr) {
                        console.log('🐍 封面搜索 stderr:', stderr);
                        try { logUpload('COVER-STDERR', String(stderr).trim().slice(-1500)); } catch (_) {}
                    }
                    if (error) {
                        console.log('🐍 封面搜索 error:', error.message);
                        // ★ 2026-09-25：把「怎么失败的」落到后台日志（以前只在控制台，
                        //   打包版用户看不到）。killed=true 说明是 60 秒超时被杀。
                        try {
                            logUpload('COVER-ERROR', JSON.stringify({
                                message: String(error.message || '').slice(0, 500),
                                code: error.code || null,
                                signal: error.signal || null,
                                killed: !!error.killed,
                                ms: Date.now() - coverT0,
                                stdoutTail: String(stdout || '').trim().slice(-800),
                                stderrTail: String(stderr || '').trim().slice(-800),
                            }));
                        } catch (_) {}
                        reject(error);
                        return;
                    }
                    try {
                        const lines = stdout.split('\n');
                        let jsonLine = '';
                        for (const line of lines) {
                            const trimmed = line.trim();
                            if (trimmed.startsWith('{')) {
                                jsonLine = trimmed;
                                break;
                            }
                        }
                        if (!jsonLine) {
                            reject(new Error('未找到 JSON 输出'));
                            return;
                        }
                        const data = JSON.parse(jsonLine);
                        resolve(data);
                    } catch (e) {
                        reject(e);
                    }
                }
            );
            
            setTimeout(() => {
                python.kill('SIGTERM');
                reject(new Error('搜索超时（60秒）'));
            }, 65000);
        });

        if (result && result.ok && result.cover_data) {
            console.log('✅ 找到封面（来源', result.source, '）');
            logUpload('COVER-RESULT', JSON.stringify({
                ok: true, ms: Date.now() - coverT0, source: result.source,
                title: result.title, cover: String(result.cover || '').slice(0, 120),
            }));
            // 维持旧契约：渲染层要的是 { success, cover_data }；
            // 顺手把书名/作者/候选带回去，便于回填与多作者弹窗。
            return {
                success: true,
                cover_data: result.cover_data,
                title: result.title || '',
                author: result.author || '',
                source: result.source || '',
                cover: result.cover || '',
                ambiguous: !!result.ambiguous,
                candidates: result.candidates || [],
            };
        } else {
            const errorMsg = (result && result.error) || '未找到封面';
            console.log('⚠️ 搜索失败:', errorMsg);
            logUpload('COVER-RESULT', JSON.stringify({ ok: false, error: errorMsg, ms: Date.now() - coverT0 }));
            return { success: false, error: errorMsg, has_author: !!(result && result.has_author) };
        }
    } catch (e) {
        console.warn('⚠️ AI 搜索封面失败:', e.message);
        logUpload('COVER-RESULT', JSON.stringify({ ok: false, error: e.message || '搜索失败', ms: Date.now() - coverT0 }));
        return { success: false, error: e.message || '搜索失败' };
    }
});

// ================================================================
// 代理加载图片
// ================================================================

ipcMain.handle('fetch-image', async (event, url) => {
    if (!url) return { success: false, error: 'URL 为空' };
    
    try {
        const response = await fetch(url, {
            headers: {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                'Referer': 'https://book.douban.com/'
            }
        });
        
        if (!response.ok) {
            throw new Error(`HTTP ${response.status}`);
        }
        
        const buffer = await response.arrayBuffer();
        const base64 = Buffer.from(buffer).toString('base64');
        const contentType = response.headers.get('content-type') || 'image/jpeg';
        
        return {
            success: true,
            data: `data:${contentType};base64,${base64}`
        };
    } catch (e) {
        console.error('图片加载失败:', e.message);
        return { success: false, error: e.message };
    }
});

// ================================================================
// 调用 Python 分章
// ================================================================

function callPythonSplitter(txtPath, customPattern, maxTitleLength, useAIFilter) {
    return new Promise((resolve, reject) => {
        const pythonScript = getPythonScript('epub_generator.py');
        const pythonPath = findPython();

        const inputData = JSON.stringify({
            txt_path: txtPath,
            custom_pattern: customPattern || null,
            max_title_length: maxTitleLength || SETTINGS.maxTitleLength,
            use_ai_filter: useAIFilter !== undefined ? useAIFilter : true,
            preview_only: true
        });

        console.log('📊 调用 Python 分章（预览模式）');
        console.log('📄 TXT 路径:', txtPath);
        console.log('📄 最大标题长度:', maxTitleLength || 40);
        console.log('📄 AI 过滤(已废弃):', useAIFilter !== undefined ? useAIFilter : true, '→ 现在统一用纯正则');

        const python = exec(`"${pythonPath}" -X utf8 "${pythonScript}"`, {
            maxBuffer: 1024 * 1024 * 50
        }, (error, stdout) => {
            if (error) {
                console.error('❌ Python 分章执行错误:', error);
                reject({ error: error.message });
                return;
            }
            try {
                const result = JSON.parse(stdout);
                if (result.success) {
                    resolve(result);
                } else {
                    reject({ error: result.error });
                }
            } catch (e) {
                console.error('❌ 解析失败:', stdout);
                reject({ error: '解析失败: ' + e.message });
            }
        });

        python.stdin.write(inputData);
        python.stdin.end();
    });
}

// ================================================================
// 调用 Python 生成 EPUB
// ================================================================

function callPythonEpubGenerator(title, author, txtPath, outputPath, coverPath, options) {
    return new Promise((resolve, reject) => {
        const pythonScript = getPythonScript('epub_generator.py');
        const pythonPath = findPython();

        let chapterMarkers = [];
        if (options && options.chapter_markers && options.chapter_markers.length > 0) {
            chapterMarkers = options.chapter_markers.filter(m => m.active !== false).map(m => ({
                title: m.title,
                line: m.line
            }));
        }

        const inputData = JSON.stringify({
            title: title || '未命名',
            author: author || '未知作者',
            txt_path: txtPath,
            output_path: outputPath,
            cover_path: coverPath || null,
            custom_pattern: (options && options.customPattern) || null,
            chapter_markers: chapterMarkers,
            max_title_length: (options && options.maxTitleLength) || SETTINGS.maxTitleLength,
            use_ai_filter: (options && options.use_ai_filter !== undefined) ? options.use_ai_filter : true,
            generate_epub: true,
            language: 'zh-CN',
            publisher: '',
            description: ''
        });

        console.log('🐍 调用 Python 生成 EPUB');
        console.log('📄 TXT 路径:', txtPath);
        console.log('📄 输出路径:', outputPath);
        console.log('📄 封面路径:', coverPath || '无');
        console.log('📄 章节标记数:', chapterMarkers.length);
        console.log('📄 最大标题长度:', (options && options.maxTitleLength) || 40);
        console.log('📄 AI 过滤(已废弃):', (options && options.use_ai_filter !== undefined) ? options.use_ai_filter : true, '→ 现在统一用纯正则');

        const python = exec(`"${pythonPath}" -X utf8 "${pythonScript}"`, {
            maxBuffer: 1024 * 1024 * 50
        }, (error, stdout) => {
            if (error) {
                console.error('🐍 Python 执行错误:', error);
                reject({ error: error.message });
                return;
            }
            try {
                const lines = stdout.split('\n');
                let jsonLine = '';
                for (const line of lines) {
                    const trimmed = line.trim();
                    if (trimmed.startsWith('{') && trimmed.includes('success')) {
                        jsonLine = trimmed;
                        break;
                    }
                }
                if (!jsonLine) {
                    console.error('❌ 未找到 JSON 输出，完整 stdout:', stdout);
                    reject({ error: '未找到 JSON 输出' });
                    return;
                }
                const result = JSON.parse(jsonLine);
                if (result.success) {
                    resolve(result);
                } else {
                    reject({ error: result.error || '未知错误' });
                }
            } catch (e) {
                console.error('🐍 解析失败:', stdout);
                reject({ error: '解析失败: ' + e.message });
            }
        });

        python.stdin.write(inputData);
        python.stdin.end();
    });
}

// ================================================================
// 预览章节
// ================================================================

ipcMain.handle('preview-chapters', async (event, txtPath, customPattern, maxTitleLength, useAIFilter) => {
    console.log('📊 预览章节请求:', txtPath);
    
    if (!txtPath || !fs.existsSync(txtPath)) {
        throw new Error('文件不存在');
    }
    
    let actualTxtPath = txtPath;
    try {
        const convertResult = await callEncodingConverter(txtPath);
        if (convertResult.success && convertResult.output_path) {
            actualTxtPath = convertResult.output_path;
            console.log('✅ 编码转换完成');
        }
    } catch (err) {
        console.warn('⚠️ 编码转换失败，尝试继续:', err);
    }
    
    const result = await callPythonSplitter(actualTxtPath, customPattern, maxTitleLength, useAIFilter);
    return result;
});

// ================================================================
// ★★★ 超分处理 ★★★
// ================================================================

async function enhanceCoverImage(imagePath) {
    if (!imagePath || !fs.existsSync(imagePath)) {
        return imagePath;
    }

    console.log('🔍 开始封面超分处理...');
    try {
        const pythonScript = getPythonScript('image_enhancer.py');
        const pythonPath = findPython();

        if (!fs.existsSync(pythonScript) || !fs.existsSync(pythonPath)) {
            console.warn('⚠️ image_enhancer.py 或 Python 环境不存在，跳过超分');
            return imagePath;
        }

        const inputData = JSON.stringify({
            image_path: imagePath,
            magnify: SETTINGS.coverMagnify
        });

        const result = await new Promise((resolve, reject) => {
            const python = exec(
                `"${pythonPath}" -X utf8 "${pythonScript}"`,
                {
                    maxBuffer: 1024 * 1024 * 50,
                    encoding: 'utf-8',
                    windowsHide: true,
                    timeout: 120000
                },
                (error, stdout, stderr) => {
                    if (stderr) {
                        console.log('🐍 超分 stderr:', stderr);
                    }
                    if (error) {
                        reject(error);
                        return;
                    }
                    try {
                        const lines = stdout.split('\n');
                        let jsonLine = '';
                        for (const line of lines) {
                            const trimmed = line.trim();
                            if (trimmed.startsWith('{')) {
                                jsonLine = trimmed;
                                break;
                            }
                        }
                        if (!jsonLine) {
                            reject(new Error('未找到 JSON 输出'));
                            return;
                        }
                        const data = JSON.parse(jsonLine);
                        resolve(data);
                    } catch (e) {
                        reject(e);
                    }
                }
            );
            python.stdin.write(inputData);
            python.stdin.end();
        });

        if (result && result.success) {
            console.log('✅ 封面超分成功:', result.message);
            if (result.width_before && result.width_after) {
                console.log(`   ${result.width_before}x${result.height_before} → ${result.width_after}x${result.height_after}`);
            }
            // ★ 超分可能换了文件（PNG → <原名>_sr.jpg），必须把新路径交回去，
            //   否则后面 pandoc 会按旧扩展名声明 media-type 而拿到 JPEG 内容。
            return result.output_path || imagePath;
        } else {
            console.warn('⚠️ 封面超分失败:', result ? result.message : '未知错误');
            return imagePath;
        }
    } catch (err) {
        console.warn('⚠️ 超分处理异常:', err.message);
        return imagePath;
    }
}

// ================================================================
// 转换 EPUB（含封面超分）
// ================================================================

ipcMain.handle('convert-txt-to-epub', async (event, txtPath, options) => {
    console.log('========================================');
    console.log('📄 转换请求');
    console.log('📄 txtPath:', txtPath);
    console.log('📄 章节标记数:', options && options.chapter_markers ? options.chapter_markers.length : 0);
    console.log('📄 最大标题长度:', (options && options.maxTitleLength) || 40);
    console.log('📄 封面:', (options && options.coverImage) ? '有' : '无');
    console.log('📄 AI 过滤(已废弃):', (options && options.use_ai_filter !== undefined) ? options.use_ai_filter : true, '→ 现在统一用纯正则');
    console.log('========================================');

    if (!txtPath || typeof txtPath !== 'string') {
        throw new Error('文件路径无效');
    }
    if (!fs.existsSync(txtPath)) {
        throw new Error(`文件不存在: ${txtPath}`);
    }
    const stats = fs.statSync(txtPath);
    if (!stats.isFile()) {
        throw new Error(`路径不是文件: ${txtPath}`);
    }

    let actualTxtPath = txtPath;
    try {
        const convertResult = await callEncodingConverter(txtPath);
        if (convertResult.success && convertResult.output_path) {
            actualTxtPath = convertResult.output_path;
            console.log('✅ 编码转换完成');
        }
    } catch (err) {
        console.warn('⚠️ 编码转换失败，使用原文件:', err);
    }

    const outputFileName = `${((options && options.title) || '未命名').replace(/[\\/:*?"<>|]/g, '_')}.epub`;
    // 输出目录：设置里指定了就写那儿，否则跟源文件放一起
    const outputDir = (SETTINGS.outputDir || '').trim() || path.dirname(actualTxtPath);
    try { if (!fs.existsSync(outputDir)) fs.mkdirSync(outputDir, { recursive: true }); } catch (e) {}
    const outputPath = path.join(outputDir, outputFileName);

    // 处理封面
    let coverFilePath = null;
    const coverImage = options && options.coverImage;
    if (coverImage) {
        try {
            if (typeof coverImage === 'string' && coverImage.startsWith('data:image')) {
                const matches = coverImage.match(/^data:image\/(\w+);base64,(.+)$/);
                if (matches) {
                    // ★ 安全修复：扩展名原来直接取 (\w+)，可写出 .exe/.bat 等任意扩展名到输出目录。
                    //   配合 library-reveal 的 shell.openPath 就是一条「写入 + 执行」链。
                    const ALLOWED_COVER_EXT = ['jpg', 'jpeg', 'png', 'webp', 'gif', 'bmp'];
                    let ext = String(matches[1]).toLowerCase();
                    if (ext === 'jpeg') ext = 'jpg';
                    if (!ALLOWED_COVER_EXT.includes(ext)) {
                        console.warn(`⚠️ 封面格式不被允许（${matches[1]}），跳过封面`);
                        ext = null;
                    }
                    if (!ext) throw new Error('封面扩展名不在白名单内');
                    const base64Data = matches[2];
                    const tempCoverPath = path.join(outputDir, `_temp_cover_${Date.now()}.${ext}`);
                    fs.writeFileSync(tempCoverPath, Buffer.from(base64Data, 'base64'));
                    coverFilePath = tempCoverPath;
                    console.log('✅ 封面已保存为临时文件:', coverFilePath);
                }
            } else if (typeof coverImage === 'string' && fs.existsSync(coverImage)) {
                coverFilePath = coverImage;
                console.log('✅ 使用封面文件:', coverFilePath);
            } else {
                console.log('⚠️ 封面数据格式未知，跳过');
            }
        } catch (err) {
            console.warn('⚠️ 保存封面失败:', err);
        }
    }

    // ★★★ 调用超分处理封面 ★★★
    // 超分后可能换了文件（PNG → <原名>_sr.jpg），必须接收新路径
    if (coverFilePath && fs.existsSync(coverFilePath)) {
        const enhancedPath = await enhanceCoverImage(coverFilePath);
        if (enhancedPath && fs.existsSync(enhancedPath)) {
            coverFilePath = enhancedPath;
        }
    }

    const safeOptions = options || {};
    const chapterMarkers = safeOptions.chapter_markers || [];
    const maxTitleLength = safeOptions.maxTitleLength || 25;
    const useAIFilter = safeOptions.use_ai_filter !== undefined ? safeOptions.use_ai_filter : true;
    const customPattern = safeOptions.customPattern || null;

    console.log('📤 调用 Python 生成 EPUB...');
    const result = await callPythonEpubGenerator(
        safeOptions.title || '未命名',
        safeOptions.author || '未知作者',
        actualTxtPath,
        outputPath,
        coverFilePath,
        {
            customPattern: customPattern,
            chapter_markers: chapterMarkers,
            maxTitleLength: maxTitleLength,
            use_ai_filter: useAIFilter
        }
    );

    if (coverFilePath && coverFilePath.includes('_temp_cover_')) {
        try {
            fs.unlinkSync(coverFilePath);
            console.log('✅ 临时封面已清理');
        } catch (err) {
            console.warn('⚠️ 清理临时封面失败:', err);
        }
    }

    console.log('✅ 转换完成，输出:', outputPath);

    // ★★★ 自动导出：txt + epub 副本到 EXPORT_DIR（永不清空，不影响软件运行）★★★
    const finalEpubPath = result.output_path || outputPath;
    exportDownloadedFiles(actualTxtPath, finalEpubPath);

    return {
        success: true,
        outputPath: result.output_path || outputPath,
        chapterCount: result.chapter_count || 0
    };
});

// ================================================================
// ★★★ 下载小说：2026-09-29 换成 novel_dl.py（2 书源纯 HTTP 聚合） ★★★
//   旧实现是 Selenium 浏览器自动化 + pyautogui 固定坐标点击 + DeepSeek 判来源，
//   又慢又脆还要 API key，已整体替换。
//   源顺序（命中即停）：笔尖中文 → 悠久小说网
// ================================================================

const PYTHON_PATH = findPython();

// 调用 novel_dl.py 下载小说
// 契约不变：argv = ['-X','utf8', <脚本>, '--download-dir=<目录>', <书名>, '--author=<作者>']
//           stdout 的 {"notFound":true}/{"success":false} 行仍是失败信号，
//           成功仍以"下载目录里出现本次新增的 .txt"判定（下面那段逻辑没动）
function callNovelDownloader(bookName, opts = {}) {
    // ★ 2026-10-01（用户：「我搜无职转生他怎么不给我跳选搜索结果和选卷，直接就开始下了」）：
    //   `pickLightNovel=true` 时给 novel_dl.py 加 `--ln-picker` —— 命中轻小说**不直接下**，
    //   只把书号交回来，由渲染层弹【轻小说】选卷窗（候选列表 + 分卷勾选）。
    //   仍然只在"三个网文源全失败"之后才查轻小说，网文速度不受影响。
    const pickLightNovel = !!opts.pickLightNovel;
    return new Promise((resolve) => {
        // ★★★ 打包后 .py 文件在 app.asar.unpacked/ 目录下
        // 用 process.resourcesPath（指向 resources/） + 'app.asar.unpacked'
        const pythonScript = app.isPackaged
            ? path.join(process.resourcesPath, 'app.asar.unpacked', 'novel_dl.py')
            : path.join(__dirname, 'novel_dl.py');
        const downloadDir = DOWNLOAD_DIR;

        // ★ 修复「假成功」：记下本次下载的开始时刻。
        //   原来只按 mtime 取目录里最新的 txt，上次残留的旧书会被当成这次的成果 →
        //   界面报"下载成功"却拿到错的书。现在只认本次新出现的文件。
        //   （下面还会清空 download/，但扫描口径必须独立成立，不能依赖清空成功）
        const downloadStartedAt = Date.now();

        // ★ 下载前清空 download/（避免上次下载残留干扰）
        try {
            if (fs.existsSync(downloadDir)) {
                const oldFiles = fs.readdirSync(downloadDir);
                let cleared = 0;
                for (const f of oldFiles) {
                    const fp = path.join(downloadDir, f);
                    try {
                        if (fs.statSync(fp).isFile()) {
                            fs.unlinkSync(fp);
                            cleared++;
                        }
                    } catch (e) {}
                }
                console.log(`🧹 下载前清理 download/：已删除 ${cleared} 个文件`);
            }
        } catch (e) {
            console.log('⚠️ 下载前清理失败：', e.message);
        }

        if (!fs.existsSync(pythonScript)) {
            resolve({ success: false, error: 'novel_dl.py 不存在' });
            return;
        }

        // 确保下载目录存在
        try { fs.mkdirSync(downloadDir, { recursive: true }); } catch (e) {}

        console.log('📥 启动下载器下载:', bookName);
        console.log('📁 保存到:', downloadDir);

        // ★★★ 拆分书名@作者（用户输入格式：吞没@泡泡藻）—— 不要用空格分隔，避免和"书名带空格"冲突
        const sepIdx = bookName.indexOf('@');
        let titlePart, authorPart = '';
        if (sepIdx >= 0) {
            titlePart = bookName.slice(0, sepIdx).trim();
            authorPart = bookName.slice(sepIdx + 1).trim();
        } else {
            titlePart = bookName.trim();
        }
        console.log(`📖 拆分结果: 书名="${titlePart}" 作者="${authorPart}"`);

        // ★ 修复空格路径 bug：用两个独立 argv 参数，不做任何分隔符拼接
        const args = [
            '-X', 'utf8',
            pythonScript,
            `--download-dir=${downloadDir}`,
            titlePart   // ★ 纯书名作单个 argv，不再拼接 \x00
        ];
        if (authorPart) {
            args.push(`--author=${authorPart}`);  // 作者独立 argv
        }
        // ★★★ 2026-10-01（接回模块化后的 linovelib.py）★★★
        //   [轻小说] 这一路是 novel_dl.py **内部再起一个进程**跑 linovelib.py 的 ——
        //   命令行的"下载小说"按钮若不带下面三个参数，新脚本的
        //   「自动续 Cookie / 指定 Cookie 文件 / 切域名」在这条路上永远用不上：
        //     · cf_clearance 一过期就只会报「正文被站方截断」，用户得自己去浏览器重拷 Cookie；
        //     · 不指 cookie 文件 → 续期写脚本目录，打包后是 Program Files（不可写）。
        //   autoCookie 默认开：这是"下载小说"兜底路径，用户不会盯着，让它自己救活更合理。
        migrateLegacyLinovelibCookie();
        args.push('--cookie-file', LN_COOKIE_FILE);
        if (SETTINGS.linovelibAutoCookie !== false) { args.push('--auto-cookie'); }
        if (SETTINGS.linovelibBase) { args.push('--ln-base', String(SETTINGS.linovelibBase)); }
        if (pickLightNovel) { args.push('--ln-picker'); }

        const python = execFile(PYTHON_PATH, args,
            {
                maxBuffer: 1024 * 1024 * 50,
                encoding: 'utf-8',
                // ★ 2026-09-29：下载路径改为 novel_dl.py（纯 HTTP 2 书源聚合）后
                //   **不再需要任何 API key** —— DeepSeek 已从全项目的作者/封面/下载三处清零。
                env: buildPythonEnv({
                    PATH: __dirname + ';' + (process.env.PATH || ''),
                    // ★ 2026-10-01：插图中转目录。novel_dl.py 会把它继承给孙进程 linovelib.py
                    //   （脚本自己也认这个变量，写不了才退到 %TEMP%）。
                    //   不指的话默认写**脚本目录**，打包后是 Program Files → 建目录就失败。
                    LINOVELIB_WORK_DIR: LN_WORK_DIR
                })
            },
            (error, stdout, stderr) => {
                if (stdout) console.log('🐍 stdout:', stdout.substring(0, 500));
                if (stderr) console.log('🐍 stderr:', stderr.substring(0, 500));

                // ★★★ 检查 stdout 中是否有"未找到本书"或"浏览器已关闭"的特殊标记 ★★★
                let notFoundMessage = null;
                let pyErrorMessage = null;  // ★ Python 通过 _emit_error 输出的 JSON 错误消息
                let pyFailedChapters = 0;   // ★ 2026-09-30：轻小说源"有 N 章没抓到"（站点限流）
                let needPick = null;        // ★ 2026-10-01：轻小说命中，请界面弹选卷窗
                try {
                    if (stdout) {
                        const lines = stdout.split(/\r?\n/);
                        console.log(`🐍 检测 ${lines.length} 行 stdout`);
                        for (const line of lines) {
                            const trimmed = line.trim();
                            if (!trimmed) continue;
                            // ★ 调试：只对疑似 JSON 行才尝试解析
                            if (!trimmed.includes('{') && !trimmed.startsWith('{')) continue;
                            try {
                                const obj = JSON.parse(trimmed);
                                console.log('🐍 解析到 JSON:', JSON.stringify(obj));
                                // ★ 2026-10-01：轻小说命中且开了 --ln-picker → 交给界面弹选卷窗
                                if (obj && obj.needPick === true && obj.novelId) {
                                    needPick = obj;
                                    break;
                                }
                                if (obj && obj.notFound === true) {
                                    notFoundMessage = obj.error || '未找到本书';
                                    break;
                                }
                                if (obj && obj.success === false && obj.error) {
                                    // ★ Python 的 _emit_error 输出的 {"success": false, "error": "..."}
                                    pyErrorMessage = obj.error;
                                    break;
                                }
                                if (obj && obj.success === true && obj.failed) {
                                    // ★ 轻小说路径：抓取时被站点限流、有章没抓到 → 如实上报给界面
                                    pyFailedChapters = Array.isArray(obj.failed) ? obj.failed.length : 1;
                                }
                            } catch (parseErr) {
                                // 不是 JSON 行，跳过
                            }
                        }
                    }
                } catch (e) {
                    // ignore
                }
                if (needPick) {
                    console.log(`🎯 命中轻小说：${needPick.title}（书号 ${needPick.novelId}）→ 交给界面选卷`);
                    resolve({
                        success: false,
                        needPick: true,
                        novelId: String(needPick.novelId),
                        title: needPick.title || '',
                        author: needPick.author || '',
                        // ★ 界面输入框要回填**书名**：输纯数字会被当成"按书号查"，
                        //   直接跳过"搜索结果候选列表"那一步（用户实测报的就是这个）。
                        query: needPick.query || needPick.title || '',
                        filePath: null,
                        fileName: null
                    });
                    return;
                }
                if (notFoundMessage) {
                    console.log(`✅ 命中 notFound 标记：${notFoundMessage}`);
                    resolve({
                        success: false,
                        notFound: true,
                        error: notFoundMessage,
                        filePath: null,
                        fileName: null
                    });
                    return;
                }

                // 找最新生成的 txt 文件
                let downloadedFile = null;
                try {
                    if (fs.existsSync(downloadDir)) {
                        const allTxt = fs.readdirSync(downloadDir);
                        const freshTxt = allTxt.filter(f => {
                            // ★ 2026-09-30：轻小说源（novel_dl 里的 [轻小说] 一路）产出的是 **.epub**，
                            //   不是 .txt —— 一起收，否则会被当成"没有产物"而误报失败。
                            if (!f.endsWith('.txt') && !f.endsWith('.epub')) return false;
                            try {
                                const st = fs.statSync(path.join(downloadDir, f));
                                return Math.max(st.mtimeMs, st.birthtimeMs || 0) >= downloadStartedAt;
                            } catch (e) { return false; }
                        });
                        const staleTxt = allTxt.length - freshTxt.length;
                        if (staleTxt > 0) {
                            console.log(`⚠️ 下载目录里有 ${staleTxt} 个非本次生成的文件，已忽略（不计入成功判定）`);
                        }
                        const files = freshTxt
                            .map(f => ({
                                name: f,
                                path: path.join(downloadDir, f),
                                mtime: fs.statSync(path.join(downloadDir, f)).mtimeMs
                            }))
                            .sort((a, b) => b.mtime - a.mtime);
                        if (files.length > 0) {
                            downloadedFile = files[0];
                        }
                    }
                } catch (e) {
                    console.log('⚠️ 扫描下载目录失败:', e.message);
                }

                const exitOk = !error || error.code === 0;
                if (exitOk && downloadedFile) {
                    console.log('✅ 下载流程完成:', downloadedFile.name);
                    // ★★★ 自动导出：刚下载的产物副本到 EXPORT_DIR（永不清空）★★★
                    //   ★ 2026-09-30：轻小说源产出的是**带封面的 EPUB**（不走 TXT 那条路），
                    //     所以这里按扩展名决定 label，并把 kind='epub' 透传给渲染层 ——
                    //     渲染层据此**跳过 TXT→EPUB 转换**，只刷新书库 + 把书加进传输框。
                    const isEpub = String(downloadedFile.name).toLowerCase().endsWith('.epub');
                    exportFileToExportDir(downloadedFile.path, { label: isEpub ? 'EPUB' : 'TXT' });
                    resolve({
                        success: true,
                        kind: isEpub ? 'epub' : 'txt',
                        // EPUB 已经进书库了 → 直接把书库里的路径给渲染层（传输框要用它）
                        filePath: isEpub
                            ? path.join(getExportDir(), downloadedFile.name)
                            : downloadedFile.path,
                        fileName: downloadedFile.name,
                        failedChapters: pyFailedChapters,   // ★ 轻小说限流导致没抓到的章数
                        output: stdout
                    });
                } else if (exitOk && !downloadedFile) {
                    resolve({
                        success: false,
                        error: '本次下载未产出新的 txt 文件（已忽略 download 目录里的旧文件）。'
                             + '常见原因：书名/作者没匹配到该书，或下载被中断。',
                        filePath: null,
                        fileName: null
                    });
                } else {
                    console.error('❌ 下载执行错误:', error);
                    // ★ 优先用 Python 通过 _emit_error 输出的简洁 JSON 错误消息
                    //   而不是 Node execFile 的 "Command failed: ..." 长串
                    const friendlyError = pyErrorMessage
                        || (error && error.message)
                        || '下载失败';
                    resolve({
                        success: false,
                        error: friendlyError,
                        filePath: downloadedFile?.path || null,
                        fileName: downloadedFile?.name || null
                    });
                }
            }
        );

        // ★ 2026-09-29：把子进程输出**实时**推给界面。
        //   为什么必须做：长书 1000+ 章要跑几分钟到十几分钟，而原来界面全程毫无动静 →
        //   用户以为卡死就把窗口关了（实测缓存里其实已经下了 40 章）。
        //   进度行不带 `{`，不会干扰上面那段 JSON 契约扫描。
        // ★ 2026-09-29 诊断：把转发给界面的进度行**带时间戳落盘**。
        //   背景：用户反馈“进度条卡在 30% 然后最后一秒直接跳 100%”。
        //   有了这个文件就能分辨：行在这里是连续的（→界面渲染问题），
        //   还是到某一刻就断了（→管道/投递问题）。
        // ★★★ 2026-09-30（同类 bug：打包后路径假设失效）：
        //   原来写 `path.join(__dirname, '..', '_dl-progress.log')` —— 打包后 `__dirname` 在
        //   `resources\app.asar` 内部，`..` 就是 `resources\`，**等于往安装目录里丢调试日志** ✗。
        //   这是排查「进度条不动」时的诊断文件，**只在开发模式写**；打包版不写（保持安装目录干净），
        //   打包版要看进度就靠 `logs\upload-*.log`（渲染层 console 已经转发进去了）。
        const dlProgressLog = app.isPackaged ? null : path.join(RUN_ROOT, '_dl-progress.log');
        const pushDownloadLog = (buf) => {
            const chunkText = buf.toString('utf-8');
            try {
                if (dlProgressLog && (chunkText.indexOf('@@PROGRESS') >= 0 || chunkText.indexOf('进度 ') >= 0)) {
                    fs.appendFileSync(dlProgressLog, new Date().toISOString() + ' ' + chunkText);
                }
            } catch (e) { /* 写不进就算了 */ }
            try {
                if (mainWindow && !mainWindow.isDestroyed()) {
                    mainWindow.webContents.send('download-log', buf.toString('utf-8'));
                }
            } catch (e) { /* 窗口已关就算了 */ }
        };
        if (python && python.stdout) python.stdout.on('data', pushDownloadLog);
        if (python && python.stderr) python.stderr.on('data', pushDownloadLog);
    });
}

ipcMain.handle('download-novel', async (event, bookName, opts) => {
    // ★★★★ 阉割版（2026-10-02）：下载执行已移除 —— 界面保留，点了弹法规提示 ★★★★
    console.log('⛔ [阉割版] 下载小说请求被拦截（该部分暂不开放使用）:', bookName);
    return linovelibBlocked('download-novel');
});

// ================================================================
// ★★★ v3 增量：苹果图书功能（百度网盘上传 + 授权）★★★
// ================================================================

const APPLEBOOKS_DOWNLOAD_DIR = DOWNLOAD_DIR;

// 授权进程句柄
let currentAuthProcess = null;
// WiFi 传书进程句柄（★ 2026-09-23 新增：以前这里没有句柄，关 WiFi 窗口时
// 只能 `taskkill /f /im chromedriver.exe` 全局杀 —— 会把「授权流程」正在用的
// chromedriver 一起杀掉，连带毁掉正在抓授权码的浏览器。改成按进程树杀。）
let currentWifiProcess = null;
let currentWifiDone = null;   // 让超时分支能标记「已收工」

// 1. 选择文件
ipcMain.handle('applebooks-select-files', async () => {
    try {
        if (!fs.existsSync(APPLEBOOKS_DOWNLOAD_DIR)) {
            fs.mkdirSync(APPLEBOOKS_DOWNLOAD_DIR, { recursive: true });
        }
        const result = await dialog.showOpenDialog(mainWindow, {
            title: '选择要放入苹果图书的文件',
            defaultPath: APPLEBOOKS_DOWNLOAD_DIR,
            properties: ['openFile', 'multiSelections'],
            filters: [
                { name: '图书文件', extensions: ['epub', 'txt', 'pdf', 'mobi', 'azw3'] },
                { name: '所有文件', extensions: ['*'] }
            ]
        });
        if (result.canceled || !result.filePaths.length) {
            return { success: false, canceled: true, files: [] };
        }
        return { success: true, canceled: false, files: result.filePaths };
    } catch (e) {
        return { success: false, error: e.message };
    }
});

// 2. 复制文件到 download
ipcMain.handle('applebooks-copy-files', async (event, filePaths) => {
    try {
        if (!Array.isArray(filePaths) || filePaths.length === 0) {
            return { success: false, error: '未选择文件' };
        }
        if (!fs.existsSync(APPLEBOOKS_DOWNLOAD_DIR)) {
            fs.mkdirSync(APPLEBOOKS_DOWNLOAD_DIR, { recursive: true });
        }
        const results = [];
        for (const src of filePaths) {
            if (!fs.existsSync(src)) {
                results.push({ src, success: false, error: '文件不存在' });
                continue;
            }
            const name = path.basename(src);
            const finalDst = path.join(APPLEBOOKS_DOWNLOAD_DIR, name);
            // ★ 2026-09-26：同名**覆盖**，不再生成 书名_1.epub（内容相同则跳过复制）
            if (!(fs.existsSync(finalDst) && sameFileContent(src, finalDst))) {
                fs.copyFileSync(src, finalDst);
            }
            results.push({ src, dst: finalDst, success: true, name: path.basename(finalDst) });
        }
        const okCount = results.filter(r => r.success).length;
        return {
            success: okCount > 0,
            copied: okCount,
            total: filePaths.length,
            results,
            downloadDir: APPLEBOOKS_DOWNLOAD_DIR
        };
    } catch (e) {
        return { success: false, error: e.message };
    }
});

// 3. 上传到百度网盘（实时回传日志）
ipcMain.handle('applebooks-upload', async (event, options = {}) => {
    let localDir = options.localDir || APPLEBOOKS_DOWNLOAD_DIR;
    const remoteDir = options.remoteDir || SETTINGS.baiduRemoteDir || '/epub_download_backup';
    const ondup = options.ondup || SETTINGS.baiduOnDup || 'overwrite';

    // ★ 2026-09-25（用户要求「先复制到临时文件夹、再对临时文件夹传输」）：
    //   不管从哪进来（队列 / 一键 / 旧界面选文件），都先统一暂存到 sendqueue\，
    //   再由 bypy 只对这个目录上传。来源、清单、以及「队列属于哪个通道」
    //   都写进 logs\upload-<日期>.log 的 STAGE 行。
    //   ★ 2026-09-24 的约束仍然成立：upload_folder_cmd.py 是逐文件强制覆盖上传，
    //   所以暂存目录必须**只装本次要传的书**（每次发送前清空），否则历史书会被一起重传。
    const wantPaths = Array.isArray(options.paths)
        ? options.paths.filter((p) => typeof p === 'string' && fs.existsSync(p))
        : [];
    const staged = stageTransferFiles(wantPaths, 'applebooks');
    if (!staged.ok) {
        console.warn('☁️ 上传中止：统一暂存失败 →', staged.error);
        logUpload('ABORT', '统一暂存失败 → ' + staged.error);
        return {
            success: false,
            empty: true,
            logFile: uploadLogPath(),
            error: staged.error + '。先在「书库」勾选书籍并点「批量传输」（队列里要有书），回到传输页再点发送。'
        };
    }
    localDir = staged.dir;

    console.log('☁️ 苹果图书上传:', { localDir, remoteDir, ondup, files: wantPaths.length });
    logUpload('START', JSON.stringify({
        localDir, remoteDir, ondup, files: staged.files.length,
        stageReason: staged.reason, stageFiles: staged.files,
        paths: wantPaths, exec: `${PYTHON_PATH} -X utf8 upload_folder_cmd.py <localDir> ${remoteDir} ${ondup}`
    }));

    // ★ 2026-09-23：本地目录为空时必须明确失败。以前 bypy 同步 0 个文件也返回成功，
    //   界面显示「✓ 完成」→ 用户以为传上去了，实际网盘里什么都没有（排查时极难发现）。
    try {
        const probe = fs.existsSync(localDir) ? fs.readdirSync(localDir) : [];
        if (!probe.length) {
            console.warn('☁️ 上传中止：本地目录为空 →', localDir);
            logUpload('ABORT', '本地目录为空 → ' + localDir);
            return {
                success: false,
                empty: true,
                logFile: uploadLogPath(),
                error: '没有要上传的文件：' + localDir + ' 是空的。先在「书库」勾选书籍并点「批量传输」（队列里要有书），回到传输页再点发送。'
            };
        }
    } catch (e) {
        console.warn('☁️ 上传前检查本地目录失败:', e.message);
        logUpload('ABORT', '上传前检查本地目录失败: ' + e.message);
    }

    return await new Promise((resolve) => {
        // ★ 2026-09-25：暂存目录 sendqueue\ 会在每次发送时被 stageTransferFiles() 清空重填，
        //   所以这里不需要（也不能）再删目录 —— 删了就找不到刚复制进去的书了。
        //   （2026-09-23 的旧注释说「sendqueue 持久、增量跳过」已作废：用户要求重发就重发。）
        const finish = (payload) => {
            // ★ 2026-09-23：把上传结论打進主进程控制台（npm start 那个窗口）。
            //   以前这里只有开始的上传日志、没有结论，出问题时只能靠"猜"——界面报
            //   ✓完成/✗失败后，控制台看不到任何证据。现在三个返回路径都会留下结论。
            try {
                console.log('☁️ 苹果图书上传结果:', JSON.stringify({
                    success: !!(payload && payload.success),
                    empty: !!(payload && payload.empty),
                    timeout: !!(payload && payload.timeout),
                    authFailed: !!(payload && payload.authFailed),
                    ms: (payload && payload.ms) || null,
                    // ★ 2026-09-23：transferred=0 表示「云端已有同名同大小 → 一个字节都没传」
                    //   （bypy 判 SAME 跳过），不是失败。
                    sync: (payload && payload.sync) || null,
                    error: (payload && payload.error) ? String(payload.error).slice(0, 300) : null,
                    tail: String((payload && (payload.stdout || payload.stderr)) || '').slice(-600)
                }));
            } catch (_) {}
            resolve(payload);
        };
        const script = getPythonScript('upload_folder_cmd.py');
        if (!fs.existsSync(script)) {
            finish({ success: false, error: 'upload_folder_cmd.py 不存在' });
            return;
        }
        // ★ 安全修复：execFile + argv 数组（原来 localDir/remoteDir/ondup 拼进命令串）
        // ★ 顺带修复：mainWindow 在关窗后为 null，webContents 也会被销毁，
        //   原来无保护地 send 会抛 TypeError/Object destroyed，且无全局兜底 → 主进程崩溃。
        const safeSend = (channel, payload) => {
            if (mainWindow && !mainWindow.isDestroyed()) {
                mainWindow.webContents.send(channel, payload);
            }
        };
        let done = false;
        let timer = null;
        const startedAt = Date.now();
        // ★ 2026-09-23（用户：「不用去检查授权了，直接就是上传失败，请检查授权」）：
        //   这里以前在检测到"未授权"输出时会调用 _wipeLocalAuthAndRecheck()
        //   —— 删掉本地 token 再重新检查授权。那条路会被 bypy 的正常输出误命中
        //   （正则里的 errno 那项），把好端端的授权清掉，用户看到的是
        //   「授权明明好好的，点一下上传就变未授权」。
        //   现在**只报结论、绝不动授权**：失败返回 authFailed=true，
        //   界面显示「上传失败，请检查授权」；授权检查只保留用户手动点的那条路。
        const python = execFile(PYTHON_PATH,
            ['-X', 'utf8', script, localDir, remoteDir, ondup],
            {
                maxBuffer: 1024 * 1024 * 50,
                encoding: 'utf-8',
                // ★ 2026-09-23：PYTHONUNBUFFERED 让 bypy 的输出实时写进 upload-*.log。
                //   不然 bypy 的 stdout 是块缓冲，所有进度都在进程退出那一刻才吐出来，
                //   日志里根本看不出"时间花在哪一步"（排查慢的时候吃过这个亏）。
                env: { ...process.env, PYTHONIOENCODING: 'utf-8', PYTHONUNBUFFERED: '1' }
            },
            (error, stdout, stderr) => {
                done = true;
                if (timer) clearTimeout(timer);
                if (stdout) safeSend('applebooks-upload-log', stdout);
                if (stderr) safeSend('applebooks-upload-log', '[stderr] ' + stderr);
                const ms = Date.now() - startedAt;
                // ★ 2026-09-23：解析 upload_folder_cmd.py 最后那行机器可读结论
                //   （📊 同步结果: uploaded=N rapid=N copied=N skipped=N failed=N）。
                //   用途：界面要能分清「真的传了几本」和「云端已有同名同大小 →
                //   一个字节都没传（bypy 判 SAME 直接跳过）」—— 后者以前界面只会
                //   含糊地显示「完成」，用户以为没传上去。
                const sync = (() => {
                    const m = String(stdout || '').match(
                        /同步结果:\s*uploaded=(\d+)\s+rapid=(\d+)\s+copied=(\d+)\s+skipped=(\d+)\s+failed=(\d+)(?:\s+warn=(\d+))?/
                    );
                    if (!m) return null;
                    return {
                        uploaded: +m[1], rapid: +m[2], copied: +m[3],
                        skipped: +m[4], failed: +m[5],
                        // warn = 已知无害的错误行（旧秒传接口 31023，bypy 会自动回退成普通上传）
                        warn: m[6] ? +m[6] : 0,
                        transferred: (+m[1]) + (+m[2]) + (+m[3])
                    };
                })();
                if (error) {
                    // ★ 语言无关的失败分类：bypy 自己的报错永远是英文（"not authorized" 等），
                    //   前端不再正则匹配中文错误文本 → 中英文界面行为一致。
                    const blob = String(stderr || '') + ' ' + String(stdout || '') + ' ' + String(error.message || '');
                    const authFailed = /not\s+authorized|invalid[_ ]?token|expired[_ ]?token|\b401\b/i.test(blob);
                    logUpload('RESULT', JSON.stringify({
                        ok: false, ms,
                        code: (error && typeof error.code === 'number') ? error.code : null,
                        signal: error.signal || null, authFailed, sync,
                        error: String(error.message || '').slice(0, 500),
                        tail: String(stdout || stderr || '').slice(-2000)
                    }));
                    finish({ success: false, error: error.message, stdout, stderr, authFailed, needAuth: authFailed, logFile: uploadLogPath(), ms, sync });
                } else {
                    logUpload('RESULT', JSON.stringify({ ok: true, ms, sync }));
                    finish({
                        success: true, stdout, stderr, logFile: uploadLogPath(), ms, sync,
                        uploaded: sync ? sync.uploaded : null,
                        rapid: sync ? sync.rapid : null,
                        copied: sync ? sync.copied : null,
                        transferred: sync ? sync.transferred : null
                    });
                }
            }
        );
        // ★ 2026-09-23：记住正在跑的上传子进程 —— 关窗退出时不再把 bypy 留成孤儿进程
        //   （以前窗口一关 python/bypy 还在后台跑，临时目录也就永远不清理）。
        activeUploadChild = python;
        python.on('exit', () => { if (activeUploadChild === python) activeUploadChild = null; });
        // ★ 上传必须能"给出结论"：bypy 未授权/网络卡住时会在交互提示上无限等待，
        //   以前这里没有任何超时 → 一键流程第 5 步永远卡在"正在上传…"。
        //   超时后杀掉子进程并返回明确原因，让前端能提示用户去检查授权。
        timer = setTimeout(() => {
            if (done) return;
            done = true;
            _killTree(python);
            logUpload('RESULT', JSON.stringify({ ok: false, timeout: true, ms: Date.now() - startedAt }));
            finish({
                success: false,
                logFile: uploadLogPath(),
                error: '上传超时（5 分钟未完成）：可能是百度网盘未授权或网络异常，请检查授权后重试',
                timeout: true
            });
        }, 300000);
        python.stdout.on('data', (data) => {
            const s = data.toString('utf-8');
            safeSend('applebooks-upload-log', s);
            logUpload('OUT', s);
        });
        python.stderr.on('data', (data) => {
            const s = data.toString('utf-8');
            safeSend('applebooks-upload-log', '[stderr] ' + s);
            logUpload('ERR', s);
        });
    });
});

// ★ 4.5 百度网盘授权状态查询（2026-09-23 新增）
//   以前通道卡片上的「已授权」是写死的装饰文本，看不出到底授权没有。
//   ★ 2026-09-23 改：已改为跑 get_auth_code.py --check（直连官方 xpan uinfo）
//     判断，不再用 `bypy info` —— 原因见 _probeBaiduAuthOnce 上面那段注释。
// 3.5 静默续期：用 refresh_token 换新的 access_token，全程不需要用户登录
//     ★ 2026-09-23 实测确认（tools\_refresh_token_probe.py）：百度 OAuth 的
//       grant_type=refresh_token 可以换到新的 access_token，并且返回**可轮换的新
//       refresh_token**，所以只要在过期前续一次，授权就能一直续下去。
//       换句话说：「授权过期 → 让用户去浏览器登录复制授权码」这个流程本身就是多余的，
//       浏览器只应该在「从来没授权过」或「refresh_token 也废了」这两种情况下出现。
ipcMain.handle('applebooks-refresh-token', async () => {
    return await new Promise((resolve) => {
        let done = false;
        let timer = null;
        let child = null;
        const finish = (payload) => {
            if (done) return;
            done = true;
            if (timer) clearTimeout(timer);
            resolve(payload);
        };
        try {
            const script = getPythonScript('get_auth_code.py');
            child = execFile(PYTHON_PATH,
                ['-X', 'utf8', script, '--refresh'],
                {
                    maxBuffer: 1024 * 1024,
                    encoding: 'utf-8',
                    timeout: 120000,
                    env: { ...process.env, PYTHONIOENCODING: 'utf-8', PYTHONUTF8: '1' }
                },
                (error, stdout, stderr) => {
                    const out = String(stdout || '') + String(stderr || '');
                    // 成功判定用脚本自己打印的字，不依赖退出码之外的东西
                    const ok = !error && out.indexOf('续期成功') >= 0;
                    finish({ success: ok, refreshed: ok, stdout, stderr, error: error ? error.message : null });
                }
            );
        } catch (e) {
            finish({ success: false, refreshed: false, error: String(e && e.message || e) });
            return;
        }
        timer = setTimeout(() => {
            if (child) _killTree(child);
            finish({ success: false, refreshed: false, error: '续期超时', timeout: true });
        }, 130000);
    });
});

// 单次授权检查：返回 {success, authorized, ...}
// ★ 2026-09-23 改：以前这里跑 `python -c "import bypy; bypy.ByPy().info()"`
//   并用输出里有没有 "Quota" 判断。这个做法必坏：
//     · bypy info 自己重试 5 次（退避 5/10/15/20 秒），必然超过下面 12 秒的上限
//       → 每次检查都报「检查授权状态超时」；
//     · 未授权时 bypy 会**等 stdin 交互**而不是报错，execFile 下就一直挂着。
//   现在改成跑脚本自己的检查，退出码就是结论（0=有效 3=需要重新授权 1=检查出错）。
//
// ★★ 2026-09-23 第二轮（用户：「百度网盘检查授权太慢了，有没有快一点的方法，
//    比如直接看有没有 token」）：脚本有两个检查模式，这里按需要选：
//      · fast=true  → `--check-fast`：只读本地 bypy.json 有没有 access_token，
//                     不发网络请求，亚毫秒返回。**通道徽标 / 切通道 / 启动时都用它**。
//      · fast=false → `--check`：真打一次百度 uinfo 接口，这台机器实测 15～22 秒。
//                     只在用户明确点「检查授权状态」时才用。
//   过期了怎么发现？不是靠轮询，是靠**上传失败**：上传失败时界面直接显示
//   「上传失败，请检查授权」，由用户自己决定要不要重新授权（不再自动清 token）。
function _probeBaiduAuthOnce(fast = false) {
    return new Promise((resolve) => {
        let done = false;
        let timer = null;
        let child = null;
        const finish = (payload) => {
            if (done) return;
            done = true;
            if (timer) clearTimeout(timer);
            resolve(payload);
        };
        try {
            const script = getPythonScript('get_auth_code.py');
            if (!fs.existsSync(script)) {
                finish({ success: false, authorized: false, error: 'get_auth_code.py 不存在' });
                return;
            }
            child = execFile(PYTHON_PATH,
                ['-X', 'utf8', script, fast ? '--check-fast' : '--check'],
                {
                    maxBuffer: 1024 * 1024,
                    encoding: 'utf-8',
                    timeout: fast ? 15000 : 45000,
                    env: { ...process.env, PYTHONIOENCODING: 'utf-8', PYTHONUTF8: '1' }
                },
                (error, stdout, stderr) => {
                    const out = String(stdout || '') + String(stderr || '');
                    const code = error && typeof error.code === 'number' ? error.code : 0;
                    // 3 = 脚本明确说「需要重新授权」；其它非 0 视为检查出错
                    if (code === 3) {
                        finish({ success: true, authorized: false, needsReauth: true, stdout, stderr });
                        return;
                    }
                    if (error) {
                        finish({ success: false, authorized: false, error: error.message, stdout, stderr });
                        return;
                    }
                    const authorized = out.indexOf('✅') >= 0;
                    finish({ success: true, authorized, stdout, stderr, fast: !!fast });
                }
            );
        } catch (e) {
            finish({ success: false, authorized: false, error: String(e && e.message || e) });
            return;
        }
        // 兜底超时：脚本内部 HTTP 超时 30 秒（--check）／本地读文件（--check-fast）
        timer = setTimeout(() => {
            if (child) _killTree(child);
            finish({ success: false, authorized: false, error: '检查授权状态超时', timeout: true });
        }, fast ? 20000 : 50000);
    });
}

// ★ 2026-09-23 已删除 _wipeLocalAuthAndRecheck()。
//   它原来在「上传报未授权」时清掉本地 token 再快速检查一次授权，但判定用的正则
//   会被 bypy 的正常输出误命中（errno 那一项），结果变成「授权明明好好的，
//   点一下上传反而变未授权」。
//   用户明确要求：「不用去检查授权了，直接就是上传失败，请检查授权」。
//   现在上传失败只由界面显示结论，绝不改动本地授权数据；
//   检查授权只保留用户手动点「检查授权状态」那一条路。

ipcMain.handle('applebooks-auth-status', async () => {
    // ★ 用户明确点「检查授权状态」→ 才走网络真验证（含静默续期）。
    return await _baiduAuthStatusFull(false);
});

// 苹果图书通道状态：给「三合一传输页」的通道徽标 / 切通道 / 启动时用。
//   ★ 2026-09-23 第二轮（用户：「检查授权太慢了」）：默认走 **fast**（只读本地 token，
//     亚毫秒），不再每次切通道都打百度接口（那要 15～22 秒）。
//     full=true 时才走网络真验证 + refresh_token 静默续期 —— 只给
//     「检查授权状态」按钮 / 「重新授权」后用。
//   ★ 提取成函数是为了让 tx-channel-status 和 applebooks-auth-status 走**同一套**
//     判定，不出现「徽标红着但其实能自动续期」这种自相矛盾的显示。
async function _baiduAuthStatusFull(fast = false) {
    let r = await _probeBaiduAuthOnce(!!fast);
    if (r.success && r.authorized) return r;

    // ★ fast 模式到此为止：本地没 token 就是没授权，不需要（也不该）打网络。
    if (fast) return r;

    // ★ 自愈：access_token 过期是常态（30 天），而 refresh_token 可以静默换新的。
    //   所以这里先自己续一次，续上了就当授权正常 —— 用户完全不需要看见浏览器。
    //   只有「续期也失败」才落回「需要重新授权」。
    try {
        const rr = await new Promise((resolve) => {
            const script = getPythonScript('get_auth_code.py');
            execFile(PYTHON_PATH, ['-X', 'utf8', script, '--refresh'],
                { maxBuffer: 1024 * 1024, encoding: 'utf-8', timeout: 120000,
                  env: { ...process.env, PYTHONIOENCODING: 'utf-8', PYTHONUTF8: '1' } },
                (error, stdout, stderr) => {
                    const out = String(stdout || '') + String(stderr || '');
                    resolve({ success: !error && out.indexOf('续期成功') >= 0, stdout, stderr });
                });
        });
        if (rr.success) {
            const again = await _probeBaiduAuthOnce(false);
            if (again.success && again.authorized) {
                return { ...again, refreshed: true, refreshLog: rr.stdout };
            }
        }
        return { ...r, refreshTried: true, refreshLog: rr.stdout, refreshFailed: true };
    } catch (e) {
        return { ...r, refreshTried: true, refreshError: String(e && e.message || e) };
    }
}

// ★ 4.7 各传输通道自己的状态检测（2026-09-23 新增）
//   背景：传输页三个通道的徽标以前只有「苹果图书」是真的在检测（跑百度授权检查），
//   WiFi 和 Kindle 两个徽标要么写死、要么永远空着 —— 用户明确要求：
//   「把另外两个传输通道的状态检测改成他们自己的，不是百度网盘」。
//
//   所以 /!\ 这三个通道各查各的，绝不共用百度网盘那套结论：
//     · applebooks → 百度授权（= applebooks-auth-status，含 refresh_token 静默续期）
//     · wifi       → 本地传书服务起没起 + 局域网地址（跟百度网盘没有任何关系）
//     · kindle     → kindle_config.json 里的 SMTP 配置齐不齐、收件人是谁
//   返回 { success, channel, state, name, detail, extra[], tip }，
//   state 取值口径跟界面的 setChannelStatus 一致：ok / bad / off / run。
ipcMain.handle('tx-channel-status', async (event, channel) => {
    const ch = String(channel || '');

    if (ch === 'applebooks') {
        let r = { success: false, authorized: false };
        // ★ 2026-09-23 第二轮：通道徽标/切通道只做 **本地快速检查**（读 token 文件，
        //   亚毫秒），不再每次打百度接口 —— 那要 15～22 秒，用户明说太慢。
        //   真过期由「上传失败」暴露：上传失败时界面显示「上传失败，请检查授权」，
        //   由用户自己决定要不要重新授权（不再自动清 token）。
        try { r = await _baiduAuthStatusFull(true); } catch (e) { /* 保持失败态 */ }
        const ok = !!(r && r.success && r.authorized);
        // 账号名从脚本输出里取（--check-fast 打「已授权（账号：xxx）」，
        // --check 打「授权有效（账号：xxx）」，两种都认）
        let who = '';
        const mv = String((r && r.stdout) || '').match(/账号[:：]\s*([^\s)）]+)/);
        if (mv) who = mv[1];
        return {
            success: true, channel: 'applebooks',
            state: ok ? 'ok' : 'off',
            name: '经百度网盘中转',
            detail: ok ? (who ? ('账号 ' + who) : '百度网盘') : '未授权 / 已过期',
            // ★ 2026-09-23：用户不要信息卡里的额外项（远端目录/续期方式…），
            //   结论只走下面那一行小字。留空数组让渲染端不需要额外分支。
            extra: [],
            tip: ok ? '已授权' : '未授权',
            refreshed: !!(r && r.refreshed),
            fast: true,
        };
    }

    if (ch === 'wifi') {
        // ★ 本地传书窗口起没起：currentWifiProcess 是「打开传书窗口后跑着的上传进程」。
        //   注意：WiFi 直传是「手机浏览器打开上传页 → 本机 chromedriver 自动填表上传」，
        //   本机地址不是给手机访问的（手机自己就是服务端），所以不再显示那个地址。
        const running = !!(currentWifiProcess && !currentWifiProcess.killed);
        return {
            success: true, channel: 'wifi',
            state: running ? 'ok' : 'off',
            name: '局域网直传',
            detail: running ? '传书窗口已打开，等待输入网址' : '还没打开传书窗口',
            extra: [],
            tip: running ? '传书服务运行中' : '待启动',
        };
    }

    if (ch === 'kindle') {
        // ★ 2026-09-30 重构：Kindle 通道不再走 SMTP 邮箱，改成 Amazon 官方「Send to Kindle」。
        //   它的「状态」= 是否已授权（登录过 Amazon 一次），与邮箱配置彻底无关。
        const s = stkGetState();
        const ok = !!s.authorized;
        return {
            success: true, channel: 'kindle',
            state: ok ? 'ok' : 'off',
            name: 'Send to Kindle',
            detail: ok ? '经 Amazon 官方通道（约 1 分钟到手）' : '',
            extra: [],
            tip: ok ? '已授权' : '未授权',
            error: ok ? null : '未授权：点「Kindle 授权」登录 Amazon 一次',
        };
    }
    return { success: false, error: '未知通道：' + ch };
});

// 4. 打开授权新窗口
// ★ 2026-09-30：授权改成**页面内弹窗** —— 这里不再建窗口，只通知主界面打开弹窗
ipcMain.handle('applebooks-open-auth', async () => {
    try { if (mainWindow && !mainWindow.isDestroyed()) { mainWindow.webContents.send('ui-open-modal', 'applebooks'); } return { success: true }; }
    catch (e) { return { success: false, error: e.message }; }
});
// 4.5. 打开"操作说明"新窗口（齿轮按钮 → 全局总说明）
ipcMain.handle('applebooks-open-help', async () => {
    try {
        const helpWin = new BrowserWindow({
            width: 1180,
            height: 780,
        minWidth: 900,
        minHeight: 600,
            parent: mainWindow,
            modal: false,
            title: 'EasyPub 操作说明',
            backgroundColor: '#0B0D10',
            icon: path.join(__dirname, 'icon.ico')
        });
        helpWin.loadFile('help.html');
        helpWin.on('closed', () => {
            // ★ 通知主窗口：操作说明小窗口关闭了（不跳转到任何特定页面）
            if (mainWindow && !mainWindow.isDestroyed()) {
                mainWindow.webContents.send('help-window-closed');
            }
        });
        return { success: true };
    } catch (e) {
        return { success: false, error: e.message };
    }
});

// 4.6. 打开"苹果图书 · 完整操作指引"新窗口（苹果图书页 📖 按钮专用）
ipcMain.handle('applebooks-open-guide', async () => {
    try {
        const guideWin = new BrowserWindow({
            width: 960,
            height: 760,
            parent: mainWindow,
            modal: false,
            title: '苹果图书 · 完整操作指引',
            backgroundColor: '#0B0D10',
            webPreferences: {
                // 指引页要跟随主界面的中英切换（window.pageLang 语言桥）
                preload: path.join(__dirname, 'applebooks-help-preload.js'),
                contextIsolation: true,
                nodeIntegration: false
            },
            icon: path.join(__dirname, 'icon.ico')
        });
        guideWin.loadFile('applebooks-help.html');
        return { success: true };
    } catch (e) {
        return { success: false, error: e.message };
    }
});

// 5. 启动 open_auth_page.py
ipcMain.handle('applebooks-start-auth', async (event) => {
    const senderWebContents = event.sender;
    // 渲染进程/窗口可能已经被关掉：子进程回调里直接 send 会抛
    // "TypeError: Object has been destroyed"（未捕获 → 弹错误框 → 打断用户操作）。
    // 日志发不出去可以接受，但绝不能反过来把主进程搞崩。
    const safeSend = (ch, payload) => {
        try { if (senderWebContents && !senderWebContents.isDestroyed()) senderWebContents.send(ch, payload); } catch (e) {}
    };
    try {
        if (currentAuthProcess && !currentAuthProcess.killed) {
            _killTree(currentAuthProcess);
            currentAuthProcess = null;
        }
        const script = getPythonScript('open_auth_page.py');
        if (!fs.existsSync(script)) {
            safeSend('applebooks-auth-log', '❌ open_auth_page.py 不存在\n');
            return { success: false, error: 'open_auth_page.py 不存在' };
        }
        // ★ 修复空格路径 bug：用 execFile + 独立 argv，不用环境变量
        // ★★ 修复（P0）：原来无条件用 process.resourcesPath。
        //    打包后它 = ...\<app>\resources\  —— 正确；
        //    但开发模式它 = node_modules\electron\dist\resources\ —— 那里只有 default_app.asar，
        //    根本没有 chromedriver.exe，于是 open_auth_page.py 一进来就「❌ chromedriver.exe 不存在」
        //    直接 return，浏览器永远打不开（用户看到的正是这个）。
        //    chromedriver.exe 在开发模式下就躺在项目根目录（与 main.js 同级）。
        //    与 kindle 那处（见 kd-* handler）保持同一套判定逻辑。
        let chromedriverPath = app.isPackaged
            ? path.join(process.resourcesPath, 'chromedriver.exe')
            : path.join(__dirname, 'chromedriver.exe');
        // 兜底：万一两个位置判断反了（例如 asar 布局变化），再找一遍另一个位置
        if (!fs.existsSync(chromedriverPath)) {
            const alt = app.isPackaged
                ? path.join(__dirname, 'chromedriver.exe')
                : path.join(process.resourcesPath, 'chromedriver.exe');
            if (fs.existsSync(alt)) chromedriverPath = alt;
        }
        console.log('[auth] chromedriver →', chromedriverPath, fs.existsSync(chromedriverPath) ? '(存在)' : '(不存在!)');
        if (!fs.existsSync(chromedriverPath)) {
            const msg = `❌ 找不到 chromedriver.exe：${chromedriverPath}\n   请确认安装包完整，或把 chromedriver.exe 放到程序目录下。\n`;
            safeSend('applebooks-auth-log', msg);
            return { success: false, error: '找不到 chromedriver.exe' };
        }
        const args = [
            '-X', 'utf8',
            script,
            '--chromedriver-path', chromedriverPath
        ];
        // ★ 自动抓码已整个关闭（2026-09-23，用户要求）：不再生成/读取 auth_code.txt。
        //   这里只负责把历史上残留下来的旧文件清掉，避免任何东西再去读它。
        try {
            const stale = path.join(__dirname, 'auth_code.txt');
            if (fs.existsSync(stale)) fs.unlinkSync(stale);
        } catch (e) {}
        currentAuthProcess = execFile(PYTHON_PATH, args,
            {
                maxBuffer: 1024 * 1024 * 50,
                encoding: 'utf-8',
                env: {
                    ...process.env,
                    PYTHONIOENCODING: 'utf-8'
                }
            },
            (error, stdout, stderr) => {
                if (stdout) safeSend('applebooks-auth-log', stdout);
                if (stderr) safeSend('applebooks-auth-log', '[stderr] ' + stderr);
                safeSend('applebooks-auth-done', {
                    success: !error,
                    error: error ? error.message : null
                });
                currentAuthProcess = null;
            }
        );
        currentAuthProcess.stdout.on('data', (data) => {
            safeSend('applebooks-auth-log', data.toString('utf-8'));
        });
        currentAuthProcess.stderr.on('data', (data) => {
            safeSend('applebooks-auth-log', '[stderr] ' + data.toString('utf-8'));
        });
        return { success: true, message: '授权流程已启动，请在浏览器中完成登录并复制授权码' };
    } catch (e) {
        return { success: false, error: e.message };
    }
});

// 6. 【已移除】取「open_auth_page.py 自动抓到的授权码」。
//
//    ★★ 2026-09-23：**自动抓码整个关掉**（用户要求）。原来的做法是
//    open_auth_page.py 用正则从授权页面上把码抠出来写进 auth_code.txt，
//    这里读出来给授权窗、由授权窗**自动填进输入框并自动提交**。
//    撤掉它的理由：
//      1) 页面上的码可能早就过期/被用过（浏览器能活 15 分钟），抓到的等于废码，
//         百度回 invalid_grant，用户看到的是「我什么都没干它就报授权失败」；
//      2) 自动化把真正的失败原因（invalid_grant / redirect_uri / 码被改过）
//         全盖住了，没法归因；
//      3) 用户明确要求输入框必须是空的、码由自己输。
//    现在：open_auth_page.py 只开浏览器、不写文件；授权窗只显示提示，不碰输入框。
//    原来的 ipcMain.handle('applebooks-grab-code') 已删除 —— 渲染进程再调它只会
//    得到 “No handler registered” 的 rejection，被调用侧的 try/catch 吞掉。
//
// 6.5 清掉上一轮残留的授权码（授权窗口一打开就调）。
//     ★ 自动抓码关闭后这个基本是空的，保留是因为要清掉历史版本留在磁盘上的旧文件。
ipcMain.handle('applebooks-clear-code', async () => {
    try {
        const codeFile = path.join(__dirname, 'auth_code.txt');
        if (fs.existsSync(codeFile)) fs.unlinkSync(codeFile);
        return { ok: true };
    } catch (e) {
        return { ok: false, error: e.message };
    }
});

// 7. 提交授权码（启动 get_auth_code.py）
ipcMain.handle('applebooks-submit-auth-code', async (event, authCode) => {
    if (!authCode || !authCode.trim()) {
        return { success: false, error: '授权码不能为空' };
    }
    const senderWebContents = event.sender;
    // 渲染进程/窗口可能已经被关掉：子进程回调里直接 send 会抛
    // "TypeError: Object has been destroyed"（未捕获 → 弹错误框 → 打断用户操作）。
    // 日志发不出去可以接受，但绝不能反过来把主进程搞崩。
    const safeSend = (ch, payload) => {
        try { if (senderWebContents && !senderWebContents.isDestroyed()) senderWebContents.send(ch, payload); } catch (e) {}
    };
    return await new Promise((resolve) => {
        try {
            const script = getPythonScript('get_auth_code.py');
            if (!fs.existsSync(script)) {
                resolve({ success: false, error: 'get_auth_code.py 不存在' });
                return;
            }
            // ★ 安全修复：execFile + argv 数组（authCode 是用户粘贴的字符串，原来直接拼进命令串）
            let authDone = false;
            let authTimer = null;
            const proc = execFile(PYTHON_PATH,
                ['-X', 'utf8', script, authCode.trim()],
                {
                    maxBuffer: 1024 * 1024 * 50,
                    encoding: 'utf-8',
                    env: { ...process.env, PYTHONIOENCODING: 'utf-8' }
                },
                (error, stdout, stderr) => {
                    if (authDone) return;      // 已被超时分支处理过
                    authDone = true;
                    clearTimeout(authTimer);
                    if (stdout) safeSend('applebooks-auth-log', stdout);
                    if (stderr) safeSend('applebooks-auth-log', '[stderr] ' + stderr);
                    // ★ 只有脚本自己打印「授权成功」才算成功。
                    //   以前只看 returncode：授权码被丢弃/bypy 直接退出时 returncode=0，
                    //   界面于是报「授权成功」，其实什么都没换。
                    const out = (stdout || '') + (stderr || '');
                    const ok = !error && out.includes('授权成功');
                    resolve({
                        success: ok,
                        error: error ? error.message : (ok ? null : '授权码未被接受（请重新走一遍授权流程）'),
                        stdout
                    });
                }
            );
            // ★ 2026-09-23 改：脚本现在**完全不经过 bypy** 了 ——
            //   换 token 直连百度 OAuth（20 秒上限）、验证直连官方 xpan 接口
            //   （VERIFY_TIMEOUT=30 秒上限），正常一次往返在 1 秒内完成。
            //   以前这里要留 320 秒，是因为 bypy 自己会重试 5 次
            //   (10s/20s/30s/40s，实测无效授权码跑满 103 秒)，必须比它宽松。
            //   现在 90 秒 = 两个 30 秒上限 + 余量，网络再差也够用；
            //   真出意外时用户不用再干等 5 分钟。
            //   现在 150 秒 = 两个 30 秒上限 + 余量（这台机器实测百度要 15 秒以上
            //   才回一次响应，所以余量必须给足），真出意外时用户不用干等 5 分钟。
            authTimer = setTimeout(() => {
                if (authDone) return;
                authDone = true;
                _killTree(proc);
                const msg = '⚠️ 授权超时（150 秒未完成）：网络异常或授权码已过期，请重新点「重新授权」再试';
                try { safeSend('applebooks-auth-log', msg); } catch (e) {}
                resolve({ success: false, error: msg, timeout: true });
            }, 150 * 1000);
            proc.stdout.on('data', (data) => {
                safeSend('applebooks-auth-log', data.toString('utf-8'));
            });
            proc.stderr.on('data', (data) => {
                safeSend('applebooks-auth-log', '[stderr] ' + data.toString('utf-8'));
            });
        } catch (e) {
            resolve({ success: false, error: e.message });
        }
    });
});

// 7. 停止授权流程
ipcMain.handle('applebooks-stop-auth', async () => {
    try {
        if (currentAuthProcess && !currentAuthProcess.killed) {
            // ★ 2026-09-23：原来是 child.kill()（只杀 python 本身，chromedriver
            //   和 Chrome 会留下），后面还用全局 taskkill 兜底 —— 又会误杀别的
            //   流程的 chromedriver。改成按进程树杀，干净且不越界。
            _killTree(currentAuthProcess);
            currentAuthProcess = null;
        }
        return { success: true };
    } catch (e) {
        return { success: false, error: e.message };
    }
});

// 8. 列出 download 目录里的文件
ipcMain.handle('applebooks-list-files', async () => {
    try {
        if (!fs.existsSync(APPLEBOOKS_DOWNLOAD_DIR)) {
            fs.mkdirSync(APPLEBOOKS_DOWNLOAD_DIR, { recursive: true });
        }
        const files = fs.readdirSync(APPLEBOOKS_DOWNLOAD_DIR)
            .filter(f => !f.startsWith('.') && !f.endsWith('.crdownload'))
            .map(f => {
                const fp = path.join(APPLEBOOKS_DOWNLOAD_DIR, f);
                const stats = fs.statSync(fp);
                return { name: f, path: fp, size: stats.size, mtime: stats.mtimeMs };
            })
            .sort((a, b) => b.mtime - a.mtime);
        return { success: true, files, downloadDir: APPLEBOOKS_DOWNLOAD_DIR };
    } catch (e) {
        return { success: false, error: e.message };
    }
});

// 9. 清空 download 目录
ipcMain.handle('applebooks-clear-download', async () => {
    try {
        if (!fs.existsSync(APPLEBOOKS_DOWNLOAD_DIR)) {
            return { success: true, cleared: 0 };
        }
        const files = fs.readdirSync(APPLEBOOKS_DOWNLOAD_DIR);
        let count = 0;
        for (const f of files) {
            const fp = path.join(APPLEBOOKS_DOWNLOAD_DIR, f);
            try {
                if (fs.statSync(fp).isFile()) {
                    fs.unlinkSync(fp);
                    count++;
                }
            } catch (e) {}
        }
        return { success: true, cleared: count };
    } catch (e) {
        return { success: false, error: e.message };
    }
});

// 10. 获取任意文件的大小（用于自动填充 epub 后显示文件大小）
ipcMain.handle('stat-file', async (event, filePath) => {
    try {
        if (!filePath || !fs.existsSync(filePath)) {
            return { success: false, error: '文件不存在' };
        }
        const stats = fs.statSync(filePath);
        return {
            success: true,
            size: stats.size,
            mtime: stats.mtimeMs,
            name: path.basename(filePath)
        };
    } catch (e) {
        return { success: false, error: e.message };
    }
});

// ================================================================
// ★★★ v4 增量：WiFi 传书（通过 Selenium 自动上传到 WiFi 网址）★★★
// ================================================================

const WIFI_DOWNLOAD_DIR = DOWNLOAD_DIR;

// 1. 打开 WiFi 传书输入小窗口
// ★ 2026-09-30：WiFi 传书改成**页面内弹窗** —— 不再建窗口，只通知主界面打开弹窗
ipcMain.handle('wifi-open-page', async () => {
    try { if (mainWindow && !mainWindow.isDestroyed()) { mainWindow.webContents.send('ui-open-modal', 'wifi'); } return { success: true }; }
    catch (e) { return { success: false, error: e.message }; }
});
// 2. 启动 wifi_upload.py 上传
ipcMain.handle('wifi-upload', async (event, url, files) => {
    if (!url || !url.trim()) {
        return { success: false, error: '网址不能为空' };
    }
    const senderWebContents = event.sender;
    try {
        const script = getPythonScript('wifi_upload.py');
        if (!fs.existsSync(script)) {
            return { success: false, error: 'wifi_upload.py 不存在' };
        }
        // 自动补全 http://
        let fullUrl = url.trim();
        if (!fullUrl.startsWith('http://') && !fullUrl.startsWith('https://')) {
            fullUrl = 'http://' + fullUrl;
        }
        const chromedriverPath = app.isPackaged
            ? path.join(process.resourcesPath, 'chromedriver.exe')
            : path.join(__dirname, 'chromedriver.exe');
        const env = {
            ...process.env,
            PYTHONIOENCODING: 'utf-8',
            CHROMEDRIVER_PATH: chromedriverPath
        };
        // ★ 安全修复：execFile + argv 数组。
        //   原来把 SETTINGS.wifiDelay（经 settings-set 无校验落盘）拼进命令串，
        //   形如 1" & <cmd> & " 即可二次触发命令注入。
        //   顺带修复：窗口关闭后向已销毁的 webContents.send 会抛异常且无全局兜底 → 主进程崩溃。
        const safeSend = (channel, payload) => {
            try { if (senderWebContents && !senderWebContents.isDestroyed()) senderWebContents.send(channel, payload); } catch (e) {}
        };
        // ★ 2026-09-25 统一暂存（用户要求三条通道一致）：先把本次要传的书复制进
        //   sendqueue\_wifi，再让 wifi_upload.py 只看这个暂存目录 / 这份暂存清单。
        //   以前是直接把 download\ 交给脚本自己 os.listdir，源目录混着 .covers\ 之类。
        const list = (Array.isArray(files) && files.length)
            ? files
            : (pendingBatch && pendingBatch.target === 'wifi' ? pendingBatch.files : null);
        const staged = stageTransferFiles(list, 'wifi');
        if (!staged.ok) {
            console.warn('WiFi 传书中止：统一暂存失败 →', staged.error);
            logUpload('ABORT', 'WiFi 统一暂存失败 → ' + staged.error);
            return { success: false, error: staged.error };
        }
        const wifiArgs = ['-X', 'utf8', script,
                          '--download-dir', staged.dir,
                          '--delay', String(Number(SETTINGS.wifiDelay) || 0),
                          // ★ 2026-10-05：网址直接用参数传（不再只靠 stdin 交互）
                          '--url', fullUrl];
        {
            // ★ 书库批量传输：把勾选（且已暂存）的文件写成临时 JSON，脚本读它
            files = staged.files;
            const listPath = path.join(app.getPath('temp'), 'wifi-file-list.json');
            fs.writeFileSync(listPath, JSON.stringify(files), 'utf-8');
            wifiArgs.push('--file-list', listPath);
            console.log('📋 WiFi 按清单传', files.length, '本（暂存目录 ' + staged.dir + '）');
        }

        // ★ WiFi 传书也要能给结论：手机端关掉页面 / 不在同一网段时，
        //   chromedriver 与本地 http 服务会无限等待，以前这里没有超时，
        //   界面就永远停在"传输中"。15 分钟是给大文件留的余量。
        let wifiDone = false;
        let wifiTimer = null;
        const python = execFile(PYTHON_PATH, wifiArgs,
            {
                maxBuffer: 1024 * 1024 * 50,
                encoding: 'utf-8',
                env
            },
            (error, stdout, stderr) => {
                if (wifiDone) return;          // 已被超时分支处理过
                wifiDone = true;
                clearTimeout(wifiTimer);
                if (stdout) safeSend('wifi-upload-log', stdout);
                if (stderr) safeSend('wifi-upload-log', '[stderr] ' + stderr);
                safeSend('wifi-upload-done', {
                    success: !error,
                    error: error ? error.message : null
                });
            }
        );
        currentWifiProcess = python;
        // ★ 实时回传 stdout/stderr
        const _wifiLogLine = (s) => { try { logUpload('WIFI-LOG', String(s).trim().slice(0, 400)); } catch (e) {} };
        python.stdout.on('data', (data) => {
            safeSend('wifi-upload-log', data.toString('utf-8'));
            _wifiLogLine(data.toString('utf-8'));
        });
        python.stderr.on('data', (data) => {
            safeSend('wifi-upload-log', '[stderr] ' + data.toString('utf-8'));
            _wifiLogLine('[stderr] ' + data.toString('utf-8'));
        });
        wifiTimer = setTimeout(() => {
            if (wifiDone) return;
            wifiDone = true;
            _killTree(python);
            const msg = '⚠️ WiFi 传书超时（15 分钟未完成）：请确认手机和电脑在同一个 WiFi、'
                      + '浏览器页面没有关闭，然后重试';
            safeSend('wifi-upload-log', msg);
            safeSend('wifi-upload-done', { success: false, error: msg, timeout: true });
        }, 15 * 60 * 1000);
        // 把 URL 通过 stdin 传进去（wifi_upload.py 用 input() 读取）
        python.stdin.write(fullUrl + '\n');
        python.stdin.end();
        // 保存 URL 到本地记录（去重，最新在前，最多 20 条）
        try {
            const historyPath = path.join(APP_ROOT, 'wifi_urls.txt');
            let lines = [];
            if (fs.existsSync(historyPath)) {
                lines = fs.readFileSync(historyPath, 'utf-8').split(/\r?\n/).filter(s => s.trim());
            }
            // 移除已存在的相同 URL，插到最前
            lines = lines.filter(u => u.trim() !== fullUrl);
            lines.unshift(fullUrl);
            // 最多保留 20 条
            lines = lines.slice(0, 20);
            fs.writeFileSync(historyPath, lines.join('\n') + '\n', 'utf-8');
        } catch (e) {
            console.warn('保存 WiFi URL 历史失败:', e.message);
        }
        return { success: true, message: '已启动 WiFi 传书流程' };
    } catch (e) {
        return { success: false, error: e.message };
    }
});

// 读取 WiFi URL 历史
ipcMain.handle('wifi-get-urls', async () => {
    try {
        const historyPath = path.join(APP_ROOT, 'wifi_urls.txt');
        if (!fs.existsSync(historyPath)) return { success: true, urls: [] };
        const lines = fs.readFileSync(historyPath, 'utf-8').split(/\r?\n/).filter(s => s.trim());
        return { success: true, urls: lines };
    } catch (e) {
        return { success: false, error: e.message, urls: [] };
    }
});

// 3. 停止 WiFi 上传
ipcMain.handle('wifi-stop', async () => {
    try {
        // ★ 2026-09-23：原来这里 `taskkill /f /im chromedriver.exe` 全局杀 ——
        //   会误杀「授权流程 / 其他流程」正在用的 chromedriver。改成只杀
        //   WiFi 传书自己的进程树（python → chromedriver → Chrome）。
        if (currentWifiProcess && !currentWifiProcess.killed) {
            _killTree(currentWifiProcess);
        }
        currentWifiProcess = null;
        currentWifiDone = true;   // 让 15 分钟超时分支不要再补一刀
        return { success: true };
    } catch (e) {
        return { success: false, error: e.message };
    }
});

// 4. 复制文件到 download 目录（与苹果图书复用）
ipcMain.handle('wifi-copy-files', async (event, filePaths) => {
    try {
        if (!Array.isArray(filePaths) || filePaths.length === 0) {
            return { success: false, error: '未选择文件' };
        }
        if (!fs.existsSync(WIFI_DOWNLOAD_DIR)) {
            fs.mkdirSync(WIFI_DOWNLOAD_DIR, { recursive: true });
        }
        const results = [];
        for (const src of filePaths) {
            if (!fs.existsSync(src)) {
                results.push({ src, success: false, error: '文件不存在' });
                continue;
            }
            const name = path.basename(src);
            const finalDst = path.join(WIFI_DOWNLOAD_DIR, name);
            // ★ 2026-09-26：同名**覆盖**，不再生成 书名_1.epub（内容相同则跳过复制）
            if (!(fs.existsSync(finalDst) && sameFileContent(src, finalDst))) {
                fs.copyFileSync(src, finalDst);
            }
            results.push({ src, dst: finalDst, success: true, name: path.basename(finalDst) });
        }
        const okCount = results.filter(r => r.success).length;
        return {
            success: okCount > 0,
            copied: okCount,
            total: filePaths.length,
            results
        };
    } catch (e) {
        return { success: false, error: e.message };
    }
});

// ================================================================
// ★★★ Kindle 传书（SMTP 推送） ★★★
// ================================================================

const KINDLE_CONFIG_PATH = path.join(APP_ROOT, 'kindle_config.json');
let currentKindleProcess = null;

// 0. 复制单个文件到 download/（与苹果图书 / WiFi 复用同目录）
ipcMain.handle('kindle-copy-file', async (event, srcPath) => {
    try {
        if (!srcPath || !fs.existsSync(srcPath)) {
            return { success: false, error: '源文件不存在' };
        }
        const downloadDir = path.join(APP_ROOT, 'download');
        if (!fs.existsSync(downloadDir)) {
            fs.mkdirSync(downloadDir, { recursive: true });
        }
        const name = path.basename(srcPath);
        const dst = path.join(downloadDir, name);
        // ★ 2026-09-26：同名**覆盖**，不再生成 书名_1.epub。
        //   以前每复制一次同一本书就多一个 _1/_2，用户在传输文件夹里看到的就是「重复的书」。
        //   内容完全一样就跳过（保留原文件，也省掉一次整本拷贝）。
        if (!(fs.existsSync(dst) && sameFileContent(srcPath, dst))) {
            fs.copyFileSync(srcPath, dst);
        }
        const stat = fs.statSync(dst);
        return {
            success: true,
            path: dst,
            name: path.basename(dst),
            size: stat.size
        };
    } catch (e) {
        return { success: false, error: e.message };
    }
});

// ★ 2026-09-30：Amazon 登录窗口（Send to Kindle 网页上传通道用）
//   为什么需要：用户要「书进 Kindle 云端」，官方唯一免邮箱的通道就是 sendtokindle 网页/客户端上传；
//   它要求登录 Amazon → 用一个**应用内窗口**收账号密码，再注入真实登录页（验证码就地完成）。
//   ★ 密码只在渲染层内存里用一次，不写 settings.json、不进日志；只留 persist:amazon 的登录态。
// ★ 2026-09-30：把 EPUB 上传到 Kindle 云端（官方 sendtokindle 网页通道，走已登录的 persist:amazon）
async function stkUploadFile(fileInput, opts = {}) {
    // ★ 支持一次传多本（官方页面本来就允许多选）
    const files = (Array.isArray(fileInput) ? fileInput : [fileInput]).filter((f) => f && fs.existsSync(f));
    if (!files.length) { return { ok: false, error: '没有可上传的文件' }; }
    const t0 = Date.now();
    const w = new BrowserWindow({
        width: 900, height: 720, show: opts.show === true,   // ★ 默认隐藏（后台传）
        title: 'Send to Kindle 上传中…',
        backgroundColor: '#0B0D10',
        webPreferences: { partition: 'persist:amazon', contextIsolation: true, nodeIntegration: false },
    });
    const probe = () => w.webContents.executeJavaScript(
        '(function(){return JSON.stringify({' +
        'url:location.href,' +
        'signIn:!!document.querySelector("#s2k-home-wrapper-sign-in-view"),' +
        'fileInputs:document.querySelectorAll("input[type=file]").length,' +
        'buttons:[].slice.call(document.querySelectorAll("button,[role=button],a.button,a[role=button]"))' +
        '.map(function(b){return (b.innerText||b.textContent||"").trim();})' +
        '.filter(function(s){return s && s.length<30;}).slice(0,25)' +
        '});})()', true).then(JSON.parse);
    try {
        await w.loadURL('https://www.amazon.com/sendtokindle');
        await new Promise((r) => setTimeout(r, 4500));   // 等 SPA 渲染
        let st = await probe();
        logUpload('STK-PROBE', JSON.stringify({ url: st.url, signIn: st.signIn, fileInputs: st.fileInputs, buttons: st.buttons }));
        if (st.signIn) {
            stkSetState(false);        // ★ 自纠偏：确实没登录就清掉标记
            return { ok: false, error: '还没授权：请点「Kindle 授权」登录 Amazon 一次', url: st.url };
        }
        // ── CDP：拦截文件选择框（这个页面没有常驻 input[type=file]，点按钮时才创建）──
        w.webContents.debugger.attach('1.3');
        const dbg = (method, params) => w.webContents.debugger.sendCommand(method, params || {});
        await dbg('Page.enable');
        await dbg('DOM.enable');
        await dbg('Page.setInterceptFileChooserDialog', { enabled: true });
        let chooserNode = null;
        w.webContents.debugger.on('message', (ev, method, params) => {
            if (method === 'Page.fileChooserOpened' && params && params.backendNodeId) {
                chooserNode = params.backendNodeId;
            }
        });
        // ① 清掉页面上已有的旧文件（按钮文案实测是 Remove all）
        const cleaned = await w.webContents.executeJavaScript(
            '(function(){var bs=[].slice.call(document.querySelectorAll("button,a,input[type=submit]"));' +
            'for(var i=0;i<bs.length;i++){var t=((bs[i].innerText||bs[i].value||"")||"").trim();' +
            'if(/^(remove all|clear all|清空|全部移除)$/i.test(t)){bs[i].click();return t;}}return "";})()', true);
        if (cleaned) { logUpload('STK-CLEAN', JSON.stringify({ clicked: cleaned })); await _stkSleep(800); }
        // ② 有 input 就直接设；没有就点按钮 + 拦截
        const doc = await dbg('DOM.getDocument', { depth: -1 });
        const q = await dbg('DOM.querySelector', { nodeId: doc.root.nodeId, selector: 'input[type=file]' });
        const fileName0 = files.map((f) => path.basename(f)).join(', ');
        if (q && q.nodeId) {
            await dbg('DOM.setFileInputFiles', { files: files, nodeId: q.nodeId });
            logUpload('STK-FILE-SET', JSON.stringify({ via: 'input', files: fileName0 }));
        } else {
            const hitTxt = await w.webContents.executeJavaScript(
                '(function(){var re=/(select files|add more files|choose files|browse|选择文件|添加文件|上传)/i;' +
                'var bs=[].slice.call(document.querySelectorAll("button,a,input[type=submit],[role=button]"));' +
                'for(var i=0;i<bs.length;i++){var t=((bs[i].innerText||bs[i].value||"")||"").trim();' +
                'if(t&&t.length<40&&re.test(t)){bs[i].click();return t;}}return "";})()', true);
            logUpload('STK-CHOOSER-CLICK', JSON.stringify({ clicked: hitTxt }));
            for (let k = 0; k < 30 && !chooserNode; k += 1) { await _stkSleep(300); }
            if (chooserNode) {
                await dbg('DOM.setFileInputFiles', { files: files, backendNodeId: chooserNode });
                logUpload('STK-FILE-SET', JSON.stringify({ via: 'chooser', files: fileName0 }));
            } else {
                const btns = await w.webContents.executeJavaScript(
                    '(function(){return [].slice.call(document.querySelectorAll("button,a,input[type=submit]"))' +
                    '.map(function(b){return ((b.innerText||b.value||"")||"").trim();}).filter(Boolean).slice(0,15);})()', true);
                logUpload('STK-NO-CHOOSER', JSON.stringify({ clicked: hitTxt, buttons: btns }));
                return { ok: false, error: '没能触发文件选择（页面按钮：' + JSON.stringify(btns) + '）' };
            }
        }
        // ── 等下一步（标题/发送按钮出现），然后点发送 ──
        let clicked = '', sent = false;
        for (let i = 0; i < 30; i++) {
            await new Promise((r) => setTimeout(r, 1000));
            const clickedJs = await w.webContents.executeJavaScript(
                '(function(){var bs=[].slice.call(document.querySelectorAll("button,[role=button],input[type=submit],a.button"));' +
                'for(var i=0;i<bs.length;i++){var t=(bs[i].innerText||bs[i].value||"").trim();' +
                'if(/^(Send|Send to Kindle|发送|发送至 Kindle)/i.test(t)){bs[i].click();return t;}}' +
                'return "";})()', true);
            if (clickedJs) { clicked = clickedJs; sent = true; break; }
            if (i === 12) {
                const st2 = await probe();
                logUpload('STK-WAIT', JSON.stringify({ url: st2.url, buttons: st2.buttons }));
            }
        }
        logUpload('STK-SEND-CLICK', JSON.stringify({ clicked: clicked, sent: sent }));
        // ★ 2026-09-30（用户定调）：「只要发出去就算成功，不用管到没到；
        //   无授权就失败，有授权肯定成功」→ 点完 Send 立即返回，不再轮询等确认文案。
        if (!sent) {
            const btns2 = await w.webContents.executeJavaScript(
                '(function(){return [].slice.call(document.querySelectorAll("button,a,input[type=submit]"))' +
                '.map(function(b){return ((b.innerText||b.value||"")||"").trim();}).filter(Boolean).slice(0,15);})()', true);
            logUpload('STK-DONE', JSON.stringify({ ok: false, reason: 'no-send-button', buttons: btns2 }));
            return { ok: false, error: '没找到「Send」按钮（页面可能改版）：' + JSON.stringify(btns2) };
        }
        await _stkSleep(1500);       // 给页面一点时间把请求发出去
        const ms = Date.now() - t0;
        logUpload('STK-DONE', JSON.stringify({ ok: true, sent: true, clicked: clicked, ms: ms }));
        return { ok: true, sent: true, clicked: clicked, ms: ms,
                 note: '已发送到 Amazon（通常 1 分钟内出现在 Kindle 云端）' };
    } catch (e) {
        logUpload('STK-ERROR', JSON.stringify({ error: String(e && e.message || e) }));
        return { ok: false, error: String(e && e.message || e) };
    } finally {
        setTimeout(() => { try { if (!w.isDestroyed()) { w.close(); } } catch (e) {} }, opts.keepOpen ? 60000 : 4000);
    }
}

ipcMain.handle('stk-upload', async (event, payload) => {
    const f = String((payload && payload.file) || '').trim();
    if (!f || !fs.existsSync(f)) { return { ok: false, error: '文件不存在: ' + f }; }
    return await stkUploadFile(f, { show: false });   // ★ 后台传，不弹窗
});


// ★ 2026-09-30：批量上传（一次选多本，官方页面支持）
ipcMain.handle('stk-upload-batch', async (event, payload) => {
    const list = Array.isArray(payload && payload.files) ? payload.files : [];
    const files = list.filter((f) => f && fs.existsSync(f));
    if (!files.length) { return { ok: false, error: '没有可上传的文件' }; }
    return await stkUploadFile(files, { show: false });   // ★ 后台传，不弹窗
});
// 测试入口：上传书库里**最新的那个 EPUB**
ipcMain.handle('stk-upload-newest', async () => {
    try {
        const dir = getExportDir();
        const list = fs.readdirSync(dir).filter((f) => f.toLowerCase().endsWith('.epub'))
            .map((f) => ({ f, t: fs.statSync(path.join(dir, f)).mtimeMs }))
            .sort((a, b) => b.t - a.t);
        if (!list.length) { return { ok: false, error: '书库（export）里没有 EPUB' }; }
        const target = path.join(dir, list[0].f);
        logUpload('STK-NEWEST', JSON.stringify({ file: list[0].f }));
        return await stkUploadFile(target, { show: true });
    } catch (e) { return { ok: false, error: String(e && e.message || e) }; }
});

// ★ 2026-09-30：登录窗口把过程写进 logs\upload-<日期>.log（STK-AUTH 行），方便事后核对
// ★ 2026-09-30：登录态落一个文件（登录窗口报成功时写、上传遇到登录视图时删）
//   为什么不直接读 cookie：Node 里没有 sqlite，读 Cookies 库太重；这个标记足够，
//   而且上传时若发现未登录会立刻把它删掉（自纠偏）。
const STK_STATE_PATH = path.join(app.getPath('userData'), 'stk-session.json');
function stkSetState(authorized, note) {
    try {
        if (authorized) {
            fs.writeFileSync(STK_STATE_PATH, JSON.stringify({ authorized: true, at: new Date().toISOString(), note: note || '' }), 'utf-8');
        } else if (fs.existsSync(STK_STATE_PATH)) {
            fs.unlinkSync(STK_STATE_PATH);
        }
    } catch (e) {}
}
function stkGetState() {
    try {
        if (!fs.existsSync(STK_STATE_PATH)) { return { authorized: false }; }
        return JSON.parse(fs.readFileSync(STK_STATE_PATH, 'utf-8')) || { authorized: false };
    } catch (e) { return { authorized: false }; }
}
// ★ 2026-09-30 v4：授权状态**直接查 Amazon 会话 cookie**（之前的标记文件是你登录前生成的，
//   所以显示成「未授权」）。查不到 cookie 才回退看标记文件。
function stkCheckCookies() {
    return new Promise((resolve) => {
        const py = findPython();
        const ck = path.join(app.getPath('userData'), 'Partitions', 'amazon', 'Network', 'Cookies');
        if (!py || !fs.existsSync(ck)) { resolve(false); return; }
        const code = 'import sqlite3,sys\n' +
            'c=sqlite3.connect(sys.argv[1])\n' +
            'ns={r[0] for r in c.execute("select name from cookies where host_key like \'%amazon%\'")}\n' +
            'print(1 if ns & {"session-token","sst-main","at-main","x-main"} else 0)';
        try {
            execFile(py, ['-X', 'utf8', '-c', code, ck], { timeout: 10000 }, (err, stdout) => {
                resolve(!err && String(stdout || '').trim() === '1');
            });
        } catch (e) { resolve(false); }
    });
}
ipcMain.handle('stk-status', async () => {
    const s = stkGetState();
    if (s.authorized) { return { success: true, authorized: true, at: s.at || null, via: 'state' }; }
    const ok = await stkCheckCookies();
    if (ok) { stkSetState(true, 'cookie-check'); return { success: true, authorized: true, at: new Date().toISOString(), via: 'cookie' }; }
    return { success: true, authorized: false, via: 'none' };
});
ipcMain.handle('stk-auth-ok', async () => { stkSetState(true, 'auth-window'); logUpload('STK-AUTH', '登录成功（窗口已确认）'); return { ok: true }; });

ipcMain.handle('stk-auth-log', async (event, payload) => {
    try {
        const msg = String((payload && payload.msg) || '').slice(0, 400);
        if (msg) { logUpload('STK-AUTH', msg); }
        return { ok: true };
    } catch (e) { return { ok: false }; }
});

// ══════════════════════════════════════════════════════════════════
// ★ 2026-09-30 v3：Send to Kindle 授权自动化（**隐藏窗口**跑，只有验证码/OTP 才 show）
//   小弹窗不内嵌网页；这里：打开 sendtokindle → 点登录入口 → 填邮箱 → 填密码 → 提交 → 轮询成功。
//   密码只在本函数内存里活一次；日志只记步骤名，不记凭据。
// ══════════════════════════════════════════════════════════════════
function _stkSleep(ms) { return new Promise((r) => setTimeout(r, ms)); }
async function _stkExec(w, code) {
    try { return await w.webContents.executeJavaScript(code, true); } catch (e) { return null; }
}
function _stkProbeJs() {
    return '(function(){return JSON.stringify({' +
        'url:location.href,' +
        'hasEmail:!!document.querySelector("#ap_email, input[name=email], input[type=email]"),' +
        'hasPwd:!!document.querySelector("#ap_password, input[name=password], input[type=password]"),' +
        'hasOtp:!!document.querySelector("#auth-mfa-otpcode, #cvf-input-code, #input-otp-code, input[name=otpCode], input[name=code], input[autocomplete=one-time-code]"),' +
        'hasCaptcha:!!document.querySelector("#captchacharacters, #auth-captcha-image, img[src*=captcha], form[action*=validateCaptcha]"),' +
        'signInView:!!document.querySelector("#s2k-home-wrapper-sign-in-view"),' +
        'needHuman:/\\/ap\\/(mfa|cvf|challenge|signin)|validateCaptcha|auth-mfa/i.test(location.href),' +
        'uploadView:!!document.querySelector("#s2k-home-wrapper-upload-view, input[type=file]")' +
        ',' + 'loggedIn:!!document.querySelector("#nav-item-signout, a[href*=sign-out], #nav-hamburger-menu")||/退出登录|Sign\s*Out/i.test((document.querySelector("#nav-link-accountList")||{}).innerText||"")' +
        '});})()';
}
function _stkFillJs(sels, val) {
    return '(function(){var sels=' + JSON.stringify(sels) + ';' +
        'for(var i=0;i<sels.length;i++){var el=document.querySelector(sels[i]);if(!el){continue;}' +
        'el.focus();try{el.value=' + JSON.stringify(val) + ';}catch(e){continue;}' +
        'el.dispatchEvent(new Event("input",{bubbles:true}));' +
        'el.dispatchEvent(new Event("change",{bubbles:true}));return sels[i];}return "";})()';
}
function _stkClickJs(sels) {
    return '(function(){var sels=' + JSON.stringify(sels) + ';' +
        'for(var i=0;i<sels.length;i++){var el=document.querySelector(sels[i]);if(el){el.click();return sels[i];}}' +
        'return "";})()';
}
function _stkClickTextJs(reSrc) {
    // ★ 2026-09-30：排除「通行密钥 / passkey / 安全密钥」—— 点了会弹 Windows 系统窗
    return '(function(){var re=new RegExp(' + JSON.stringify(reSrc) + ',"i");' +
        'var ban=/passkey|通行密钥|安全密钥|security\\s*key|指纹|Face\\s*ID/i;' +
        'var bs=[].slice.call(document.querySelectorAll("button,a,input[type=submit],[role=button]"));' +
        'for(var i=0;i<bs.length;i++){var t=((bs[i].innerText||bs[i].value||bs[i].textContent)||"").trim();' +
        'if(!t||t.length>40||ban.test(t)){continue;}' +
        'if(re.test(t)){bs[i].click();return t;}}return "";})()';
}
// ★ 2026-09-30：写日志前脱敏 —— 邮箱/手机号一律打码（页面文字里常带账号）
function _stkScrub(s) {
    return String(s == null ? '' : s)
        .replace(/[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}/g, '<邮箱已隐藏>')
        .replace(/\b1[3-9]\d{9}\b/g, '<手机号已隐藏>')
        .replace(/\b\d{3}-\d{3}-\d{4}\b/g, '<电话已隐藏>');
}

async function _stkProbe(w) {
    const s = await _stkExec(w, _stkProbeJs());
    try { return JSON.parse(s) || {}; } catch (e) { return {}; }
}
// ★ 把 probe 结果翻成一句人话（日志里看这个，别再看 JSON）
function _stkDesc(st) {
    st = st || {};
    const b = [];
    b.push(st.hasEmail ? '有邮箱框' : '无邮箱框');
    b.push(st.hasPwd ? '有密码框' : '无密码框');
    if (st.signInView) { b.push('显示登录入口'); }
    if (st.uploadView) { b.push('已是上传界面'); }
    if (st.hasCaptcha) { b.push('⚠要验证码'); }
    if (st.hasOtp) { b.push('⚠要OTP'); }
    if (st.needHuman) { b.push('⚠需要人工验证'); }
    if (st.loggedIn) { b.push('✓已登录(有账号问候语)'); }
    return b.join('、');
}

function _stkFindView() {
    // 页面里唯一的 <webview>（授权弹窗里那个）
    try {
        for (const w of webContents.getAllWebContents()) {
            try { if (w.getType() === 'webview') { return w; } } catch (e) {}
        }
    } catch (e) {}
    return null;
}

async function stkLoginAutomation(email, password, say) {
    // ★ 2026-09-30 v5：登录窗口默认隐藏；需要人工介入（验证码/OTP/没进展）时才显示出来
    const w = new BrowserWindow({
        width: 900, height: 760, show: false,
        title: '需要验证码 / OTP —— 请在这里完成',
        webPreferences: { partition: 'persist:amazon', contextIsolation: true, nodeIntegration: false },
    });
    let interactive = false;
    const showWin = (why) => {
        if (interactive) { return; }
        interactive = true;
        try { w.show(); w.setTitle('需要你操作：' + why); } catch (e) {}
        say('NEED', why + '：已弹出登录窗口，请在那里完成，完成后我会自动继续');
    };
    try {
        say('INFO', '打开 Amazon 登录页…');
        await w.loadURL('https://www.amazon.com/sendtokindle');
        await _stkSleep(4000);
        let st = await _stkProbe(w);
        say('INFO', '第1步 页面状态：' + _stkDesc(st));
        if (st.uploadView && !st.signInView) { stkSetState(true, 'already'); say('OK', '已经授权过了，无需重复登录'); return { ok: true, already: true }; }
        // ① 先点页面自己的登录入口（SPA 的登录视图）
        if (st.signInView && !st.hasEmail) {
            say('INFO', '点击页面上的登录入口…');
            const hit = await _stkExec(w, _stkClickTextJs('sign\\s*in|登录|登入'));
            say('INFO', '命中入口：' + (hit || '(没找到，直接看看表单)'));
            await _stkSleep(3500);
            st = await _stkProbe(w);
            say('INFO', '第2步（点完入口）：' + _stkDesc(st));
        }
        // ② 邮箱+密码可能同页：有哪个填哪个，然后提交一次（★ 关键修正）
        if (st.hasEmail && st.hasPwd) {
            say('INFO', '邮箱+密码同页 → 一次填完再提交');
            await _stkExec(w, _stkFillJs(['#ap_email', 'input[name="email"]', 'input[type="email"]'], email));
            await _stkExec(w, _stkFillJs(['#ap_password', 'input[name="password"]', 'input[type="password"]'], password));
            await _stkExec(w, _stkClickJs(['#signInSubmit', 'input[name="signIn"]', 'button[type="submit"]', '#continue input', '#continue']));
        } else if (st.hasEmail) {
            say('INFO', '只有邮箱框 → 填邮箱后继续');
            await _stkExec(w, _stkFillJs(['#ap_email', 'input[name="email"]', 'input[type="email"]'], email));
            await _stkExec(w, _stkClickJs(['#continue input', '#continue', 'input[name="continue"]', 'input[type="submit"]']));
            await _stkSleep(3000);
            st = await _stkProbe(w);
            say('INFO', '第3步（提交邮箱后）：' + _stkDesc(st));
            if (st.hasPwd) {
                say('INFO', '填写密码并提交…');
                await _stkExec(w, _stkFillJs(['#ap_password', 'input[name="password"]', 'input[type="password"]'], password));
                await _stkExec(w, _stkClickJs(['#signInSubmit', 'input[name="signIn"]', 'button[type="submit"]']));
            }
        } else if (st.hasPwd) {
            say('INFO', '只有密码框 → 直接填密码提交');
            await _stkExec(w, _stkFillJs(['#ap_password', 'input[name="password"]', 'input[type="password"]'], password));
            await _stkExec(w, _stkClickJs(['#signInSubmit', 'input[name="signIn"]', 'button[type="submit"]']));
        } else {
            say('INFO', '页面上没有登录表单（可能已登录，或需要人工）');
        }
        // ③ 轮询结果；需要人工就放窗口；20 秒没进展也让用户接管
        for (let i = 0; i < 120; i += 1) {
            await _stkSleep(1000);
            st = await _stkProbe(w);
            // ★ 2026-09-30：登录成功的铁证 = 右上角出现账号问候语（Hello, xxx）或退出登录链接；
            //   原来只认 #s2k-home-wrapper-upload-view / input[type=file]，而这个页面的文件框是按需生成的，
            //   于是「明明已登录」却一直等（用户实测反馈）。
            const isIn = st.loggedIn || (st.uploadView && !st.signInView && !st.hasEmail && !st.hasPwd);
            if (isIn) {
                stkSetState(true, 'login');
                say('OK', '登录成功，授权已保存（以后传书不用再登录）');
                interactive = false;                      // 允许 finally 关窗口
                try { if (!w.isDestroyed()) { w.destroy(); } } catch (e) {}
                return { ok: true };
            }
            if (st.hasCaptcha || st.hasOtp || st.needHuman) {
                showWin(st.hasCaptcha ? 'Amazon 要求验证码' : (st.hasOtp ? 'Amazon 要求短信/邮件验证码' : 'Amazon 要求额外验证'));
            }
            if (i === 20 && !interactive) {
                const txt = await _stkExec(w, '(function(){return (document.body?document.body.innerText:"").replace(/\\s+/g," ").slice(0,160);})()');
                say('INFO', '20 秒没有进展，页面文字：' + _stkScrub(String(txt || '')).slice(0, 140));
                showWin('自动登录没能完成（可能密码/验证需要你确认）');
            }
            if (i % 20 === 19) { say('INFO', '仍在等待…当前：' + _stkDesc(st)); }
        }
        say('ERR', '超时：还没检测到登录成功');
        return { ok: false, error: '超时，未检测到登录成功（可再点一次登录）' };
    } catch (e) {
        say('ERR', String((e && e.message) || e));
        return { ok: false, error: String((e && e.message) || e) };
    } finally {
        if (!interactive) { setTimeout(() => { try { if (!w.isDestroyed()) { w.destroy(); } } catch (e) {} }, 1500); }
    }
}
// ★ 2026-09-30：清空登录信息（换账号用）—— 清 persist:amazon 的 cookie/存储 + 删标记文件
ipcMain.handle('stk-logout', async () => {
    try {
        const ses = session.fromPartition('persist:amazon');
        await ses.clearStorageData({
            storages: ['cookies', 'localstorage', 'indexdb', 'cachestorage',
                       'serviceworkers', 'websql', 'shadercache', 'filesystem'],
        });
        try { await ses.clearCache(); } catch (e) {}
        stkSetState(false);
        logUpload('STK-LOGOUT', '登录信息已清空（换账号）');
        return { ok: true };
    } catch (e) {
        logUpload('STK-LOGOUT-ERR', String((e && e.message) || e));
        return { ok: false, error: String((e && e.message) || e) };
    }
});

ipcMain.handle('stk-login', async (event, payload) => {
    const email = String((payload && payload.email) || '').trim();
    const password = String((payload && payload.password) || '');
    if (!email || !password) { return { ok: false, error: '请填 Amazon 邮箱和密码' }; }
    const win = BrowserWindow.fromWebContents(event.sender);
    const say = (tag, msg) => {
        try { if (win && !win.isDestroyed()) { win.webContents.send('stk-auth-state', { tag: tag, msg: _stkScrub(msg) }); } } catch (e) {}
        logUpload('STK-AUTH-' + tag, _stkScrub(msg));
    };
    return await stkLoginAutomation(email, password, say);
});


// 1. 打开 Kindle 邮箱配置小窗口
// 1.5 打开 Kindle 操作指引窗口（图文教程）
ipcMain.handle('kindle-open-help', async () => {
    try {
        const helpWin = new BrowserWindow({
            width: 920,
            height: 720,
            title: 'Kindle 传书 · 完整操作指引',
            backgroundColor: '#0B0D10',
            webPreferences: {
                // 指引页要跟随主界面的中英切换（window.pageLang 语言桥）
                preload: path.join(__dirname, 'kindle-help-preload.js'),
                contextIsolation: true,
                nodeIntegration: false
            },
            icon: path.join(__dirname, 'icon.ico')
        });
        helpWin.loadFile('kindle-help.html');
        return { success: true };
    } catch (e) {
        return { success: false, error: e.message };
    }
});

// 2. 读取 Kindle 邮箱配置
// 3. 保存 Kindle 邮箱配置
// 4. 测试 SMTP 连接（直接调 kindle_send.py --test）
// 5. 发送文件到 Kindle（直接运行 kindle_send.py，日志流回主窗口）
// ================================================================
// ★★★ Kindle 批量推送：一本一本连着发 ★★★
// ================================================================
// Kindle 走 SMTP，每本一封独立邮件。这里在 Node 侧循环，
// kindle_send.py 保持"一文件一进程"的简单结构：
//   · 某本失败不影响后面的（各自独立连接）
//   · 日志天然按本分段，看得清
// 每本之间的间隔从设置读（秒 → 毫秒）。给 SMTP 留余量，避免被限流。
function kindleDelayMs() { return Math.max(0, Number(SETTINGS.kindleDelay) || 0) * 1000; }
let kindleBatchAborted = false;

function sleep(ms) {
    return new Promise((r) => setTimeout(r, ms));
}

// ★ 连子进程一起杀：bypy / chromedriver / smtp 这类子进程自己还会再起子进程，
//   而子进程继承了我们的 stdout/stderr 管道句柄。只 child.kill() 的话管道
//   永远等不到 EOF —— 后续任何读管道的动作都会永久挂住（2026-09-23 实测
//   get_auth_code.py 就是这样「卡住不动」的）。
function _killTree(child) {
    if (!child) return;
    try {
        if (process.platform === 'win32' && child.pid) {
            execFile('taskkill', ['/F', '/T', '/PID', String(child.pid)], () => {});
        } else {
            child.kill('SIGKILL');
        }
    } catch (e) {}
}

// 发一本，包成 Promise 方便循环等待
// 支持传单个路径（老调用方式）或路径数组（批量）
// 6. 停止 Kindle 推送
// ================================================================
// ★★★ 书库：读取 export 目录、改元数据、删除 ★★★
// ================================================================
// EPUB 就是 zip，交给 Python 后端的 library_scan.py 处理
// （标准库自带 zipfile，不用给 Node 加依赖）。
function callLibraryBackend(payload) {
    return new Promise((resolve) => {
        const script = getPythonScript('library_scan.py');
        const pythonPath = findPython();

        if (!fs.existsSync(script)) {
            resolve({ success: false, error: 'library_scan.py 不存在' });
            return;
        }

        const child = exec(
            `"${pythonPath}" -X utf8 "${script}"`,
            {
                maxBuffer: 1024 * 1024 * 100,
                encoding: 'utf-8',
                env: { ...process.env, PYTHONIOENCODING: 'utf-8' },
                timeout: 180000
            },
            (error, stdout, stderr) => {
                if (stderr) console.log('📚 书库后端:', String(stderr).trim());
                if (error) {
                    resolve({ success: false, error: error.message, stderr });
                    return;
                }
                try {
                    const line = String(stdout).split('\n').find(l => l.trim().startsWith('{'));
                    if (!line) throw new Error('后端没有输出 JSON');
                    resolve(JSON.parse(line));
                } catch (e) {
                    resolve({ success: false, error: '解析失败: ' + e.message, stdout });
                }
            }
        );

        child.stdin.write(JSON.stringify(payload));
        child.stdin.end();
    });
}

// 扫描书库
ipcMain.handle('library-scan', async () => {
    console.log('📚 扫描书库:', getExportDir());
    const r = await callLibraryBackend({ action: 'scan', dir: getExportDir() });
    if (r && r.success) console.log(`📚 找到 ${r.books.length} 项`);
    else console.warn('⚠️ 扫描失败:', r && r.error);
    return r;
});

// 修改书名 / 作者 / 简介
ipcMain.handle('library-update', async (event, payload) => {
    if (!payload || typeof payload !== 'object') {
        return { success: false, error: '参数无效' };
    }
    // ★ 安全修复：原来直接把 payload.path 透传给后端，可原地改写任意 zip/epub 的元数据。
    let safePath;
    try {
        safePath = assertPathAllowed(payload.path, '修改元数据');
    } catch (e) {
        console.warn('🚫 拒绝修改:', e.message);
        return { success: false, error: e.message };
    }
    console.log('📚 修改元数据:', safePath);
    return await callLibraryBackend({
        action: 'update',
        path: safePath,
        title: payload.title,
        author: payload.author,
        description: payload.description
    });
});

// 删除（支持多选）
ipcMain.handle('library-delete', async (event, paths) => {
    const list = Array.isArray(paths) ? paths : [paths];
    // ★ 安全修复：原来把任意路径透传给后端 os.remove()，等于任意文件删除。
    //   现在逐个校验必须位于书库/下载目录内。
    let safeList;
    try {
        safeList = list.map(p => assertPathAllowed(p, '删除'));
    } catch (e) {
        console.warn('🚫 拒绝删除:', e.message);
        return { success: false, error: e.message, deleted: [], failed: [] };
    }
    console.log('📚 删除:', safeList.length, '项');
    return await callLibraryBackend({ action: 'delete', paths: safeList });
});

// 在资源管理器中显示（不传参就打开 export 目录）
ipcMain.handle('library-reveal', async (event, target) => {
    try {
        if (target && fs.existsSync(target)) {
            shell.showItemInFolder(target);
        } else {
            if (!fs.existsSync(getExportDir())) fs.mkdirSync(getExportDir(), { recursive: true });
            shell.openPath(getExportDir());
        }
        return { success: true };
    } catch (e) {
        return { success: false, error: e.message };
    }
});

// 书库目录本身（界面要用它显示路径）
ipcMain.handle('library-dir', async () => {
    return { success: true, dir: getExportDir() };
});

// ★ 导入外部书籍：弹系统文件选择框，把选中的书复制进书库目录。
//   外来的 EPUB 不需要任何转译——阅读器（foliate-js）本来就是通用解析器，
//   一并放开的 mobi/azw3/fb2/cbz 也能直接读。顺手做一次魔数校验，
//   免得选进来的其实是别的文件（或在选择框里手改了扩展名）。
const IMPORT_EXT = ['.epub', '.mobi', '.azw3', '.azw', '.fb2', '.fbz', '.cbz'];

function sniffEbookKind(buf) {
    if (!buf || buf.length < 4) return null;
    if (buf[0] === 0x50 && buf[1] === 0x4b && (buf[2] === 0x03 || buf[2] === 0x05 || buf[2] === 0x07)) return 'zip';   // EPUB / FBZ / CBZ
    if (buf.toString('latin1', 60, 68) === 'BOOKMOBI') return 'mobi';                                                    // MOBI / AZW / AZW3
    const head = buf.toString('utf8', 0, 400).replace(/^\uFEFF/, '').trimStart();
    if (head.startsWith('<?xml') || head.startsWith('<FictionBook')) return 'fb2';
    return null;
}

ipcMain.handle('library-import', async (event, injectedPaths) => {
    // ★ 只给自动化测试用的旁路：探针脚本没法点系统文件框，用 EPUBUI_IMPORT_PATHS
    //   把选择结果喂进来，走的还是下面同一段导入逻辑（含魔数校验/重名处理）。
    //   正常安装运行时这个变量是空的，等于不存在。
    let paths = injectedPaths;
    if (!Array.isArray(paths) || !paths.length) {
        paths = null;
        const envPaths = (process.env.EPUBUI_IMPORT_PATHS || '').split('|').map(s => s.trim()).filter(Boolean);
        if (envPaths.length) {
            console.log('🧪 [测试旁路] 从 EPUBUI_IMPORT_PATHS 取到', envPaths.length, '个路径');
            paths = envPaths;
        }
    }
    if (!paths) {
        const win = BrowserWindow.fromWebContents(event.sender);
        const picked = await dialog.showOpenDialog(win || undefined, {
            title: '导入书籍到书库',
            buttonLabel: '导入',
            properties: ['openFile', 'multiSelections'],
            filters: [
                { name: '电子书', extensions: ['epub', 'mobi', 'azw3', 'azw', 'fb2', 'fbz', 'cbz'] },
                { name: 'EPUB', extensions: ['epub'] },
                { name: '全部文件', extensions: ['*'] }
            ]
        }).catch(e => ({ canceled: true, error: e.message }));
        if (!picked || picked.canceled || !picked.filePaths || !picked.filePaths.length) {
            return { success: true, canceled: true, imported: [], failed: [] };
        }
        paths = picked.filePaths;
    }

    const dir = getExportDir();
    if (!fs.existsSync(dir)) fs.mkdirSync(dir, { recursive: true });

    const imported = [];
    const failed = [];
    const skipped = [];   // ★ 2026-09-27：内容已经在书库里的（不再派生 _1/_2 副本）
    for (const src of paths) {
        try {
            const base = path.basename(src);
            if (!fs.statSync(src).isFile()) throw new Error('不是文件');
            const ext = path.extname(base).toLowerCase();
            if (!IMPORT_EXT.includes(ext)) throw new Error('不支持的格式 ' + (ext || '(无扩展名)'));
            const kind = sniffEbookKind(fs.readFileSync(src).subarray(0, 400));
            if (!kind) throw new Error('内容不是电子书（扩展名与实际格式不符？）');

            // ★ 2026-09-27：内容完全一样的书，库里已经有一本就够了 —— 跳过
            //   （以前会派生 书名_1.epub，书库看起来就是「同一本书好几本」）
            const dupPath = findSameContentInExport(src, null);
            if (dupPath) {
                console.log('📥 内容重复，跳过导入:', base, '（书库里已有', path.basename(dupPath), '）');
                skipped.push({ file: base, existing: path.basename(dupPath) });
                continue;
            }

            // 同名但内容不同：不覆盖用户已有的书 → 书名_1.epub
            const nameBase = path.basename(base, ext);
            let target = path.join(dir, base);
            let n = 0;
            while (fs.existsSync(target)) {
                n += 1;
                target = path.join(dir, `${nameBase}_${n}${ext}`);
                if (n > 500) throw new Error('同名文件太多');
            }
            if (path.resolve(target) === path.resolve(src)) throw new Error('这本已经在书库里了');

            fs.copyFileSync(src, target);
            console.log('📥 已导入:', base, '→', path.basename(target));
            imported.push({ file: path.basename(target), path: target, kind });
        } catch (e) {
            console.warn('⚠️ 导入失败:', path.basename(src), e.message);
            failed.push({ file: path.basename(src), error: e.message });
        }
    }
    return { success: true, canceled: false, dir, imported, failed, skipped };
});

// 选一个目录（输出目录用）
ipcMain.handle('pick-directory', async () => {
    try {
        const r = await dialog.showOpenDialog(mainWindow, {
            title: '选择输出目录',
            properties: ['openDirectory', 'createDirectory']
        });
        if (r.canceled || !r.filePaths.length) return { success: false, canceled: true };
        return { success: true, dir: r.filePaths[0] };
    } catch (e) {
        return { success: false, error: e.message };
    }
});



// ================================================================
// ★★★ 书库批量传输：统一的待传清单 ★★★
// ================================================================
// 三个通道共用一份清单。书库设置它，然后打开对应的现有界面；
// 界面通过 batch-get 读到清单，用原有的按钮和流程执行。
//   target: 'wifi' | 'kindle' | 'applebooks'
let pendingBatch = null;   // { target, files: [...] }

ipcMain.handle('batch-set', async (event, target, files) => {
    const list = Array.isArray(files) ? files.filter((p) => p && fs.existsSync(p)) : [];
    pendingBatch = list.length ? { target: target, files: list } : null;
    console.log('📚 批量清单:', target, list.length, '本');
    return { success: true, count: list.length };
});

ipcMain.handle('batch-get', async (event, wantTarget) => {
    if (!pendingBatch) return { success: true, count: 0, files: [] };
    // 允许界面只取属于自己通道的清单
    if (wantTarget && pendingBatch.target !== wantTarget) {
        return { success: true, count: 0, files: [], otherTarget: pendingBatch.target };
    }
    return { success: true, count: pendingBatch.files.length, files: pendingBatch.files.slice() };
});

ipcMain.handle('batch-clear', async () => {
    pendingBatch = null;
    return { success: true };
});

// ================================================================
// ★★★ 2026-09-26：一次任务 = 一条独立 sequence ★★★
// ================================================================
// 用户原话：「每次任务的 sequence 都是独立的」—— 选的做法是
// 「开始新任务时把上一次的 sendqueue 清空重置」（比「每任务一个目录」简单）。
//
// 病根（用户实测：传完一次一键、再传一次一键，上一次的书还在队列里）：
//   1) pendingBatch 与界面上的 BATCH 都是只追加、从不在任务边界清空；
//      batch-clear 以前只有 batch-recv.js / wifi-page-preload.js 两个调用者，
//      没有任何地方在「新任务开始」时清清单；
//   2) wipeStageDir 只清本通道 —— 苹果/百度用根目录时会 continue 跳过
//      _wifi / _kindle，所以换通道后上一次的暂存文件还躺在磁盘上。
//
// 规矩：一键 / 「已导入 TXT 一键」/ 书库「批量传输」= 各算一条新 sequence，
//       开跑前把「清单 + 暂存目录」整体重置；任务内部仍可累积多本。
//       发送前的 wipeStageDir（只清本通道）保留，作为多通道并发的第二层保险。
// 注意：「下载小说」不调用它（下载自己会清 download\，且下载后往往还要
//       接着转换/追加，属于同一条 sequence）；发送完成后也不自动清队列。
let taskSeq = 0;
function beginNewTask(reason) {
    taskSeq += 1;
    const clearedPending = pendingBatch ? pendingBatch.files.length : 0;
    pendingBatch = null;

    // 暂存目录整体重建（连 _wifi / _kindle 一起 —— 这才是「sendqueue 已更新」）
    let wiped = 0;
    try {
        if (fs.existsSync(SENDQUEUE_DIR)) {
            wiped = fs.readdirSync(SENDQUEUE_DIR).length;
            fs.rmSync(SENDQUEUE_DIR, { recursive: true, force: true });
        }
        fs.mkdirSync(SENDQUEUE_DIR, { recursive: true });
        for (const sub of Object.values(STAGE_SUBDIRS)) {
            fs.mkdirSync(path.join(SENDQUEUE_DIR, sub), { recursive: true });
        }
    } catch (e) {
        console.warn('重置暂存目录失败:', e.message);
    }

    // 通知所有窗口把界面上的「待发送队列」清空
    try {
        BrowserWindow.getAllWindows().forEach((w) => {
            if (!w.isDestroyed()) {
                w.webContents.send('transfer-task-reset', { seq: taskSeq, reason: reason || '' });
            }
        });
    } catch (_) {}

    logUpload('TASK', JSON.stringify({
        phase: 'begin', seq: taskSeq, reason: reason || '',
        clearedPending: clearedPending, wipedStageEntries: wiped, stageDir: SENDQUEUE_DIR
    }));
    console.log(`🆕 新任务 #${taskSeq}（${reason || ''}）：清单 ${clearedPending} 本已清，暂存目录已重建`);
    return { success: true, seq: taskSeq, reason: reason || '' };
}

ipcMain.handle('batch-new-task', async (event, reason) => beginNewTask(reason));

// 书库导出：把选中的文件复制到用户指定目录
ipcMain.handle('library-export', async (event, paths) => {
    const list = (Array.isArray(paths) ? paths : [paths]).filter((p) => p && fs.existsSync(p));
    if (!list.length) return { success: false, error: '没有可导出的文件' };

    try {
        const r = await dialog.showOpenDialog(mainWindow, {
            title: '选择导出目录',
            properties: ['openDirectory', 'createDirectory']
        });
        if (r.canceled || !r.filePaths.length) return { success: false, canceled: true };

        const dir = r.filePaths[0];
        const done = [];
        const failed = [];
        for (const src of list) {
            try {
                let target = path.join(dir, path.basename(src));
                // 重名就加 _1 _2 …（不覆盖用户已有文件）
                if (fs.existsSync(target)) {
                    const ext = path.extname(src);
                    const base = path.basename(src, ext);
                    let n = 1;
                    while (fs.existsSync(path.join(dir, `${base}_${n}${ext}`))) n++;
                    target = path.join(dir, `${base}_${n}${ext}`);
                }
                fs.copyFileSync(src, target);
                done.push(target);
            } catch (e) {
                console.warn('⚠️ 导出失败:', src, e.message);
                failed.push(path.basename(src));
            }
        }
        console.log('📤 导出完成:', done.length, '成功 /', failed.length, '失败 →', dir);
        return { success: true, dir, count: done.length, failed };
    } catch (e) {
        return { success: false, error: e.message };
    }
});

/* ============================================================
   内置阅读窗口（foliate-js 引擎，见 reader.html / reader.js）
   - 书库双击一本书 → 打开这里
   - 窗口单例：已经开着时只换书（不叠一摞窗口）
   - 书的解析完全在渲染进程里做（foliate-js 能直接 fetch 本地文件），
     主进程只负责开窗和把路径传过去
   ============================================================ */
let readerWin = null;

ipcMain.handle('reader-open', async (event, filePath) => {
    try {
        if (!filePath || typeof filePath !== 'string') {
            return { success: false, error: '缺少书籍路径' };
        }
        const abs = path.resolve(filePath);
        if (!fs.existsSync(abs)) {
            return { success: false, error: '文件不存在：' + abs };
        }
        const ext = path.extname(abs).toLowerCase();
        if (ext !== '.epub') {
            return { success: false, error: '阅读器目前只支持 EPUB（这个文件是 ' + ext + '）' };
        }

        // 已经开着 → 直接换书
        if (readerWin && !readerWin.isDestroyed()) {
            readerWin.loadFile('reader.html', { query: { file: abs } });
            readerWin.show();
            readerWin.focus();
            return { success: true, reused: true };
        }

        readerWin = new BrowserWindow({
            width: 1180,
            height: 820,
            minWidth: 760,
            minHeight: 520,
            title: 'EasyPub 阅读',
            backgroundColor: '#0B0D10',
            autoHideMenuBar: true,
            webPreferences: {
                preload: path.join(__dirname, 'reader-preload.js'),
                contextIsolation: true,
                nodeIntegration: false
            },
            icon: path.join(__dirname, 'icon.ico')
        });
        await readerWin.loadFile('reader.html', { query: { file: abs } });
        readerWin.on('closed', () => { readerWin = null; });
        console.log('📖 打开阅读窗口:', path.basename(abs));
        return { success: true };
    } catch (e) {
        console.error('❌ 打开阅读窗口失败:', e.message);
        return { success: false, error: e.message };
    }
});
