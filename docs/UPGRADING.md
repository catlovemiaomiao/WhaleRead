# Upgrading from the preview

Keep the old application, preferences and important book/task files before
replacing the app. In Settings, import preview preferences, select your existing
task folder, and grant access to your book folder through the native file panels.
The import merges library and reading data and saves a settings recovery snapshot.

Custom model/API profiles and their credentials need separate setup. The new
edition defaults to local HY-MT2 7B when no translation profile is saved. If your
preview used a self-hosted 30B service, add its existing endpoint and exact Model
ID in Settings, save its key to Keychain, select it for **Translation**, and check
its destination and sending permission. Then try a short fictional passage and
confirm that the selection remains after reopening. Do the same separately for
Review, Ask AI and PDF OCR when you use those features.

The old Ask AI endpoint/model can appear as a profile without a saved key. The
Settings action to import the old `ask-key.json` saves and verifies that key in
Keychain; it keeps the old file until you explicitly choose to delete it. Private
OCR uses the separate WhaleRead OCR service and its service key, not a general
chat model. Neither connection is supplied in the public download.

Completed translations stay in their existing task folders and can be reopened.
Do not edit checkpoint/model IDs to force a partially translated task onto a
different route. Keep the original task; create a new edition when its original
model profile cannot be restored.

## 中文

升级前保留旧应用、偏好文件和重要书籍/任务。设置中先导入预览版偏好，再选择旧任务目录并授权书籍目录；导入会合并书库、阅读记录，并备份导入前的设置。

**模型/API 档案及密钥需要单独恢复。** 没有保存翻译档案时，新版默认选择本机 HY-MT2 7B。如果你原来默认用自托管 30B，请在设置中填回自己的接口、准确模型 ID 和密钥，保存到钥匙串，然后在“翻译模型”中选中它，核对发送目的地与授权。先试译一小段虚构文字，再重开应用确认默认选择保留。校阅、问 AI 和 PDF OCR 的选择各自独立。

旧问 AI 档案可能只保留接口与模型名称，没有密钥。可以用设置中的“导入旧问 AI 密钥到钥匙串”迁移已有 `ask-key.json`；原文件仍保留，只有明确选择删除才会移除。DGX OCR 则需要独立 OCR 服务和对应服务密钥。公开安装包不包含你的私人连接或凭据。

已完成译本保留在原任务目录，可以重新打开。不要直接改断点中的模型 ID 来绕过模型绑定；无法恢复原档案的未完成任务应保留原件并另建译本。
