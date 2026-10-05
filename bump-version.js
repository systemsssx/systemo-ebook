#!/usr/bin/env node
/**
 * 刷新 index.html 里本地 CSS/JS 的版本号
 *
 * 为什么需要：
 *   Chromium 对 file:// 的样式表/脚本缓存很顽固，
 *   清 Cache 目录也不一定失效 —— 改了 CSS 却看不到变化。
 *   给 URL 加 ?v=<时间戳> 能让它必然重新加载。
 *
 * 用法：
 *   node bump-version.js      （或 npm run bump）
 *
 * ★ 每次改完 css/js 都要跑一次，否则你看到的还是旧文件。
 */
const fs = require('fs');
const path = require('path');

const ROOT = __dirname;
const FILE = path.join(ROOT, 'index.html');

const now = new Date();
const pad = (n) => String(n).padStart(2, '0');
const VER = ''
    + pad(now.getMonth() + 1)
    + pad(now.getDate())
    + pad(now.getHours())
    + pad(now.getMinutes())
    + pad(now.getSeconds());

let html = fs.readFileSync(FILE, 'utf-8');

// 1. 去掉旧的 ?v=
html = html.replace(/(href|src)="([\w\-.]+\.(?:css|js))(\?v=[^"]*)?"/g, '$1="$2"');

// 2. 加新的
let n = 0;
html = html.replace(/(href|src)="([\w\-.]+\.(?:css|js))"/g, (m, attr, name) => {
    n++;
    return `${attr}="${name}?v=${VER}"`;
});

fs.writeFileSync(FILE, html, 'utf-8');

console.log(`✓ 版本号 ${VER} 已写入 ${n} 个本地资源:`);
html.replace(/(?:href|src)="([\w\-.]+\.(?:css|js))\?v=(\d+)"/g, (m, name) => {
    console.log(`    ${name}`);
    return m;
});
