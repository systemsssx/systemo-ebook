<div align="center">

<img src="docs/icon.png" width="96" alt="systemo ebook">

# systemo ebook

**Windows 电子书工作台 —— 下载 · 制作 · 传输 · 阅读 · 推荐**

从一个书名，到一本能在手机 / Kindle / 苹果图书上读的 EPUB —— **全程本地完成，不上传任何文件。**

![platform](https://img.shields.io/badge/platform-Windows%2010%2F11-0078d4)
![electron](https://img.shields.io/badge/Electron-27-47848f?logo=electron&logoColor=white)
![python](https://img.shields.io/badge/Python-3.11%2B-3776ab?logo=python&logoColor=white)
![release](https://img.shields.io/badge/release-v1.1.0-2ea043)
![license](https://img.shields.io/badge/license-All%20rights%20reserved-lightgrey)

[![GitHub stars](https://img.shields.io/github/stars/systemsssx/systemo-ebook?style=social)](https://github.com/systemsssx/systemo-ebook/stargazers)

</div>

**[简体中文](README.md) | [English](README_EN.md)**

> **本仓库是完整版 v1.1.0 的源码与文档**：下载、制作、传输、阅读、推荐全部开放。
> 成品安装包见 [Releases](../../releases)（Windows 10/11 x64，解压即用，无需安装 Python / Node）。
> ⭐ **如果这个项目帮到了你，欢迎点个 Star 支持一下** —— 这是个人项目最直接的动力。

---

## 🚀 三分钟上手

1. 打开 [Releases](../../releases)，下载最新的 `systemo-ebook-v1.1.0-full.rar`；
2. 解压到**可写目录**（例如 `D:\systemo ebook`，不要放 `C:\Program Files`）；
3. 双击 `EasyPub.exe` 即可使用。首次启动会多等几秒（初始化后端进程），属正常现象。

> 发布包已自带：冻结的 Python 后端（PyInstaller）、ChromeDriver、Pandoc、字体与封面增强模型。
> 请**整个文件夹一起**解压 / 移动，不要只拿一个 exe。

---

## 🧭 它是什么

不是单点小工具，而是一条完整的「电子书生产线」：
**下载 / 导入 → 自动分章 → 找作者封面 → 生成 EPUB → 传到设备 → 入库存档 → 内置阅读器**。
做成一个本地应用，数据不出本机，面向「多设备阅读 + 排版重排」这个真实痛点。

| 模块 | 做什么 |
|---|---|
| ⬇️ **下载** | 输入书名或 `书名@作者` → 多源检索（笔趣阁爬虫源 / UU看書精校源 / 久久 / 哔哩轻小说选卷）；显示真实进度，随时可取消 |
| 📝 **制作** | TXT 自动分章（识别卷 / 章 / 节；可手动增删改、实时预览）→ 联网搜作者 → 搜封面（小图自动升级）→ 生成带封面与目录的 EPUB |
| 📤 **传输** | 三条通道：① 苹果图书（百度网盘中转）② **WiFi 直传**（手机浏览器打开地址即收）③ **Send to Kindle**（Amazon 官方通道） |
| 📚 **书库** | 制作完成的 EPUB 自动入库；双击开读、右键详情、多选批量传输 / 导出 / 删除 |
| 🧭 **推荐** | 多源榜单聚合（微信读书 / 起点 / 番茄 / 晋江 / 哔哩轻小说）；综合推荐每批 14 本，轻小说 5 个榜单可翻页 |
| 📖 **阅读** | 内置 **foliate-js** 阅读器：目录 / 书签 / 全文搜索 / 主题（深色 · 浅色 · 护眼）/ 字体 / 字号 / 行距 / 翻页与滚动 / 边距 / 对齐 |
| ⚡ **一键** | 书名 → 搜索 → 下载 → 分章 → 找封面 → 转换 → 传输，一条流水线走完 |
| 🧩 **可扩展书源** | **源码开放**：下载源码后可自行新增 / 修改书源与解析规则（`novelmeta/`、`novel_dl.py`、`rank_pool.py`、`linovelib.py`），接自己的书源和榜单 |
| 🎛️ **界面** | 月夜插画 / 简洁背景一键切换；中英双语；宋黑混排 + 直角控件的本地工作台 |

---

## 📸 界面

| 下载（多源检索） | 选择下载源（精校源 / 普通源） |
|---|---|
| ![](docs/screenshots/01-download.png) | ![](docs/screenshots/02-source-choice.png) |

| 制作（自动分章） | 章节预览 |
|---|---|
| ![](docs/screenshots/03-make.png) | ![](docs/screenshots/04-chapters.png) |

| 传输（三条通道） | 书库 |
|---|---|
| ![](docs/screenshots/05-send.png) | ![](docs/screenshots/06-library.png) |

| 精选推荐（多源榜单，14 本 / 批） | 轻小说榜单（5 榜 + 分页） |
|---|---|
| ![](docs/screenshots/07-recommend.png) | ![](docs/screenshots/08-recommend-ln.png) |

| 内置阅读器（护眼双栏） | 一键完成 |
|---|---|
| ![](docs/screenshots/09-reader.png) | ![](docs/screenshots/10-oneclick.png) |

| English UI |
|---|
| ![](docs/screenshots/11-english.png) |

---

## 🏗 架构

```
┌──────────────────────────── Electron ────────────────────────────┐
│ 渲染层（原生 JS，无框架）                                           │
│   index.html · library.js · i18n.js · settings-ui.js               │
│   resona-workspace / studio / layout / theme / bridge.css          │
│   background-switch.js（月夜 / 简洁背景）                          │
│        │  window.electronAPI.*（preload.js / contextBridge）       │
│ 主进程 main.js                                                     │
│   · 窗口与 IPC  · 三条传输通道  · 暂存队列 sendqueue\              │
│   · 把子进程 stdout 转发到界面，并写入 logs\upload-*.log           │
└───────────────────────────────┬──────────────────────────────────┘
                                │ spawn
        ┌───────────────────────┴────────────────────────┐
        │ 开发：python  xxx.py                            │
        │ 发布：resources\backend\easypub-backend.exe     │ ← PyInstaller 冻结
        └───────────────────────┬────────────────────────┘
                                │ stdout: @@INFO / @@PROGRESS / @@RESULT / @@RANK + JSON
┌───────────────────────────────┴──────────────────────────────────┐
│ Python 后端                                                       │
│   novel_dl.py / xbiquzw.py / uukanshu.py   网文下载（爬虫源/精校源）│
│   epub_generator.py   TXT → 分章 → 带封面 EPUB                     │
│   meta_lookup.py + novelmeta/   作者/封面多源查询、榜单聚合        │
│   linovelib.py        哔哩轻小说（选卷 / 插图 / 自组 EPUB）        │
│   wifi_upload.py / get_auth_code.py / upload_folder_cmd.py  传输   │
│   image_enhancer.py   本地封面超分（ONNX，无需联网）               │
│   backend_entry.py    打包入口（把以上模块串起来）                 │
└──────────────────────────────────────────────────────────────────┘
```

**三条传输通道的实现**：

| 通道 | 实现 |
|---|---|
| 苹果图书 | `bypy` → 上传到百度网盘 → 手机端「苹果图书」/「文件」App 取回 |
| WiFi 直传 | 本地起 HTTP 服务 + `Selenium` 驱动 Chrome，把文件传到手机打开的页面（要求同一局域网） |
| Send to Kindle | Amazon 官方 Send to Kindle 网页通道（登录后上传，密码不落盘） |

**进程间协议**：Python 每个阶段向 stdout 打一行 `@@MARKER {json}`，主进程解析后转发给界面
（`@@INFO` 提示 / `@@PROGRESS` 进度 / `@@RESULT` 结果 / `@@RANK` 候选排序）。

---

## 🧰 从源码运行（开发者）

| 类别 | 需要 | 说明 |
|---|---|---|
| **Node.js** | 18+（实测 20） | 跑 Electron |
| **Electron** | `^27.3.0`（devDependency） | 桌面壳 |
| **electron-builder** | `^24.0.0`（devDependency） | 打包 |
| **Node 依赖** | `@readium/css` `foliate-js` | EPUB 阅读 / 排版样式 |
| **Python** | **3.11+** | 后端脚本 |
| **浏览器** | 本机安装 **Google Chrome** | Selenium 驱动它完成 WiFi 直传 / 封面兜底 |
| **ChromeDriver** | 与本机 Chrome 主版本一致 | WiFi 直传 / 授权页自动化 |
| **Pandoc** | 3.10.2（发布包已内置） | 格式转换 |

```powershell
pip install -r requirements.txt
npm install
npm start                      # 开发模式
npx electron-builder --win dir --x64   # 打免安装版（产物在 dist\win-unpacked）
```

**仓库里不含的大文件**（都在发布包里，克隆源码后可自行补齐）：

- `fonts/`（Noto Sans SC / Noto Serif SC / Inter，约 42 MB）—— 缺失时界面回退系统字体；
- `models/`（封面超分 ONNX，约 4.6 MB）—— 缺失时超分降级；
- `chromedriver.exe`（约 24 MB）与 `pandoc-3.10.2/` —— 浏览器自动化 / 转换所需，发布包内置。

### 🧩 自定义书源（下载源码后自己改）

书源与解析规则全部在源码里，**可自由增删改**：

- `novelmeta/` —— 书源适配层（搜索、元数据、榜单、封面）；
- `novel_dl.py` —— 下载调度与源的优先级逻辑；
- `rank_pool.py` —— 推荐池（榜单聚合与配额）；
- `linovelib.py` —— 哔哩轻小说源（选卷 / 插图 / 组书）。

新增书源、修改解析规则，或接自己的榜单源后，重新构建后端（`backend.spec`）或在开发模式下直接跑脚本即可。

> 改完 CSS / JS 记得执行 `npm run bump` 刷新 `index.html` 里的 `?v=` 缓存戳，否则界面可能仍用旧缓存。

---

## 🔧 首次使用要配置什么

- **苹果图书通道**：先在「传输」页做一次**百度网盘授权**（浏览器登录后把授权码粘回）；
- **Send to Kindle**：需要**登录 Amazon 账号**（登录态只存本机会话分区，密码不落盘）；
- **WiFi 直传**：手机和电脑连**同一个 WiFi**，按弹窗提示在手机浏览器打开地址即可；
- **封面超分 / 文件名**：制作页底部可设置输出目录等选项。

---

## 🔒 数据与隐私

- 配置与数据都在**程序自己目录**下：`settings.json`、`logs\`、`export\`、`sendqueue\`、`download\`；
  整个目录拷走即迁移，**不含任何账号密码**（登录态按浏览器会话分区存放）。
- 仓库 `.gitignore` 已排除所有凭据类文件（`settings.json`、`kindle_config.json`、`*cookies*`、`wifi_urls.txt`）。
- 发布包是**零数据**的：不含任何设置、书库、登录态；首次打开书库为空属正常。
- 联网查询到的作者 / 封面信息来自各公开站点，版权归原站点与作者所有。

---

## ⚠️ 免责声明（求生欲还是要有的）

* **用爱发电**：这是一个纯粹为了“让自己看书更爽”而写出来的个人折腾项目，仅供编程学习与技术研究，完全无任何商业盈利行为（不卖课、不收费、无广告）。
* **技术本质**：项目中的网文/轻小说下载与封面搜刮功能，本质上是对公开网络信息的个人离线缓存与自动化重排（相当于用代码替你完成了“打开网页 ➔ 摘录文字 ➔ 加上封面排成EPUB”的机械劳动），旨在解决多设备阅读的排版与传输痛点，并非为了建立盗版分发平台。
* **版权倡导**：好故事值得被温柔对待，更值得被付费支持。本工具生成的电子书仅限个人离线自用与护眼设备转码，严禁用于任何形式的二次分发、传播或商业牟利。看书遇到喜欢的作品，请一定去正版平台订阅支持原作者！

---

## ⭐ 如果这个项目帮到了你

如果 systemo ebook 帮你少折腾了几个小时，欢迎点右上角的 **Star** ⭐ 支持一下 ——
这是个人项目最直接的动力，也能让更多需要「TXT / 网文 → 带封面 EPUB」的人找到它。

[![GitHub stars](https://img.shields.io/github/stars/systemsssx/systemo-ebook?style=social&label=Star)](https://github.com/systemsssx/systemo-ebook/stargazers)

## 📦 版本与更新

- 当前源码对应 **v1.1.0（2026-10-25）**；历史版本（v1.0.0，阉割版）已归档。
- 更新日志：[CHANGELOG.md](CHANGELOG.md)；安装包：[Releases](../../releases)。
- 发布包建议保留 `win-unpacked` 内的全部文件，升级时整目录替换（用户数据在程序目录，注意先备份）。

## 🙏 致谢与第三方

- 桌面壳 [Electron](https://www.electronjs.org/)；阅读器 [foliate-js](https://github.com/johnfactotum/foliate-js)；排版样式 [@readium/css](https://github.com/readium/css)；
- 打包 [PyInstaller](https://pyinstaller.org/)、[electron-builder](https://www.electron.build/)；格式转换 [Pandoc](https://pandoc.org/)；简繁转换 [OpenCC](https://github.com/BYVoid/OpenCC)；浏览器自动化 [Chrome for Testing](https://googlechromelabs.github.io/chrome-for-testing/)；
- 各书源站点、榜单与元数据接口的版权归原站方与作者所有；界面背景 / 封面插画为项目自制或本地 AI 生成，仅用于界面装饰。

## 📄 许可

- 本项目代码仅供学习交流，**All rights reserved**（未声明开源许可证，请勿商用或二次分发）；
- 第三方组件的许可与版权归各自项目所有，详见仓库内 `LICENSE.chromedriver` 与 `opencc/LICENSE` 等文件。
