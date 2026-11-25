"""가사 읽기와 소절 위치 맞추기.

자막 글자는 원본 가사를 그대로 쓰고, 위치만 보컬 음량(그리고 선택적으로 받아쓰기)으로 찾는다.
"""
from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass, field

import numpy as np

SECTION_RE = re.compile(r"^\[(.+?)\]\s*$")
HANGUL_RE = re.compile(r"[가-힣]")
WORD_RE = re.compile(r"[가-힣]+|[A-Za-z']+|\d+|[^\s]")


@dataclass
class Line:
    """가사 한 줄과 그 시간."""

    idx: int
    section: str
    text: str
    start: float = -1.0
    end: float = -1.0
    words: list[dict] = field(default_factory=list)  # {"w": 글자, "start": 초, "end": 초}
    source: str = ""  # 위치를 정한 방법

    def to_dict(self) -> dict:
        """JSON 저장용 사전."""
        return asdict(self)


def parse(text: str) -> list[Line]:
    """[Verse] 같은 구역 표시가 있는 가사를 줄 목록으로 바꾼다. 빈 줄은 건너뛴다."""
    lines, section, i = [], "", 0
    for raw in text.splitlines():
        s = raw.strip()
        if not s:
            continue
        m = SECTION_RE.match(s)
        if m:
            section = m.group(1).strip()
            continue
        lines.append(Line(idx=i, section=section, text=s))
        i += 1
    if not lines:
        raise ValueError("가사에 내용이 없습니다")
    return lines


def _eng_syllables(w: str) -> int:
    w = w.lower().strip("'")
    if not w:
        return 0
    groups = re.findall(r"[aeiouy]+", w)
    n = len(groups)
    if w.endswith("e") and n > 1 and not w.endswith(("le", "ee")):
        n -= 1
    return max(1, n)


def tokens(text: str) -> list[tuple[str, int]]:
    """줄을 (단어, 음절 수)로 나눈다. 한글은 글자마다 1음절, 문장부호는 0."""
    out = []
    for w in WORD_RE.findall(text):
        if HANGUL_RE.match(w):
            out.append((w, len(w)))
        elif re.match(r"[A-Za-z']+", w):
            out.append((w, _eng_syllables(w)))
        elif w.isdigit():
            out.append((w, len(w)))
        else:
            out.append((w, 0))
    return out


def syllables(text: str) -> int:
    """줄의 음절 수."""
    return max(1, sum(n for _, n in tokens(text)))


def align(lines: list[Line], segs: list[tuple[float, float]], max_lines: int = 3, max_segs: int = 16) -> list[Line]:
    """가사 줄을 보컬 소절 후보에 순서대로 맞춘다(동적 계획법).

    한 줄이 여러 소절에 걸치거나, 한 소절에 여러 줄이 들어가는 경우를 모두 허용한다.
    비용은 '소절 길이 ÷ (음절 수 × 평균 음절 길이)'의 로그 제곱, 건너뛴 소절은 벌점.
    """
    n, m = len(lines), len(segs)
    if m == 0:
        raise ValueError("보컬 구간을 찾지 못했습니다(보컬 분리본 음량을 확인)")
    syl = np.array([syllables(l.text) for l in lines], float)
    sung = sum(b - a for a, b in segs)
    rate = sung / syl.sum()  # 음절 하나의 평균 길이(초)
    gap_cum = np.concatenate([[0.0], np.cumsum([segs[q + 1][0] - segs[q][1] for q in range(m - 1)])])
    INF = 1e18
    cost = np.full((n + 1, m + 1), INF)
    back: dict[tuple[int, int], tuple[int, int, str]] = {}
    cost[0, 0] = 0
    for i in range(n + 1):
        for j in range(m + 1):
            c0 = cost[i, j]
            if c0 >= INF:
                continue
            if j < m:  # 소절 건너뛰기(애드리브·잡음)
                seg_len = segs[j][1] - segs[j][0]
                c = c0 + 1.2 + 2.0 * min(seg_len, 3.0)
                if c < cost[i, j + 1]:
                    cost[i, j + 1] = c
                    back[(i, j + 1)] = (i, j, "skip")
            if i < n:  # 가사 줄 건너뛰기(노래에서 빠진 줄)
                c = c0 + 6.0
                if c < cost[i + 1, j]:
                    cost[i + 1, j] = c
                    back[(i + 1, j)] = (i, j, "drop")
            for k in range(1, max_lines + 1):
                if i + k > n:
                    break
                exp = syl[i:i + k].sum() * rate
                for l in range(1, max_segs + 1):
                    if j + l > m:
                        break
                    a, b = segs[j][0], segs[j + l - 1][1]
                    gaps = gap_cum[j + l - 1] - gap_cum[j]
                    if gaps > 2.5:
                        break
                    dur = b - a - 0.5 * gaps
                    c = c0 + math.log(max(dur, 0.05) / exp) ** 2 + 0.15 * (k - 1) + 0.05 * (l - 1)
                    if c < cost[i + k, j + l]:
                        cost[i + k, j + l] = c
                        back[(i + k, j + l)] = (i, j, "map")
    # 역추적
    i, j = n, m
    while (i, j) != (0, 0):
        pi, pj, op = back[(i, j)]
        if op == "map":
            a, b = segs[pj][0], segs[j - 1][1]
            group = lines[pi:i]
            tot = sum(syllables(g.text) for g in group)
            t = a
            for g in group:
                d = (b - a) * syllables(g.text) / tot
                g.start, g.end, g.source = round(t, 3), round(t + d, 3), "보컬음량"
                t += d
        i, j = pi, pj
    _fill_missing(lines)
    return lines


