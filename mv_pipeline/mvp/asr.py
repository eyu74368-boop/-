"""(선택) 로컬 받아쓰기로 줄 시작 위치를 다듬는다.

방법: 짧은 창(기본 2.2초 = 한 마디)을 8분음표(0.28초)씩 옮겨 가며 받아쓴다.
줄의 첫 단어가 창 맨 앞에 온전히 들리다가, 다음 창에서 앞이 잘려 사라지면
'마지막으로 온전했던 창의 시작'을 그 단어의 시작으로 본다.
받아쓰기 글자는 틀릴 수 있으므로 위치 찾기에만 쓰고, 자막은 원본 가사를 쓴다.

받아쓰기 엔진은 명령어로 바꿔 끼운다. 예) Gemma 4 E2B를 돌리는 스크립트:
    --asr-cmd "python C:/tools/gemma_asr.py {wav}"
명령은 표준출력으로 받아쓴 글자만 내면 된다.
"""
from __future__ import annotations

import difflib
import os
import shlex
import subprocess
import tempfile

from .lyrics import Line, tokens


def _cut(src: str, start: float, dur: float, out: str) -> None:
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{max(start, 0):.3f}", "-t", f"{dur:.3f}",
                    "-i", src, "-ac", "1", "-ar", "16000", out], check=True)


def transcribe(cmd: str, wav: str, timeout: int = 120) -> str:
    """명령어 틀의 {wav}를 파일 경로로 바꿔 실행하고 출력 글자를 돌려준다."""
    args = [a.replace("{wav}", wav) for a in shlex.split(cmd, posix=os.name != "nt")]
    try:
        r = subprocess.run(args, capture_output=True, timeout=timeout, check=True)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError) as e:
        raise RuntimeError(f"받아쓰기 실패: {e}") from e
    return r.stdout.decode("utf-8", errors="ignore").strip()


def _norm(w: str) -> str:
    return "".join(ch for ch in w.lower() if ch.isalnum())


def _first_word_present(text: str, word: str) -> bool:
    """받아쓴 글자의 앞 두 단어 안에 목표 단어가 (비슷하게) 있는지."""
    heads = [_norm(w) for w, _ in tokens(text)][:2]
    target = _norm(word)
    return any(difflib.SequenceMatcher(None, h, target).ratio() >= 0.7 for h in heads if h)


def refine(src: str, lines: list[Line], cmd: str, window: float = 2.22, step: float = 0.278,
           search: float = 1.2, log=print) -> int:
    """각 줄 시작을 받아쓰기로 다듬는다. 바뀐 줄 수를 돌려준다."""
    changed = 0
    with tempfile.TemporaryDirectory() as td:
        wav = os.path.join(td, "w.wav")
        for l in lines:
            words = [w for w, n in tokens(l.text) if n > 0]
            if not words:
                continue
            first = words[0]
            t = l.start - search
            last_whole = None
            while t <= l.start + search:
                _cut(src, t, window, wav)
                try:
                    txt = transcribe(cmd, wav)
                except RuntimeError as e:
                    log(f"  줄 {l.idx}: {e}")
                    break
                if _first_word_present(txt, first):
                    last_whole = t
                elif last_whole is not None:
                    break  # 앞이 잘려 사라짐 → 직전 창 시작이 단어 시작
                t += step
            if last_whole is not None and abs(last_whole - l.start) > 0.05:
                l.start, l.source = round(max(last_whole, 0), 3), "받아쓰기"
                changed += 1
    return changed
