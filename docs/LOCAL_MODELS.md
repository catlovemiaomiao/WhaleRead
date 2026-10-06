# 本机 HY-MT2

1.19 正式开源版支持 Ollama 上的 **HY-MT2 1.8B Q8** 和 **7B Q4**。设置 → 模型与 API → 翻译模型中分别显示 `Hy-MT2 1.8B · Ollama`、`Hy-MT2 7B · Ollama`。默认仍为 7B；旧预览版保存的 `local_1_8b` 任务继续绑定原来的 1.8B 模型。

模型在本机 Ollama 中运行，使用 `http://127.0.0.1:11434/v1`，不需要 API Key。应用本身不含模型权重，也不会代你安装或启动 Ollama。请先启动 Ollama，并从 [腾讯官方 GGUF 仓库](https://huggingface.co/tencent/Hy-MT2-1.8B-GGUF) 下载 `Hy-MT2-1.8B-Q8_0.gguf`（约 1.91 GB）。模型内存占用还包括运行时和上下文，不能用文件大小代替内存需求。

把权重放入 `~/Models/hy-mt2/`，在源码目录中执行以下命令注册 1.8B；此步骤不要求同时下载 7B。注册脚本需要 Python 3 和 Ollama。

```sh
scripts/register_local_models.sh 1.8b
```

权重位于其他目录时：

```sh
scripts/register_local_models.sh 1.8b --model-dir "/path/to/models"
```

注册后的模型名是 `jingdu-hy-mt2:1.8b-q8`。选中该预设后刷新模型状态，再做一次短文试译。应用会检测这个准确的模型名；仅安装 7B 不会把 1.8B 显示为已就绪。使用自己的模型名、量化版本或兼容服务时，可添加自定义模型档案，填写该服务的准确 Model ID。

1.8B 新译本默认单路请求，每块最多 1,400 个原文字符、8 段；7B 保持原有的 2,400 字符、12 段。较长的单个段落可能仍超过字符目标，应用不会为凑长度截断段落。已有断点沿用该任务原先的规则和模型；比较 1.8B、7B 或云端模型时请新建译本。校阅和问 AI 也能选择该档案，但本次 1.8B 验证范围是翻译，不据此承诺通用问答或校阅质量。
