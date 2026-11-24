'use strict';

const fs = require('fs');
const path = require('path');
const http = require('http');
const https = require('https');
const net = require('net');
const tls = require('tls');
const crypto = require('crypto');
const { spawn, execFile } = require('child_process');

const SUBSCRIPTION_URL = process.env.EASYPUB_PROXY_SUB_URL || '';
const GOOGLE_204_URL = 'https://www.gstatic.com/generate_204';
const PREFERRED_REGION_RE = /(日本|🇯🇵|\bJP\b|Tokyo|Osaka|韩国|🇰🇷|\bKR\b|Seoul|台湾|🇹🇼|\bTW\b|Taipei|香港|🇭🇰|\bHK\b|Hong\s*Kong|新加坡|🇸🇬|\bSG\b|Singapore|狮城)/i;
const INFO_NODE_RE = /(剩余流量|套餐到期|过期时间|官网|距离下次重置|流量重置|到期时间|expire|traffic|reset)/i;

function probeTcp(host, port, timeoutMs = 1200) {
    return new Promise((resolve) => {
        if (!host || !port) { resolve(false); return; }
        const sock = new net.Socket();
        let done = false;
        const finish = (ok) => {
            if (done) return;
            done = true;
            try { sock.destroy(); } catch (e) {}
            resolve(ok);
        };
        sock.setTimeout(timeoutMs);
        sock.once('connect', () => finish(true));
        sock.once('timeout', () => finish(false));
        sock.once('error', () => finish(false));
        try {
            sock.connect(Number(port), host);
        } catch (e) {
            finish(false);
        }
    });
}

async function findFreePort(startPort, forbiddenPorts = new Set()) {
    for (let p = startPort; p < startPort + 120; p++) {
        if (forbiddenPorts.has(p)) continue;
        // Check if currently free by briefly binding 127.0.0.1
        const ok = await new Promise((resolve) => {
            const srv = net.createServer();
            srv.unref();
            srv.once('error', () => resolve(false));
            srv.listen(p, '127.0.0.1', () => {
                srv.close(() => resolve(true));
            });
        });
        if (ok) return p;
    }
    return startPort + 200;
}

