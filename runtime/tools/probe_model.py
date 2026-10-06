"""One-shot model probe used by the settings panel; source arrives over stdin."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import translate_range as engine
from task_config import detect_source_language
from provider_transport import completion_url


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-base", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--auth-path", required=True)
    parser.add_argument("--auth-provider", default="none")
    parser.add_argument("--request-timeout", type=int, default=180)
    args = parser.parse_args()
    try:
        source = sys.stdin.read(3001).strip()
        if not source:
            raise ValueError("没有收到试译原文")
        if len(source) > 3000:
            raise ValueError("试译原文超过 3000 个字符")
        try:
            source_language = detect_source_language(source)
        except ValueError:
            source_language = "外语"
        prompt = (
            f"将以下{source_language}翻译为简体中文，只输出翻译结果，不要额外解释：\n\n"
            + source
        )
        base = args.api_base.rstrip("/")
        url = completion_url(base)
        key = engine.load_key(Path(args.auth_path).expanduser(), args.auth_provider)
        translation, elapsed = engine.translate_once(
            url, args.model, key, prompt, int(args.request_timeout)
        )
        print(
            json.dumps(
                {"ok": True, "translation": translation, "seconds": round(elapsed, 3)},
                ensure_ascii=False,
            ),
            flush=True,
        )
        return 0
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)[:500]}, ensure_ascii=False), flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
