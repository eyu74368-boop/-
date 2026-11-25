"""장면 배치표(EDL). CSV 한 줄 = 장면 하나.

열: start(곡 위치 초 또는 '마디:박' 예 12:1), clip(파일, 비우면 빈 장면), in, out(클립 구간 초),
    transition(앞 장면에서 넘어오는 섞기 길이 초), speed(비우면 in~out을 자리 길이에 맞춤),
    top(1이면 자막을 위로), prompt(빈 장면 생성용 영어 지시문), note(메모)
장면을 바꿀 때는 이 파일의 한 줄만 고치고 다시 렌더링하면 된다.
"""
from __future__ import annotations

import csv
import os
from dataclasses import dataclass

from .audio import Timeline
from .lyrics import Line

FIELDS = ["start", "clip", "in", "out", "transition", "speed", "top", "prompt", "note"]


@dataclass
class Shot:
    """배치표 한 줄."""

    start: float
    clip: str = ""
    cin: float = 0.0
    cout: float = 0.0
    transition: float = 0.0
    speed: float = 0.0
    top: bool = False
    prompt: str = ""
    note: str = ""
    end: float = 0.0  # 다음 장면 시작(계산값)

    @property
    def length(self) -> float:
        """곡에서 차지하는 길이(초)."""
        return self.end - self.start


def parse_time(v: str, tl: Timeline) -> float:
    """'12.5'(초) 또는 '8:3'(8번째 마디 3번째 박, 1부터 셈)을 초로 바꾼다."""
    v = str(v).strip()
    if ":" in v:
        bar, beat = v.split(":")
        b = int(bar) - 1
        if not 0 <= b < len(tl.bars):
            raise ValueError(f"마디 번호가 범위 밖: {v}")
        return round(tl.bars[b] + (float(beat) - 1) * tl.beat, 3)
    return float(v)


def _f(v, default=0.0) -> float:
    try:
        return float(v) if v is not None and str(v).strip() != "" else default
    except (TypeError, ValueError):
        return default


def load(path: str, tl: Timeline) -> list[Shot]:
    """배치표를 읽어 시작 순으로 정렬하고 각 장면의 끝을 채운다."""
    shots = []
    with open(path, encoding="utf-8-sig", newline="") as f:
        for n, row in enumerate(csv.DictReader(f), start=2):
            if not (row.get("start") or "").strip() or row["start"].lstrip().startswith("#"):
                continue
            try:
                shots.append(Shot(start=parse_time(row["start"], tl), clip=(row.get("clip") or "").strip(),
                                  cin=_f(row.get("in")), cout=_f(row.get("out")),
                                  transition=_f(row.get("transition")), speed=_f(row.get("speed")),
                                  top=(row.get("top") or "").strip() in ("1", "y", "예"),
                                  prompt=(row.get("prompt") or "").strip(), note=(row.get("note") or "").strip()))
            except (ValueError, KeyError) as e:
                raise ValueError(f"{path} {n}번째 줄 오류: {e}") from e
    if not shots:
        raise ValueError("배치표가 비어 있습니다")
    shots.sort(key=lambda s: s.start)
    if 0 < shots[0].start < 0.5:
        shots[0].start = 0.0  # 첫 마디가 0초 근처면 0초부터
    elif shots[0].start > 0:
        shots.insert(0, Shot(start=0.0, note="자동: 곡 시작"))
    for a, b in zip(shots, shots[1:]):
        a.end = b.start
    shots[-1].end = tl.duration
    base = os.path.dirname(os.path.abspath(path))
    for s in shots:
        if s.clip and not os.path.isabs(s.clip):
            s.clip = os.path.join(base, s.clip)
    return shots


def save(path: str, shots: list[Shot]) -> None:
    """배치표를 CSV로 저장한다(엑셀에서 바로 열림)."""
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for s in shots:
            w.writerow({"start": f"{s.start:.3f}", "clip": s.clip, "in": s.cin or "", "out": s.cout or "",
                        "transition": s.transition or "", "speed": s.speed or "", "top": "1" if s.top else "",
                        "prompt": s.prompt, "note": s.note})


def skeleton(tl: Timeline, lines: list[Line], bars_per_shot: int = 2) -> list[Shot]:
    """빈 배치표 초안: 마디 경계마다 장면을 두고, 구역이 바뀌는 마디에서는 반드시 장면을 바꾼다."""
    change_bars = set()
    prev = None
    for l in lines:
        if l.section != prev:
            change_bars.add(int(round((l.start - tl.bars[0]) / tl.bar)))
            prev = l.section
    shots, since = [], bars_per_shot
    for k, t in enumerate(tl.bars):
        if k in change_bars or since >= bars_per_shot:
            sung = [l for l in lines if t <= l.start < t + tl.bar * bars_per_shot]
            sec = sung[0].section if sung else ""
            shots.append(Shot(start=t, transition=0.0 if k in change_bars else 0.25,
                              note=f"{sec} | " + " / ".join(l.text for l in sung)[:120] if sung else "간주"))
            since = 0
        since += 1
    return shots