function parseWinProxyServer(raw) {
    const s = String(raw || '').trim();
    if (!s) return null;
    // Possible formats: "127.0.0.1:7897" OR "http=127.0.0.1:7897;https=127.0.0.1:7897"
    let target = s;
    if (s.includes('=')) {
        const parts = s.split(';').map(x => x.trim()).filter(Boolean);
        const httpsPart = parts.find(p => /^https=/i.test(p));
        const httpPart = parts.find(p => /^http=/i.test(p));
        const picked = httpsPart || httpPart || parts[0] || '';
        target = picked.replace(/^https?=/i, '').trim();
    }
    target = target.replace(/^https?:\/\//i, '').replace(/\/+$/, '');
    const m = target.match(/^([a-zA-Z0-9.\-_:\[\]]+):(\d+)$/);
    if (!m) return null;
    return {
        host: m[1].replace(/^\[|\]$/g, ''),
        port: parseInt(m[2], 10),
        url: `http://${target}`
    };
}

function detectWindowsSystemProxy() {
    return new Promise((resolve) => {
        if (process.platform !== 'win32') {
            resolve({ detected: false, enabled: false, server: '', url: '', alive: false });
            return;
        }
        execFile('reg', [
            'query',
            'HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Internet Settings'
        ], { encoding: 'utf8', windowsHide: true, timeout: 2500 }, async (err, stdout) => {
            if (err || !stdout) {
                resolve({ detected: false, enabled: false, server: '', url: '', alive: false });
                return;
            }
            const enMatch = stdout.match(/ProxyEnable\s+REG_DWORD\s+(0x[0-9a-fA-F]+|\d+)/);
            const srvMatch = stdout.match(/ProxyServer\s+REG_SZ\s+([^\r\n]+)/);
            const enabled = enMatch ? parseInt(enMatch[1], enMatch[1].startsWith('0x') ? 16 : 10) === 1 : false;
            const rawServer = srvMatch ? srvMatch[1].trim() : '';
            const parsed = parseWinProxyServer(rawServer);
            if (!enabled || !parsed) {
                resolve({ detected: false, enabled, server: rawServer, url: '', port: 0, alive: false });
                return;
            }
            const alive = await probeTcp(parsed.host, parsed.port, 1000);
            resolve({
                detected: true,
                enabled: true,
                server: rawServer,
                host: parsed.host,
                port: parsed.port,
                url: parsed.url,
                alive
            });
        });
    });
}

function httpRequestViaProxy(targetUrl, proxyUrl, timeoutMs = 8000, headers = {}) {
    return new Promise((resolve, reject) => {
        const u = new URL(targetUrl);
        const p = new URL(proxyUrl);
        const req = http.request({
            host: p.hostname,
            port: Number(p.port),
            method: 'CONNECT',
            path: `${u.hostname}:${u.port || (u.protocol === 'https:' ? 443 : 80)}`,
            timeout: timeoutMs
        });
        let settled = false;
        const done = (err, val) => {
            if (settled) return;
            settled = true;
            if (err) reject(err); else resolve(val);
        };
        req.on('connect', (res, socket) => {
            if (res.statusCode !== 200) {
                socket.destroy();
                done(new Error(`CONNECT status ${res.statusCode}`));
                return;
            }
            if (u.protocol === 'https:') {
                const tlsSock = tls.connect({
                    socket,
                    servername: u.hostname,
                    timeout: timeoutMs
                }, () => {
                    sendHttpOnSocket(tlsSock, u, headers, timeoutMs, done);
                });
                tlsSock.on('error', (e) => done(e));
                tlsSock.setTimeout(timeoutMs, () => { tlsSock.destroy(); done(new Error('TLS timeout')); });
            } else {
                sendHttpOnSocket(socket, u, headers, timeoutMs, done);
            }
        });
        req.on('timeout', () => { req.destroy(); done(new Error('Proxy CONNECT timeout')); });
        req.on('error', (e) => done(e));
        req.end();
    });
}

function sendHttpOnSocket(sock, u, headers, timeoutMs, done) {
    const reqPath = (u.pathname || '/') + (u.search || '');
    const allHeaders = Object.assign({
        Host: u.host,
        Connection: 'close',
        'Accept-Encoding': 'identity',
        'User-Agent': 'clash-verge/v2.0.2'
    }, headers || {});
    let rawReq = `GET ${reqPath} HTTP/1.1\r\n`;
    for (const [k, v] of Object.entries(allHeaders)) {
        rawReq += `${k}: ${v}\r\n`;
    }
    rawReq += '\r\n';
    const chunks = [];
    sock.setTimeout(timeoutMs, () => { sock.destroy(); done(new Error('HTTP read timeout')); });
    sock.on('data', (c) => chunks.push(c));
    sock.on('error', (e) => done(e));
    sock.on('end', () => {
        try {
            const buf = Buffer.concat(chunks);
            const idx = buf.indexOf('\r\n\r\n');
            if (idx < 0) { done(new Error('Invalid HTTP response')); return; }
            const head = buf.slice(0, idx).toString('latin1');
            let body = buf.slice(idx + 4);
            const statusLine = head.split('\r\n')[0] || '';
            const sm = statusLine.match(/HTTP\/\d\.\d\s+(\d+)/);
            const statusCode = sm ? parseInt(sm[1], 10) : 0;
            if (/transfer-encoding:\s*chunked/i.test(head)) {
                body = decodeChunked(body);
            }
            done(null, { statusCode, body });
        } catch (e) {
            done(e);
        }
    });
    sock.write(rawReq);
}

function decodeChunked(buf) {
    const out = [];
    let pos = 0;
    while (pos < buf.length) {
        const rn = buf.indexOf('\r\n', pos);
        if (rn < 0) break;
        const sizeHex = buf.slice(pos, rn).toString('ascii').split(';')[0].trim();
        const size = parseInt(sizeHex, 16);
        if (!size || isNaN(size)) break;
        pos = rn + 2;
        out.push(buf.slice(pos, pos + size));
        pos += size + 2;
    }
    return Buffer.concat(out);
}

function httpGetDirect(urlStr, headers = {}, timeoutMs = 10000) {
    return new Promise((resolve, reject) => {
        const u = new URL(urlStr);
        const lib = u.protocol === 'https:' ? https : http;
        const req = lib.get({
            hostname: u.hostname,
            port: u.port || undefined,
            path: u.pathname + u.search,
            headers: Object.assign({ 'User-Agent': 'clash-verge/v2.0.2' }, headers),
            timeout: timeoutMs
        }, (res) => {
            if (res.statusCode >= 300 && res.statusCode < 400 && res.headers.location) {
                const next = new URL(res.headers.location, u).toString();
                res.resume();
                httpGetDirect(next, headers, timeoutMs).then(resolve, reject);
                return;
            }
            const chunks = [];
            res.on('data', (c) => chunks.push(c));
            res.on('end', () => resolve({ statusCode: res.statusCode || 0, body: Buffer.concat(chunks) }));
            res.on('error', reject);
        });
        req.on('timeout', () => { req.destroy(); reject(new Error('Request timeout')); });
        req.on('error', reject);
    });
}

function parseSubscriptionProxies(rawText) {
    let text = String(rawText || '').trim();
    if (!text) throw new Error('订阅内容为空');

    // If base64-encoded YAML without `proxies:`, try decoding base64 first
    if (!/^\s*proxies\s*:/m.test(text) && /^[A-Za-z0-9+/=\r\n_-]+$/.test(text.replace(/\s+/g, ''))) {
        try {
            const decoded = Buffer.from(text.replace(/\s+/g, ''), 'base64').toString('utf8');
            if (/^\s*proxies\s*:/m.test(decoded) || /name\s*:/m.test(decoded)) {
                text = decoded;
            }
        } catch (e) {}
    }

    const lines = text.split(/\r?\n/);
    const startIdx = lines.findIndex((l) => /^proxies\s*:\s*$/.test(l));
    if (startIdx < 0) {
        throw new Error('订阅文本中未找到 proxies: 节点列表');
    }

    const proxyLines = [];
    for (let i = startIdx + 1; i < lines.length; i++) {
        const line = lines[i];
        if (!line.trim() || line.trim().startsWith('#')) continue;
        // Stop when a top-level YAML key begins
        if (/^[a-zA-Z0-9_-]+\s*:/.test(line)) break;
        if (/^\s+-\s*\{/.test(line) || /^\s+-\s+name\s*:/.test(line) || /^\s{2,}[a-zA-Z0-9_-]+\s*:/.test(line) || /^\s{2,}/.test(line)) {
            proxyLines.push(line);
        }
    }

    if (!proxyLines.length) {
        throw new Error('订阅 proxies 列表为空');
    }

    // Extract node names cleanly
    const names = [];
    for (const line of proxyLines) {
        const mQuoted = line.match(/-\s*\{\s*name\s*:\s*(['"])(.*?)\1/);
        const mPlain = line.match(/-\s*\{\s*name\s*:\s*([^,}]+)\s*,/);
        const mBlock = line.match(/^\s+-\s+name\s*:\s*(['"]?)(.+?)\1\s*$/);
        let name = '';
        if (mQuoted) name = mQuoted[2];
        else if (mPlain) name = mPlain[1];
        else if (mBlock) name = mBlock[2];
        name = String(name || '').trim().replace(/^['"]|['"]$/g, '');
        if (name && !names.includes(name)) names.push(name);
    }

    const realNodes = names.filter((n) => !INFO_NODE_RE.test(n));
    const usableNodes = realNodes.length ? realNodes : names;
    if (!usableNodes.length) {
        throw new Error('未解析到可用代理节点名');
    }

    return {
        rawProxiesBlock: 'proxies:\n' + proxyLines.join('\n'),
        allNames: names,
        usableNodes
    };
}

function yamlSingleQuote(s) {
    return "'" + String(s).replace(/'/g, "''") + "'";
}

function classifyRegion(name) {
    const s = String(name || '');
    if (/日本|🇯🇵|\bJP\b|Tokyo|Osaka/i.test(s)) return 'JP';
    if (/韩国|🇰🇷|\bKR\b|Seoul/i.test(s)) return 'KR';
    if (/台湾|🇹🇼|\bTW\b|Taipei/i.test(s)) return 'TW';
    if (/香港|🇭🇰|\bHK\b|Hong\s*Kong/i.test(s)) return 'HK';
    if (/新加坡|🇸🇬|\bSG\b|Singapore|狮城/i.test(s)) return 'SG';
    return 'OTHER';
}

function createProxyManager(opts = {}) {
    const app = opts.app;
    const isPackaged = !!(app && app.isPackaged);
    const baseDir = opts.baseDir || __dirname;
    const userDataDir = opts.userDataDir || (app ? app.getPath('userData') : path.join(baseDir, '.proxy-data'));
    const subUrl = opts.subscriptionUrl || SUBSCRIPTION_URL;
    const onStateChange = typeof opts.onStateChange === 'function' ? opts.onStateChange : () => {};

    const runtimeDir = path.join(userDataDir, 'mihomo-runtime');
    const subCacheFile = path.join(userDataDir, 'proxy-subscription.yaml');
    const configPath = path.join(runtimeDir, 'config.yaml');
    const ctrlSecret = crypto.randomBytes(12).toString('hex');
    const bridgeToken = crypto.randomBytes(12).toString('hex');

    const state = {
        initialized: false,
        ready: false,
        testing: false,
        enabled: false,             // 默认关闭开关
        reason: 'init',
        mixedPort: 0,
        ctrlPort: 0,
        bridgePort: 0,
        activeNode: '',
        activeRegion: '',
        activeLatencyMs: null,
        proxyMode: 'direct',        // 'mihomo' | 'system-fallback' | 'direct'
        systemProxy: { detected: false, enabled: false, server: '', url: '', alive: false },
        availableNodes: [],
        lastError: '',
        subNodeCount: 0
    };

    let mihomoProc = null;
    let bridgeServer = null;
    let initPromise = null;
    let testPromise = null;
    let stopping = false;

    function resolveMihomoBin() {
        const cands = [
            isPackaged ? path.join(process.resourcesPath, 'app.asar.unpacked', 'tools', 'mihomo', 'mihomo.exe') : '',
            isPackaged ? path.join(process.resourcesPath, 'tools', 'mihomo', 'mihomo.exe') : '',
            path.join(baseDir, 'tools', 'mihomo', 'mihomo.exe')
        ].filter(Boolean);
        return cands.find((p) => fs.existsSync(p)) || '';
    }

    function getStatus() {
        return {
            initialized: state.initialized,
            ready: state.ready,
            testing: state.testing,
            enabled: state.enabled,
            reason: state.reason,
            mixedPort: state.mixedPort,
            proxyUrl: getProxyUrl() || '',
            controlBridgeUrl: getControlBridgeUrl(),
            bridgeToken,
            activeNode: state.activeNode,
            activeRegion: state.activeRegion,
            activeLatencyMs: state.activeLatencyMs,
            proxyMode: state.proxyMode,
            systemProxy: state.systemProxy,
            availableNodes: state.availableNodes.slice(0, 100),
            subNodeCount: state.subNodeCount,
            lastError: state.lastError
        };
    }

    const LN_PRIORITY_RE = /(🇹🇼|台湾|台灣|台北|新北|高雄|\bTW\b|Taiwan|🇭🇰|香港|Hong\s*Kong|\bHK\b|HKBN|HGC|PCCW)/i;
    function isLnPriorityNode(node) {
        if (!node) return false;
        const r = String(node.region || '').toUpperCase();
        if (r === 'TW' || r === 'HK' || r === 'TAIWAN' || r === 'HONGKONG' || r === 'HONG KONG') return true;
        return LN_PRIORITY_RE.test(`${node.region || ''} ${node.name || ''}`);
    }

    function orderNodesForMode(mode = 'general', excludeSet = new Set()) {
        const excluded = excludeSet instanceof Set
            ? excludeSet
            : new Set(Array.isArray(excludeSet) ? excludeSet.filter(Boolean) : []);
        const pool = state.availableNodes.filter((n) => n && n.name && !excluded.has(n.name));
        if (mode === 'lightnovel' || mode === 'ln') {
            const twHk = pool.filter(isLnPriorityNode).sort((a, b) => a.delay - b.delay);
            const rest = pool.filter((n) => !isLnPriorityNode(n)).sort((a, b) => a.delay - b.delay);
            return [...twHk, ...rest];
        }
        const pref = pool.filter((n) => n.preferred).sort((a, b) => a.delay - b.delay);
        const rest = pool.filter((n) => !n.preferred).sort((a, b) => a.delay - b.delay);
        return [...pref, ...rest];
    }

    async function waitReadyAndTested() {
        if (!state.initialized && initPromise) {
            try { await initPromise; } catch (e) {}
        }
        if (testPromise) {
            await Promise.race([testPromise, new Promise((r) => setTimeout(r, 12000))]);
        }
    }

    function getProxyUrl() {
        if (!state.enabled) return null;
        if (state.ready && state.mixedPort > 0) {
            return `http://127.0.0.1:${state.mixedPort}`;
        }
        if (state.systemProxy && state.systemProxy.detected && state.systemProxy.alive && state.systemProxy.url) {
            return state.systemProxy.url;
        }
        return null;
    }

    function getControlBridgeUrl() {
        return state.bridgePort > 0 ? `http://127.0.0.1:${state.bridgePort}/switch` : '';
    }

    function emitState() {
        try {
            onStateChange(getStatus());
        } catch (e) {}
    }

    async function syncElectronSessions() {
        try {
            const { session } = require('electron');
            const ses = session.fromPartition('persist:linovelib');
            const pUrl = getProxyUrl();
            if (pUrl) {
                const hostPort = pUrl.replace(/^https?:\/\//i, '');
                await ses.setProxy({
                    mode: 'fixed_servers',
                    proxyRules: `http=${hostPort};https=${hostPort}`,
                    proxyBypassRules: 'localhost,127.0.0.1,<local>'
                });
            } else {
                // Explicitly use direct so host WinInet proxy never conflicts when switch is OFF
                await ses.setProxy({ mode: 'direct' });
            }
        } catch (e) {
            // May run outside Electron app lifecycle during standalone tests
        }
    }

    async function setEnabled(on, reason = 'manual') {
        const next = !!on;
        const prev = state.enabled;
        state.enabled = next;
        state.reason = String(reason || (next ? 'on' : 'off'));
        state.proxyMode = next
            ? (state.ready ? 'mihomo' : ((state.systemProxy && state.systemProxy.alive) ? 'system-fallback' : 'direct'))
            : 'direct';
        await syncElectronSessions();
        if (prev !== next || reason) {
            console.log(`🌐 [Proxy] 开关=${next ? 'ON' : 'OFF'} reason=${state.reason} mode=${state.proxyMode} node=${state.activeNode || '无'}`);
            emitState();
        }
        return getStatus();
    }

    function mihomoApi(method, apiPath, bodyObj = null, timeoutMs = 5000) {
        return new Promise((resolve, reject) => {
            if (!state.ctrlPort) { reject(new Error('ctrlPort not set')); return; }
            const payload = bodyObj ? JSON.stringify(bodyObj) : null;
            const req = http.request({
                hostname: '127.0.0.1',
                port: state.ctrlPort,
                path: apiPath,
                method,
                headers: Object.assign({
                    Authorization: `Bearer ${ctrlSecret}`
                }, payload ? {
                    'Content-Type': 'application/json',
                    'Content-Length': Buffer.byteLength(payload)
                } : {}),
                timeout: timeoutMs
            }, (res) => {
                const chunks = [];
                res.on('data', (c) => chunks.push(c));
                res.on('end', () => {
                    const txt = Buffer.concat(chunks).toString('utf8');
                    let data = null;
                    try { data = txt ? JSON.parse(txt) : {}; } catch (e) { data = { raw: txt }; }
                    resolve({ statusCode: res.statusCode || 0, data });
                });
            });
            req.on('timeout', () => { req.destroy(); reject(new Error('mihomo API timeout')); });
            req.on('error', reject);
            if (payload) req.write(payload);
            req.end();
        });
    }

    async function startControlBridge() {
        if (bridgeServer) return;
        const sysPort = (state.systemProxy && state.systemProxy.port) ? state.systemProxy.port : 0;
        const forbidden = new Set([sysPort, state.mixedPort, state.ctrlPort].filter(Boolean));
        state.bridgePort = await findFreePort(29190, forbidden);
        const readJsonBody = (req) => new Promise((resolve, reject) => {
            const chunks = [];
            req.on('data', (c) => chunks.push(c));
            req.on('end', () => {
                try {
                    resolve(JSON.parse(Buffer.concat(chunks).toString('utf8') || '{}'));
                } catch (e) {
                    reject(e);
                }
            });
            req.on('error', reject);
        });

        await new Promise((resolve) => {
            bridgeServer = http.createServer(async (req, res) => {
                if (req.method === 'GET' && req.url === '/status') {
                    res.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8' });
                    res.end(JSON.stringify({ ok: true, status: getStatus() }));
                    return;
                }
                const isSwitch = (req.url === '/switch');
                const isRotate = (req.url === '/rotate' || req.url === '/switch/rotate');
                if (req.method === 'POST' && (isSwitch || isRotate)) {
                    const tok = req.headers['x-easypub-token'];
                    if (tok !== bridgeToken) {
                        res.writeHead(403, { 'Content-Type': 'application/json' });
                        res.end(JSON.stringify({ ok: false, error: 'forbidden' }));
                        return;
                    }
                    try {
                        const body = await readJsonBody(req);
                        if (isSwitch) {
                            const st = (body.enabled && body.mode)
                                ? await ensureModeNode(body.mode, body.reason || 'python')
                                : await setEnabled(!!body.enabled, body.reason || 'python');
                            res.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8' });
                            res.end(JSON.stringify({
                                ok: true,
                                enabled: st.enabled,
                                proxyUrl: st.proxyUrl,
                                activeNode: st.activeNode,
                                activeRegion: st.activeRegion
                            }));
                        } else {
                            const r = await rotateNode({
                                mode: body.mode || 'general',
                                exclude: Array.isArray(body.exclude) ? body.exclude : [],
                                reason: body.reason || 'python-rotate'
                            });
                            res.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8' });
                            res.end(JSON.stringify(r));
                        }
                    } catch (e) {
                        res.writeHead(400, { 'Content-Type': 'application/json' });
                        res.end(JSON.stringify({ ok: false, error: e.message }));
                    }
                    return;
                }
                res.writeHead(404);
                res.end();
            });
            bridgeServer.listen(state.bridgePort, '127.0.0.1', () => resolve());
            bridgeServer.on('error', (e) => {
                console.warn('⚠️ [Proxy] control bridge error:', e.message);
                resolve();
            });
        });
    }

    async function fetchSubscriptionYaml(sysProxy) {
        if (!subUrl) {
            if (fs.existsSync(subCacheFile)) {
                return fs.readFileSync(subCacheFile, 'utf8');
            }
            throw new Error('未配置代理订阅链接（可通过 EASYPUB_PROXY_SUB_URL 环境变量或本地 proxy-subscription.yaml 提供）');
        }
        let raw = '';
        let lastErr = '';
        // 1. Try direct first
        try {
            const r = await httpGetDirect(subUrl, { 'User-Agent': 'clash-verge/v2.0.2' }, 10000);
            if (r.statusCode === 200 && r.body.length > 100) {
                raw = r.body.toString('utf8');
            } else {
                lastErr = `HTTP ${r.statusCode}`;
            }
        } catch (e) {
            lastErr = e.message;
        }
        // 2. Fallback via live user system proxy if direct failed
        if (!raw && sysProxy && sysProxy.detected && sysProxy.alive && sysProxy.url) {
            try {
                const r = await httpRequestViaProxy(subUrl, sysProxy.url, 10000, { 'User-Agent': 'clash-verge/v2.0.2' });
                if (r.statusCode === 200 && r.body.length > 100) {
                    raw = r.body.toString('utf8');
                }
            } catch (e) {
                lastErr += ' / via-sys-proxy: ' + e.message;
            }
        }
        if (raw) {
            fs.mkdirSync(path.dirname(subCacheFile), { recursive: true });
            fs.writeFileSync(subCacheFile, raw, 'utf8');
            return raw;
        }
        // 3. Fallback to local cached subscription
        if (fs.existsSync(subCacheFile)) {
            console.warn(`⚠️ [Proxy] 订阅拉取失败 (${lastErr})，改用本地缓存订阅`);
            return fs.readFileSync(subCacheFile, 'utf8');
        }
        throw new Error(`订阅下载失败且无本地缓存: ${lastErr}`);
    }

    function stopMihomoChild() {
        if (!mihomoProc) return;
        const pid = mihomoProc.pid;
        try { mihomoProc.kill(); } catch (e) {}
        if (process.platform === 'win32' && pid) {
            try {
                execFile('taskkill', ['/F', '/T', '/PID', String(pid)], { windowsHide: true }, () => {});
            } catch (e) {}
        }
        mihomoProc = null;
        state.ready = false;
    }

    async function testSingleNodeDelay(nodeName) {
        const enc = encodeURIComponent(nodeName);
        const target = encodeURIComponent(GOOGLE_204_URL);
        try {
            const r = await mihomoApi('GET', `/proxies/${enc}/delay?timeout=3500&url=${target}`, null, 4500);
            if (r.statusCode === 200 && r.data && typeof r.data.delay === 'number' && r.data.delay > 0) {
                return {
                    name: nodeName,
                    delay: r.data.delay,
                    region: classifyRegion(nodeName),
                    preferred: PREFERRED_REGION_RE.test(nodeName)
                };
            }
        } catch (e) {}
        return null;
    }

    async function testAndPickBestNode(usableNodes) {
        if (testPromise) return testPromise;
        testPromise = (async () => {
            state.testing = true;
            emitState();
            try {
                async function runBatch(list) {
                    const results = [];
                    const conc = 8;
                    let idx = 0;
                    async function worker() {
                        while (idx < list.length) {
                            const cur = list[idx++];
                            const hit = await testSingleNodeDelay(cur);
                            if (hit) results.push(hit);
                        }
                    }
                    await Promise.all(Array.from({ length: Math.min(conc, list.length) }, worker));
                    results.sort((a, b) => a.delay - b.delay);
                    return results;
                }

                // Probe ALL usable subscription nodes so full pool is available for failover rotation
                const allWorking = await runBatch(usableNodes);
                state.availableNodes = allWorking;
                if (!allWorking.length) {
                    state.activeNode = '';
                    state.activeRegion = '';
                    state.activeLatencyMs = null;
                    state.lastError = '所有节点连接 Google 测试均超时';
                    return null;
                }

                const prefWorking = allWorking.filter((n) => n.preferred);
                const pickOrder = prefWorking.length ? prefWorking : allWorking;

                // Select best node in AUTO_SELECT and verify Google 204 through mixed-port
                for (const cand of pickOrder.slice(0, 4)) {
                    await mihomoApi('PUT', '/proxies/AUTO_SELECT', { name: cand.name }, 3000);
                    try {
                        const verify = await httpRequestViaProxy(GOOGLE_204_URL, `http://127.0.0.1:${state.mixedPort}`, 4500, {});
                        if (verify.statusCode === 204 || verify.statusCode === 200) {
                            state.activeNode = cand.name;
                            state.activeRegion = cand.region;
                            state.activeLatencyMs = cand.delay;
                            state.lastError = '';
                            console.log(`✅ [Proxy] 选中可用节点: ${cand.name} (${cand.delay}ms，共测通 ${allWorking.length}/${usableNodes.length} 个节点)`);
                            return cand;
                        }
                    } catch (e) {
                        console.warn(`⚠️ [Proxy] 节点 ${cand.name} Google 204 复核失败: ${e.message}`);
                    }
                }

                // Fallback to fastest reported node even if 204 check flaked
                const first = pickOrder[0];
                await mihomoApi('PUT', '/proxies/AUTO_SELECT', { name: first.name }, 3000);
                state.activeNode = first.name;
                state.activeRegion = first.region;
                state.activeLatencyMs = first.delay;
                state.lastError = '';
                return first;
            } finally {
                state.testing = false;
                emitState();
            }
        })();
        try {
            return await testPromise;
        } finally {
            testPromise = null;
        }
    }

    async function selectNode(nodeName) {
        if (!state.ready || !nodeName) return { ok: false, error: '代理引擎未就绪' };
        await mihomoApi('PUT', '/proxies/AUTO_SELECT', { name: nodeName }, 3000);
        const hit = state.availableNodes.find((n) => n.name === nodeName);
        state.activeNode = nodeName;
        state.activeRegion = classifyRegion(nodeName);
        state.activeLatencyMs = hit ? hit.delay : null;
        emitState();
        return { ok: true, status: getStatus() };
    }

    async function ensureModeNode(mode = 'general', reason = 'mode') {
        await waitReadyAndTested();
        if (state.ready && !state.availableNodes.length) {
            await retestNodes();
        }
        if (state.ready && state.availableNodes.length) {
            const ordered = orderNodesForMode(mode);
            const curNode = state.availableNodes.find((n) => n.name === state.activeNode) || null;
            const isLnMode = (mode === 'lightnovel' || mode === 'ln');
            const curPriorityOk = !isLnMode || (curNode && isLnPriorityNode(curNode)) || !ordered.some(isLnPriorityNode);
            if ((!curNode || !curPriorityOk) && ordered.length) {
                const target = ordered[0];
                try {
                    await mihomoApi('PUT', '/proxies/AUTO_SELECT', { name: target.name }, 3000);
                    state.activeNode = target.name;
                    state.activeRegion = target.region;
                    state.activeLatencyMs = target.delay;
                    console.log(`🎯 [Proxy] 按模式(${mode})切换节点 -> ${target.name} (${target.delay}ms)`);
                } catch (e) {
                    console.warn(`⚠️ [Proxy] 按模式切换节点失败: ${e.message}`);
                }
            }
        }
        return setEnabled(true, reason);
    }

    async function rotateNode(opts = {}) {
        await waitReadyAndTested();
        if (!state.ready) {
            return { ok: false, rotated: false, error: '代理引擎未就绪', remainingCount: 0, status: getStatus() };
        }
        if (!state.availableNodes.length) {
            await retestNodes();
        }
        if (!state.availableNodes.length) {
            return { ok: false, rotated: false, error: '无可用代理节点', remainingCount: 0, status: getStatus() };
        }

        const exclude = new Set(Array.isArray(opts.exclude) ? opts.exclude.filter(Boolean) : []);
        const prevNode = state.activeNode || '';
        if (prevNode) exclude.add(prevNode);

        const mode = opts.mode || 'general';
        const candidates = orderNodesForMode(mode, exclude);
        if (!candidates.length) {
            return {
                ok: false,
                rotated: false,
                exhausted: true,
                prevNode,
                activeNode: state.activeNode,
                activeRegion: state.activeRegion,
                error: '全部可用节点均已尝试',
                remainingCount: 0,
                status: getStatus()
            };
        }

        const next = candidates[0];
        await mihomoApi('PUT', '/proxies/AUTO_SELECT', { name: next.name }, 3000);
        state.activeNode = next.name;
        state.activeRegion = next.region;
        state.activeLatencyMs = next.delay;
        if (!state.enabled) {
            state.enabled = true;
            state.proxyMode = 'mihomo';
            await syncElectronSessions();
        }
        state.reason = String(opts.reason || 'rotate');
        console.log(`🔄 [Proxy] 节点轮换(${state.reason}): ${prevNode || '无'} -> ${next.name} (${next.delay}ms, 剩余可试 ${candidates.length - 1} 个)`);
        emitState();
        return {
            ok: true,
            rotated: true,
            prevNode,
            activeNode: next.name,
            activeRegion: next.region,
            activeLatencyMs: next.delay,
            proxyUrl: getProxyUrl() || '',
            remainingCount: candidates.length - 1,
            status: getStatus()
        };
    }

    async function init(forceRefresh = false) {
        if (initPromise && !forceRefresh) return initPromise;
        initPromise = (async () => {
            try {
                state.lastError = '';
                state.systemProxy = await detectWindowsSystemProxy();
                if (state.systemProxy.detected) {
                    console.log(`🔎 [Proxy] 检测到宿主机系统代理: ${state.systemProxy.server} (alive=${state.systemProxy.alive})，内部端口将完全避让`);
                }

                await startControlBridge();

                const mihomoBin = resolveMihomoBin();
                if (!mihomoBin) {
                    state.lastError = '未找到内置 mihomo.exe';
                    state.initialized = true;
                    emitState();
                    return getStatus();
                }

                const rawYaml = await fetchSubscriptionYaml(state.systemProxy);
                const parsed = parseSubscriptionProxies(rawYaml);
                state.subNodeCount = parsed.usableNodes.length;

                const sysPort = (state.systemProxy && state.systemProxy.port) ? state.systemProxy.port : 0;
                const forbidden = new Set([sysPort, state.bridgePort].filter(Boolean));
                if (!state.mixedPort) state.mixedPort = await findFreePort(27890, forbidden);
                forbidden.add(state.mixedPort);
                if (!state.ctrlPort) state.ctrlPort = await findFreePort(29090, forbidden);

                const cfg = [
                    `mixed-port: ${state.mixedPort}`,
                    `bind-address: 127.0.0.1`,
                    `allow-lan: false`,
                    `mode: rule`,
                    `log-level: warning`,
                    `geodata-mode: false`,
                    `external-controller: 127.0.0.1:${state.ctrlPort}`,
                    `secret: ${yamlSingleQuote(ctrlSecret)}`,
                    `dns:`,
                    `  enable: false`,
                    parsed.rawProxiesBlock,
                    `proxy-groups:`,
                    `  - name: AUTO_SELECT`,
                    `    type: select`,
                    `    proxies:`,
                    ...parsed.usableNodes.map((n) => `      - ${yamlSingleQuote(n)}`),
                    `rules:`,
                    `  - DOMAIN,localhost,DIRECT`,
                    `  - IP-CIDR,127.0.0.0/8,DIRECT,no-resolve`,
                    `  - MATCH,AUTO_SELECT`,
                    ``
                ].join('\n');

                fs.mkdirSync(runtimeDir, { recursive: true });
                fs.writeFileSync(configPath, cfg, 'utf8');

                stopMihomoChild();
                const cleanEnv = Object.assign({}, process.env);
                delete cleanEnv.HTTP_PROXY;
                delete cleanEnv.HTTPS_PROXY;
                delete cleanEnv.http_proxy;
                delete cleanEnv.https_proxy;
                delete cleanEnv.ALL_PROXY;
                delete cleanEnv.all_proxy;

                mihomoProc = spawn(mihomoBin, ['-d', runtimeDir, '-f', configPath], {
                    windowsHide: true,
                    stdio: ['ignore', 'pipe', 'pipe'],
                    env: cleanEnv
                });
                let mihomoBootErr = '';
                mihomoProc.stdout.on('data', (d) => {
                    const txt = d.toString('utf8').trim();
                    if (/error|fatal/i.test(txt)) console.warn('[mihomo]', txt);
                });
                mihomoProc.stderr.on('data', (d) => {
                    const txt = d.toString('utf8').trim();
                    mihomoBootErr += txt + '\n';
                    if (txt) console.warn('[mihomo-err]', txt);
                });
                mihomoProc.on('exit', (code) => {
                    if (!stopping && code !== 0) {
                        console.warn(`⚠️ [Proxy] mihomo 退出 code=${code}`);
                        state.ready = false;
                        emitState();
                    }
                });

                // Wait up to 6s for Mihomo controller API to listen
                let ctrlUp = false;
                for (let i = 0; i < 30; i++) {
                    if (await probeTcp('127.0.0.1', state.ctrlPort, 300)) {
                        try {
                            const r = await mihomoApi('GET', '/version', null, 1500);
                            if (r.statusCode === 200) { ctrlUp = true; break; }
                        } catch (e) {}
                    }
                    await new Promise((r) => setTimeout(r, 200));
                }

                if (!ctrlUp) {
                    state.lastError = '内置节点内核启动超时' + (mihomoBootErr ? `: ${mihomoBootErr.slice(0, 120)}` : '');
                    state.initialized = true;
                    emitState();
                    return getStatus();
                }

                state.ready = true;
                state.initialized = true;
                // Keep default switch state OFF as requested!
                state.enabled = false;
                state.proxyMode = 'direct';
                await syncElectronSessions();
                emitState();

                // Test JP/KR/TW/HK/SG nodes against Google in background
                await testAndPickBestNode(parsed.usableNodes);
                return getStatus();
            } catch (e) {
                state.lastError = e.message || String(e);
                state.initialized = true;
                state.testing = false;
                console.warn('⚠️ [Proxy] 初始化异常:', state.lastError);
                emitState();
                return getStatus();
            }
        })();
        return initPromise;
    }

    async function retestNodes() {
        if (!state.ready) {
            return init(true);
        }
        try {
            const r = await mihomoApi('GET', '/proxies/AUTO_SELECT', null, 3000);
            const all = (r.data && Array.isArray(r.data.all)) ? r.data.all : [];
            if (all.length) {
                await testAndPickBestNode(all);
            }
        } catch (e) {
            state.lastError = e.message;
            emitState();
        }
        return getStatus();
    }

    async function stop() {
        stopping = true;
        stopMihomoChild();
        if (bridgeServer) {
            try { bridgeServer.close(); } catch (e) {}
            bridgeServer = null;
        }
        await syncElectronSessions();
    }

    return {
        init,
        stop,
        getStatus,
        getProxyUrl,
        getControlBridgeUrl,
        getBridgeToken: () => bridgeToken,
        setEnabled,
        selectNode,
        ensureModeNode,
        rotateNode,
        retestNodes
    };
}

module.exports = { createProxyManager, parseSubscriptionProxies, detectWindowsSystemProxy };
