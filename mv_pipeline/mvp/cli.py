"""명령줄 진입점.

  python -m mvp analyze 노래.mp3 --lyrics 가사.txt [--vocals 보컬.mp3] -o 작업폴더
  python -m mvp sheet 클립.mp4 [-o 시트.jpg]
  python -m mvp subs 작업폴더 [--font "Griun Cocochoitoon"]     # lyrics_timed.json을 고친 뒤 자막만 다시
  python -m mvp render 작업폴더 [--preview] [--fontsdir 글꼴폴더]
  python -m mvp review 작업폴더
  python -m mvp all 노래.mp3 --lyrics 가사.txt --vocals 보컬.mp3 -o 작업폴더
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

from . import audio, edl, lyrics, scenes, sheets, subs
from .render import render


def _p(*a):
    print(*a, flush=True)


def _cfg_path(work: str) -> str:
    return os.path.join(work, "project.json")


def load_cfg(work: str) -> dict:
    """작업 폴더 설정(project.json)을 읽는다."""
    try:
        with open(_cfg_path(work), encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        sys.exit(f"{work}에 project.json이 없습니다. 먼저 analyze를 실행하세요.")


def _timeline(work: str) -> audio.Timeline:
    with open(os.path.join(work, "timeline.json"), encoding="utf-8") as f:
        return audio.Timeline(**json.load(f))


def _lines(work: str) -> list[lyrics.Line]:
    with open(os.path.join(work, "lyrics_timed.json"), encoding="utf-8") as f:
        return [lyrics.Line(**d) for d in json.load(f)]


def cmd_analyze(a) -> None:
    """노래 분석 → 시간표·가사 위치·자막·배치표 초안·보고서."""
    os.makedirs(a.out, exist_ok=True)
    t0 = time.time()
    _p("1) 박자 분석")
    tl = audio.analyze(a.song, bpm=a.bpm)
    _p(f"   BPM {tl.bpm} · 한 박 {tl.beat:.3f}s · 한 마디 {tl.bar:.3f}s · 마디 {len(tl.bars)}개 · 반복 간격 {tl.repeat_lag_bars}마디")
    with open(a.lyrics, encoding="utf-8-sig") as f:
        lines = lyrics.parse(f.read())
    _p(f"2) 가사 위치 ({len(lines)}줄)")
    act, hop = audio.vocal_activity(a.vocals or a.song)
    segs = audio.phrases(act, hop)
    _p(f"   보컬 소절 후보 {len(segs)}개{'(분리본 없음: 원곡 음량 사용, 정확도 낮음)' if not a.vocals else ''}")
    lyrics.align(lines, segs)
    if a.asr_cmd:
        from .asr import refine
        _p("   받아쓰기로 다듬는 중")
        _p(f"   받아쓰기로 바뀐 줄 {refine(a.vocals or a.song, lines, a.asr_cmd, window=tl.bar, step=tl.beat / 2, log=_p)}개")
    lyrics.snap(lines, tl.grid8())
    lyrics.split_words(lines)
    checks = lyrics.chorus_check(lines, tl.bar)
    with open(os.path.join(a.out, "timeline.json"), "w", encoding="utf-8") as f:
        json.dump(tl.__dict__, f, ensure_ascii=False, indent=1)
    with open(os.path.join(a.out, "lyrics_timed.json"), "w", encoding="utf-8") as f:
        json.dump([l.to_dict() for l in lines], f, ensure_ascii=False, indent=1)
    with open(os.path.join(a.out, "lyrics.ass"), "w", encoding="utf-8") as f:
        f.write(subs.build_ass(lines, font=a.font))
    edl_path = os.path.join(a.out, "edl.csv")
    if os.path.exists(edl_path) and not a.new_edl:
        _p("   edl.csv가 이미 있어 그대로 둠(--new-edl로 새로 만들기)")
    else:
        edl.save(edl_path, edl.skeleton(tl, lines))
    cfg = {"song": os.path.abspath(a.song), "vocals": os.path.abspath(a.vocals) if a.vocals else "",
           "lyrics": os.path.abspath(a.lyrics), "font": a.font, "style": a.style}
    with open(_cfg_path(a.out), "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=1)
    write_report(a.out, tl, lines, checks, segs)
    _p(f"끝 ({time.time() - t0:.0f}초) → {a.out}")


def write_report(work: str, tl: audio.Timeline, lines: list[lyrics.Line], checks: list[dict], segs) -> None:
    """휴대폰에서 읽을 분석 보고서(좁은 목록 형식)."""
    m = [f"# 노래 분석 보고서", "",
         f"- 길이 {tl.duration:.1f}초",
         f"- BPM {tl.bpm} (한 박 {tl.beat:.3f}초, 한 마디 {tl.bar:.3f}초)",
         f"- 첫 마디 시작 {tl.bars[0]:.3f}초, 마디 {len(tl.bars)}개",
         f"- 소리 반복 간격 {tl.repeat_lag_bars}마디 = {tl.repeat_lag_bars * tl.bar:.2f}초 (유사도 {tl.repeat_score})",
         f"- 보컬 소절 후보 {len(segs)}개", "", "## 후렴 교차 확인", ""]
    if checks:
        for c in checks:
            ok = "맞음" if tl.repeat_lag_bars and abs(c["간격마디"] / tl.repeat_lag_bars - round(c["간격마디"] / tl.repeat_lag_bars)) < 0.1 else "확인 필요"
            m.append(f"- [{c['구역']}] {c['앞']}s → {c['뒤']}s : {c['간격초']}초 = {c['간격마디']}마디 ({ok})")
    else:
        m.append("- 반복되는 구역 없음")
    m += ["", "## 소절 시간표", ""]
    sec = None
    for l in lines:
        if l.section != sec:
            m += ["", f"**[{l.section}]**"]
            sec = l.section
        bar_no = int((l.start - tl.bars[0]) // tl.bar) + 1
        beat_no = int(((l.start - tl.bars[0]) % tl.bar) // tl.beat) + 1
        flag = "" if l.source == "보컬음량" or l.source == "받아쓰기" else f" ⚠{l.source}"
        m.append(f"- {l.start:6.2f}s (마디 {bar_no}:{beat_no}) {l.text}{flag}")
    with open(os.path.join(work, "report.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(m) + "\n")


def cmd_subs(a) -> None:
    """lyrics_timed.json(손으로 고친 시간 포함)으로 자막만 다시 만든다."""
    cfg = load_cfg(a.work)
    lines = _lines(a.work)
    if a.resplit:
        lyrics.split_words(lines)
    shots = edl.load(os.path.join(a.work, "edl.csv"), _timeline(a.work))
    for l in lines:  # 배치표에서 top=1인 장면의 자막은 위로
        mid = (l.start + l.end) / 2
        l.top = any(s.top and s.start <= mid < s.end for s in shots)
    with open(os.path.join(a.work, "lyrics.ass"), "w", encoding="utf-8") as f:
        f.write(subs.build_ass(lines, font=a.font or cfg.get("font", "Griun Cocochoitoon")))
    _p("자막 다시 만듦 → lyrics.ass")


def cmd_render(a) -> None:
    """배치표대로 렌더링한다(빈 장면 목록도 함께 갱신)."""
    cfg = load_cfg(a.work)
    tl = _timeline(a.work)
    shots = edl.load(os.path.join(a.work, "edl.csv"), tl)
    with open(os.path.join(a.work, "scenes.md"), "w", encoding="utf-8") as f:
        f.write(scenes.empty_list(shots, cfg.get("style", "")))
    a.resplit = False
    a.font = None
    cmd_subs(a)
    out = a.output or os.path.join(a.work, "mv_preview.mp4" if a.preview else "mv.mp4")
    t0 = time.time()
    _p(f"렌더링 ({len(shots)}장면{', 미리보기' if a.preview else ''})")
    render(shots, tl, cfg["song"], os.path.join(a.work, "lyrics.ass"), out, a.work, preview=a.preview,
           fontsdir=a.fontsdir, log=_p)
    _p(f"끝 ({time.time() - t0:.0f}초) → {out}")


def cmd_review(a) -> None:
    """완성본 검수 시트를 만든다."""
    video = a.video or next((os.path.join(a.work, v) for v in ("mv.mp4", "mv_preview.mp4")
                             if os.path.exists(os.path.join(a.work, v))), None)
    if not video:
        sys.exit("렌더링된 영상이 없습니다")
    out = os.path.join(a.work, "review.jpg")
    sheets.review_sheet(video, _lines(a.work), out)
    _p(f"검수 시트 → {out}")


def cmd_sheet(a) -> None:
    """클립 썸네일 시트."""
    out = a.out or os.path.splitext(a.clip)[0] + "_sheet.jpg"
    sheets.clip_sheet(a.clip, out, every=a.every)
    _p(f"시트 → {out}")


def main(argv=None) -> None:
    """명령 해석."""
    ap = argparse.ArgumentParser(prog="mvp", description="노래 mp3 + 가사 → 뮤직비디오")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add_analyze(p):
        p.add_argument("song")
        p.add_argument("--lyrics", required=True)
        p.add_argument("--vocals", help="보컬 분리본(demucs vocals). 있으면 가사 위치가 훨씬 정확")
        p.add_argument("-o", "--out", required=True)
        p.add_argument("--bpm", type=float)
        p.add_argument("--asr-cmd", help="받아쓰기 명령 틀. {wav} 자리에 2.2초 파일 경로가 들어감")
        p.add_argument("--font", default="Griun Cocochoitoon")
        p.add_argument("--style", default="", help="빈 장면 프롬프트 뒤에 붙일 화풍(영어)")
        p.add_argument("--new-edl", action="store_true")

    add_analyze(sub.add_parser("analyze"))
    p = sub.add_parser("subs")
    p.add_argument("work")
    p.add_argument("--font")
    p.add_argument("--resplit", action="store_true", help="줄 시간만 고쳤을 때 단어 시간을 다시 나눔")
    p = sub.add_parser("render")
    p.add_argument("work")
    p.add_argument("--preview", action="store_true")
    p.add_argument("--fontsdir")
    p.add_argument("--output")
    p = sub.add_parser("review")
    p.add_argument("work")
    p.add_argument("--video")
    p = sub.add_parser("sheet")
    p.add_argument("clip")
    p.add_argument("-o", "--out")
    p.add_argument("--every", type=float, default=0.5)
    p = sub.add_parser("all")
    add_analyze(p)
    p.add_argument("--preview", action="store_true")
    p.add_argument("--fontsdir")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "analyze":
            cmd_analyze(a)
        elif a.cmd == "subs":
            cmd_subs(a)
        elif a.cmd == "render":
            cmd_render(a)
        elif a.cmd == "review":
            cmd_review(a)
        elif a.cmd == "sheet":
            cmd_sheet(a)
        elif a.cmd == "all":
            cmd_analyze(a)
            a.work, a.output, a.video = a.out, None, None
            cmd_render(a)
            cmd_review(a)
    except (audio.AudioError, ValueError, RuntimeError) as e:
        sys.exit(f"오류: {e}")


if __name__ == "__main__":
    main()
