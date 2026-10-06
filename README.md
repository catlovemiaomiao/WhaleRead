<p align="center"><img src="docs/brand/whaleread-icon.png" width="128" alt="WhaleRead icon"></p>

# WhaleRead / 鲸读

**Free, GPL-3.0 open source. No subscription, paid unlock or required donation.**

WhaleRead is a macOS reading workspace for local TXT, Markdown and EPUB books:
translate, read source and translation side by side, keep bookmarks and notes,
review a passage with its source evidence, and ask AI about what you are reading.

[Website and historical demo](https://whaleread-astra.kunyu575.chatgpt.site/) ·
[Download 1.19.0](https://github.com/catlovemiaomiao/WhaleRead/releases/tag/v1.19.0) ·
[Product Hunt](https://www.producthunt.com/products/whaleread)

## Download and upgrade

- **WhaleRead 1.19.0 / build 67** — Apple Silicon (M1 or newer), **macOS 15 or later**.
- Download `WhaleRead-1.19.0-macOS-arm64.zip` from the release, verify it against
  `SHA256SUMS.txt`, and follow the included English/Chinese installation notes.
- Keep a copy of your old app and important reading data before upgrading.
  Libraries, notes and saved tasks are not part of the download. The new
  sandboxed edition can import preview preferences and ask you to authorize
  previous book/task folders through the native file panels.
- This build is ad-hoc signed and is not Apple-notarized. macOS may ask you to
  approve the downloaded application in **System Settings → Privacy & Security**.
  See [Apple's opening instructions](https://support.apple.com/en-us/102445).
- English is the default for a clean installation; 简体中文 is available in Settings.
- For an older Mac, the previous [1.18.1 preview](https://github.com/catlovemiaomiao/WhaleRead/releases/tag/v1.18.1-preview.1)
  remains available. Its feature set and requirements differ from 1.19.

## What's new in 1.19

- Independent model profiles for **translation, review and Ask AI**. Use a local,
  self-hosted or cloud service with a compatible Chat Completions API.
- Built-in **HY-MT2 1.8B Q8 and 7B Q4/Ollama** presets. Weights are downloaded
  separately; the application includes no model.
- API credentials in **macOS Keychain**, separate sending permission for each
  feature, bounded responses and retries, and saved tasks bound to their routes.
- Sandboxed file access, recoverable preview preference import, translation-copy
  export and saved reading positions, annotations and bookmarks.
- Experimental **research PDF batch translation** using a separately configured
  OCR service. Skip repeated page approval, pause and resume, and keep completed
  paragraphs. Drafts remain marked for human review.

The optional OCR service can run PP-OCRv6 small and PP-DocLayoutV3 on your DGX or
another Linux machine. The Mac calls it through a private connection; OCR weights,
server accounts and SSH credentials are not bundled. See [service setup](deployment/ocr_service/README.md).
Research drafts currently translate into Simplified Chinese and export HTML
comparison. Formulas/tables retain image crops; this is not reconstructed PDF
export or a guarantee of scientific translation accuracy.

## Choose your models

Install [Ollama](https://ollama.com/download) for the local presets. Follow
[local model instructions](docs/LOCAL_MODELS.md) to download the official Tencent
GGUF weights and register the exact WhaleRead model tags. Settings can test a
short passage before you translate a book.

For cloud or self-hosted use, add the provider's endpoint and exact Model ID in
Settings, save your own API Key if required, choose a profile for each feature,
and review its destination/material before granting permission. Reading does not
require an AI account. API providers may charge for usage separately.

DeepSeek and Qwen compatible routes were tested with small fictional samples.
That is compatibility evidence, not a ranking of every model or provider.
Quality, language coverage and format adherence vary; human review stays part
of the workflow. Automated post-translation review currently supports Simplified
and Traditional Chinese translations.

## Privacy

The public download contains no API key, private server address, book, reading
history, personal preference or OCR/model weight. Your library, notes and tasks
stay on the Mac. Local HY presets send requests to local Ollama; custom routes
including loopback tunnels need separate consent for translation, review,
Ask AI and OCR. The app displays the material sent by each feature.

Read [the full privacy explanation](docs/PRIVACY.md). Please exclude books,
credentials and personal paths from public Issues.

## Source and build

Application code is licensed under **GPL-3.0-only**. Dependencies and their
notices retain their original license terms. [Build instructions](docs/OPEN_SOURCE_BUILD.md)
and the [corresponding component source](docs/COMPONENT_SOURCE.md) describe the
standalone build, Qt/PySide sources, and local component replacement/re-signing.
The GitHub edition starts without App Store purchase verification.

## Voluntary support

WhaleRead is free. If it helps you, you can optionally support continued work
through Alipay. Donations do not unlock features or buy API usage.

<img src="docs/support/alipay.jpg" width="320" alt="Voluntary Alipay donation QR code supplied by the WhaleRead author">

The payment image is supplied only as the author's voluntary-support destination;
Alipay branding and other third-party marks remain with their respective owners.
The application does not read or store payment account or transaction information.

## Acknowledgements

The original multilingual product and release were built with help from
**GPT-6 Astra**, **GPT-5.6 Sol** and **DeepSeek**. The architecture audit, language
boundaries and evidence-based review were part of the original Astra Challenge
work. New release claims remain separate from the historical 1.18 demo.
Runtime models and service destinations remain under the reader's control.
