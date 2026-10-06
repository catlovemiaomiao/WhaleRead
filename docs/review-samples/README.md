# Review material

`harbor.txt` and `harbor.epub` contain the same five original fictional story
paragraphs. The EPUB adds two chapter headings and a local table of contents.
They contain no private book, customer record, service credential or personal
path. Both can be opened directly without an AI account. The paid Store app
still requires its valid Store purchase; “no AI account” refers to reading.

The EPUB can be reproduced without network access:

```sh
python3 scripts/build_review_sample.py
```

Run this from the complete checkout. An existing identical output is retained;
a different file is never overwritten. Verification of the supplied EPUB's
structure, body text and actual WhaleRead parser is recorded separately; it is
not a claim of final team-signed real-window acceptance.

1. Open the candidate with a fresh review profile. Confirm the welcome text,
   then choose **Start reading** and **Choose book** to open `harbor.txt`.
2. Add a bookmark and a paragraph note. Quit and reopen; both should remain.
   Open `harbor.epub` as well, navigate between **The crossing** and **The letter**
   through the contents, add a bookmark and confirm that reopening restores it.
3. In **Settings**, choose the supplied review API profile for translation.
   Read the destination/material disclosure and explicitly enable permission.
   Try a short translation first. Keep personal keys out of review material.
4. Finish translation, open **Bilingual** reading, then use a separately selected
   Ask AI profile to ask what “burning a hole” means. Save the answer as a note.
5. Annotate the literal rendering if the selected model misses that idiom.
   Review it, confirm a suggestion, then verify that the original machine
   translation is retained while the reviewed edition contains the accepted edit.
6. Export a copy through the native save panel. Quit/reopen and resume the
   finished task; it must retain the same saved output.

The release owner must provide a separate temporary review service or account
in App Store Connect's private review notes. That dependency is currently open;
no personal DSH/ETF key is included here. A local test fixture is not a publicly
reachable review service.

中文验收：首次使用 → 打开虚构文本 → 书签和笔记 → 退出重开 → 配置审核专用
API 并明确授权 → 翻译 → 双语阅读和问答 → 标注习语并确认校订 → 导出 → 再次
重开。模型接口是否工作与译文质量分开核对。

## Research PDF

[`research-scan.pdf`](research-scan.pdf) is a six-page original, image-only
synthetic document. It contains fictional text and numbers, with no private
material or credentials. SHA-256:
`d0956fec24a60d261660fa75a2616c82fc4a6ea9da6fe20f38f8fb34c0e19e7d`.
It is a small workflow sample, not a representative OCR or translation benchmark.

| Page | Material to check |
| --- | --- |
| 1 | English text, `07:35`, `1,250.75`, `89.50`, cancelled `A7B-2049` and approved `A7B-2094` |
| 2 | Chinese text, Traditional Chinese characters, `0.08` versus `0.80`, date `2026-11-03` |
| 3 | Japanese text, the same numbers and units |
| 4 | Intentionally degraded copy of page 1 |
| 5 | Two columns; read `LEFT-START` through `LEFT-END`, then the right column |
| 6 | Formula images `E = mc²` and `(a + b)/c`; table rows Lamp A / 12 / 345.67, Lamp B / 3 / 89.50, Total / 15 / 435.17 |

1. Obtain the separate temporary review OCR service from the release owner's
   private review notes. In **Settings**, add its WhaleRead OCR endpoint, model
   and credential; select it for **PDF OCR** and enable that feature's sending
   permission. A Chat Completions endpoint alone cannot recognize PDF images.
   Custom loopback endpoints also require permission: they may forward to a
   server through a tunnel. Select and authorize the translation profile
   separately; the built-in local HY presets require their model to be installed.
2. Open **Research PDF** and choose `research-scan.pdf` through the native file
   panel. Enable the document batch option, then translate the document. Batch
   mode proceeds without approving every page.
3. Pause after at least one translated block has been saved. Quit the app,
   reopen it and resume. Previously completed blocks should retain their output;
   the document and batch choice should reopen without selecting the PDF again.
4. Check the six pages against the table above. Use the original-position and
   comfortable reading views to compare column order, numbers and visual blocks.
   Batch results must remain marked for human review; batch completion does not
   approve pages. Formula and table images should remain available.
5. Export HTML with the native save panel. Open the exported file with its
   accompanying image folder and check all six pages. This is an HTML draft,
   not a reconstructed PDF.

中文科研验收：另行配置审核 OCR 与翻译服务 → 分别确认发送许可 → 原生面板
打开六页原创扫描 PDF → 启用整篇批量 → 有已保存译块后暂停 → 退出重开并续译
→ 核对数字、分栏、公式和表格图片 → 确认仍标为待人工核对 → 导出 HTML。
审核服务的外部可访问性、正式签名和真实购买仍需独立验收；个人 DGX 服务不是
已经提供给 Apple 的审核环境。
