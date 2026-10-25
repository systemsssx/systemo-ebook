/* ============================================================
   i18n.js — 中英切换（轻量，不引库）
   ------------------------------------------------------------
   用法：
     · 元素加 data-i18n="key"，切语言时自动替换 textContent
     · 属性用 data-i18n-title / data-i18n-ph
     · 代码里调 window.__i18n.t(key)

   Key 的两种形态：
     1. 语义 key（nav.download 等）—— 外壳那批，中英都要维护
     2. 中文原文 key（"拖拽 TXT 文件至此"）—— 静态界面那批
        中文模式下直接返回 key 本身，所以 zh 字典里不用列。
        好处：自动生成不用人工命名；坏处：中文改了译文会失配，
             这种情况会原样显示中文（不会崩，也不会显示空）。
   ============================================================ */
(function () {
    'use strict';

    var ZH = {
        'nav.download': '下载',
        'nav.make': '制作',
        'nav.send': '传输',
        'nav.library': '书库',
        'nav.auto': '一键',
        'nav.help': '帮助',
        'sub.info': '书籍信息',
        'sub.name': '书名作者',
        'sub.chapters': '章节划分',
        'sub.cover': '封面设置',
        'sub.applebooks': '苹果图书',
        'sub.wifi': 'WiFi 传书',
        'sub.kindle': 'Kindle 传书',
        'nav.api': 'API'
    };

    var EN = {
        "简洁背景": "Simple background",
        "切换为简洁背景": "Switch to simple background",
        "切换为场景背景": "Switch to scenic background",
        "nav.download": "Download",
        "nav.make": "Make",
        "nav.send": "Send",
        "nav.library": "Library",
        "nav.auto": "One-Click",
        "nav.help": "Help",
        "nav.api": "API",
        "sub.info": "Book Info",
        "sub.name": "Title & Author",
        "sub.chapters": "Chapters",
        "sub.cover": "Cover",
        "sub.applebooks": "Apple Books",
        "sub.wifi": "WiFi Transfer",
        "sub.kindle": "Kindle",
        "提示": "Notice",
        "知道了": "Got it",
        "这本书有多个同名结果": "Several books share this title",
        "请选择你要的那一本（作者不同）：": "Pick the one you want (authors differ):",
        "已导入 TXT，点此一键": "TXT imported — click to run the rest",
        "已导入 TXT 就点下面的按钮：分章 → 封面 → 转换 → 上传": "Already imported a TXT? Use the button below: split → cover → convert → upload",
        "⚠️ 请先在「书籍信息」页导入 TXT 文件": "⚠️ Import a TXT file on the Book Info page first",
        "[1/4] 分章完成后等待": "[1/4] Waiting after split",
        "[1/4] 等待分章页就绪": "[1/4] Waiting for the chapters page",
        "[2/4] 封面完成，跳转": "[2/4] Cover done",
        "[2/4] 等待封面页就绪": "[2/4] Waiting for the cover page",
        "[3/4] 等待书籍信息页就绪": "[3/4] Waiting for the book info page",
        "⏳ [1/4] 正在分章（可能需要 30s+）...": "⏳ [1/4] Splitting chapters (may take 30s+)...",
        "⏳ [2/4] 正在搜索封面...": "⏳ [2/4] Searching for a cover...",
        "⏳ [3/4] 正在转换为 EPUB（可能需要 30s+）...": "⏳ [3/4] Converting to EPUB (may take 30s+)...",
        "⏳ [4/4] 切到苹果图书页上传...": "⏳ [4/4] Switching to Apple Books...",
        "⏳ [4/4] 正在打开 WiFi 传书窗口...": "⏳ [4/4] Opening the WiFi window...",
        "⚠️ [1/4] 分章超时或无结果，尝试继续": "⚠️ [1/4] Split timed out — continuing",
        "⚠️ [2/4] 封面搜索超时，立即继续（无封面模式）": "⚠️ [2/4] Cover search timed out — continuing without one",
        "⚠️ [2/4] 未找到封面，立即继续（无封面模式）": "⚠️ [2/4] No cover found — continuing without one",
        "⚠️ [3/4] 转换超时或未知错误": "⚠️ [3/4] Conversion timed out or errored",
        "✅ [1/4] 分章完成，共 ": "✅ [1/4] Split into ",
        "✅ [2/4] 封面已找到": "✅ [2/4] Cover found",
        "✅ [3/4] EPUB 已生成": "✅ [3/4] EPUB generated",
        "❌ [1/4] 找不到 #btnPreviewChapters": "❌ [1/4] #btnPreviewChapters not found",
        "❌ [2/4] 找不到 #btnSearchCover": "❌ [2/4] #btnSearchCover not found",
        "❌ [3/4] 找不到 #convertBtn": "❌ [3/4] #convertBtn not found",
        "❌ [3/4] 转换失败（请看「书籍信息」页按钮状态）": "❌ [3/4] Conversion failed — check the button on Book Info",
        "🎉 [4/4] Kindle 推送成功！全部完成 ✅": "🎉 [4/4] Sent to Kindle — all done ✅",
        "⏳ [4/4] 正在推送到 Kindle 邮箱...": "⏳ [4/4] Sending to your Kindle address...",
        "上传": "Upload",
        "传输": "Transfer",
        "下载过程中请勿操作电脑防止误触": "Don't touch the computer during download",
        "轻小说可在书名后加卷号（不写＝第 1 卷）：败北女角太多了 第九卷（输书名会自动弹选卷窗）": "Light novels: append the volume, e.g. 败北女角太多了 第九卷 (default: vol 1)",
        "保存": "Save",
        "保存修改": "Save changes",
        "修改后自动保存": "Saved automatically",
        "删除": "Delete",
        "禁用": "Disable",
        "启用": "Enable",
        "恢复": "Restore",
        "永久删除": "Delete permanently",
        "刷新": "Refresh",
        "导入书籍": "Import books",
        "取消": "Cancel",
        "添加": "Add",
        "移除": "Remove",
        "重置": "Reset",
        "全选": "Select all",
        "打开目录": "Open folder",
        "在文件夹中显示": "Show in folder",
        "多选": "Select",
        "退出多选": "Exit select",
        "扫描中…": "Scanning…",
        "单击阅读 · 右键详情": "Click to read · Right-click for details",
        "双击阅读": "Double-click to read",
        "没有匹配的书": "No matching books",
        "换个关键词，或切回「全部」": "Try another keyword, or switch back to All",
        "取消全选": "Deselect all",
        "无封面": "No cover",
        "TXT 没有元数据，只能改文件名": "TXT has no metadata — only the file name can change",
        "保存中…": "Saving…",
        "保存失败": "Save failed",
        "确定删除 ": "Delete ",
        " 个文件？": " file(s)?",
        "这些文件会从 export 目录里真正删掉，不进回收站。": "These files are permanently removed from the export folder (not sent to the Recycle Bin).",
        "📚 已删除": "📚 Deleted",
        "删除失败：": "Delete failed: ",
        "批量传输需要在应用内使用": "Batch send only works inside the app",
        "导出需要在应用内使用": "Export only works inside the app",
        "📤 已导出": "📤 Exported",
        "已导出 ": "Exported ",
        " 本到": " book(s) to",
        "失败：": "Failed: ",
        "导出失败：": "Export failed: ",
        "换封面会走「制作 → 封面设置」的搜索 + 超分流程，后续接上。": "Changing the cover will reuse the Make → Cover search + upscale flow (to be wired up).",
        "⚠️ library API 未连接": "⚠️ library API not connected",
        "⚠️ 书库扫描失败:": "⚠️ Library scan failed: ",
        "⚠️ 书库扫描异常:": "⚠️ Library scan error: ",
        "批量传输": "Batch send",
        "编辑信息": "Edit info",
        "更换封面": "Change cover",
        "清除封面": "Remove cover",
        "联网搜索封面": "Search online",
        "点击上传封面": "Click to upload",
        "恢复默认": "Reset to default",
        "更改": "Change",
        "授权": "Authorize",
        "转换": "Convert",
        "预览章节": "Preview",
        "章节编辑": "Edit",
        "📋 章节编辑": "📋 Edit chapters",
        "📖 操作说明": "📖 How to use",
        "📖 操作指引": "📖 Guide",
        "📧 邮箱": "📧 Email",
        "⚠ 未配置邮箱": "⚠ Email not set",
        "选择上传方式": "Choose upload method",
        "书名作者": "Title & Author",
        "章节划分": "Chapters",
        "下载": "Download",
        "一键完成": "One-click creation",
        "书名 或 书名@作者（用@分隔）": "Title, or Title@Author (use @)",
        "搜索书名或作者…": "Search title or author…",
        "输入书名": "Enter title",
        "输入作者": "Enter author",
        "封面设置": "Cover",
        "Kindle 传书": "Kindle",
        "WiFi 传书": "WiFi Transfer",
        "中文": "中文",
        "systemoebook · 电子书制作": "systemoebook · EPUB Maker",
        "拖拽 TXT 文件至此": "Drop a TXT file here",
        "或点击选择 · 支持 .txt": "or click to browse · .txt only",
        "未命名": "Untitled",
        "未命名.txt": "Untitled.txt",
        "最大标题长度": "Max title length",
        "超过此字数的行不会被识别为章节标题": "Longer lines are not treated as chapter titles",
        "请先导入 TXT 文件，然后点击「预览章节」": "Import a TXT file, then click Preview chapters",
        "章节列表": "Chapter list",
        "共": "Total",
        "章": "chapters",
        "0 章": "0 chapters",
        "📊 共": "📊 Total",
        "置信度:": "Confidence:",
        "个问题": "issues",
        "已删除": "Deleted",
        "已选": "Selected",
        "项": "items",
        "添加章节：": "Add chapter:",
        "章节名称": "Chapter title",
        "行号": "Line no.",
        "序号": "No.",
        "操作": "Action",
        "💡 手动添加章节：输入章节名称和所在行号 · 点击「删除」标记删除 · 点击「恢复」恢复 · 点击「永久删除」彻底移除": "💡 Add chapters manually: enter a title and line number · Delete marks it · Restore undoes · Purge removes it for good",
        "封面图片": "Cover image",
        "从书名和作者自动生成文字封面": "Generate a text cover from title and author",
        "支持 JPG / PNG / WebP · 点击上传 · 联网搜索根据书名和作者匹配": "JPG / PNG / WebP · click to upload · online search matches title and author",
        "封面超分倍数": "Upscale",
        "不超分": "Off",
        "2 倍": "2×",
        "3 倍": "3×",
        "4 倍（推荐）": "4× (recommended)",
        "小图放大更清晰，本地模型约 0.6 秒": "Sharper on small images · ~0.6s locally",
        "输出到": "Output to",
        "与源文件同目录": "Same folder as source",
        "书名": "Title",
        "作者": "Author",
        "书名@作者": "Title@Author",
        "查找作者需要一定时间，请耐心等待": "Looking up the author — this takes a moment",
        "示例：吞没@泡泡藻 · 或只写 · 吞没": "e.g. 吞没@泡泡藻 · or just · 吞没",
        "输入格式：": "Format: ",
        "（用 @ 分隔，可只写书名）": "(separate with @; title alone is fine)",
        "小说": "Fiction",
        "学术": "Academic",
        "诗歌": "Poetry",
        "拖拽图书文件至此": "Drop book files here",
        "或点击选择 · 支持 .epub / .txt / .pdf": "or click to browse · .epub / .txt / .pdf",
        "或点击选择 · 支持 .epub / .azw3 / .mobi / .pdf": "or click to browse · .epub / .azw3 / .mobi / .pdf",
        "苹果图书（上传到百度网盘）": "Apple Books (upload to Baidu Netdisk)",
        "WiFi 传书（弹窗）": "WiFi Transfer (dialog)",
        "网盘目录": "Netdisk folder",
        "重名时": "On duplicate",
        "覆盖": "Overwrite",
        "跳过": "Skip",
        "询问": "Ask",
        "前 4 步已自动完成（下载 → 分章 → 找封面 → 转换），请选择最后上传方式：": "The first 4 steps are done (download → split → cover → convert). Choose how to send:",
        "点击猫爪一键完成：下载 → 分章 → 封面 → 转换 → 上传": "One click runs: download → split → cover → convert → send",
        "中间任何步骤出错会暂停并提示，请保持窗口打开": "Any error pauses the run and reports — keep this window open",
        "书库是空的": "Your library is empty",
        "在「制作」里转换成功的 EPUB 会自动出现在这里": "EPUBs converted under Make show up here automatically",
        "全部": "All",
        "书籍详情": "Book details",
        "书 名": "TITLE",
        "作 者": "AUTHOR",
        "简 介": "DESCRIPTION",
        "格 式": "FORMAT",
        "章节数": "Chapters",
        "文件大小": "Size",
        "修改时间": "Modified",
        "改动会写回原 EPUB，不会重新转换": "Changes are written back to the EPUB — no re-conversion",
        "就绪": "Ready",
        "访问主页": "Visit homepage",
        "预设：": "Preset: ",
        "默认": "Default",
        "（唯一输出格式）": "(only output format)",
        "导出": "Export",
        "本，选择传输方式：": "books — pick a transfer method:",
        "传到同一局域网内的阅读设备": "To a reading device on the same Wi-Fi",
        "作为附件发送到 Kindle 邮箱": "As an attachment to your Kindle address",
        "复制到下载目录后上传百度网盘": "Copy to the download folder, then upload to Baidu Netdisk",
        "苹果图书": "Apple Books",
        "来自书库：本次将": "From library: sending",
        "本": "book(s)",
        "Kindle 操作指引": "Kindle guide",
        "打开传书窗口": "Open transfer window",
        "苹果图书操作指引": "Apple Books guide",
        "邮箱配置": "Email settings",
        "请先填写书名": "Enter a title first",
        "+ 添加文件": "+ Add files",
        "AI 搜索": "AI Lookup",
        "Kindle 推送": "Kindle",
        "TXT 转 EPUB · 全流程本地完成，不上传任何文件": "TXT → EPUB · fully local, nothing uploaded",
        "WiFi 直传": "WiFi Direct",
        "✏️ 章节编辑": "✏️ Edit chapters",
        "✏️ 编辑章节": "✏️ Edit chapters",
        "三合一：苹果图书（百度网盘）· WiFi 直传 · Kindle 邮箱推送": "Three ways: Apple Books (Baidu Netdisk) · WiFi Direct · Kindle email",
        "书籍信息": "Book Info",
        "从书库拖书进来，或点这里从本地添加": "Drag books in from the library, or add them locally",
        "传输到设备": "Transfer",
        "传输在后台进行，可以随时切到别的板块。": "Transfers run in the background — switch sections freely.",
        "关闭": "Close",
        "列表只用于核对；改动请在编辑器里做": "Read-only list; make changes in the editor",
        "制作新书": "New Book",
        "原始尺寸": "Original",
        "去「书库」勾选后点「批量传输」，或点右下角从本地添加": "Pick books in Library → Batch send, or add files locally",
        "发到 Kindle 邮箱": "To your Kindle address",
        "发送": "Send",
        "发送到 Kindle 邮箱": "Send to your Kindle address",
        "弹出新窗口": "Opens a new window",
        "上传到百度网盘": "Upload to Baidu Netdisk",
        "填错也没关系，可以随时在书库里改": "Don't worry if it's wrong — editable in the library",
        "导入 TXT 后点「预览切分」，这里会显示拆出了多少章": "Import a TXT and hit Preview split to see the chapter count",
        "封面预览": "Cover Preview",
        "将显示在 EPUB 的书名页与书库中": "Shown on the EPUB title page and in the library",
        "已授权": "Authorized",
        "已超分": "Upscaled",
        "开始": "Start",
        "当前通道": "Current Channel",
        "待发送队列": "Transfer queue",
        "待启动": "Idle",
        "手机浏览器上传": "Upload from phone browser",
        "方式": "Method",
        "更换": "Change",
        "本地副本": "Local copy",
        "查看全部章节": "View all chapters",
        "标题最长": "Max title length",
        "清除": "Clear",
        "源文件": "Source File",
        "章节总数": "chapters",
        "章节预览": "Chapter Preview",
        "经百度网盘中转": "Relayed via Baidu Netdisk",
        "耗时": "Time",
        "说明": "Notes",
        "账户": "Account",
        "超分后": "Upscaled",
        "超过这个长度的一律当作正文，不拆成章节标题": "Longer lines count as body text, not chapter titles",
        "输出至": "Output to",
        "还没有添加文件": "No files yet",
        "远端目录": "Remote folder",
        "重名策略": "On duplicate",
        "重新拆分": "Re-split",
        "重新授权": "Re-authorize",
        "重新搜索封面": "Search cover again",
        "队列是空的": "The queue is empty",
        "预览切分": "Preview split",
        "尚未配置": "Not set",
        "(行": "(line",
        "... 还有": "... and",
        "✅ 分章成功！共": "✅ Split succeeded —",
        "✅ 已保存": "✅ Saved",
        "✅ 已生成：": "✅ Generated:",
        "个章节": "chapters",
        "字": "chars",
        "行": "line",
        "第{n}章": "Chapter {n}",
        "<div style=\"color:#8e8e93; font-size: 12px; padding:8px 0;\">请点击「预览章节」</div>": "<div style=\"color:#8e8e93; font-size: 12px; padding:8px 0;\">Click Preview chapters</div>",
        "<div style=\"color:#8e8e93; font-size: 12px; padding:8px 0;\">请先导入 TXT 文件</div>": "<div style=\"color:#8e8e93; font-size: 12px; padding:8px 0;\">Import a TXT file first</div>",
        "<div style=\"color:#8e8e93; font-size: 12px; padding:8px 0;\">未检测到章节</div>": "<div style=\"color:#8e8e93; font-size: 12px; padding:8px 0;\">No chapters detected</div>",
        "<svg viewBox=\"0 0 24 24\"><polyline points=\"1 6 1 22 8 18 16 22 23 18 23 2 16 6 8 2 1 6\"/><polyline points=\"8 2 8 18\"/><polyline points=\"16 6 16 22\"/></svg><span class=\"label\">预览章节</span>": "<svg viewBox=\"0 0 24 24\"><polyline points=\"1 6 1 22 8 18 16 22 23 18 23 2 16 6 8 2 1 6\"/><polyline points=\"8 2 8 18\"/><polyline points=\"16 6 16 22\"/></svg><span class=\"label\">Preview</span>",
        " · 正在跳转…": " · Redirecting…",
        " · 等待页面稳定（": " · Stabilizing (",
        " 章": " chapters",
        "  章": " chapters",
        "秒）...": "s)...",
        "[2/5] 分章完成后等待": "[2/5] Waiting after split",
        "[2/5] 等待分章页就绪": "[2/5] Waiting for the chapters page",
        "[3/5] 封面完成，跳转": "[3/5] Cover done",
        "[3/5] 等待封面页就绪": "[3/5] Waiting for the cover page",
        "[4/5] 等待书籍信息页就绪": "[4/5] Waiting for the book info page",
        "⏳ Kindle 推送中...": "⏳ Sending to Kindle...",
        "⏳ [1/5] 正在下载小说...": "⏳ [1/5] Downloading novel...",
        "⏳ [2/5] 正在分章（可能需要 30s+）...": "⏳ [2/5] Splitting chapters (may take 30s+)...",
        "⏳ [3/5] 正在搜索封面...": "⏳ [3/5] Searching for a cover...",
        "⏳ [4/5] 正在转换为 EPUB（可能需要 30s+）...": "⏳ [4/5] Converting to EPUB (may take 30s+)...",
        "⏳ [5/5] 切到苹果图书页上传...": "⏳ [5/5] Switching to Apple Books...",
        "⏳ [5/5] 正在打开 WiFi 传书窗口...": "⏳ [5/5] Opening the WiFi window...",
        "⏳ 上传到百度网盘中...": "⏳ Uploading to Baidu Netdisk...",
        "⏳ 搜索中…": "⏳ Searching…",
        "⏳ 正在启动下载器，请耐心等待（30秒-2分钟）…": "⏳ Starting the downloader — 30s to 2min…",
        "⏳ 等待作者信息（最多 8 秒）...": "⏳ Waiting for author info (max 8s)...",
        "⚠ 未配置": "⚠ Not set",
        "⚠ 检查失败": "⚠ Check failed",
        "⚠️ [2/5] 分章超时或无结果，尝试继续": "⚠️ [2/5] Split timed out — continuing",
        "⚠️ [3/5] 封面搜索超时，立即继续（无封面模式）": "⚠️ [3/5] Cover search timed out — continuing without one",
        "⚠️ [3/5] 未找到封面，立即继续（无封面模式）": "⚠️ [3/5] No cover found — continuing without one",
        "⚠️ [4/5] 转换超时或未知错误": "⚠️ [4/5] Conversion timed out or errored",
        "⚠️ 下载完成但未在 download 目录找到文件": "⚠️ Download finished but nothing landed in the download folder",
        "⚠️ 只能用一个 @ 分隔，不要重复": "⚠️ Use only one @ separator",
        "⚠️ 已取消": "⚠️ Cancelled",
        "⚠️ 拖拽文件未能读取路径，请用点击方式选择": "⚠️ Couldn't read the dropped path — click to browse instead",
        "⚠️ 拖拽无法读取本地路径，请点击文件框选择文件": "⚠️ Drops don't expose local paths — click the box instead",
        "⚠️ 搜索失败，请手动输入": "⚠️ Search failed — enter it manually",
        "⚠️ 未找到作者，请手动输入": "⚠️ Author not found — enter it manually",
        "⚠️ 未连接到 Electron": "⚠️ Not connected to Electron",
        "⚠️ 请先点击「📧 邮箱」配置收件人 / SMTP": "⚠️ Click 📧 Email to set the recipient / SMTP first",
        "⚠️ 请先输入书名": "⚠️ Enter a title first",
        "⚠️ 请先选择要发送的图书文件": "⚠️ Pick a book file to send first",
        "⚠️ 请用 @ 分隔书名和作者，例如：吞没@泡泡藻": "⚠️ Separate title and author with @, e.g. 吞没@泡泡藻",
        "✅ [1/5] 下载完成：": "✅ [1/5] Downloaded: ",
        "✅ [2/5] 分章完成，共 ": "✅ [2/5] Split into ",
        "✅ [3/5] 封面已找到": "✅ [3/5] Cover found",
        "✅ [4/5] EPUB 已生成": "✅ [4/5] EPUB generated",
        "✅ 下载完成：": "✅ Downloaded: ",
        "✅ 已上传封面": "✅ Cover uploaded",
        "✅ 已找到作者": "✅ Author found",
        "✓ 完成": "✓ Done",
        "✗ 失败": "✗ Failed",
        "❌ Electron API 未连接": "❌ Electron API not connected",
        "❌ Electron API 未连接，请用 npm start 启动": "❌ Electron API not connected — start with npm start",
        "❌ Kindle API 未连接": "❌ Kindle API not connected",
        "❌ WiFi 功能不可用": "❌ WiFi unavailable",
        "❌ [2/5] 找不到 #btnPreviewChapters": "❌ [2/5] #btnPreviewChapters not found",
        "❌ [3/5] 找不到 #btnSearchCover": "❌ [3/5] #btnSearchCover not found",
        "❌ [4/5] 找不到 #convertBtn": "❌ [4/5] #convertBtn not found",
        "❌ [4/5] 转换失败（请看「书籍信息」页按钮状态）": "❌ [4/5] Conversion failed — check the button on Book Info",
        "❌ 上传失败：": "❌ Upload failed: ",
        "❌ Kindle 推送失败：": "❌ Kindle send failed: ",
        "❌ Kindle 推送异常：": "❌ Kindle send error: ",
        "❌ 上传失败：未授权。请先授权！": "❌ Upload failed: not authorized — authorize first",
        "❌ 上传异常：": "❌ Upload error: ",
        "❌ 下载失败：": "❌ Download failed: ",
        "❌ 下载异常：": "❌ Download error: ",
        "❌ 分章失败：": "❌ Chapter split failed: ",
        "❌ 启动失败：": "❌ Failed to start: ",
        "❌ 复制失败：": "❌ Copy failed: ",
        "❌ 复制文件失败：": "❌ Copying files failed: ",
        "❌ 异常：": "❌ Error: ",
        "❌ 打开 WiFi 窗口失败：": "❌ Failed to open the WiFi window: ",
        "❌ 打开传输窗口失败：": "❌ Failed to open the transfer window: ",
        "❌ 打开指引窗口失败：": "❌ Failed to open the guide: ",
        "❌ 打开授权窗口失败：": "❌ Failed to open the authorization window: ",
        "❌ 打开操作指引失败：": "❌ Failed to open the guide: ",
        "❌ 打开说明窗口失败：": "❌ Failed to open the help window: ",
        "❌ 打开邮箱配置窗口失败：": "❌ Failed to open email settings: ",
        "❌ 推送失败": "❌ Send failed",
        "❌ 推送失败：": "❌ Send failed: ",
        "❌ 未找到封面，请手动上传": "❌ No cover found — upload one manually",
        "❌ 未找到本书": "❌ Book not found",
        "❌ 未连接到 Electron 环境": "❌ Not running in Electron",
        "❌ 未连接到 Electron 环境，请使用 npm start 启动": "❌ Not running in Electron — start with npm start",
        "❌ 读取邮箱配置失败：": "❌ Failed to read email settings: ",
        "❌ 选择文件失败：": "❌ File selection failed: ",
        "上传中…": "Uploading…",
        "本次上传": "Uploading",
        "本（队列）": "file(s) from the send queue",
        "上传超时(5分钟)": "Upload timed out (5 min)",
        "传输中...": "Sending...",
        "作者信息就绪": "Author info ready",
        "分章失败：": "Chapter split failed: ",
        "已保存": "Saved",
        "已启动 Kindle 推送": "Kindle send started",
        "已清除封面": "Cover cleared",
        "已配置": "Configured",
        "收件人：": "To: ",
        "未知作者": "Unknown author",
        "未知错误": "Unknown error",
        "确定要永久删除该章节吗？": "Permanently delete this chapter?",
        "请先导入一个 TXT 文件！": "Import a TXT file first!",
        "请先点击「预览章节」获取章节数据！": "Click Preview first to load chapter data!",
        "请先点击「预览章节」获取章节数据，或手动添加章节标记！": "Click Preview first, or add chapter marks manually!",
        "请先点击右侧「邮箱」按钮配置": "Click the Email button on the right first",
        "请拖入 .txt 文件！": "Drop a .txt file!",
        "请至少保留一个章节！": "Keep at least one chapter!",
        "请输入章节名称！": "Enter a chapter title!",
        "转换中…": "Converting…",
        "转换失败：": "Conversion failed: ",
        "重置所有章节修改？": "Reset all chapter edits?",
        "🎉 Kindle 推送完成！": "🎉 Sent to Kindle!",
        "🎉 Kindle 推送完成": "🎉 Kindle send complete",
        "🎉 [5/5] Kindle 推送成功！全部完成 ✅": "🎉 [5/5] Sent to Kindle — all done ✅",
        "⏳ [5/5] 正在推送到 Kindle 邮箱...": "⏳ [5/5] Sending to your Kindle address...",
        "⚠️ 还没配置 Kindle 邮箱": "⚠️ Kindle email not configured",
        "⚠️ 还没配置 Kindle 邮箱：请到「发送」页点 Kindle 卡片的「邮箱」按钮填写": "⚠️ Kindle email not configured — go to Transfer and click the Email button on the Kindle card",
        "🎉 WiFi 传书完成！全部流程结束 ✅": "🎉 WiFi transfer done — all steps complete ✅",
        "🎉 上传成功": "🎉 Upload succeeded",
        "🎉 上传成功！全部完成 ✅": "🎉 Upload succeeded — all done ✅",
        "🎉 全部完成！一键流程成功": "🎉 All done — one-click run succeeded",
        "💡 支持 JPG / PNG / WebP": "💡 JPG / PNG / WebP supported",
        "📄 已准备文件：": "📄 File ready: ",
        "📄 文件：": "📄 File: ",
        "📧 正在推送文件到 Kindle...": "📧 Sending to Kindle...",
        "WiFi 传书弹窗已打开，请在那里完成上传（关闭后这里会显示完成）": "WiFi dialog opened — finish the upload there (this updates when you close it)",
        "🔍 正在搜索封面...": "🔍 Searching for a cover...",
        "🔍 正在查找作者，请耐心等待...": "🔍 Looking up the author...",
        "🔍 联网搜索封面": "🔍 Search cover online",
        "🚀 启动 Kindle 推送...": "🚀 Starting Kindle send...",
        "等待发送…": "Waiting to send...",
        "检查中…": "Checking...",
        "⚠️ 百度网盘未授权或授权已过期，点上方「重新授权」": "⚠️ Baidu Netdisk is not authorized or the token expired — click \"Re-authorize\" above",
        "🔄 授权已自动续期（access_token 过期但 refresh_token 有效，无需重新登录）": "🔄 Authorization renewed automatically (access_token expired but refresh_token is still valid — no need to log in again)",
        "推送中…": "Sending...",
        "授权中…": "Authorizing...",
        "传输中…": "Transferring...",
        "未授权": "Not authorized",
        "已过期": "Expired",
        "检查失败": "Check failed",
        "传书服务运行中": "Transfer service running",
        "传书窗口已打开，等待输入网址": "Transfer window open — enter the URL",
        "还没打开传书窗口": "Transfer window not open yet",
        "配置不完整": "Incomplete config",
        "配置中": "Configure",
        "✅ 完成": "✅ Done",
        "⚠️ 上传超时（5 分钟未完成）：可能是百度网盘未授权或网络异常，请点上方「重新授权」按钮完成授权后重试": "⚠️ Upload timed out after 5 minutes — Baidu Netdisk may not be authorized, or the network is stuck. Click \"Re-authorize\" above and try again",
        "🔐 百度网盘授权已失效，正在切到授权页…": "🔐 Baidu Netdisk authorization expired — opening the auth page...",
        "✅ 授权成功！": "✅ Authorized!",
        "❌ 授权失败": "❌ Authorization failed",
        "⚠️ 授权未完成（本地仍保留原授权）": "⚠️ Authorization not completed (your previous authorization is kept)",
        "开始发送": "Start sending",
        "开始上传到百度网盘": "Start uploading to Baidu Netdisk",
        "上传失败：": "Upload failed: ",
        "上传异常：": "Upload error: ",
        "上传失败": "Upload failed",
        "上传失败，请检查授权": "Upload failed — please check the authorization",
        "日志": "log",
        "打开授权窗口（请点上方「重新授权」完成）": "Opening the authorization window (click \"Re-authorize\" above to finish)",
        "打开授权窗口失败：": "Failed to open the authorization window: ",
        "⚠️ 授权超时（5 分钟未完成）：授权码可能已过期或网络异常，请重新点「重新授权」再试": "⚠️ Authorization timed out after 5 minutes — the code may have expired or the network is stuck. Click \"Re-authorize\" and try again",
        "🎉 WiFi 传书完成": "🎉 WiFi transfer complete",
        "❌ WiFi 传书失败：": "❌ WiFi transfer failed: ",
        "完成": "Done",
        "WiFi 传书完成": "WiFi transfer complete",
        "WiFi 传书失败：": "WiFi transfer failed: ",
        "Kindle 推送完成": "Sent to Kindle",
        "Kindle 推送失败：": "Kindle send failed: ",
        "推送失败": "Send failed",
        "授权失败": "Authorization failed",
        "未配置": "Not configured",
        "上传完成": "Upload complete",
        "云端已是最新，无需上传": "Already up to date in the cloud — nothing to upload",
        "✓ 已是最新": "✓ Up to date",
        "用时": "took",
        "秒": "s",
        "输出为 EPUB": "EPUB output",
        "苹果图书（百度网盘） · WiFi 直传 · Send to Kindle": "Apple Books (Baidu Netdisk) · WiFi · Send to Kindle",
        "经 Amazon 官方通道": "Via Amazon",
        "Kindle 授权": "Authorize Kindle",
        "输入格式：书名@作者（用 @ 分隔，可只写书名）": "Enter title@author, or just the title",
        "书库": "Library",
        "制作完成的 EPUB 会自动出现在这里。": "Your finished EPUBs appear here automatically.",
        "我的书库": "My library",
        "精选推荐": "Discover",
        "综合推荐": "All recommendations",
        "轻小说": "Light novels",
        "Send to Kindle": "Send to Kindle",
        "经 Amazon 官方通道上传": "Upload via Amazon",
        "Cookie 过期自动续期（会弹一次 Edge，约 10 秒）": "Renew expired cookies (opens Edge for about 10 seconds)",
        "刷新 Cookie": "Refresh cookies",
        "要下「精校源」吗？": "Download the proofread edition?",
        "下载精校源（UU看書）": "Proofread source (UU)",
        "速度非常慢 · 会多次失败重试 · 需挂 VPN": "Very slow · Multiple retries · VPN required",
        "就下普通源（笔趣阁）": "Standard source (Biquge)",
        "快 · 一分钟级别 · 正文可能带站方样板": "Fast · About a minute · May contain site notices",
        "经 Amazon 官方通道上传到云端": "Upload to Kindle via Amazon",
        "☁️ Kindle 授权": "☁️ Authorize Kindle",
        "登录 Amazon 一次（Kindle 绑定的那个账号）。之后在「传输」页传书会直接进你的 Kindle 云端，约 1 分钟到手。": "Sign in with the Amazon account linked to your Kindle. Books sent from Transfer will appear in your Kindle cloud in about a minute.",
        "Amazon 邮箱 / 手机号": "Amazon email / phone",
        "密码（不保存）": "Password (not stored)",
        "若弹出 Windows「通行密钥 / 安全密钥」窗口，直接点「取消」即可 —— 不影响授权。": "If Windows asks for a passkey or security key, select Cancel to continue signing in.",
        "清空登录信息": "Clear sign-in data",
        "登录": "Sign in",
        "☁️ 百度网盘授权": "☁️ Authorize Baidu Netdisk",
        "点「打开浏览器授权」→ 在弹出的浏览器里登录百度网盘并授权 → 把页面上的授权码粘到下面 → 提交。": "Open the authorization browser, sign in to Baidu Netdisk, and approve access. Copy the authorization code, paste it below, and submit.",
        "打开浏览器授权": "Open authorization browser",
        "授权码": "Authorization code",
        "提交授权": "Submit code",
        "在手机浏览器里打开下面这个地址（手机和电脑要同一个 WiFi），选好书后回到这里点「开始传输」。": "Enter the address shown by your phone's book-transfer page. Keep both devices on the same WiFi, then select Start transfer.",
        "手机上的地址": "Address shown on your phone",
        "刷新间隔（秒）": "Refresh interval (seconds)",
        "历史网址": "Recent addresses",
        "开始传输": "Start transfer",
        "准备中…": "Preparing…",
        "⏳ 正在查询书源并下载…（长书要几分钟，进度就写在这行）": "⏳ Finding sources and downloading… Long books may take a few minutes.",
        "精校源（UU看書）有这本书 —— 请选择用哪条源下载…": "A proofread edition is available on UU. Choose a source…",
        "已取消下载": "Download cancelled",
        "走精校源（UU看書）：速度非常慢，会多次失败重试，": "Using UU: very slow, with multiple retries,",
        "走普通源（笔趣阁）…": "Using the standard source (Biquge)…",
        "📚 这是轻小说 —— 已为你打开选书窗：": "📚 Light novel: choose a book in the selection window:",
        "　（先在搜索结果里选一部，再勾卷下载）": " (Select a book, then choose volumes to download)",
        "有 ": "There are ",
        " 章没抓到（站点限流），建议稍后再下一次补全": " missing chapters due to site rate limits. Retry later to complete them.",
        "✅ 轻小说已完成：": "✅ Light novel complete: ",
        " · 已进书库 · 已加入传输框": " · Added to library and transfer queue",
        "✅ [1/1] 轻小说已完成（直接出 EPUB）：": "✅ [1/1] Light novel EPUB created: ",
        " · 已进书库 · 已加入传输框（发送请到「传输到设备」页）": " · Added to library and transfer queue (send from Transfer)",
        "WiFi 传书弹窗已打开：请在弹窗里填地址并点「开始传输」": "WiFi dialog opened. Enter the address and select Start transfer.",
        "⏱ WiFi 传书超时未完成（5 分钟）—— 请回弹窗确认后重试": "⏱ WiFi transfer timed out after 5 minutes. Check the dialog and retry.",
        "弹窗已关闭，但没有收到传输成功信号": "Dialog closed without a successful transfer result",
        "⚠️ WiFi 传书未成功：": "⚠️ WiFi transfer failed: ",
        "⚠️ WiFi 传书未成功": "⚠️ WiFi transfer failed",
        "🎉 [5/5] WiFi 传书完成 ✅": "🎉 [5/5] WiFi transfer complete ✅",
        "🎉 [4/4] WiFi 传书完成 ✅": "🎉 [4/4] WiFi transfer complete ✅",
        "⏳ [5/5] 正在上传到 Kindle 云端（Send to Kindle）...": "⏳ [5/5] Uploading to Kindle cloud…",
        "⏳ [4/4] 正在上传到 Kindle 云端（Send to Kindle）...": "⏳ [4/4] Uploading to Kindle cloud…",
        "❌ Send to Kindle 未就绪（请重启应用）": "❌ Send to Kindle is unavailable. Restart the app.",
        "⏳ Send to Kindle 上传中...": "⏳ Uploading to Kindle…",
        "🎉 Send to Kindle 上传完成": "🎉 Kindle upload complete",
        "🎉 [5/5] 已提交到 Kindle 云端，约 1 分钟到手 ✅": "🎉 [5/5] Sent to Kindle cloud; allow about a minute ✅",
        "🎉 [4/4] 已提交到 Kindle 云端，约 1 分钟到手 ✅": "🎉 [4/4] Sent to Kindle cloud; allow about a minute ✅",
        "❌ Send to Kindle 上传失败：": "❌ Kindle upload failed: ",
        "❌ Send to Kindle 上传失败": "❌ Kindle upload failed",
        "❌ Send to Kindle 上传异常：": "❌ Kindle upload error: ",
        "精校源（UU看書）已经找到这本书了。要不要用精校源下载？": "A proofread edition was found on UU. Use this source?",
        "⚠️ 精校源爬取速度非常慢，会经过多次失败重试，而且要挂 VPN；想快就选普通源（笔趣阁）。": "⚠️ The proofread source is very slow, needs multiple retries and requires a VPN. Choose the standard source for speed.",
        "⚠️ 精校源爬取速度非常慢，": "⚠️ The proofread source is very slow,",
        "🔀 精校源（UU看書）有这本书 —— 请选择走精校源还是普通源": "🔀 A proofread edition is available. Choose proofread or standard source.",
        "⚠ 未授权": "⚠ Not authorized",
        "已登录 Amazon，可直接上传到 Kindle 云端": "Signed in to Amazon; ready to upload to Kindle cloud",
        "请先点「Kindle 授权」登录 Amazon 一次": "Select Authorize Kindle to sign in to Amazon first",
        "⚠ 状态未知": "⚠ Status unknown",
        "⚠️ 还没授权：请点上方「☁️ 授权」登录 Amazon 一次": "⚠️ Not authorized. Select Authorize to sign in to Amazon.",
        "❌ 读取授权状态失败：": "❌ Failed to read authorization status: ",
        "🚀 启动 Send to Kindle 上传...": "🚀 Starting Kindle upload…",
        "已发送到 Kindle 云端": "Sent to Kindle cloud",
        "✅ 已发送到 Kindle 云端": "✅ Sent to Kindle cloud",
        "❌ 发送失败：": "❌ Send failed: ",
        "⚠️ 请先填写书名": "⚠️ Enter a book title first",
        "工作区": "Workspace",
        "系统": "System",
        "制作过程在本机完成，不上传源文件。": "Books are made locally. Source files stay on your computer.",
        "语言": "Language",
        "制作": "Make",
        "一键": "One-click",
        "字 · 超过长度按正文处理": "characters · Longer headings are treated as body text",
        "点击更换封面": "Change cover",
        "待命名的故事": "Untitled story",
        "作者未填写": "Author not set",
        "准备出发的书": "Books ready to send",
        "下载小说": "Find a book",
        "输入书名，按匹配结果查找可用来源。": "Enter a title to find available sources.",
        "网文优先": "Web novels first",
        "每个故事，都值得被找到。": "Every story deserves to be found.",
        "输入书名，开始寻找属于你的下一本书。": "Enter a title to find your next read.",
        "开始检索": "Find book",
        "可以只写书名，也可以用": "Enter a title, or use",
        "书名 @ 作者": "title @ author",
        "精确查找。": "for a more precise search.",
        "例如": "For example",
        "吞没 @ 泡泡藻": "Swallowed @ Paopaozao",
        "轻小说支持在选卷窗口中挑选卷册；下载期间请避免操作浏览器。": "Choose light novel volumes in the selection window. Avoid using the browser while downloading.",
        "藏书 · 月下": "Books under moonlight",
        "让故事": "Let stories",
        "在此成书": "become books",
        "下载 → 分章 → 找封面 → 转换 → 选择上传方式": "Download → Chapters → Cover → Convert → Transfer",
        "中途可暂停": "Pause along the way",
        "从一个名字，到一本好书。": "From a title to your next book.",
        "把繁琐交给流程，把阅读留给自己。": "Let the workflow handle the details. Enjoy the reading.",
        "开始制作": "Create book",
        "寻找书籍": "Find book",
        "识别章节": "Detect chapters",
        "匹配封面": "Find cover",
        "生成 EPUB": "Create EPUB",
        "选择传输": "Choose transfer",
        "月点击": "Monthly views",
        "周点击": "Weekly views",
        "月推荐": "Monthly picks",
        "收藏": "Favorites",
        "新书": "New books",
        "正在准备推荐…": "Loading recommendations…",
        "哔哩轻小说下载": "Light novel download",
        "抓公开章节 + 插图自组 EPUB ·": "Build an EPUB from public chapters and illustrations ·",
        "零积分 · 免登录 · 带插图": "No credits · No sign-in · Illustrated",
        "查询": "Search",
        "分卷": "Volumes",
        "全选 / 全不选": "Select / deselect all",
        "先查询，再勾选要下的卷": "Search first, then select volumes to download",
        "取消下载": "Cancel download",
        "下载选中": "Download selected",
        "👉 请在浏览器里点一下第一章": "👉 Open the first chapter in the browser",
        "这本书要登录/权限，脚本自己进不去 —— 请在已打开的浏览器里点一下第一章的链接。": "This book requires sign-in or access. Open the first chapter in the browser that is already running.",
        "我知道了（后台继续等）": "Got it (keep waiting)",
        "🍪 取 Cookie（用你自己的浏览器）": "🍪 Get cookies in your browser",
        "点「开始」会打开一个": "Select Start to open an",
        "应用内的浏览器窗口": "in-app browser window",
        "（不是外部浏览器、也不是脚本浏览器）。": "(separate from your external or automated browser).",
        "在弹出的窗口里会打开": "The window will open",
        "在里面点到": "Navigate to",
        "正文页": "the chapter text",
        "（确认看得见正文；要登录就登，弹验证码就点）": "Make sure chapter text is visible. Sign in or complete verification if asked.",
        "什么都不用点也不用关窗口": "Leave the window open",
        "🧨 浏览器关不干净？点这里强制结束它": "🧨 Browser stuck? Force close it",
        "关掉这个窗口（后台继续等）": "Close dialog (keep waiting)",
        "等待操作…": "Ready when you are…",
        "粘贴授权码…": "Paste authorization code…",
        "例如：192.168.1.100:8080": "e.g. 192.168.1.100:8080",
        "授权功能不可用": "Authorization is unavailable",
        "浏览器启动失败": "Browser failed to start",
        "浏览器启动或运行失败，请检查 Chrome 与 ChromeDriver 版本。": "Browser startup failed. Check that Chrome and ChromeDriver versions match.",
        "请粘贴授权码并提交": "Paste the authorization code and submit",
        "WiFi 功能不可用": "WiFi transfer is unavailable",
        "刷新间隔须为 1 到 30 秒": "Refresh interval must be between 1 and 30 seconds",
        "打开弹窗失败": "Failed to open dialog",
        "启动失败": "Failed to start",
        "等待授权…": "Waiting for authorization…",
        "正在打开浏览器授权页…": "Opening the authorization browser…",
        "正在提交授权码…": "Submitting authorization code…",
        "提交中…": "Submitting…",
        "❌ 请先粘贴授权码": "❌ Paste the authorization code first",
        "❌ 请先填手机上打开的地址": "❌ Enter the address shown on your phone",
        "开始传输…": "Starting transfer…",
        "授权成功": "Authorized",
        "✅ 授权成功": "✅ Authorized",
        "✅ 授权完成": "✅ Authorization complete",
        "传输失败": "Transfer failed",
        "✅ 传输完成": "✅ Transfer complete",
        "✓ 已完成": "✓ Complete",
        "增加": "Increase",
        "减少": "Decrease",
        "章节起始行": "Chapter start line",
        "✓ 已授权": "✓ Authorized",
        "帮助": "Help",
        "转 换": "Convert",
        "选好目的地，让好故事随身而行。": "Choose a destination and take your stories with you.",
        "百度网盘": "Baidu Netdisk",
        "《Re:从零开始的异世界生活》第一卷 第一章": "Re:ZERO, volume 1, chapter 1",
        "的": " — ",
        "—— 脚本每 2 秒看一次， 认出正文页就自动取 cookie 并关窗": "— The app checks every two seconds, captures cookies when chapter text is visible, and closes the window.",
        "（这条路绕开了 Selenium 的机器人特征，也不用去解 浏览器加密库 —— cookie 由 Chromium 自己解密。）": "(This avoids Selenium automation fingerprints. Chromium handles cookie decryption.)",
        "最小化": "Minimize",
        "最大化": "Maximize",
        "图书工作室插画": "Book studio illustration",
        "自动制作流程": "Book creation workflow",
        "书号 / 网址 / 书名，如 3095 或 败北女角太多了": "Book ID, URL or title, e.g. 3095",
        "终止正在跑的抓取进程": "Stop the active download",
        "Amazon 账号密码": "Amazon password",
        "WiFi 传输正在进行，请勿重复启动": "WiFi transfer is already running. Wait for it to finish."
    };

    var cur = 'zh';
    // 动态翻译独立维护原文与节点缓存，避免与原有词典查询、语言开关相互耦合。
    var domTranslator = createDomTranslator(EN, function () { return cur; });

    function createDomTranslator(englishDictionary, getLanguage) {
        const TRANSLATABLE_ATTRIBUTES = ['placeholder', 'title', 'aria-label'];
        const SKIP_CONTENT = [
            'script', 'style', 'textarea', 'code', 'pre', '[data-i18n-skip]',
            '.stk-log', '.lib-meta .n', '.lib-author', '.rec-name', '.rec-author',
            '#defaultCoverTitle', '#defaultCoverAuthor', '#fileNameDisplay'
        ].join(',');
        const STATUS_SEPARATOR = / · | → | \/ /;
        const CAPTURED_SEPARATOR = /( · | → | \/ )/;
        const WHOLE_SEPARATOR = /^( · | → | \/ )$/;
        const textSources = new WeakMap();
        const attributeSources = new WeakMap();
        const originalCopy = Object.create(null);
        const messagePrefixes = Object.keys(englishDictionary)
            .filter(key => key.length > 3 && /[:：]$/.test(key))
            .sort((left, right) => right.length - left.length);

        // 原文索引用于还原通过 T() 写入的英文，不处理用户输入和书籍元数据。
        Object.keys(englishDictionary).forEach(key => {
            if (/[\u4e00-\u9fff]/.test(key)) originalCopy[englishDictionary[key]] = key;
        });

        function restoreSource(raw) {
            const core = raw.trim();
            return originalCopy[core] != null ? raw.replace(core, originalCopy[core]) : raw;
        }

        function resolveSource(raw, cached) {
            // 内容仍为上次的译文时，复用原文；业务写入新内容时重新记录。
            return cached && raw === cached.output ? cached.source : restoreSource(raw);
        }

        function translateCopy(source) {
            if (getLanguage() !== 'en') return source;
            const core = source.trim();
            if (englishDictionary[core] != null) return source.replace(core, englishDictionary[core]);

            const normalized = core.replace(/\s+/g, ' ');
            if (englishDictionary[normalized] != null) {
                return source.replace(core, englishDictionary[normalized]);
            }
            const chapters = core.match(/^(\d+)\s*章$/);
            if (chapters) return source.replace(core, chapters[1] + ' chapters');

            // 先匹配最长前缀，保留错误详情、路径等动态内容。
            for (const prefix of messagePrefixes) {
                if (core.indexOf(prefix) === 0) return source.replace(prefix, englishDictionary[prefix]);
            }
            // 传输状态由多个文案拼接，分隔符和排列保持不变。
            if (STATUS_SEPARATOR.test(source)) {
                return source.split(CAPTURED_SEPARATOR).map(part => {
                    return WHOLE_SEPARATOR.test(part) ? part : translateCopy(part);
                }).join('');
            }
            return source;
        }

        function translateText(node) {
            if (!node.parentElement || node.parentElement.closest(SKIP_CONTENT)) return;
            const raw = node.nodeValue;
            const source = resolveSource(raw, textSources.get(node));
            const output = translateCopy(source);
            textSources.set(node, { source, output });
            if (raw !== output) node.nodeValue = output;
        }

        function translateAttributes(element) {
            if (element.closest(SKIP_CONTENT)) return;
            const cachedAttributes = attributeSources.get(element) || {};
            TRANSLATABLE_ATTRIBUTES.forEach(name => {
                if (!element.hasAttribute(name)) return;
                const raw = element.getAttribute(name);
                const source = resolveSource(raw, cachedAttributes[name]);
                const output = translateCopy(source);
                cachedAttributes[name] = { source, output };
                if (raw !== output) element.setAttribute(name, output);
            });
            attributeSources.set(element, cachedAttributes);
        }

        function translateTree(root) {
            if (root.nodeType === Node.TEXT_NODE) {
                translateText(root);
                return;
            }
            if (root.nodeType !== Node.ELEMENT_NODE && root.nodeType !== Node.DOCUMENT_NODE) return;
            if (root.nodeType === Node.ELEMENT_NODE) translateAttributes(root);
            const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
            while (walker.nextNode()) translateText(walker.currentNode);
            root.querySelectorAll('[placeholder],[title],[aria-label]').forEach(translateAttributes);
        }

        function observe(root) {
            // 只遍历实际变化的子树；相同译文不重复写入，避免自身触发的变更形成循环。
            const observer = new MutationObserver(records => {
                const changedRoots = new Set();
                records.forEach(record => {
                    if (record.type === 'childList') {
                        record.addedNodes.forEach(node => changedRoots.add(node));
                    } else {
                        changedRoots.add(record.target);
                    }
                });
                changedRoots.forEach(node => {
                    if (node.isConnected) translateTree(node);
                });
            });
            observer.observe(root, {
                subtree: true,
                childList: true,
                characterData: true,
                attributes: true,
                attributeFilter: TRANSLATABLE_ATTRIBUTES
            });
            return observer;
        }

        return { translateTree, observe };
    }

    function dict() {
        return cur === 'en' ? EN : ZH;
    }

    // 中文模式下，中文原文 key 直接返回自身
    function t(key, fallback) {
        var d = dict();
        if (d[key] != null) return d[key];
        if (cur === 'zh') return key;
        return (fallback != null) ? fallback : key;
    }

    function apply(root) {
        var scope = root || document;
        var d = dict();

        Array.prototype.forEach.call(scope.querySelectorAll('[data-i18n]'), function (el) {
            var k = el.dataset.i18n;
            // 只替换初始提示；进行中的动态状态交给原文缓存翻译，避免被重置。
            var source = ZH[k] || k;
            var english = EN[k];
            if (el.textContent.trim() !== source.trim() &&
                el.textContent.trim() !== String(english || '').trim()) return;
            if (d[k] != null) el.textContent = d[k];
            else if (cur === 'zh') el.textContent = k;
        });
        Array.prototype.forEach.call(scope.querySelectorAll('[data-i18n-title]'), function (el) {
            var k = el.dataset.i18nTitle;
            if (d[k] != null) el.title = d[k];
            else if (cur === 'zh') el.title = k;
        });
        Array.prototype.forEach.call(scope.querySelectorAll('[data-i18n-ph]'), function (el) {
            var k = el.dataset.i18nPh;
            if (d[k] != null) el.placeholder = d[k];
            else if (cur === 'zh') el.placeholder = k;
        });
        domTranslator.translateTree(scope);

        document.documentElement.setAttribute('lang', cur === 'en' ? 'en' : 'zh-CN');
        document.documentElement.setAttribute('data-lang', cur);

        var sw = document.getElementById('langSwitch');
        if (sw) {
            Array.prototype.forEach.call(sw.querySelectorAll('div'), function (el) {
                el.classList.toggle('on', el.dataset.lang === cur);
            });
        }
    }

    function setLang(lang) {
        cur = (lang === 'en') ? 'en' : 'zh';
        apply();
        document.dispatchEvent(new CustomEvent('languagechange', { detail: { language: cur } }));
        console.log('🌐 语言已切换:', cur, '（覆盖', document.querySelectorAll('[data-i18n]').length, '处）');
    }

    window.__i18n = { t: t, setLang: setLang, apply: apply, get: function () { return cur; } };

    function init() {
        apply();
        // 页面初次翻译后，后续只处理节点与文案变化，不进行定时轮询。
        domTranslator.observe(document.body);
    }
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