def _fill_missing(lines: list[Line]) -> None:
    """맞추지 못한 줄은 앞뒤 줄 사이에 끼워 넣는다(자막이 빠지지 않게)."""
    for k, l in enumerate(lines):
        if l.start >= 0:
            continue
        prev_end = next((x.end for x in reversed(lines[:k]) if x.start >= 0), 0.0)
        nxt = next((x.start for x in lines[k + 1:] if x.start >= 0), prev_end + 2.0)
        l.start, l.end, l.source = round(prev_end, 3), round(max(nxt, prev_end + 0.5), 3), "추정(빠짐)"


def snap(lines: list[Line], grid: np.ndarray, tol: float = 0.12) -> None:
    """줄 시작을 가까운 8분음표 격자에 붙인다(차이가 tol초 이내일 때만)."""
    for l in lines:
        k = int(np.argmin(np.abs(grid - l.start)))
        if abs(grid[k] - l.start) <= tol:
            l.start = round(float(grid[k]), 3)
    for a, b in zip(lines, lines[1:]):
        if a.end > b.start:
            a.end = b.start


def split_words(lines: list[Line]) -> None:
    """줄 시간을 음절 수 비례로 단어마다 나눈다(노래방 효과 \\kf용)."""
    for l in lines:
        toks = [(w, max(n, 0)) for w, n in tokens(l.text)]
        tot = sum(n for _, n in toks) or 1
        t, words = l.start, []
        span = max(l.end - l.start, 0.2)
        for w, n in toks:
            d = span * n / tot
            words.append({"w": w, "start": round(t, 3), "end": round(t + d, 3)})
            t += d
        l.words = words


def chorus_check(lines: list[Line], bar: float) -> list[dict]:
    """같은 첫 줄로 시작하는 구역(후렴 등)의 간격을 마디 단위로 계산한다.

    분석에서 얻은 반복 간격(예: 16마디)과 맞으면 위치가 맞다는 교차 확인이 된다.
    """
    firsts: dict[str, list[float]] = {}
    prev_sec = None
    for l in lines:
        if l.section != prev_sec:
            firsts.setdefault(f"{l.section}|{l.text}", []).append(l.start)
            prev_sec = l.section
    out = []
    for key, ts in firsts.items():
        if len(ts) < 2:
            continue
        sec, text = key.split("|", 1)
        for a, b in zip(ts, ts[1:]):
            gap = b - a
            out.append({"구역": sec, "첫줄": text, "앞": round(a, 2), "뒤": round(b, 2),
                        "간격초": round(gap, 2), "간격마디": round(gap / bar, 2)})
    return out
