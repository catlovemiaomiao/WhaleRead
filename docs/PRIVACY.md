# WhaleRead / 鲸读隐私说明

Effective / 生效日期：2026-10-06

鲸读把书库、阅读位置、笔记、翻译任务与缓存保存在你的 Mac 上。软件不包含广告、使用情况分析或向开发者发送书籍内容的服务。EPUB 章节在本机显示；出版者脚本与远程资源被禁用。

阅读 EPUB/TXT 不需要 AI 服务。使用本机 HY-MT2/Ollama 预设时，请求发往本机回环地址，正文不发送到云端。

GitHub 分发版免费开源，不使用 App Store 购买验证，不要求 Apple 账户或打赏。自愿打赏通过你主动打开的第三方支付入口完成；鲸读应用不读取或保存付款账户、金额或交易记录。

使用你配置的其他服务时，设置会显示目的地、模型和每项功能的发送材料，并分别取得你的同意：

回环地址也可能通过代理或私有隧道连接另一台机器。除内置本机 HY-MT2 预设外，你添加的接口均按功能确认发送材料，不能仅凭地址看起来在本机就自动授权。

- 翻译：原文译块、邻段语境、术语表与翻译要求。
- 校阅：待核对的原文、译文、人工标注与校阅要求。
- 问 AI：书名、选文、最多五段原文、问题与本轮问答历史。
- PDF OCR：当前 PDF 页的完整图像；批量时依次发送各页。DGX 通过你配置的私有隧道接入，回环地址也可能把页面转发至另一台机器，因此 OCR 单独取得发送同意。应用不内置服务器账号或模型权重。

AI 请求只在你启动相应功能后发生。远端 API 使用 HTTPS，带凭据的请求不跟随重定向。你可以取消授权以停止后续请求；已发送的材料无法撤回。服务商的处理、日志、留存和账户计费遵循你与该服务商的约定。鲸读不能替第三方承诺数据删除或零留存。

API Key 保存在 macOS 系统钥匙串，不写入任务、偏好文件或应用包。后台任务通过进程管道取得当次请求需要的密钥。你可以在模型设置中清除密钥。预览版的旧明文密钥文件仅在你选择导入后读取；导入先核验钥匙串，删除旧文件需要你明确点击。

外部书籍和任务目录通过你的文件选择授权，并使用系统持久书签恢复访问。导入预览版偏好前先备份当前设置；旧文件保留。设置备份、书库、笔记、任务和缓存都可能包含私人内容，保存在本机，不自动上传。删除应用不保证删除外部任务文件或钥匙串项目；可先在设置清除 API Key，再自行删除不需要的任务目录。

支持与隐私问题：项目的 WhaleRead Issues 页面。避免在公开问题中上传书籍、个人路径、密钥或完整私有日志。
https://github.com/catlovemiaomiao/WhaleRead/issues

---

WhaleRead stores your library, reading positions, notes, translation tasks and caches on your Mac. It includes no advertising, usage analytics or service that sends book content to the developer. EPUB chapters are displayed locally with publisher scripts and remote resources disabled.

Reading EPUB/TXT does not require an AI service. The local HY-MT2/Ollama preset sends requests to this Mac’s loopback address and keeps book text off cloud services.

The GitHub edition is free and open source. It does not verify an App Store purchase or require an Apple account or donation. Voluntary donations use a third-party payment destination you choose to open. The WhaleRead application does not read or save payment accounts, amounts or transaction records.

For other services you configure, settings show the destination, model and material sent by each feature and ask for separate consent:

A loopback address can reach another machine through a proxy or private tunnel. Apart from the built-in local HY-MT2 presets, every profile you add requires consent for each feature, even when its visible address is on this Mac.

- Translation: source chunks, nearby context, glossary and translation instructions.
- Review: source and translated text being checked, your annotations and review instructions.
- Ask AI: book title, selection, up to five source paragraphs, question and this conversation’s history.
- PDF OCR: the complete image of the current PDF page, or each page in sequence for a batch. A private tunnel can forward a loopback endpoint to a DGX or another machine, so OCR requires separate sharing consent. The app includes no server account or model weights.

AI requests start only when you use a feature. Remote APIs require HTTPS and credential-bearing requests never follow redirects. Revoking consent stops further requests; material already sent cannot be recalled. Your provider controls processing, logging, retention and account billing. WhaleRead cannot promise deletion or zero retention on a third party’s behalf.

API Keys are stored in macOS Keychain, never in task files, preferences or the app bundle. Workers receive the credential for their current request through a process pipe. Keys can be cleared in model settings. A preview’s plaintext key file is opened only when you select it for import; Keychain is verified first and deleting the old file requires your explicit action.

You authorize external books and task folders through file selection; system bookmarks restore access. Importing preview preferences first backs up current settings and keeps original files. Settings backups, libraries, notes, tasks and caches can contain private content and remain local. Removing the app may leave external tasks and Keychain items. Clear API Keys in settings before removing any task folders you no longer need.

Support and privacy questions: WhaleRead Issues. Please exclude books, personal paths, keys and full private logs from public issues.
https://github.com/catlovemiaomiao/WhaleRead/issues
