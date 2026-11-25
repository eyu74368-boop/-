"""ASS 노래방 자막 만들기(\\kf로 글자가 차오르는 효과)."""
from __future__ import annotations

from .lyrics import Line


def _ts(t: float) -> str:
    t = max(t, 0)
    h, r = divmod(t, 3600)
    m, s = divmod(r, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def build_ass(lines: list[Line], font: str = "Griun Cocochoitoon", size: int = 72,
              fill: str = "&H0000E5FF", base: str = "&H00FFFFFF", outline: str = "&H00301A10",
              margin_v: int = 70, lead: float = 0.35, tail: float = 0.4, align: int = 2) -> str:
    """가사 줄 목록을 ASS 문자열로 만든다.

    Args:
        font: 글꼴 이름(PC에 설치된 이름과 같아야 함). 사용자 기본은 Griun 코코초이툰.
        fill: 차오른 글자 색(&HAABBGGRR), base: 차오르기 전 색.
        lead: 노래보다 먼저 자막을 띄우는 시간(초). tail: 끝난 뒤 남기는 시간.
        align: 2=아래 가운데, 8=위 가운데(얼굴을 가리면 장면별로 바꿈).
    """
    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Lyric,{font},{size},{fill},{base},{outline},&H80000000,0,0,0,0,100,100,1,0,1,5,2,{align},80,80,{margin_v},1
Style: LyricTop,{font},{size},{fill},{base},{outline},&H80000000,0,0,0,0,100,100,1,0,1,5,2,8,80,80,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    ev = []
    for i, l in enumerate(lines):
        if l.start < 0:
            continue
        nxt = lines[i + 1].start if i + 1 < len(lines) else l.end + tail
        start = min(max(l.start - lead, lines[i - 1].end if i else 0, 0), l.start)
        end = min(l.end + tail, max(nxt - 0.02, l.end))
        parts = []
        cs_lead = int(round((l.start - start) * 100))
        if cs_lead > 0:
            parts.append(f"{{\\k{cs_lead}}}")
        words = l.words or [{"w": l.text, "start": l.start, "end": l.end}]
        prev_end = l.start
        for k, w in enumerate(words):
            cs = int(round((w["end"] - prev_end) * 100))
            prev_end = w["end"]
            sep = " " if k + 1 < len(words) and _needs_space(l.text, w, words[k + 1]) else ""
            parts.append(f"{{\\kf{max(cs, 1)}}}{_esc(w['w'])}{sep}")
        style = "LyricTop" if getattr(l, "top", False) else "Lyric"
        ev.append(f"Dialogue: 0,{_ts(start)},{_ts(end)},{style},,0,0,0,,{{\\fad(120,150)}}" + "".join(parts))
    return head + "\n".join(ev) + "\n"


def _esc(s: str) -> str:
    return s.replace("{", "(").replace("}", ")").replace("\\", "/")


def _needs_space(text: str, w: dict, nxt: dict) -> bool:
    """원문에서 두 단어 사이에 공백이 있었는지 확인한다."""
    a, b = w["w"], nxt["w"]
    pos = text.find(a + " " + b)
    if pos >= 0:
        return True
    return text.find(a + b) < 0 and b not in ",.!?" and a not in "("
