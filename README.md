> **项目可视化介绍页（HTML Blog）：[https://systemsssx.github.io/systemo-ebook/](https://systemsssx.github.io/systemo-ebook/)**
>
> **[简体中文](README.md) | [English](README_EN.md) | [下载完整包](https://github.com/systemsssx/systemo-ebook/releases/tag/v2.0.0) | [更新记录](CHANGELOG.md)**

# systemo ebook

**Windows 本地电子书工作台 — 找书、成书、传书与阅读的一体化本地流水线**

[![platform](https://img.shields.io/badge/platform-Windows%2010%2F11%20x64-3A291B)](https://github.com/systemsssx/systemo-ebook/releases)
[![release](https://img.shields.io/badge/release-v2.0.0-8E6E3E)](https://github.com/systemsssx/systemo-ebook/releases/tag/v2.0.0)
[![blog](https://img.shields.io/badge/docs-HTML%20Blog-5C4631)](https://systemsssx.github.io/systemo-ebook/)
[![GitHub stars](https://img.shields.io/github/stars/systemsssx/systemo-ebook?style=social)](https://github.com/systemsssx/systemo-ebook/stargazers)

![systemo ebook Preview](docs/social-preview.png)

平时看网文或整理本地 TXT，常要在几个软件间来回倒腾：先找文本查编码，手动写规则切章节，再另找工具搜封面、修清晰、改目录，最后插数据线或借网页网盘传到手机和阅读器。

**项目定位：** 把网文与本地 TXT 整理成带高清封面和目录的规范 EPUB，直接送到手机、Kindle 或平板，全程在本地完成，尽量减少多端倒腾与排版返工的负担。

---

## 一、五步工作流：从零散文本到阅读设备

`01 找书与导入` → `02 分章与高清封面` → `03 封装规范 EPUB` → `04 四通道送达设备` → `05 入库与桌面直读`

1. **01 找书与导入**：输入书名（或 `书名@作者`）多源检索网文与轻小说分卷，从四大平台每批 14 本的聚合榜单里挑书，或直接拖入本地 TXT。
2. **02 智能分章与高清封面**：按卷、章、节规则自动切分目录并支持手动微调与预览；联网搜作者与多源封面，同图比对选出高分辨率原图后，用内置本地离线模型做清晰度增强。
3. **03 封装规范 EPUB**：本地引擎将正文、目录、元数据与高清封面打包成标准 EPUB，生成后自动清空临时下载目录，不留残余文件。
4. **04 四通道送达设备**：按手头设备选用本地通路——iCloud / OneDrive / Google Drive 本地云盘同步、手机浏览器局域网 WiFi 直传、Amazon 官方 Send to Kindle，或苹果图书（百度网盘中转）。
5. **05 本地入库与桌面直读**：成书自动归档进跨目录持久化的本地书库，支持多选批量传输或导出；双击即开内置双栏阅读器，提供羊皮、素笺、夜读三套护眼主题。

---

## 二、它是怎么干的：三大核心机制

### 1. 获取与精排：把零散文本变成规范 EPUB
- **多源下载与精校分流**：同时接入普通高速源与精校源，轻小说支持 5 个榜单分页并在卡片原地展开选卷，下载进度透明且随时可取消。
- **可控的 TXT 自动分章**：自动识别常见卷章结构，提供章节列表增删改与切分预览，避免转换后目录错乱或正文缺段。
- **同图高清选优与本地超分**：跨源比对挑出分辨率更高的原图，再用内置离线模型放大修清晰，全程不调用外部付费接口。

### 2. 四通道传书：把书稳妥送到不同设备
- **本地云盘同步（推荐苹果 / 多端用户）**：自动探测本机 iCloud Drive、OneDrive 或 Google Drive 同步文件夹，直接写入 `EasyPub` 子目录，由系统云盘静默同步到手机和平板。
- **WiFi 局域网直传（免装 App）**：电脑与手机连同一 WiFi 后，通过本机浏览器联动局域网地址直接收书，驱动版本后台自动匹配更新。
- **Send to Kindle 与苹果图书中转**：Kindle 走官方网页通道（登录态仅存本机会话分区）；苹果图书支持唤起本机 Chrome / Edge 独立空白会话完成百度网盘授权并推送。

### 3. 书库、阅读与工作台：日常顺手管理与试读
- **跨目录持久化书库**：更换导出路径不丢历史书单，传输队列直接渲染 EPUB 内嵌封面，支持批量发送、导出与清理。
- **内置三主题双栏阅读器**：基于 `foliate-js`，支持目录跳转、书签、全文检索，字号、行距、边距、翻页与滚动均可细调，内置羊皮、素笺、夜读三档视觉主题。
- **单页专注与拖拽双页分屏**：平时单页清爽操作，宽屏下把顶栏页签直接拖成左右对开双页，一边排书一边传书，并可随时切纯纸模式或英文界面。

---

## 三、传统手动做法 vs. 使用 systemo ebook

| 常见场景 | 传统手动做法 | 使用 systemo ebook |
| :--- | :--- | :--- |
| **从找到文本到生成能看的 EPUB** | 搜资源、查编码、手动正则分章，再另找工具压封面和改目录，换两三个软件才能拼出一本书。 | 在同一窗口内搜书或拖入 TXT，自动分章并从多源挑出高清原图离线超分，一键打包成规范 EPUB。 |
| **把书传到手机、iPad 和 Kindle** | 插数据线、发附件邮件、上传网页网盘再手动下载，换一台设备就要重新折腾一套传法。 | 四条本地通道按设备各取所需：云盘自动同步、局域网 WiFi 直传、官方 Send to Kindle 或百度中转。 |
| **长期积攒后的书库整理与试读** | 文件夹里散落命名混乱的 TXT 和半成品，临时缓存越积越多，想先看两眼还得另装阅读器。 | 成书自动入库、转完即清临时下载目录；书库内嵌封面直观展示，双击直接用三主题双栏阅读器打开。 |

---

## 四、界面实景

| I · 多源下载与检索 | II · TXT 分章与拱券封面槽 |
| :---: | :---: |
| ![下载页面](docs/screenshots/01-download.png) | ![制作页面](docs/screenshots/02-make.png) |
| **III · 四通道传输（含本地云盘同步）** | **IV · 持久化精装书库** |
| ![四通道传输](docs/screenshots/03-transfer-4ch.png) | ![本地书库](docs/screenshots/04-library.png) |
| **天头拖拽对开双页分屏** | **V · 一键流水线** |
| ![对开双页分屏](docs/screenshots/05-split-folio.png) | ![一键流水线](docs/screenshots/06-oneclick.png) |
| **内置阅读器 · 羊皮双栏主题** | **内置阅读器 · 胡桃夜读主题** |
| ![羊皮双栏阅读器](docs/screenshots/07-reader.png) | ![夜读双栏阅读器](docs/screenshots/08-reader-dark.png) |

---

## 五、四条工程与隐私原则

- **01 全程本地运行，不上传个人文件**：文本处理、分章、封面超分与书库管理均在自己电脑上完成，公开包保持零个人数据状态。
- **02 不绑死单一传输方式**：四条通道互相独立，不愿绑定第三方网盘时，可直接走本地云盘同步或局域网 WiFi 直传。
- **03 内置离线能力，开箱免配环境**：免安装完整包自带后端引擎、Pandoc、字体与本地超分模型，解压即用，不必自己装 Python 或申请密钥。
- **04 源码开放，书源与规则可自行扩展**：内置代理内核默认关闭且公开版不含私人订阅；开发者可直接修改或增补书源解析与榜单规则。

---

## 六、获取与上手使用

### 1. 免安装完整包（推荐普通读者）
前往 [GitHub Releases](https://github.com/systemsssx/systemo-ebook/releases/tag/v2.0.0) 下载 `systemo-ebook-v2.0.0-full.rar`（`275,575,368` 字节 / `262.8 MiB`，解压后 `335` 文件 / `901.3 MB`，SHA256：`A687ECB5E659A6088B92398E33895B9CD0899F8E1666DF5A7E65D4AB9D69272A`）。解压到可写目录（如 `D:\systemo-ebook`，勿放 `C:\Program Files`），保留整个目录并双击 `EasyPub.exe` 即可运行。

### 2. 源码运行与自定义书源（适合开发者）
环境要求：Windows 10 / 11 (x64)、Node.js 18+、Python 3.11+、Electron 27。

```powershell
pip install -r requirements.txt
npm install
npm start
```

- 自定义书源与解析规则位于根目录的 Python 模块：元数据与封面检索在 `novelmeta/`，多源正文下载在 `novel_dl.py`，聚合榜单规则在 `rank_pool.py`，轻小说分卷解析在 `linovelib.py`。改完前端 CSS / JS 后运行 `npm run bump` 刷新缓存戳。
- 公开源码与完整包均不含私人代理订阅；如需访问受限站点，可沿用系统代理或通过环境变量 `EASYPUB_PROXY_SUB_URL` 接入自有订阅。

### 3. 近期版本说明
当前版本为 **v2.0.0**（2026-11-23，完整版单线发布）：界面重做为 18 世纪西洋羊皮卷 V3 风格并加入顶栏拖拽双页分屏；新增本地云盘同步第 4 通道；百度网盘授权改为唤起本机 Chrome / Edge 独立空白会话，WiFi 与封面通道补齐浏览器驱动自动热更；封面升级为多源同图高清选优。完整历史记录见 [CHANGELOG.md](CHANGELOG.md)。

---

## 七、免责声明与致谢

- 用爱发电：这是一个纯粹为了“让自己看书更爽”而写出来的个人折腾项目，仅供编程学习与技术研究，完全无任何商业盈利行为（不卖课、不收费、无广告）。
- 技术本质：项目中的网文/轻小说下载与封面搜刮功能，本质上是对公开网络信息的个人离线缓存与自动化重排（相当于用代码替你完成了“打开网页 ➔ 摘录文字 ➔ 加上封面排成EPUB”的机械劳动），旨在解决多设备阅读的排版与传输痛点，并非为了建立盗版分发平台。
- 版权倡导：好故事值得被温柔对待，更值得被付费支持。本工具生成的电子书仅限个人离线自用与护眼设备转码，严禁用于任何形式的二次分发、传播或商业牟利。看书遇到喜欢的作品，请一定去正版平台订阅支持原作者！

基于 [Electron](https://www.electronjs.org/)、[foliate-js](https://github.com/johnfactotum/foliate-js)、[@readium/css](https://github.com/readium/css)、[Pandoc](https://pandoc.org/)、[OpenCC](https://github.com/BYVoid/OpenCC) 与 [PyInstaller](https://pyinstaller.org/) 构建。代码仅供学习交流，**All rights reserved**。
