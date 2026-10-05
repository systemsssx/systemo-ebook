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
        /* ---------- 外壳 ---------- */
        'nav.download': 'Download',
        'nav.make': 'Make',
        'nav.send': 'Send',
        'nav.library': 'Library',
        'nav.auto': 'One-Click',
        'nav.help': 'Help',
        'nav.api': 'API',
        'sub.info': 'Book Info',
        'sub.name': 'Title & Author',
        'sub.chapters': 'Chapters',
        'sub.cover': 'Cover',
        'sub.applebooks': 'Apple Books',
        'sub.wifi': 'WiFi Transfer',
        'sub.kindle': 'Kindle',

        /* ---------- ★ 2026-09-29：API 授权词条已整体删除（DeepSeek 全套下线） ----------
           搜作者 / 搜封面改由 novelmeta（meta_lookup.py）直连公开接口，不再需要任何 key。
           ★ 不许恢复这些 key：nav.api / API 授权 / 去授权 / deepseek 官方 API Key / 保存并授权 … */
        '提示': 'Notice',
        '知道了': 'Got it',

        /* ---------- ★ 2026-09-29 新增：多作者选择弹窗（novelmeta ambiguous=true 时） ---------- */
        '这本书有多个同名结果': 'Several books share this title',
        '请选择你要的那一本（作者不同）：': 'Pick the one you want (authors differ):',
        '已导入 TXT，点此一键': 'TXT imported — click to run the rest',
        '已导入 TXT 就点下面的按钮：分章 → 封面 → 转换 → 上传': 'Already imported a TXT? Use the button below: split → cover → convert → upload',
        '⚠️ 请先在「书籍信息」页导入 TXT 文件': '⚠️ Import a TXT file on the Book Info page first',

        /* ---------- ★ 完整版：「已导入 TXT，点此一键」（4 步流程）进度与结果文案 ---------- */
        '[1/4] 分章完成后等待': '[1/4] Waiting after split',
        '[1/4] 等待分章页就绪': '[1/4] Waiting for the chapters page',
        '[2/4] 封面完成，跳转': '[2/4] Cover done',
        '[2/4] 等待封面页就绪': '[2/4] Waiting for the cover page',
        '[3/4] 等待书籍信息页就绪': '[3/4] Waiting for the book info page',
        '⏳ [1/4] 正在分章（可能需要 30s+）...': '⏳ [1/4] Splitting chapters (may take 30s+)...',
        '⏳ [2/4] 正在搜索封面...': '⏳ [2/4] Searching for a cover...',
        '⏳ [3/4] 正在转换为 EPUB（可能需要 30s+）...': '⏳ [3/4] Converting to EPUB (may take 30s+)...',
        '⏳ [4/4] 切到苹果图书页上传...': '⏳ [4/4] Switching to Apple Books...',
        '⏳ [4/4] 正在打开 WiFi 传书窗口...': '⏳ [4/4] Opening the WiFi window...',
        '⚠️ [1/4] 分章超时或无结果，尝试继续': '⚠️ [1/4] Split timed out — continuing',
        '⚠️ [2/4] 封面搜索超时，立即继续（无封面模式）': '⚠️ [2/4] Cover search timed out — continuing without one',
        '⚠️ [2/4] 未找到封面，立即继续（无封面模式）': '⚠️ [2/4] No cover found — continuing without one',
        '⚠️ [3/4] 转换超时或未知错误': '⚠️ [3/4] Conversion timed out or errored',
        '✅ [1/4] 分章完成，共 ': '✅ [1/4] Split into ',
        '✅ [2/4] 封面已找到': '✅ [2/4] Cover found',
        '✅ [3/4] EPUB 已生成': '✅ [3/4] EPUB generated',
        '❌ [1/4] 找不到 #btnPreviewChapters': '❌ [1/4] #btnPreviewChapters not found',
        '❌ [2/4] 找不到 #btnSearchCover': '❌ [2/4] #btnSearchCover not found',
        '❌ [3/4] 找不到 #convertBtn': '❌ [3/4] #convertBtn not found',
        '❌ [3/4] 转换失败（请看「书籍信息」页按钮状态）': '❌ [3/4] Conversion failed — check the button on Book Info',
        '🎉 [4/4] Kindle 推送成功！全部完成 ✅': '🎉 [4/4] Sent to Kindle — all done ✅',
        '⏳ [4/4] 正在推送到 Kindle 邮箱...': '⏳ [4/4] Sending to your Kindle address...',

        /* ---------- 通用动作 ---------- */
        '上传': 'Upload',
        '传输': 'Send',
        '下载过程中请勿操作电脑防止误触': "Don't touch the computer during download",
    '轻小说可在书名后加卷号（不写＝第 1 卷）：败北女角太多了 第九卷（输书名会自动弹选卷窗）': 'Light novels: append the volume, e.g. 败北女角太多了 第九卷 (default: vol 1)',
        '保存': 'Save',
        '保存修改': 'Save changes',
        '修改后自动保存': 'Saved automatically',
        '删除': 'Delete',
        // ★ 修复：章节编辑器用的这 4 个 key 之前没定义，英文模式下会原样显示中文
        '禁用': 'Disable',
        '启用': 'Enable',
        '恢复': 'Restore',
        '永久删除': 'Delete permanently',
        '刷新': 'Refresh',
        '导入书籍': 'Import books',
        '取消': 'Cancel',
        '添加': 'Add',
        '移除': 'Remove',
        '重置': 'Reset',
        '全选': 'Select all',
        '打开目录': 'Open folder',
        '在文件夹中显示': 'Show in folder',
        '多选': 'Select',
        // ★ 修复：书库「多选」按钮切换后由 library.js 改成这个文案，
        //   但字典里没有这个 key，英文模式下会露出中文。
        '退出多选': 'Exit select',
        // ★ 修复：以下 key 都被 library.js 用到但字典里缺失，
        //   英文模式下会原样显示中文（library.js 是独立文件的 IIFE，
        //   原来既没走 i18n、字典里也没对应条目）。
        '扫描中…': 'Scanning…',
        // 书库卡片悬停提示（library.css 里是 content:"单击阅读 · 右键详情"，
        // CSS 的 content 没法走 data-i18n —— 这里留着条目，方便以后改成
        // 由 JS 设置提示文案时直接取用）
        '单击阅读 · 右键详情': 'Click to read · Right-click for details',
        '双击阅读': 'Double-click to read',
        '没有匹配的书': 'No matching books',
        '换个关键词，或切回「全部」': 'Try another keyword, or switch back to All',
        '取消全选': 'Deselect all',
        '无封面': 'No cover',
        'TXT 没有元数据，只能改文件名': 'TXT has no metadata — only the file name can change',
        '保存中…': 'Saving…',
        '保存失败': 'Save failed',
        '确定删除 ': 'Delete ',
        ' 个文件？': ' file(s)?',
        '这些文件会从 export 目录里真正删掉，不进回收站。':
            'These files are permanently removed from the export folder (not sent to the Recycle Bin).',
        '📚 已删除': '📚 Deleted',
        '删除失败：': 'Delete failed: ',
        '批量传输需要在应用内使用': 'Batch send only works inside the app',
        '导出需要在应用内使用': 'Export only works inside the app',
        '📤 已导出': '📤 Exported',
        '已导出 ': 'Exported ',
        ' 本到': ' book(s) to',
        '失败：': 'Failed: ',
        '导出失败：': 'Export failed: ',
        '换封面会走「制作 → 封面设置」的搜索 + 超分流程，后续接上。':
            'Changing the cover will reuse the Make → Cover search + upscale flow (to be wired up).',
        '⚠️ library API 未连接': '⚠️ library API not connected',
        '⚠️ 书库扫描失败:': '⚠️ Library scan failed: ',
        '⚠️ 书库扫描异常:': '⚠️ Library scan error: ',
        '批量传输': 'Batch send',
        '编辑信息': 'Edit info',
        '更换封面': 'Change cover',
        '清除封面': 'Remove cover',
        '联网搜索封面': 'Search online',
        '点击上传封面': 'Click to upload',
        '恢复默认': 'Reset to default',
        '更改': 'Change',
        '授权': 'Authorize',
        '转换': 'Convert',
        /* 下面两个是窄竖按钮，英文太长会折行、跟旁边按钮高度不齐，故用短词 */
        '预览章节': 'Preview',
        '章节编辑': 'Edit',
        '📋 章节编辑': '📋 Edit chapters',
        '📖 操作说明': '📖 How to use',
        '📖 操作指引': '📖 Guide',
        '📧 邮箱': '📧 Email',
        '⚠ 未配置邮箱': '⚠ Email not set',
        '选择上传方式': 'Choose upload method',

        /* ---------- 侧栏 / 板块 ---------- */
        '书名作者': 'Title & Author',
        '章节划分': 'Chapters',

        /* ---------- 属性里的文字（placeholder / title）---------- */
        '下载': 'Download',
        '一键完成': 'One-click',
        '书名 或 书名@作者（用@分隔）': 'Title, or Title@Author (use @)',
        '搜索书名或作者…': 'Search title or author…',
        '输入书名': 'Enter title',
        '输入作者': 'Enter author',
        '封面设置': 'Cover',
        'Kindle 传书': 'Kindle',
        'WiFi 传书': 'WiFi Transfer',
        '中文': '中文',
        'systemoebook · 电子书制作': 'systemoebook · EPUB Maker',

        /* ---------- 制作 · 书籍信息 ---------- */
        '拖拽 TXT 文件至此': 'Drop a TXT file here',
        '或点击选择 · 支持 .txt': 'or click to browse · .txt only',
        '未命名': 'Untitled',
        '未命名.txt': 'Untitled.txt',

        /* ---------- 制作 · 章节划分 ---------- */
        '最大标题长度': 'Max title length',
        '超过此字数的行不会被识别为章节标题': 'Longer lines are not treated as chapter titles',
        '请先导入 TXT 文件，然后点击「预览章节」': 'Import a TXT file, then click Preview chapters',
        '章节列表': 'Chapter list',
        '共': 'Total',
        '章': 'chapters',
        '0 章': '0 chapters',
        '📊 共': '📊 Total',
        '置信度:': 'Confidence:',
        '个问题': 'issues',
        '已删除': 'Deleted',
        '已选': 'Selected',
        '项': 'items',
        '添加章节：': 'Add chapter:',
        '章节名称': 'Chapter title',
        '行号': 'Line no.',
        '序号': 'No.',
        '操作': 'Action',
        '💡 手动添加章节：输入章节名称和所在行号 · 点击「删除」标记删除 · 点击「恢复」恢复 · 点击「永久删除」彻底移除':
            '💡 Add chapters manually: enter a title and line number · Delete marks it · Restore undoes · Purge removes it for good',

        /* ---------- 制作 · 封面设置 ---------- */
        '封面图片': 'Cover image',
        '从书名和作者自动生成文字封面': 'Generate a text cover from title and author',
        '支持 JPG / PNG / WebP · 点击上传 · 联网搜索根据书名和作者匹配':
            'JPG / PNG / WebP · click to upload · online search matches title and author',
        '封面超分倍数': 'Upscale',
        '不超分': 'Off',
        '2 倍': '2×',
        '3 倍': '3×',
        '4 倍（推荐）': '4× (recommended)',
        '小图放大更清晰，本地模型约 0.6 秒': 'Sharper on small images · ~0.6s locally',

        /* ---------- 制作 · 输出 ---------- */
        '输出到': 'Output to',
        '与源文件同目录': 'Same folder as source',

        /* ---------- 下载 ---------- */
        '书名': 'Title',
        '作者': 'Author',
        '书名@作者': 'Title@Author',
        '查找作者需要一定时间，请耐心等待': 'Looking up the author — this takes a moment',
        '示例：吞没@泡泡藻 · 或只写 · 吞没': 'e.g. 吞没@泡泡藻 · or just · 吞没',
        '输入格式：': 'Format: ',
        '（用 @ 分隔，可只写书名）': '(separate with @; title alone is fine)',
        '小说': 'Fiction',
        '学术': 'Academic',
        '诗歌': 'Poetry',

        /* ---------- 传输 ---------- */
        '拖拽图书文件至此': 'Drop book files here',
        '或点击选择 · 支持 .epub / .txt / .pdf': 'or click to browse · .epub / .txt / .pdf',
        '或点击选择 · 支持 .epub / .azw3 / .mobi / .pdf': 'or click to browse · .epub / .azw3 / .mobi / .pdf',
        '苹果图书（上传到百度网盘）': 'Apple Books (upload to Baidu Netdisk)',
        'WiFi 传书（弹窗）': 'WiFi Transfer (dialog)',
        '网盘目录': 'Netdisk folder',
        '重名时': 'On duplicate',
        '覆盖': 'Overwrite',
        '跳过': 'Skip',
        '询问': 'Ask',
        '前 4 步已自动完成（下载 → 分章 → 找封面 → 转换），请选择最后上传方式：':
            'The first 4 steps are done (download → split → cover → convert). Choose how to send:',
        '点击猫爪一键完成：下载 → 分章 → 封面 → 转换 → 上传':
            'One click runs: download → split → cover → convert → send',
        '中间任何步骤出错会暂停并提示，请保持窗口打开':
            'Any error pauses the run and reports — keep this window open',

        /* ---------- 书库 ---------- */
        '书库是空的': 'Your library is empty',
        '在「制作」里转换成功的 EPUB 会自动出现在这里':
            'EPUBs converted under Make show up here automatically',
        '全部': 'All',
        '书籍详情': 'Book details',
        '保存修改': 'Save changes',
        '书 名': 'TITLE',
        '作 者': 'AUTHOR',
        '简 介': 'DESCRIPTION',
        '格 式': 'FORMAT',
        '章节数': 'Chapters',
        '文件大小': 'Size',
        '修改时间': 'Modified',
        '改动会写回原 EPUB，不会重新转换': 'Changes are written back to the EPUB — no re-conversion',

        /* ---------- 底栏 ---------- */
        '就绪': 'Ready',
        '访问主页': 'Visit homepage',
        '预设：': 'Preset: ',
        '默认': 'Default',
        '（唯一输出格式）': '(only output format)',

        '导出': 'Export',

        /* ---------- 书库批量传输 ---------- */
        '批量传输': 'Batch send',
        '本，选择传输方式：': 'books — pick a transfer method:',
        '传到同一局域网内的阅读设备': 'To a reading device on the same Wi-Fi',
        '作为附件发送到 Kindle 邮箱': 'As an attachment to your Kindle address',
        '复制到下载目录后上传百度网盘': 'Copy to the download folder, then upload to Baidu Netdisk',
        '苹果图书': 'Apple Books',
        '来自书库：本次将': 'From library: sending',
        '本': 'book(s)',

        'Kindle 操作指引': 'Kindle guide',
        '打开传书窗口': 'Open transfer window',
        '苹果图书操作指引': 'Apple Books guide',
        '邮箱配置': 'Email settings',

        '请先填写书名': 'Enter a title first',

        /* ---------- 设计稿重建后新增的文案 ---------- */
        '+ 添加文件': '+ Add files',
        'AI 搜索': 'AI Lookup',
        'Kindle 推送': 'Kindle',
        'TXT 转 EPUB · 全流程本地完成，不上传任何文件': 'TXT → EPUB · fully local, nothing uploaded',
        'WiFi 直传': 'WiFi Direct',
        '✏️ 章节编辑': '✏️ Edit chapters',
        '✏️ 编辑章节': '✏️ Edit chapters',
        '三合一：苹果图书（百度网盘）· WiFi 直传 · Kindle 邮箱推送': 'Three ways: Apple Books (Baidu Netdisk) · WiFi Direct · Kindle email',
        '书籍信息': 'Book Info',
        '从书库拖书进来，或点这里从本地添加': 'Drag books in from the library, or add them locally',
        '传输到设备': 'Transfer',
        '传输在后台进行，可以随时切到别的板块。': 'Transfers run in the background — switch sections freely.',
        '关闭': 'Close',
        '列表只用于核对；改动请在编辑器里做': 'Read-only list; make changes in the editor',
        '制作新书': 'New Book',
        '原始尺寸': 'Original',
        '去「书库」勾选后点「批量传输」，或点右下角从本地添加': 'Pick books in Library → Batch send, or add files locally',
        '发到 Kindle 邮箱': 'To your Kindle address',
        '发送': 'Send',
        '发送到 Kindle 邮箱': 'Send to your Kindle address',
        '弹出新窗口': 'Opens a new window',
        '上传到百度网盘': 'Upload to Baidu Netdisk',
        '本': 'book(s)',
        '填错也没关系，可以随时在书库里改': "Don't worry if it's wrong — editable in the library",
        '导入 TXT 后点「预览切分」，这里会显示拆出了多少章': 'Import a TXT and hit Preview split to see the chapter count',
        '封面预览': 'Cover Preview',
        '将显示在 EPUB 的书名页与书库中': 'Shown on the EPUB title page and in the library',
        '已授权': 'Authorized',
        '已超分': 'Upscaled',
        '开始': 'Start',
        '当前通道': 'Current Channel',
        '待发送队列': 'Send Queue',
        '待启动': 'Idle',
        '手机浏览器上传': 'Upload from phone browser',
        '方式': 'Method',
        '更换': 'Change',
        '本地副本': 'Local copy',
        '查看全部章节': 'View all chapters',
        '标题最长': 'Max title length',
        '清除': 'Clear',
        '源文件': 'Source File',
        '章节总数': 'chapters',
        '章节预览': 'Chapter Preview',
        '经百度网盘中转': 'Relayed via Baidu Netdisk',
        '耗时': 'Time',
        '说明': 'Notes',
        '账户': 'Account',
        '超分后': 'Upscaled',
        '超过这个长度的一律当作正文，不拆成章节标题': 'Longer lines count as body text, not chapter titles',
        '输出至': 'Output to',
        '还没有添加文件': 'No files yet',
        '远端目录': 'Remote folder',
        '重名策略': 'On duplicate',
        '重新拆分': 'Re-split',
        '重新授权': 'Re-authorize',
        '重新搜索封面': 'Search cover again',
        '队列是空的': 'The queue is empty',
        '预览切分': 'Preview split',
        '与源文件同目录': 'Same folder as source',
        '未命名.txt': 'Untitled.txt',
        '覆盖': 'Overwrite',
        '跳过': 'Skip',
        '询问': 'Ask',
        '尚未配置': 'Not set',

        /* ---------- 插值模板里的片段 ---------- */
        '(行': '(line',
        '... 还有': '... and',
        '✅ 分章成功！共': '✅ Split succeeded —',
        '✅ 已保存': '✅ Saved',
        '✅ 已生成：': '✅ Generated:',
        '个章节': 'chapters',
        '字': 'chars',
        '章': 'chapters',
        '行': 'line',
        '第{n}章': 'Chapter {n}',

        /* ---------- HTML 片段（空状态 / 按钮内容）---------- */
        '章': 'chapters',
        '<div style="color:#8e8e93; font-size: 12px; padding:8px 0;">请点击「预览章节」</div>': '<div style="color:#8e8e93; font-size: 12px; padding:8px 0;">Click Preview chapters</div>',
        '<div style="color:#8e8e93; font-size: 12px; padding:8px 0;">请先导入 TXT 文件</div>': '<div style="color:#8e8e93; font-size: 12px; padding:8px 0;">Import a TXT file first</div>',
        '<div style="color:#8e8e93; font-size: 12px; padding:8px 0;">未检测到章节</div>': '<div style="color:#8e8e93; font-size: 12px; padding:8px 0;">No chapters detected</div>',
        '<svg viewBox="0 0 24 24"><polyline points="1 6 1 22 8 18 16 22 23 18 23 2 16 6 8 2 1 6"/><polyline points="8 2 8 18"/><polyline points="16 6 16 22"/></svg><span class="label">预览章节</span>': '<svg viewBox="0 0 24 24"><polyline points="1 6 1 22 8 18 16 22 23 18 23 2 16 6 8 2 1 6"/><polyline points="8 2 8 18"/><polyline points="16 6 16 22"/></svg><span class="label">Preview</span>',

        /* ---------- JS 动态文字（状态提示 / 按钮 / 弹窗）---------- */
        ' · 正在跳转…': ' · Redirecting…',
        ' · 等待页面稳定（': ' · Stabilizing (',
        ' 章': ' chapters',
        '  章': ' chapters',
        '秒）...': 's)...',
        '[2/5] 分章完成后等待': '[2/5] Waiting after split',
        '[2/5] 等待分章页就绪': '[2/5] Waiting for the chapters page',
        '[3/5] 封面完成，跳转': '[3/5] Cover done',
        '[3/5] 等待封面页就绪': '[3/5] Waiting for the cover page',
        '[4/5] 等待书籍信息页就绪': '[4/5] Waiting for the book info page',
        '⏳ Kindle 推送中...': '⏳ Sending to Kindle...',
        '⏳ [1/5] 正在下载小说...': '⏳ [1/5] Downloading novel...',
        '⏳ [2/5] 正在分章（可能需要 30s+）...': '⏳ [2/5] Splitting chapters (may take 30s+)...',
        '⏳ [3/5] 正在搜索封面...': '⏳ [3/5] Searching for a cover...',
        '⏳ [4/5] 正在转换为 EPUB（可能需要 30s+）...': '⏳ [4/5] Converting to EPUB (may take 30s+)...',
        '⏳ [5/5] 切到苹果图书页上传...': '⏳ [5/5] Switching to Apple Books...',
        '⏳ [5/5] 正在打开 WiFi 传书窗口...': '⏳ [5/5] Opening the WiFi window...',
        '⏳ 上传到百度网盘中...': '⏳ Uploading to Baidu Netdisk...',
        '⏳ 搜索中…': '⏳ Searching…',
        '⏳ 正在启动下载器，请耐心等待（30秒-2分钟）…': '⏳ Starting the downloader — 30s to 2min…',
        '⏳ 等待作者信息（最多 8 秒）...': '⏳ Waiting for author info (max 8s)...',
        '⚠ 未配置': '⚠ Not set',
        '⚠ 检查失败': '⚠ Check failed',
        '⚠️ [2/5] 分章超时或无结果，尝试继续': '⚠️ [2/5] Split timed out — continuing',
        '⚠️ [3/5] 封面搜索超时，立即继续（无封面模式）': '⚠️ [3/5] Cover search timed out — continuing without one',
        '⚠️ [3/5] 未找到封面，立即继续（无封面模式）': '⚠️ [3/5] No cover found — continuing without one',
        '⚠️ [4/5] 转换超时或未知错误': '⚠️ [4/5] Conversion timed out or errored',
        '⚠️ 下载完成但未在 download 目录找到文件': '⚠️ Download finished but nothing landed in the download folder',
        '⚠️ 只能用一个 @ 分隔，不要重复': '⚠️ Use only one @ separator',
        '⚠️ 已取消': '⚠️ Cancelled',
        '⚠️ 拖拽文件未能读取路径，请用点击方式选择': "⚠️ Couldn't read the dropped path — click to browse instead",
        '⚠️ 拖拽无法读取本地路径，请点击文件框选择文件': "⚠️ Drops don't expose local paths — click the box instead",
        '⚠️ 搜索失败，请手动输入': '⚠️ Search failed — enter it manually',
        '⚠️ 未找到作者，请手动输入': '⚠️ Author not found — enter it manually',
        '⚠️ 未连接到 Electron': '⚠️ Not connected to Electron',
        '⚠️ 请先点击「📧 邮箱」配置收件人 / SMTP': '⚠️ Click 📧 Email to set the recipient / SMTP first',
        '⚠️ 请先输入书名': '⚠️ Enter a title first',
        '⚠️ 请先选择要发送的图书文件': '⚠️ Pick a book file to send first',
        '⚠️ 请用 @ 分隔书名和作者，例如：吞没@泡泡藻': '⚠️ Separate title and author with @, e.g. 吞没@泡泡藻',
        '✅ [1/5] 下载完成：': '✅ [1/5] Downloaded: ',
        '✅ [2/5] 分章完成，共 ': '✅ [2/5] Split into ',
        '✅ [3/5] 封面已找到': '✅ [3/5] Cover found',
        '✅ [4/5] EPUB 已生成': '✅ [4/5] EPUB generated',
        '✅ 下载完成：': '✅ Downloaded: ',
        '✅ 已上传封面': '✅ Cover uploaded',
        '✅ 已找到作者': '✅ Author found',
        '✓ 完成': '✓ Done',
        '✗ 失败': '✗ Failed',
        '❌ Electron API 未连接': '❌ Electron API not connected',
        '❌ Electron API 未连接，请用 npm start 启动': '❌ Electron API not connected — start with npm start',
        '❌ Kindle API 未连接': '❌ Kindle API not connected',
        '❌ WiFi 功能不可用': '❌ WiFi unavailable',
        '❌ [2/5] 找不到 #btnPreviewChapters': '❌ [2/5] #btnPreviewChapters not found',
        '❌ [3/5] 找不到 #btnSearchCover': '❌ [3/5] #btnSearchCover not found',
        '❌ [4/5] 找不到 #convertBtn': '❌ [4/5] #convertBtn not found',
        '❌ [4/5] 转换失败（请看「书籍信息」页按钮状态）': '❌ [4/5] Conversion failed — check the button on Book Info',
        '❌ 上传失败：': '❌ Upload failed: ',
        '❌ Kindle 推送失败：': '❌ Kindle send failed: ',
        '❌ Kindle 推送异常：': '❌ Kindle send error: ',
        '❌ 上传失败：未授权。请先授权！': '❌ Upload failed: not authorized — authorize first',
        '❌ 上传异常：': '❌ Upload error: ',
        '❌ 下载失败：': '❌ Download failed: ',
        '❌ 下载异常：': '❌ Download error: ',
        '❌ 分章失败：': '❌ Chapter split failed: ',
        '❌ 启动失败：': '❌ Failed to start: ',
        '❌ 复制失败：': '❌ Copy failed: ',
        '❌ 复制文件失败：': '❌ Copying files failed: ',
        '❌ 异常：': '❌ Error: ',
        '❌ 打开 WiFi 窗口失败：': '❌ Failed to open the WiFi window: ',
        '❌ 打开传输窗口失败：': '❌ Failed to open the transfer window: ',
        '❌ 打开指引窗口失败：': '❌ Failed to open the guide: ',
        '❌ 打开授权窗口失败：': '❌ Failed to open the authorization window: ',
        '❌ 打开操作指引失败：': '❌ Failed to open the guide: ',
        '❌ 打开说明窗口失败：': '❌ Failed to open the help window: ',
        '❌ 打开邮箱配置窗口失败：': '❌ Failed to open email settings: ',
        '❌ 推送失败': '❌ Send failed',
        '❌ 推送失败：': '❌ Send failed: ',
        '❌ 未找到封面，请手动上传': '❌ No cover found — upload one manually',
        '❌ 未找到本书': '❌ Book not found',
        '❌ 未连接到 Electron 环境': '❌ Not running in Electron',
        '❌ 未连接到 Electron 环境，请使用 npm start 启动': '❌ Not running in Electron — start with npm start',
        '❌ 读取邮箱配置失败：': '❌ Failed to read email settings: ',
        '❌ 选择文件失败：': '❌ File selection failed: ',
        '上传中…': 'Uploading…',
        '本次上传': 'Uploading',
        '本（队列）': 'file(s) from the send queue',
        '上传超时(5分钟)': 'Upload timed out (5 min)',
        '传输中...': 'Sending...',
        '作者信息就绪': 'Author info ready',
        '分章失败：': 'Chapter split failed: ',
        '已保存': 'Saved',
        '已启动 Kindle 推送': 'Kindle send started',
        '已清除封面': 'Cover cleared',
        '已配置': 'Configured',
        '收件人：': 'To: ',
        '未知作者': 'Unknown author',
        '未知错误': 'Unknown error',
        '确定要永久删除该章节吗？': 'Permanently delete this chapter?',
        '请先导入一个 TXT 文件！': 'Import a TXT file first!',
        '请先点击「预览章节」获取章节数据！': 'Click Preview first to load chapter data!',
        '请先点击「预览章节」获取章节数据，或手动添加章节标记！': 'Click Preview first, or add chapter marks manually!',
        '请先点击右侧「邮箱」按钮配置': 'Click the Email button on the right first',
        '请拖入 .txt 文件！': 'Drop a .txt file!',
        '请至少保留一个章节！': 'Keep at least one chapter!',
        '请输入章节名称！': 'Enter a chapter title!',
        '转换中…': 'Converting…',
        '转换失败：': 'Conversion failed: ',
        '重置所有章节修改？': 'Reset all chapter edits?',
        '🎉 Kindle 推送完成！': '🎉 Sent to Kindle!',
        '🎉 Kindle 推送完成': '🎉 Sent to Kindle',
        '🎉 [5/5] Kindle 推送成功！全部完成 ✅': '🎉 [5/5] Sent to Kindle — all done ✅',
        '⏳ [5/5] 正在推送到 Kindle 邮箱...': '⏳ [5/5] Sending to your Kindle address...',
        '⚠️ 还没配置 Kindle 邮箱': '⚠️ Kindle email not configured',
        '⚠️ 还没配置 Kindle 邮箱：请到「发送」页点 Kindle 卡片的「邮箱」按钮填写': '⚠️ Kindle email not configured — go to Transfer and click the Email button on the Kindle card',
        '🎉 WiFi 传书完成！全部流程结束 ✅': '🎉 WiFi transfer done — all steps complete ✅',
        '🎉 上传成功': '🎉 Upload succeeded',
        '🎉 上传成功！全部完成 ✅': '🎉 Upload succeeded — all done ✅',
        '🎉 全部完成！一键流程成功': '🎉 All done — one-click run succeeded',
        '💡 支持 JPG / PNG / WebP': '💡 JPG / PNG / WebP supported',
        '📄 已准备文件：': '📄 File ready: ',
        '📄 文件：': '📄 File: ',
        '📧 正在推送文件到 Kindle...': '📧 Sending to Kindle...',
        'WiFi 传书弹窗已打开，请在那里完成上传（关闭后这里会显示完成）': 'WiFi dialog opened — finish the upload there (this updates when you close it)',
        '🔍 正在搜索封面...': '🔍 Searching for a cover...',
        '🔍 正在查找作者，请耐心等待...': '🔍 Looking up the author...',
        '🔍 联网搜索封面': '🔍 Search cover online',
        '🚀 启动 Kindle 推送...': '🚀 Starting Kindle send...',
        /* ---------- 发送页 · 一行状态 ---------- */
        '等待发送…': 'Waiting to send...',
        '检查中…': 'Checking...',
        '⚠️ 百度网盘未授权或授权已过期，点上方「重新授权」': '⚠️ Baidu Netdisk is not authorized or the token expired — click "Re-authorize" above',
        '🔄 授权已自动续期（access_token 过期但 refresh_token 有效，无需重新登录）': '🔄 Authorization renewed automatically (access_token expired but refresh_token is still valid — no need to log in again)',
        '推送中…': 'Sending...',
        '授权中…': 'Authorizing...',
        '传输中…': 'Transferring...',
        '未授权': 'Not authorized',
        /* ---------- 发送页 · 各通道自己的状态（WiFi / Kindle，2026-09-23 新增） ---------- */
        '已过期': 'Expired',
        '检查失败': 'Check failed',
        '传书服务运行中': 'Transfer service running',
        '传书窗口已打开，等待输入网址': 'Transfer window open — enter the URL',
        '还没打开传书窗口': 'Transfer window not open yet',
        '配置不完整': 'Incomplete config',
        '配置中': 'Configure',
        '✅ 完成': '✅ Done',
        '⚠️ 上传超时（5 分钟未完成）：可能是百度网盘未授权或网络异常，请点上方「重新授权」按钮完成授权后重试': '⚠️ Upload timed out after 5 minutes — Baidu Netdisk may not be authorized, or the network is stuck. Click "Re-authorize" above and try again',
        '🔐 百度网盘授权已失效，正在切到授权页…': '🔐 Baidu Netdisk authorization expired — opening the auth page...',
        '✅ 授权成功！': '✅ Authorized!',
        '❌ 授权失败': '❌ Authorization failed',
        '⚠️ 授权未完成（本地仍保留原授权）': '⚠️ Authorization not completed (your previous authorization is kept)',
        '开始发送': 'Start sending',
        '开始上传到百度网盘': 'Start uploading to Baidu Netdisk',
        '上传失败：': 'Upload failed: ',
        '上传异常：': 'Upload error: ',
        '未知错误': 'Unknown error',
        '上传失败': 'Upload failed',
        '上传失败，请检查授权': 'Upload failed — please check the authorization',
        '日志': 'log',
        '打开授权窗口（请点上方「重新授权」完成）': 'Opening the authorization window (click "Re-authorize" above to finish)',
        '打开授权窗口失败：': 'Failed to open the authorization window: ',
        '⚠️ 授权超时（5 分钟未完成）：授权码可能已过期或网络异常，请重新点「重新授权」再试': '⚠️ Authorization timed out after 5 minutes — the code may have expired or the network is stuck. Click "Re-authorize" and try again',
        /* ---------- 发送页 · 一行状态（三通道收尾文案，中英必须都认） ---------- */
        '🎉 WiFi 传书完成': '🎉 WiFi transfer complete',
        '❌ WiFi 传书失败：': '❌ WiFi transfer failed: ',
        '🎉 Kindle 推送完成': '🎉 Kindle send complete',
        '❌ Kindle 推送失败：': '❌ Kindle send failed: ',
        /* ★ 2026-09-23：状态行改版后不再显示 emoji（清洗掉之后按下面这些键查表），
           所以除 emoji 版本外，必须同时有「干净版」的键，否则英文界面会漏出中文。 */
        '完成': 'Done',
        'WiFi 传书完成': 'WiFi transfer complete',
        'WiFi 传书失败：': 'WiFi transfer failed: ',
        'Kindle 推送完成': 'Sent to Kindle',
        'Kindle 推送失败：': 'Kindle send failed: ',
        '推送失败': 'Send failed',
        '授权失败': 'Authorization failed',
        '未配置': 'Not configured',
        /* ★ 2026-09-23：上传成功后的小字结论（要能分清「真的传了」和「云端已是最新，
           一个字节都没传」；带上传本数与用时）。 */
        '上传完成': 'Upload complete',
        '云端已是最新，无需上传': 'Already up to date in the cloud — nothing to upload',
        '✓ 已是最新': '✓ Up to date',
        '用时': 'took',
        '秒': 's',
    };

    var cur = 'zh';

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
            if (d[k] != null) el.textContent = d[k];
            else if (cur === 'zh') el.textContent = k;
        });
        Array.prototype.forEach.call(scope.querySelectorAll('[data-i18n-title]'), function (el) {
            var k = el.dataset.i18nTitle;
            if (d[k] != null) el.title = d[k];
        });
        Array.prototype.forEach.call(scope.querySelectorAll('[data-i18n-ph]'), function (el) {
            var k = el.dataset.i18nPh;
            if (d[k] != null) el.placeholder = d[k];
        });

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
        console.log('🌐 语言已切换:', cur, '（覆盖', document.querySelectorAll('[data-i18n]').length, '处）');
    }

    window.__i18n = { t: t, setLang: setLang, apply: apply, get: function () { return cur; } };

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', function () { apply(); });
    } else {
        apply();
    }
})();
