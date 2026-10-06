"""One bounded request; credentials and book text arrive over stdin, never argv."""
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
from provider_transport import chat_request, ProviderError


MAX_CHUNKS = 2

# The continuation and truncation wording arrives from the desktop, already
# resolved in the frozen answer language of the request.  The Chinese defaults
# keep an older desktop working unchanged.
CONTINUE_PROMPT_ZH = (
    '上一条回答因输出长度上限被截断。请从截断处继续，只输出尚未完成的后续内容，'
    '不要重复、不要加开场白，并在本条内完成回答。'
)
CONTINUE_PROMPT_EN = (
    'The previous answer was cut off by the output length limit. Continue from the cut-off point, '
    'output only what is still missing, do not repeat, do not add an opening line, and finish the '
    'answer within this reply.'
)
TRUNCATED_ZH = '两次输出均达到长度上限，回答仍未完整，未自动加入随笔。'
TRUNCATED_EN = ('Both replies reached the output length limit, so the answer is still incomplete '
                'and was not added to your notes.')


def request_locale(request) -> str:
    """The answer locale frozen by the desktop, defaulting to Chinese."""
    value = str(request.get('answer_locale') or '').strip().casefold().replace('_', '-')
    return 'en' if value.startswith('en') else 'zh-CN'


def continuation_prompt(request) -> str:
    """The continuation instruction, preferring the desktop's frozen wording."""
    supplied = str(request.get('continuation_prompt') or '')
    if supplied:
        return supplied
    return CONTINUE_PROMPT_EN if request_locale(request) == 'en' else CONTINUE_PROMPT_ZH


def truncated_suffix(request) -> str:
    supplied = str(request.get('truncated_suffix') or '')
    if supplied:
        return supplied
    return TRUNCATED_EN if request_locale(request) == 'en' else TRUNCATED_ZH


def merge_continuation(answer, continuation):
    """Join a continuation while removing a short repeated boundary."""
    for size in range(min(len(answer), len(continuation), 500), 0, -1):
        if answer.endswith(continuation[:size]):
            return answer + continuation[size:]
    return answer + continuation


try:
    request = json.load(sys.stdin)
    messages = list(request['messages'])
    answer = ''
    truncated = False
    for _ in range(MAX_CHUNKS):
        chunk, finish, _elapsed = chat_request(
            request['endpoint'], request['model'], request['key'], messages,
            timeout=90, disable_thinking=request.get('disable_thinking'))
        answer = merge_continuation(answer, chunk)
        truncated = finish == 'length'
        if not truncated:
            break
        messages += [
            {'role': 'assistant', 'content': chunk},
            {'role': 'user', 'content': continuation_prompt(request)},
        ]
    if truncated:
        answer += '\n\n[' + truncated_suffix(request) + ']'
    print(json.dumps({'answer':answer, 'truncated':truncated}, ensure_ascii=False))
except Exception as exc:
    # Do not echo remote response bodies, prompts or credentials.
    detail = str(exc) if isinstance(exc, ProviderError) else type(exc).__name__
    print(json.dumps({'error':detail + ': 请求失败，请检查服务、地址和凭据。'}, ensure_ascii=False))
    sys.exit(1)
