> **Interactive Showcase & Report Blog: [https://systemsssx.github.io/systemo-ebook/](https://systemsssx.github.io/systemo-ebook/)**
>
> **[简体中文](README.md) | [English](README_EN.md) | [Download v2.0.0 Release](https://github.com/systemsssx/systemo-ebook/releases/tag/v2.0.0) | [Changelog](CHANGELOG.md)**

# systemo ebook

**Local Windows E-Book Workbench — Download · Build · 4-Channel Delivery · Read**

Positioning: Turn web novels and local TXT files into clean, cover-embedded EPUBs and deliver them to your reading devices entirely on your local machine, reducing manual reformatting and cross-device file juggling.

[![platform](https://img.shields.io/badge/platform-Windows%2010%2F11%20x64-3A291B)](https://github.com/systemsssx/systemo-ebook/releases)
[![release](https://img.shields.io/badge/release-v2.0.0-8E6E3E)](https://github.com/systemsssx/systemo-ebook/releases/tag/v2.0.0)
[![blog](https://img.shields.io/badge/docs-HTML%20Blog-5C4631)](https://systemsssx.github.io/systemo-ebook/)
[![GitHub stars](https://img.shields.io/github/stars/systemsssx/systemo-ebook?style=social)](https://github.com/systemsssx/systemo-ebook/stargazers)

![systemo ebook v2.0.0](docs/social-preview.png)

---

## Core Workflow Loop

`Search or Import` → `Chapter Split & HD Cover` → `Build EPUB` → `4-Channel Delivery` → `Local Library & Reading`

---

## Feature Overview

| Module | Capability | How It Works |
| :--- | :--- | :--- |
| **Discovery & Sources** | Multi-source search & recommendations | Aggregates 14 curated picks per batch across 4 platforms, plus 5 paginated light novel charts with inline volume selection. |
| **EPUB & HD Covers** | Smart TXT splitting & local upscaling | Detects chapters automatically, compares identical covers across sources for higher resolution, and runs offline super-resolution. |
| **4-Channel Delivery** | Cross-device local transfer | Supports Apple Books (via Baidu Netdisk), WiFi LAN transfer, Send to Kindle, and local Cloud Drive Sync. |
| **Library & Queue** | Persistent history & auto cleanup | Keeps library history across export folders, cleans temporary downloads after conversion, and shows embedded EPUB covers. |
| **Built-in Reader** | Eye-friendly two-column reading | Powered by `foliate-js` with Parchment, Plain Paper, and Night themes, TOC navigation, bookmarks, and search. |
| **Dual-Page Workspace** | One-click pipeline & split folio | Chains download, conversion, and transfer; drag any top tab into a side-by-side split folio with plain-paper and bilingual toggles. |

---

## Screenshots (Parchment V3)

| I · Multi-Source Download | II · EPUB Builder & Cover Preview |
| :---: | :---: |
| ![Download](docs/screenshots/01-download.png) | ![Build](docs/screenshots/02-make.png) |
| **III · 4-Channel Delivery (with Cloud Sync)** | **IV · Local Gilt-Spine Library** |
| ![Transfer](docs/screenshots/03-transfer-4ch.png) | ![Library](docs/screenshots/04-library.png) |
| **Draggable Dual-Page Split Folio** | **V · One-Click Pipeline** |
| ![Split Folio](docs/screenshots/05-split-folio.png) | ![One-Click](docs/screenshots/06-oneclick.png) |
| **Built-in Reader · Parchment Theme** | **Built-in Reader · Walnut Night Theme** |
| ![Reader Parchment](docs/screenshots/07-reader.png) | ![Reader Night](docs/screenshots/08-reader-dark.png) |

---

## What's New in v2.0.0

- **Parchment V3 & Dual-Page Split View**: Rebuilt in an 18th-century warm parchment aesthetic with antique bronze hairlines, draggable side-by-side split tabs, plain-paper toggle, and Chinese/English UI.
- **4th Channel — Local Cloud Drive Sync**: Auto-detects local iCloud Drive, OneDrive, or Google Drive folders and writes directly into the `EasyPub` subfolder with no third-party web relay.
- **Native Browser Auth & Driver Auto-Update**: Baidu Netdisk OAuth now opens an isolated native Chrome or Edge session (fixing stuck spinner states); WiFi transfer and cover fallback auto-update ChromeDriver in the background.
- **HD Cover Comparison & On-Demand Proxy Core**: Selects the highest-resolution matching cover before offline upscaling; the built-in proxy stays off by default, yields to system proxies, and ships with zero private subscriptions.

---

## Quick Start in 3 Minutes

1. Download `systemo-ebook-v2.0.0-full.rar` from [Releases](https://github.com/systemsssx/systemo-ebook/releases/tag/v2.0.0) and extract it to a writable directory (e.g. `D:\systemo-ebook`, not `C:\Program Files`).
2. Double-click `EasyPub.exe` — no Python or Node.js installation required (keep the full extracted folder together; an empty library on first launch is normal for a zero-data build).
3. Search or drop a TXT file, build a cover-embedded EPUB, and send it to your device or read directly inside the built-in reader.

**Release Verification (v2.0.0 / Build `1123205248` / 2026-11-23)**

- File: `systemo-ebook-v2.0.0-full.rar`
- Size: `275,575,368` bytes (`262.8 MiB`), unpacks to `335` files (`901.3 MB`)
- SHA256: `A687ECB5E659A6088B92398E33895B9CD0899F8E1666DF5A7E65D4AB9D69272A`

---

## Running from Source & Custom Book Sources

Only the full edition is maintained on a single development track (the lite edition is discontinued). Full source code is open for personal study and custom source adapters.

| Requirement | Version | Purpose |
| :--- | :--- | :--- |
| Windows | Windows 10 / 11 x64 | Desktop shell and local browser integration |
| Node.js | 18+ (Electron `^27.3.0`) | Desktop shell and UI renderer |
| Python | 3.11+ | Downloaders, EPUB builder, cover matching, and transfer scripts |
| Chrome / Edge | Stable | Isolated native OAuth session, WiFi transfer, and cover fallback |

```powershell
git clone https://github.com/systemsssx/systemo-ebook.git
cd systemo-ebook
pip install -r requirements.txt
npm install
npm start
```

- **Custom book sources**: Source adapters and chart logic live in `novelmeta/`, `novel_dl.py`, `rank_pool.py`, and `linovelib.py`. Run `npm run bump` after editing CSS/JS assets.
- **Bundled binaries**: Large runtime binaries (`fonts/`, `models/`, `pandoc-3.10.2/`, `chromedriver.exe`, `tools/mihomo/`) are included in the Release archive and excluded from the Git repository.
- **Custom proxy subscription**: Public builds contain no private subscription URL; use your system proxy or set `EASYPUB_PROXY_SUB_URL` to your own subscription endpoint.

---

## Privacy Boundaries & Disclaimer

**Privacy & Usage Boundaries**

- Both the repository and release archive ship with zero personal data, library history, cookies, or preconfigured proxy subscriptions.
- Kindle delivery requires your own Amazon sign-in, Apple Books relay authorizes Baidu Netdisk in your local browser, and WiFi transfer runs strictly on your LAN. Use Cloud Drive Sync or WiFi transfer if you prefer not to link Baidu Netdisk.

**Disclaimer**

- Non-commercial personal project: Built purely for personal reading convenience, programming study, and technical research, with no monetization, paid courses, or advertisements.
- Technical nature: Web novel downloading and cover fetching perform local offline caching and automated typesetting of public web pages to reduce multi-device reading friction, not for piracy distribution.
- Support copyright: Generated EPUBs are strictly for personal offline reading and e-reader format conversion; redistribution or commercial use is prohibited. Please subscribe on official platforms to support original authors.

---

## Acknowledgments & License

- Built with [Electron](https://www.electronjs.org/), [foliate-js](https://github.com/johnfactotum/foliate-js), [@readium/css](https://github.com/readium/css), [Pandoc](https://pandoc.org/), [OpenCC](https://github.com/BYVoid/OpenCC), and [PyInstaller](https://pyinstaller.org/).
- Source code is provided for personal study and reference (**All rights reserved**); third-party licenses belong to their respective owners.
