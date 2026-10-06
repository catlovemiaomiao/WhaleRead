#!/bin/zsh
set -eu

model_source="${WHALEREAD_MODEL_SOURCE:-hf.co/tencent/Hy-MT2-7B-GGUF:Q4_K_M}"
model_name="${WHALEREAD_MODEL_NAME:-jingdu-hy-mt2:7b-q4}"

if command -v ollama >/dev/null 2>&1; then
  ollama_bin="$(command -v ollama)"
elif [[ -x /Applications/Ollama.app/Contents/Resources/ollama ]]; then
  ollama_bin=/Applications/Ollama.app/Contents/Resources/ollama
else
  print "Ollama is required. Download it from https://ollama.com/download"
  print "需要先安装 Ollama：https://ollama.com/download"
  print ""
  read "?Press Return to close / 按回车键关闭："
  exit 1
fi

if ! "$ollama_bin" list >/dev/null 2>&1; then
  open -a Ollama >/dev/null 2>&1 || true
  print "Starting Ollama… / 正在启动 Ollama……"
  ready=0
  for _ in {1..30}; do
    if "$ollama_bin" list >/dev/null 2>&1; then
      ready=1
      break
    fi
    sleep 1
  done
  if [[ "$ready" != 1 ]]; then
    print "Ollama did not start. Open Ollama, then run this installer again."
    print "Ollama 未能启动。请先打开 Ollama，再重新运行本安装程序。"
    read "?Press Return to close / 按回车键关闭："
    exit 1
  fi
fi

print "Downloading the official Tencent Hy-MT2 7B Q4 model (about 4.62 GB)."
print "正在下载腾讯官方 Hy-MT2 7B Q4 模型（约 4.62 GB）。"
"$ollama_bin" pull "$model_source"

work_dir="$(mktemp -d -t whaleread-model)"
trap 'rm -rf "$work_dir"' EXIT
cat > "$work_dir/Modelfile" <<EOF
FROM $model_source
TEMPLATE {{ .Prompt }}
PARAMETER temperature 0.7
PARAMETER top_p 0.6
PARAMETER top_k 20
PARAMETER repeat_penalty 1.05
PARAMETER num_ctx 16384
PARAMETER num_predict 4096
EOF

"$ollama_bin" create "$model_name" -f "$work_dir/Modelfile"
"$ollama_bin" show "$model_name" >/dev/null

print ""
print "WhaleRead 7B is ready. Open WhaleRead → Settings → Translation model → Hy-MT2 7B."
print "鲸读 7B 已就绪。打开鲸读 → 设置 → 翻译模型 → Hy-MT2 7B。"
read "?Press Return to close / 按回车键关闭："
