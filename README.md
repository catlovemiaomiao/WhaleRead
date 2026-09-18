<p align="center">
  <img src="assets/whaleread-icon.png" width="128" alt="WhaleRead icon">
</p>

# WhaleRead

WhaleRead is a local-first macOS reading workspace for translating TXT, Markdown, and EPUB files, reading the source and translation side by side, reviewing awkward passages, and asking AI about the text without uploading an entire personal library.

**Website and product demo:** [whaleread-astra.kunyu575.chatgpt.site](https://whaleread-astra.kunyu575.chatgpt.site/)

## Download the macOS preview

[Download the latest WhaleRead release](https://github.com/catlovemiaomiao/WhaleRead/releases/latest)

- Apple silicon Mac (M1 or newer)
- macOS 13 or later
- English is the default on a clean installation; 简体中文 is available in Settings
- The app is about 185 MB after extraction
- The optional on-device 7B model is downloaded separately and needs about 4.62 GB

This preview is ad-hoc signed and has not yet been notarised by Apple. On first launch, Control-click or right-click `鲸读.app`, choose **Open**, and confirm. If macOS still blocks it, use **System Settings → Privacy & Security → Open Anyway**.

## Add the optional on-device model

1. Install [Ollama](https://ollama.com/download).
2. Open `Install WhaleRead 7B Model.command` from the downloaded package.
3. In WhaleRead, open **Settings → On-device Hy-MT2 7B → Refresh**.

The installer downloads Tencent's official [Hy-MT2 7B Q4_K_M model](https://huggingface.co/tencent/Hy-MT2-7B-GGUF). The model is not bundled with the application.

## Model support

Based on our testing, Hy-MT2 currently provides the best fit for WhaleRead's long-form translation workflow and is the only model family fully adapted and validated in this preview. Other models have not yet been optimised for WhaleRead. We plan to expand model support based on user feedback.

根据目前的测试结果，Hy-MT2 最适合鲸读的长文本翻译流程，也是本预览版唯一完成完整适配与验证的模型系列。其他模型暂未针对鲸读进行适配，后续将根据用户反馈逐步开放更多模型支持。

## Privacy boundary

- The download contains no API key, private endpoint, book, cache, reading history, or personal setting.
- Translation with the optional 7B model runs on the Mac through Ollama.
- Ask AI is optional. It uses only the OpenAI-compatible endpoint and credential that the user configures locally.
- WhaleRead shows the source passage and surrounding context before an Ask AI request is sent.
- Review suggestions remain separate from the reading edition until the user applies them.

## Preview notes

- Keep the original book files and important notes.
- Model output may contain errors; WhaleRead keeps review evidence and asks for human confirmation before applying changes.
- Automated post-translation review currently supports Simplified and Traditional Chinese editions. Translation, reading, and manual passage editing remain available for other target languages.

## Acknowledgements

WhaleRead 1.18 was built with help from **GPT-6 Astra**, **GPT-5.6 Sol**, and **DeepSeek**. Runtime translation remains controlled by the user and the selected model destination.

