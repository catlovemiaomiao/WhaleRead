#!/bin/zsh
set -eu

root_dir="${0:A:h:h}"
ollama_bin="${OLLAMA_BIN:-/Applications/Ollama.app/Contents/Resources/ollama}"

if [[ ! -x "$ollama_bin" ]]; then
  print -u2 -- "未找到 Ollama：$ollama_bin"
  exit 1
fi

# A lightweight installation can register only 1.8B. Resolve FROM independently
# of the template directory, without writing into the user's model folder.
exec "${HY_SETUP_PYTHON:-python3}" - "$root_dir" "$ollama_bin" "$@" <<'PY'
import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile

root, ollama = Path(sys.argv[1]), sys.argv[2]
parser = argparse.ArgumentParser(description='Register downloaded HY-MT2 GGUF files in Ollama')
parser.add_argument('model', nargs='?', choices=('1.8b', '7b', 'all'), default='all')
parser.add_argument('--model-dir', type=Path, default=Path.home() / 'Models/hy-mt2')
args = parser.parse_args(sys.argv[3:])
models = {'1.8b': ('1.8b-q8', 'Hy-MT2-1.8B-Q8_0.gguf'),
          '7b': ('7b-q4', 'Hy-MT2-7B-Q4_K_M.gguf')}
selected = models if args.model == 'all' else {args.model: models[args.model]}
paths = {name: (args.model_dir.expanduser() / spec[1]).resolve() for name, spec in selected.items()}
for path in paths.values():
    if not path.is_file():
        parser.error('GGUF file not found: ' + str(path))
with tempfile.TemporaryDirectory(prefix='whaleread-ollama-') as raw:
    for name, (tag, _) in selected.items():
        template = (root / 'runtime/models' / ('Modelfile.hy-mt2-' + name)).read_text()
        modelfile = Path(raw) / ('Modelfile.' + name)
        modelfile.write_text('FROM ' + json.dumps(str(paths[name]), ensure_ascii=False) + '\n'
                            + template.split('\n', 1)[1])
        subprocess.run([ollama, 'create', 'jingdu-hy-mt2:' + tag, '-f', str(modelfile)], check=True)
subprocess.run([ollama, 'list'], check=True)
PY
