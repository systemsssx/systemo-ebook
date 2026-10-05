/* ============================================================
   i18n-popups.js — 弹窗页（授权 / WiFi / Kindle / 指引）的中英切换
   ------------------------------------------------------------
   背景：i18n.js 是给 index.html 用的（它依赖 index.html 里的 var T
   和 settings-ui.js 的设置加载）。三个弹窗页和一个指引页是独立文档，
   原来既不加载 i18n.js，也没有 data-i18n，所以永远只有中文。

   本文件让弹窗页复用主界面 settings.json 里的 language 字段：
     · HTML 静态文字：加 data-i18n="键" / data-i18n-ph / data-i18n-title
     · JS 动态文字：window.pageI18n.t('中文原文')
     · 页面内切换：window.pageI18n.setLang('en') —— 会写回 settings.json
       并通知主窗口同步（主进程会向 mainWindow 广播 apply-language）

   字典规则与 i18n.js 一致：中文原文当 key；zh 模式直接返回 key 本身，
   所以 zh 字典里只列需要覆盖的少数条目。
   ============================================================ */
(function () {
    'use strict';

    /* ---------- 中 → 英 字典（value 为 null 表示沿用中文，不出图） ---------- */
    var EN = {
        /* ===== 通用 ===== */
        '保存': 'Save',
        '取消': 'Cancel',
        '关闭': 'Close',
        '刷新': 'Refresh',
        '重置': 'Reset',
        '确定': 'OK',
        '复制': 'Copy',
        '已复制': 'Copied',
        '测试': 'Test',
        '删除': 'Delete',
        '清空': 'Clear',
        '等待中...': 'Waiting...',
        '暂无日志': 'No logs yet',
        '等待配置...': 'Waiting for settings...',
        '等待输入网址...': 'Waiting for a URL...',

        /* ===== 授权窗口 applebooks-auth ===== */
        '百度网盘授权': 'Baidu Netdisk Authorization',
        '苹果图书通道 · 首次使用需完成一次授权': 'Apple Books channel · one-time authorization required',
        '① 打开授权页面': '① Open the authorization page',
        '② 登录并复制授权码': '② Sign in and copy the code',
        '③ 操作日志': '③ Activity log',
        '打开授权页': 'Open the authorization page',
        '打开浏览器授权': 'Open the browser to authorize',
        '填回授权码': 'Paste the code back',
        '提交授权': 'Submit',
        '等待操作…': 'Waiting...',
        '粘贴授权码…': 'Paste the authorization code...',
        '点下面的按钮，程序会自动拉起 Chrome 打开百度授权页。请在浏览器里登录百度账号并完成授权，页面上会显示一串授权码，复制它。': 'Click the button below — the app opens Chrome on the Baidu authorization page. Sign in to your Baidu account there, finish authorizing, then copy the authorization code shown on the page.',
        '把授权码粘到下面，点「提交授权」。它是一串 32 位左右的字母数字 —— 不是 API Key / Secret Key。成功后这里会变绿，然后关掉浏览器即可。': 'Paste the code below and click "Submit". It is a ~32-character string of letters and digits — NOT an API Key / Secret Key. This box turns green when it worked, then you can close the browser.',
        '打开授权页面': 'Open the authorization page',
        '登录百度网盘': 'Sign in to Baidu Netdisk',
        '粘贴授权码': 'Paste the authorization code',
        '提交授权码': 'Submit',
        '停止': 'Stop',
        '待授权': 'Not authorized',
        '已授权': 'Authorized',
        '授权失败': 'Authorization failed',
        '正在启动浏览器...': 'Starting the browser...',
        '浏览器已打开，请登录百度网盘并复制授权码': 'The browser is open — sign in to Baidu Netdisk and copy the code',
        '浏览器已关闭，可以提交授权码了': 'The browser is closed — you can submit the code now',
        '浏览器已关闭，可以去提交授权码了': 'The browser is closed — you can submit the code now',
        '请把授权码粘贴到下面的输入框': 'Paste the authorization code into the box below',
        '授权成功！': 'Authorized!',
        '授权成功，可以关闭本窗口了': 'Authorized — you can close this window now',
        '授权已取消': 'Authorization cancelled',
        '流程已停止': 'Stopped',
        /* --- 授权窗口的状态/日志动态文案 --- */
        'authAPI 未连接': 'authAPI is not connected',
        '窗口初始化异常，请重开授权窗口': 'Window failed to initialize — please reopen the authorization window',
        '正在打开浏览器…': 'Opening the browser...',
        '正在打开授权页面…': 'Opening the authorization page...',
        '授权流程已启动': 'Authorization flow started',
        '请在浏览器中完成授权，然后复制授权码填到第 2 步': 'Finish authorizing in the browser, then paste the code in step 2',
        '启动失败': 'Failed to start',
        '未知错误': 'unknown error',
        '异常': 'Error',
        '启动异常': 'Failed to start',
        '请先输入授权码': 'Enter the authorization code first',
        '提交中…': 'Submitting...',
        '正在提交授权码…': 'Submitting the authorization code...',
        '授权失败，请检查授权码是否正确': 'Authorization failed — check that the code is correct',
        '授权失败 —— 具体原因见下面日志': 'Authorization failed — see the log below for the exact reason',
        '提交异常': 'Submit failed',
        '授权进程已退出（但上面的错误未解决）': 'The authorization process exited (but the error above is still unresolved)',
        '授权进程异常': 'Authorization process error',
        '授权进程异常退出': 'The authorization process exited with an error',
        '授权窗口已就绪': 'Authorization window ready',
        // ★★★ 2026-09-23：自动抓码已整个关闭（用户要求）—— 上面这批
        //   「已自动抓到授权码…」「已在浏览器页面里看到授权码…」「已复制到剪贴板…」
        //   「页面上的授权码已过期…」的键已全部删除，对应的代码也不存在了。
        //   替换成下面这两条「纯手动」的提示：
        '请在浏览器里登录并授权 —— 页面上会显示一串授权码，复制它，粘贴到第 2 步的输入框再提交': 'Sign in and authorize in the browser — the page shows an authorization code; copy it, paste it into the box in step 2, then submit',
        '浏览器已关闭，但还没有提交授权码': 'The browser closed, but no authorization code was submitted yet',
        '还没提交授权码 —— 请再点第 1 步打开浏览器，复制页面上的授权码粘进输入框再提交': 'No code submitted yet — click step 1 again to open the browser, copy the authorization code from the page, paste it into the box and submit',

        /* ===== WiFi 传书 ===== */
        'WiFi 传书': 'WiFi Transfer',
        '自动上传 download 目录下的所有文件': 'Uploads everything in the download folder',
        '① 输入网址': '① Enter the URL',
        '② 操作日志': '② Activity log',
        '例如：192.168.1.100:8080': 'e.g. 192.168.1.100:8080',
        '📋 使用曾经的网址': '📋 Recent URLs',
        '选择曾经的网址': 'Pick a recent URL',
        '暂无历史网址': 'No recent URLs',
        '开始传输': 'Start',
        '请使用英文输入法输入网址': 'Type the URL with an English keyboard',
        '每本之后等待': 'Wait after each book',
        '秒，然后刷新页面再传下一本': 's, then refresh the page before the next book',
        '使用曾经的网址': 'Recent URLs',
        '选择曾经的网址': 'Pick a recent URL',
        '操作日志': 'Activity log',
        '请先输入网址': 'Enter a URL first',
        '传输中...': 'Sending...',
        '正在启动浏览器并上传...': 'Opening the browser and uploading...',
        '等待脚本执行完成...（关闭浏览器或等待自动结束）': 'Waiting for the script to finish (close the browser or let it end on its own)',
        '已启动': 'Started',
        '传输完成': 'Transfer complete',
        '传输失败': 'Transfer failed',
        'API 不可用': 'API unavailable',
        '刷新间隔已保存': 'Refresh interval saved',
        '秒': 's',
        '本': 'book(s)',

        /* ===== Kindle 邮箱配置 ===== */
        'Kindle 邮箱配置': 'Kindle Email Settings',
        '设置发件人 / SMTP 授权码 / 收件人（@kindle.com）': 'Sender / SMTP auth code / recipient (@kindle.com)',
        '发件人邮箱（你的发信邮箱）': 'Sender email (your mailbox)',
        '邮箱': 'Email',
        '授权码': 'Auth code',
        'SMTP 服务器': 'SMTP server',
        '服务商': 'Provider',
        '服务器': 'Server',
        '端口': 'Port',
        'QQ 邮箱 (smtp.qq.com:465)': 'QQ Mail (smtp.qq.com:465)',
        '163 邮箱 (smtp.163.com:465)': '163 Mail (smtp.163.com:465)',
        '126 邮箱 (smtp.126.com:465)': '126 Mail (smtp.126.com:465)',
        '● 复制': '● Copy',
        '授权码已复制': 'Auth code copied',
        '请手动复制': 'Please copy manually',
        '收件人（你的 Kindle 邮箱）': 'Recipient (your Kindle address)',
        '收件人': 'Recipient',
        '日志': 'Log',
        '测试连接': 'Test connection',
        '保存配置': 'Save settings',
        '例如：your-email@qq.com': 'e.g. your-email@qq.com',
        'SMTP 授权码（非登录密码）': 'SMTP auth code (not your login password)',
        'smtp.example.com': 'smtp.example.com',
        '例如：your-name@kindle.com': 'e.g. your-name@kindle.com',
        '自定义': 'Custom',
        '管理我的内容和设备': 'Manage Your Content and Devices',
        '收件人填你在亚马逊「': 'Use the Kindle address you set in Amazon\'s "',
        '」里设置的 Kindle 邮箱': '" page',
        'Kindle 端邮箱后缀统一是': 'Kindle addresses always end with',
        'Kindle 端邮箱后缀是': 'Kindle addresses end with',
        '，请将': ' — change the',
        ' 后缀改为': ' suffix to',
        ' 后缀': ' suffix',
        '配置已保存': 'Settings saved',
        '连接成功': 'Connected',
        '连接失败': 'Connection failed',
        '正在测试...': 'Testing...',
        /* --- Kindle 页的状态/校验动态文案 --- */
        '① 发件人邮箱（你的发信邮箱）': '① Sender email (your mailbox)',
        '② SMTP 服务器': '② SMTP server',
        '③ 收件人（你的 Kindle 邮箱）': '③ Recipient (your Kindle address)',
        '④ 日志': '④ Log',
        '测试中...': 'Testing...',
        '保存中...': 'Saving...',
        '🔌 测试连接': '🔌 Test connection',
        '💾 保存配置': '💾 Save settings',
        '正在测试 SMTP 连接...': 'Testing the SMTP connection...',
        '开始测试 SMTP 连接': 'Testing the SMTP connection to',
        'SMTP 连接成功！可以保存配置': 'SMTP connected — you can save the settings now',
        '异常': 'Error',
        '正在保存配置...': 'Saving the settings...',
        '正在保存配置到本地...': 'Saving the settings to disk...',
        '配置已保存！可以关闭本窗口了': 'Settings saved — you can close this window now',
        '保存成功': 'Saved',
        '保存失败': 'Save failed',
        '已加载历史配置': 'Loaded the saved settings',
        '首次配置，请填写发件人 / 收件人信息': 'First-time setup — fill in the sender and recipient details',
        '加载历史配置失败': 'Failed to load the saved settings',
        'Kindle 邮箱配置窗口已就绪': 'Kindle email settings window ready',
        '提示：SMTP 授权码是邮箱服务商提供的专用密码（不是登录密码）': 'Note: the SMTP auth code is the dedicated password from your mail provider (not your login password)',
        '请填写发件人邮箱': 'Enter the sender email',
        '发件人邮箱格式不正确': 'The sender email is not a valid address',
        '请填写 SMTP 授权码': 'Enter the SMTP auth code',
        '请填写 SMTP 服务器': 'Enter the SMTP server',
        'SMTP 端口无效': 'The SMTP port is invalid',
        '请填写收件人 Kindle 邮箱': 'Enter the recipient Kindle address',
        '收件人邮箱格式不正确': 'The recipient address is not valid',
        /* --- WiFi 页补充 --- */
        'wifiAPI 未连接': 'wifiAPI is not connected',
        '脚本异常': 'Script error',
        '异常结束': 'Ended with an error',
        '全部完成，可关闭本窗口': 'All done — you can close this window',
        'WiFi 传书窗口已就绪': 'WiFi transfer window ready',
        '提示：先打开目标设备的 WiFi 传书页面，再在此输入对应网址': 'Tip: open the WiFi transfer page on the target device first, then enter that URL here',
        '来自书库：本次将传输 %n 本': 'From your library: %n book(s) this time',
        '书库批量传输，共 %n 本': 'Library batch transfer: %n book(s)',

        /* ===== 苹果图书操作指引 applebooks-help ===== */
        '苹果图书操作指引': 'Apple Books Guide',
        '操作指引': 'Guide',
        '目录': 'Contents',
        '① 准备工作': '① Before you start',
        '② 授权百度网盘': '② Authorize Baidu Netdisk',
        '③ 上传图书': '③ Upload a book',
        '④ 常见问题': '④ FAQ',
        '回到顶部': 'Back to top',
        '📖 苹果图书 · 上传到 iPhone / iPad 完整指引': '📖 Apple Books · Full guide to uploading to iPhone / iPad',
        '电脑端 EasyPub 上传 → 百度网盘 → 手机端图书 App 打开 · 4 步搞定': 'Upload from EasyPub → Baidu Netdisk → open in the Books app · 4 steps',
        '📑 快速跳转': '📑 Quick links',
        '1. 百度网盘授权': '1. Authorize Baidu Netdisk',
        '2. 放入文件 + 上传': '2. Add a file + upload',
        '3. 手机端网盘下载': '3. Download on your phone',
        '4. 用图书 App 打开': '4. Open it in Books',
        '百度网盘授权（一次性）': 'Authorize Baidu Netdisk (one time)',
        '点击 EasyPub「苹果图书」页右侧「<b>授权</b>」按钮': 'Click the "<b>Authorize</b>" button on the right of EasyPub\'s "Apple Books" page',
        '浏览器自动打开百度 OAuth 授权页': 'The browser opens Baidu\'s OAuth page automatically',
        '登录百度账号 → 点击「<b>授权</b>」': 'Sign in to your Baidu account → click "<b>Authorize</b>"',
        '页面会显示一段<b>授权码</b>，复制它': 'The page shows an <b>authorization code</b> — copy it',
        '回到授权小窗口 → 粘贴授权码 → 点击「<b>提交授权</b>」': 'Go back to the small authorization window → paste the code → click "<b>Submit</b>"',
        '💡 授权码通常是一长串字符，复制时注意不要带空格或换行。': '💡 The code is usually a long string — make sure you do not copy spaces or line breaks with it.',
        '⚠️ 授权过程会启动 Chrome 浏览器，请确保本机已安装 Chrome 且 chromedriver.exe 版本匹配。': '⚠️ Authorization launches Chrome — make sure Chrome is installed and chromedriver.exe matches its version.',
        '放入文件 + 上传到百度网盘': 'Add a file + upload it to Baidu Netdisk',
        '把 epub / txt / pdf 等图书文件<b>拖入左侧放置框</b>（或点击放置框选择）': 'Drag an epub / txt / pdf file <b>into the drop zone on the left</b> (or click the zone to pick one)',
        '文件会自动复制到 <code>EasyPub 同目录的 download/</code>': 'The file is copied automatically to <code>EasyPub 同目录的 download/</code>',
        '点击右侧「<b>上传</b>」按钮 → 程序调用 <code>upload_folder_cmd.py</code>，用 <code>bypy syncup</code> 增量同步': 'Click the "<b>Upload</b>" button on the right → the app runs <code>upload_folder_cmd.py</code>, which incrementally syncs with <code>bypy syncup</code>',
        '网盘目标路径：<code>/我的应用数据/epub_download_backup</code>': 'Destination on Netdisk: <code>/apps/epub_download_backup</code>',
        '上传完成后按钮短暂显示「✓ 完成」': 'When the upload finishes the button briefly shows "✓ Done"',
        '💡 bypy 是增量同步，重复上传不会重复占用空间。每次只放一个文件，需要上传多本时依次操作即可。': '💡 bypy syncs incrementally, so re-uploading does not take extra space. Put in one file at a time and repeat for more books.',
        '手机端：在百度网盘里找到文件': 'On your phone: find the file in Baidu Netdisk',
        'iPhone / iPad 打开「<b>百度网盘</b>」App，登录<b>同一个</b>百度账号': 'Open the "<b>Baidu Netdisk</b>" app on your iPhone / iPad and sign in with the <b>same</b> Baidu account',
        '底部导航点「<b>文件</b>」→ 看到「<b>我的应用数据</b>」分类': 'Tap "<b>Files</b>" in the bottom bar → look for the "<b>My App Data</b>" category',
        '点进「<b>我的应用数据</b>」→ 找到 <code>epub_download_backup</code> 文件夹（这就是 EasyPub 上传的目标）': 'Open "<b>My App Data</b>" → find the <code>epub_download_backup</code> folder (this is where EasyPub uploads)',
        '在文件夹里点开你想要的那本 epub → 系统会调用默认阅读器预览': 'Tap the epub you want → the system previews it with the default reader',
        '图 1：网盘首页（点底部「文件」）': 'Fig. 1: Netdisk home (tap "Files" at the bottom)',
        '图 2：我的应用数据 → epub_download_backup': 'Fig. 2: My App Data → epub_download_backup',
        '图 3：点开 epub 文件预览': 'Fig. 3: tap the epub to preview it',
        '导出到「图书」App（系统自带 Books）': 'Export to the "Books" app (built-in Books)',
        '文件在百度网盘预览界面打开后 → 点顶部的「<b>更多</b>」（···）按钮': 'Once the file is open in the Netdisk preview → tap "<b>More</b>" (···) at the top',
        '在弹出的分享菜单里 → 找到「<b>用图书打开</b>」（Books 图标，蓝白橙色书）': 'In the share sheet that appears → find "<b>Open in Books</b>" (the Books icon, a blue/white/orange book)',
        '点击后 → epub 自动添加到 iPhone / iPad 自带的「图书」App': 'After tapping it, the epub is added to the built-in "Books" app on your iPhone / iPad',
        '打开系统「<b>图书</b>」App → 底部「<b>图书馆</b>」就能看到这本书': 'Open the system "<b>Books</b>" app → the book appears under "<b>Library</b>" at the bottom',
        '图 4：点「更多」→ 选「用图书打开」': 'Fig. 4: tap "More" → choose "Open in Books"',
        '⚠️ 如果分享菜单里<b>没有「图书」图标</b>：说明系统没把 Books 注册为接收 App。<br>解决：iPhone 设置 → 「图书」→ 打开「<b>在共享表单中显示</b>」开关 → 回网盘重新分享即可。': '⚠️ If the share sheet has <b>no "Books" icon</b>, the system has not registered Books as a receiving app.<br>Fix: iPhone Settings → "Books" → turn on "<b>Show in Share Sheet</b>" → share again from Netdisk.',
        '💡 epub 一旦导入「图书」App，会自动同步到所有登录同一 Apple ID 的设备（iPhone / iPad / Mac）。': '💡 Once an epub is imported into the "Books" app, it syncs automatically to every device signed in with the same Apple ID (iPhone / iPad / Mac).',
        '✅ 整个流程：EasyPub 上传 → 百度网盘同步 → 手机点 epub → 选「图书」打开 → 在 iPhone 看书': '✅ The whole flow: upload from EasyPub → sync via Baidu Netdisk → tap the epub on your phone → choose "Books" → read it on your iPhone',

        /* ===== Kindle 操作指引 kindle-help ===== */
        'Kindle 传书 · 完整操作指引': 'Kindle Transfer · Full Guide',
        'Kindle 传书': 'Kindle Transfer',
        '📖 Kindle 传书 · 完整操作指引': '📖 Kindle Transfer · Full Guide',
        '从注册 / 登录 Amazon 账号到 Kindle 收到图书 · 6 步搞定': 'From signing up / signing in to Amazon to getting the book on Kindle · 6 steps',
        '⚠ 重要：只有 <code>amazon.com</code> 外区账号支持 Kindle 邮箱推送，<code>amazon.cn</code> 已停用。': '⚠ Important: only <code>amazon.com</code> (non-China) accounts support Kindle email delivery; <code>amazon.cn</code> is discontinued.',
        '1. 打开 amazon.com': '1. Open amazon.com',
        '2. 切到中文': '2. Switch to Chinese',
        '3. 输入邮箱': '3. Enter your email',
        '4A. 注册（无账号）': '4A. Sign up (no account)',
        '4B. 登录（有账号）': '4B. Sign in (have an account)',
        '5. 手机验证 → Not now': '5. Phone check → Not now',
        '6. 添加信任邮箱 + 找 Kindle 端邮箱': '6. Add an approved sender + find your Kindle address',
        '7. 获取发件邮箱 SMTP 授权码': '7. Get the SMTP auth code for your sender mailbox',
        '打开 amazon.com（外区）': 'Open amazon.com (non-China site)',
        '在浏览器地址栏输入 <b>amazon.com</b>（不是 amazon.cn）': 'Type <b>amazon.com</b> in the address bar (not amazon.cn)',
        '首次访问会看到浏览器自带的中文翻译弹窗（如图）': 'On your first visit the browser shows its own translate prompt (see the image)',
        '<b>注意：</b>这是浏览器的翻译功能，<b>不是</b>亚马逊自己切语言——所以<b>不要点浏览器弹窗的「中文」</b>，那个翻译效果差': '<b>Note:</b> that is the browser\'s translation feature, <b>not</b> Amazon switching languages — so <b>do not click "Chinese" in the browser prompt</b>; that translation is poor',
        '把亚马逊网站切到中文': 'Switch the Amazon site to Chinese',
        '看页面<b>顶部搜索框的左边</b>，有一个「<b>🇺🇸 EN</b>」国旗下拉按钮': 'Look to the <b>left of the search box at the top</b> — there is a "<b>🇺🇸 EN</b>" flag dropdown',
        '点击它 → 弹出语言列表': 'Click it → a language list appears',
        '选择「<b>中文 (简体)</b>」或「<b>中文 (繁體)</b>」': 'Choose "<b>中文 (简体)</b>" or "<b>中文 (繁體)</b>"',
        '页面会自动刷新成中文版本（亚马逊官方翻译，比浏览器翻译好得多）': 'The page reloads in Chinese (Amazon\'s own translation, far better than the browser\'s)',
        '输入邮箱地址（统一入口）': 'Enter your email address (single entry point)',
        '点页面顶部右上角「<b>Hello, sign in</b>」 → 在下拉里点「<b>Sign in</b>」（登录）或「<b>Start here</b>」（注册）': 'Click "<b>Hello, sign in</b>" at the top right → in the dropdown click "<b>Sign in</b>" or "<b>Start here</b>"',
        '进入登录页后，在「<b>输入手机号或邮箱</b>」框里填你的邮箱（如 <code>xxx@qq.com</code>）': 'On the sign-in page, type your email into the "<b>Enter mobile number or email</b>" box (e.g. <code>xxx@qq.com</code>)',
        '点黄色「<b>继续</b>」按钮': 'Click the yellow "<b>Continue</b>" button',
        '系统会自动判断：邮箱未注册过 → 走 <b>4A 注册流程</b>；已注册过 → 走 <b>4B 登录流程</b>': 'Amazon decides automatically: new email → <b>4A sign-up</b>; existing email → <b>4B sign-in</b>',
        '两条路：注册 或 登录（按系统提示选）': 'Two paths: sign up or sign in (follow the prompt)',
        '路径 A · 没账号': 'Path A · no account',
        '系统显示「<b>看起来您是亚马逊的新用户</b>」 → 点黄色「<b>继续创建账户</b>」 → 填姓名（中文名即可）+ 密码（≥6 位）+ 再次输入密码 → 点「<b>继续</b>」 → 亚马逊给邮箱发验证信 → 点链接激活': 'Amazon shows "<b>Looks like you are a new customer</b>" → click the yellow "<b>Create your Amazon account</b>" → fill in your name (Chinese name is fine) + password (6+ characters) + password again → click "<b>Continue</b>" → Amazon emails you a verification link → click it to activate',
        '路径 B · 已有账号': 'Path B · existing account',
        '系统直接进入「<b>登录</b>」页（已识别你的邮箱）→ 在「<b>密码</b>」框填密码 → 点黄色「<b>登录</b>」 → 跳到第 5 步': 'Amazon goes straight to the "<b>Sign in</b>" page (your email is recognized) → type your password in "<b>Password</b>" → click the yellow "<b>Sign in</b>" → continue to step 5',
        '手机号验证 → 直接点「Not now」': 'Phone verification → just click "Not now"',
        '登录成功后，亚马逊可能弹「<b>Keep hackers out</b>」页面，要你添加手机号做二次验证': 'After signing in, Amazon may show a "<b>Keep hackers out</b>" page asking for a phone number for two-step verification',
        '<b>这一步是可选的</b>，我们只是用 Kindle 推送功能，不需要手机号验证': '<b>This step is optional</b> — we only use Kindle delivery, so phone verification is not needed',
        '直接点页面下方的蓝色「<b>Not now</b>」链接跳过': 'Just click the blue "<b>Not now</b>" link near the bottom to skip',
        '跳过之后会进入 Amazon 首页，登录完成': 'After skipping you land on the Amazon home page — sign-in is done',
        '添加信任邮箱 + 找 Kindle 端邮箱': 'Add an approved sender + find your Kindle address',
        '登录后 → 页面<b>左上角点「全部」</b>菜单（汉堡按钮）': 'After signing in → click the "<b>All</b>" menu (hamburger button) at the <b>top left</b>',
        '在弹出的大菜单里 → 找到「<b>数字内容和设备 → Kindle 电子阅读器</b>」并点击': 'In the big menu → find "<b>Digital Content and Devices → Kindle E-readers</b>" and click it',
        '进入「<b>管理我的 Kindle</b>」页面 → 点「<b>首选项</b>」标签': 'On the "<b>Manage Your Kindle</b>" page → click the "<b>Preferences</b>" tab',
        '<b>下滑到底部</b>，找到「<b>已认可的发件人电子邮箱列表</b>」一栏': '<b>Scroll to the bottom</b> and find "<b>Approved Personal Document E-mail List</b>"',
        '点「<b>添加新的电子邮件地址</b>」→ 填你用来发书的邮箱（如 <code>xxx@qq.com</code>）→ 保存': 'Click "<b>Add a new approved e-mail address</b>" → enter the mailbox you send books from (e.g. <code>xxx@qq.com</code>) → save',
        '<b>重要：</b>亚马逊会给该邮箱发一封确认信，<b>必须点链接确认</b>才生效（不点会被当成垃圾邮件拒收）': '<b>Important:</b> Amazon emails a confirmation to that address — you <b>must click the link</b> for it to take effect (otherwise deliveries are rejected as spam)',
        '<b style="color:#34c759;">📮 找 Kindle 端邮箱（收件人）</b><br>同一个「<b>首选项</b>」页面 → 下滑到「<b>个人文档设置</b>」一栏 → 找「<b>「发送至 Kindle」电子邮箱</b>」表格<br>只要你的 Kindle 设备 / App 登录了同一个 Amazon 账号，这里就会自动列出对应的 <code>xxx@kindle.com</code> 邮箱（如：<b>您的设备</b>、<b>您的手机</b>）<br>复制其中一个作为「<b>收件人</b>」填到 EasyPub「📧 邮箱」配置里': '<b style="color:#34c759;">📮 Find your Kindle address (the recipient)</b><br>On the same "<b>Preferences</b>" page → scroll to "<b>Personal Document Settings</b>" → find the "<b>Send-to-Kindle E-mail Settings</b>" table<br>As long as your Kindle device / app is signed in to the same Amazon account, the matching <code>xxx@kindle.com</code> addresses are listed here (e.g. <b>Your Device</b>, <b>Your Phone</b>)<br>Copy one as the "<b>recipient</b>" into EasyPub\'s "📧 Email" settings',
        '<b>⚠ 修改 Kindle 端邮箱（避免与发件邮箱混淆）</b><br>在「发送至 Kindle 电子邮箱」表格里点对应设备的「<b>编辑</b>」→ 把默认生成的乱码邮箱（如 <code>xxx_urXgDO@kindle.com</code>）<b>改成你容易分辨的名字</b>（如 <code>my-kindle-001@kindle.com</code>）<br><span class="dim">原因：亚马逊自动生成的 Kindle 邮箱名会包含用户名片段，跟你的发件邮箱看着很像，容易填错或被邮箱系统当成自己发给自己过滤掉。改成完全不同的名字就不会出问题。</span>': '<b>⚠ Rename your Kindle address (so it is not confused with the sender mailbox)</b><br>In the "Send-to-Kindle E-mail Settings" table click "<b>Edit</b>" next to the device → change the random default address (e.g. <code>xxx_urXgDO@kindle.com</code>) to <b>something easy to tell apart</b> (e.g. <code>my-kindle-001@kindle.com</code>)<br><span class="dim">Why: Amazon\'s generated Kindle address contains a fragment of your username and looks very similar to your sender mailbox, so it is easy to mix up or be filtered as mail sent to yourself. A completely different name avoids the problem.</span>',
        '<b>⚠ 关闭「个人文档存档」+ 改用「云端手动下载」工作流</b><br>在「个人文档设置」里 → 「<b>个人文档存档</b>」一栏 → 点「<b>存档设置</b>」→ <b>取消勾选「启用存档功能」</b> → 点「<b>更新</b>」<br><span class="dim">原因：</span>整本书直接推到 Kindle 设备会很慢甚至失败。推荐工作流是「<b>推送到云端 → 手动下载</b>」——EasyPub 把书发到你的 Amazon 云端，<b>不</b>自动同步到 Kindle 设备，然后你在 Kindle 设备 / App 里的云端列表里手动点下载。这样既不会因为整本推送卡死，也不会因为自动同步出现大量重复书。': '<b>⚠ Turn off "Personal Document Archiving" and switch to the "send to cloud → download manually" flow</b><br>Under "Personal Document Settings" → "<b>Personal Document Archiving</b>" → click "<b>Archiving Options</b>" → <b>uncheck "Enable archiving"</b> → click "<b>Update</b>"<br><span class="dim">Why:</span> pushing a whole book straight to the Kindle device is slow and can fail. The recommended flow is "<b>send to the cloud → download manually</b>": EasyPub delivers the book to your Amazon cloud, it does <b>not</b> auto-sync to the Kindle device, and you tap download in the cloud list on your Kindle device / app. This avoids both the hang of a full-book push and the pile of duplicate books that auto-sync creates.',
        '获取发件邮箱的「SMTP 授权码」': 'Get the "SMTP auth code" for your sender mailbox',
        '这一步是配置<b>你自己的发件邮箱</b>（163 / QQ / Gmail / Outlook 等）<b>不是 Amazon 账号</b>——用 EasyPub 推送时它会登录你的发件邮箱把书发到 Kindle': 'This step configures <b>your own sender mailbox</b> (163 / QQ / Gmail / Outlook, etc.) — <b>not your Amazon account</b>. When EasyPub sends a book it signs in to that mailbox and delivers the book to Kindle',
        '<b>注意：SMTP 授权码 ≠ 邮箱登录密码</b>，是专门给第三方客户端用的密钥': '<b>Note: the SMTP auth code is NOT your mailbox login password</b> — it is a key made for third-party clients',
        '以 <b>163 邮箱</b>为例（其它邮箱操作类似，路径已附在最后）：': 'Using <b>163 Mail</b> as the example (other providers are similar; their paths are at the end):',
        '顶部导航点「<b>设置</b>」→ 左侧菜单点「<b>POP3/SMTP/IMAP</b>」': 'Click "<b>Settings</b>" in the top bar → click "<b>POP3/SMTP/IMAP</b>" in the left menu',
        '先确保「<b>IMAP/SMTP服务</b>」和「<b>POP3/SMTP服务</b>」都是「<b>已开启</b>」状态（如果不是点「开启」）': 'First make sure both "<b>IMAP/SMTP Service</b>" and "<b>POP3/SMTP Service</b>" are "<b>Enabled</b>" (if not, click "Enable")',
        '下滑到「<b>授权密码管理</b>」一栏 → 点底部「<b>新增授权码</b>」按钮': 'Scroll down to "<b>Authorization Password Management</b>" → click "<b>Add authorization code</b>" at the bottom',
        '手机扫码或短信验证 → 拿到 16 位授权码（<b>只显示一次，复制保存</b>）': 'Scan the QR code with your phone or verify by SMS → you get a 16-character auth code (<b>shown only once — copy and save it</b>)',
        '回到 EasyPub「📧 邮箱」配置 → 「<b>授权码</b>」一栏粘贴这个 16 位字符串': 'Back in EasyPub\'s "📧 Email" settings → paste that 16-character string into "<b>Auth code</b>"',
        '📋 其它邮箱的授权码获取路径（点击展开）': '📋 Where to get auth codes for other mailboxes (click to expand)',
        '<b>QQ 邮箱</b>：网页版 → 设置 → 账户 → 「<b>POP3/IMAP/SMTP/Exchange/CardDAV/CalDAV服务</b>」→ 开启「<b>IMAP/SMTP服务</b>」→ 点「<b>生成授权码</b>」<br><b>126 邮箱</b>：和 163 一样，在「设置 → POP3/SMTP/IMAP」里<br><b>Gmail</b>：账号安全 → 开启<b>两步验证</b> → 「<b>应用专用密码</b>」→ 选「邮件 + 其他（自定义名）」→ 生成 16 位密码<br><b>Outlook / Hotmail</b>：账户安全 → 「<b>应用密码</b>」→ 生成（同样需要先开两步验证）<br><b>Yahoo Mail</b>：账户安全 → 「<b>生成应用密码</b>」→ 选「桌面邮件」': '<b>QQ Mail</b>: web version → Settings → Account → "<b>POP3/IMAP/SMTP/Exchange/CardDAV/CalDAV Service</b>" → enable "<b>IMAP/SMTP Service</b>" → click "<b>Generate authorization code</b>"<br><b>126 Mail</b>: same as 163, under "Settings → POP3/SMTP/IMAP"<br><b>Gmail</b>: Account security → turn on <b>2-Step Verification</b> → "<b>App passwords</b>" → choose "Mail + Other (custom name)" → generate a 16-character password<br><b>Outlook / Hotmail</b>: Account security → "<b>App passwords</b>" → generate (also requires 2-Step Verification first)<br><b>Yahoo Mail</b>: Account security → "<b>Generate app password</b>" → choose "Desktop mail"',
        '✅ 全部完成后，关掉本窗口，回到 EasyPub「Kindle 传书」页 → 点「📧 邮箱」配置 SMTP（发件邮箱 + 授权码 + 你的 Kindle 端邮箱）→ 选好文件 → 点「传输」→ 书会到你的 Amazon 云端，在 Kindle 设备 / App 手动下载': '✅ When everything is done, close this window and go back to EasyPub\'s "Kindle Transfer" page → click "📧 Email" to configure SMTP (sender mailbox + auth code + your Kindle address) → pick your file → click "Send" → the book goes to your Amazon cloud, then download it manually on your Kindle device / app',
        '① 获取 Kindle 邮箱': '① Get your Kindle address',
        '② 配置发件邮箱': '② Set the sender mailbox',
        '③ 推送图书': '③ Send a book',
        '④ 手机热点传书（可选）': '④ Phone hotspot transfer (optional)',
        '⑤ 注意事项': '⑤ Notes',
        '⑥ 常见问题': '⑥ FAQ',
        '⑦ 补充说明': '⑦ More details',
        '展开更多': 'Show more',
        '常见错误': 'Common errors',
        '提示': 'Tip',
        '注意': 'Note',
        '重要': 'Important',

        // ===== 阅读窗口（reader.html） =====
        'EasyPub 阅读': 'EasyPub Reader',
        '⭐ 添加书签': '⭐ Bookmark',
        '双击阅读': 'Double-click to read',
        '目录': 'Contents',
        '书签': 'Bookmarks',
        '搜索': 'Search',
        '内容': 'Contents',
        '添加书签': 'Add bookmark',
        '返回': 'Back',
        '主题': 'Theme',
        '深色': 'Dark',
        '浅色': 'Light',
        '护眼': 'Sepia',
        '字体': 'Font',
        '宋体': 'Serif',
        '黑体': 'Sans',
        '字号': 'Text size',
        '行距': 'Line spacing',
        '翻页': 'Layout',
        '翻页「仿真」': 'Paginated',
        '滚动': 'Scrolled',
        '边距': 'Margins',
        '窄': 'Narrow',
        '标准': 'Normal',
        '宽': 'Wide',
        '对齐': 'Alignment',
        '左对齐': 'Left',
        '两端对齐': 'Justified',
        '搜索全书…': 'Search in book…',
        '输入关键词后回车': 'Type a keyword and press Enter',
        '关闭': 'Close',
        '上一页': 'Previous page',
        '下一页': 'Next page',
        '点右侧': 'Tap right side',
        '正在解析书籍…': 'Opening book…',
        '打开失败：': 'Failed to open: ',
        '没有指定书籍文件': 'No book file specified',
        '没有找到匹配的内容': 'No matches found',
        '搜索中…': 'Searching…',
        '搜索出错：': 'Search failed: ',
        '还没有书签。读到想记住的地方，点顶部 ⭐ 添加。': 'No bookmarks yet. Tap ⭐ at the top to bookmark where you are.',
        '删除': 'Delete',
        '章': 'chapters',
        '处': 'matches',
        '第 %n 章': 'Chapter %n',
        '第 %n 处': 'Location %n',
        '宋体 / 思源宋体': 'Songti / Noto Serif SC',
        '黑体 / 思源黑体': 'Heiti / Noto Sans SC'
    };

    var cur = 'zh';
    var listeners = [];
    var langAPI = (typeof window !== 'undefined' && window.pageLang) ? window.pageLang : null;

    function t(key, fallback) {
        if (key == null) return '';
        if (cur === 'en') {
            if (EN[key] != null) return EN[key];
            return (fallback != null) ? fallback : key;
        }
        return key;
    }

    function apply(root) {
        var scope = root || document;
        Array.prototype.forEach.call(scope.querySelectorAll('[data-i18n]'), function (el) {
            var k = el.getAttribute('data-i18n');
            var v = t(k);
            if (v !== k || cur === 'en') el.textContent = v;
        });
        Array.prototype.forEach.call(scope.querySelectorAll('[data-i18n-ph]'), function (el) {
            el.placeholder = t(el.getAttribute('data-i18n-ph'));
        });
        Array.prototype.forEach.call(scope.querySelectorAll('[data-i18n-title]'), function (el) {
            el.title = t(el.getAttribute('data-i18n-title'));
        });
        Array.prototype.forEach.call(scope.querySelectorAll('[data-i18n-html]'), function (el) {
            el.innerHTML = t(el.getAttribute('data-i18n-html'));
        });

        document.documentElement.setAttribute('lang', cur === 'en' ? 'en' : 'zh-CN');
        document.documentElement.setAttribute('data-lang', cur);

        var sw = document.getElementById('pageLangSwitch');
        if (sw) {
            Array.prototype.forEach.call(sw.querySelectorAll('div'), function (el) {
                el.classList.toggle('on', el.getAttribute('data-lang') === cur);
            });
        }
    }

    function setLang(lang, opts) {
        cur = (lang === 'en') ? 'en' : 'zh';
        apply();
        for (var i = 0; i < listeners.length; i++) {
            try { listeners[i](cur); } catch (e) {}
        }
        if (!(opts && opts.local)) {
            try { if (langAPI && langAPI.set) langAPI.set(cur); } catch (e) {}
        }
    }

    // 静态文字先按中文渲染（无闪烁），拿不到 settings 时也不影响使用
    window.pageI18n = {
        t: t,
        apply: apply,
        setLang: setLang,
        get: function () { return cur; },
        // 给「已经渲染过、又带运行时数字/变量」的文案用：切语言时重渲染
        onChange: function (cb) { if (typeof cb === 'function') listeners.push(cb); }
    };

    /* ---------- 页面内切换开关（如果这个页面放了 #pageLangSwitch） ---------- */
    function bindSwitch() {
        var sw = document.getElementById('pageLangSwitch');
        if (!sw) return;
        Array.prototype.forEach.call(sw.querySelectorAll('div'), function (el) {
            el.addEventListener('click', function () { setLang(el.getAttribute('data-lang')); });
        });
    }

    /* ---------- 读主界面设置里的 language，并跟随主窗口切换 ---------- */
    function syncFromSettings() {
        if (!langAPI || !langAPI.get) return;
        Promise.resolve(langAPI.get()).then(function (r) {
            var v = r && r.success && r.settings ? r.settings.language : null;
            if (v) apply();
            if (v) setLang(v, { local: true });
        }).catch(function () {});
    }

    function init() {
        bindSwitch();
        if (langAPI && langAPI.onChange) {
            langAPI.onChange(function (lang) { setLang(lang, { local: true }); });
        }
        syncFromSettings();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
