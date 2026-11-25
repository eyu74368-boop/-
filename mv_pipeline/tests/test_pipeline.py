"""합성 신호로 파이프라인 핵심 기능을 시험한다(실행: python -m pytest mv_pipeline/tests)."""
import os
import subprocess
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from mvp import audio, edl, lyrics, subs  # noqa: E402


def _click_track(path, bpm=108.0, dur=30.0, sr=22050):
    """킥(마디 첫 박 강조) 클릭 트랙을 wav로 만든다."""
    y = np.zeros(int(sr * dur), np.float32)
    period = 60 / bpm
    t = np.arange(int(0.03 * sr)) / sr
    for k, b in enumerate(np.arange(0.2, dur - 0.1, period)):
        amp = 1.0 if k % 4 == 0 else 0.4
        freq = 60 if k % 4 == 0 else 1500
        s = int(b * sr)
        y[s:s + len(t)] += amp * np.sin(2 * np.pi * freq * t) * np.exp(-t * 80)
    raw = (np.clip(y, -1, 1) * 32767).astype("<i2").tobytes()
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "s16le", "-ar", str(sr), "-ac", "1", "-i", "-", path],
                   input=raw, check=True)


@pytest.fixture(scope="module")
def click(tmp_path_factory):
    p = str(tmp_path_factory.mktemp("a") / "click.wav")
    _click_track(p)
    return p


def test_bpm_and_bars(click):
    tl = audio.analyze(click)
    assert abs(tl.bpm - 108) < 0.6
    assert abs(tl.bar - 2.222) < 0.02
    assert abs(tl.bars[0] - 0.2) < 0.05  # 첫 마디 = 강한 킥


def test_parse_and_syllables():
    ls = lyrics.parse("[Verse]\n눈부시게, 나를 다시 써\n\n[Chorus]\nEvery eye, we shine\n")
    assert [l.section for l in ls] == ["Verse", "Chorus"]
    assert lyrics.syllables(ls[0].text) == 9
    assert lyrics.syllables("Every eye") == 4


def test_align_order_and_ass():
    ls = lyrics.parse("[A]\n하나 둘 셋 넷\n다섯 여섯\n일곱\n")
    segs = [(1.0, 2.6), (3.0, 4.6), (5.0, 5.8)]
    lyrics.align(ls, segs)
    assert [round(l.start, 1) for l in ls] == [1.0, 3.0, 5.0]
    lyrics.split_words(ls)
    text = subs.build_ass(ls)
    assert text.count("Dialogue:") == 3 and "\\kf" in text


def test_edl_bar_time(tmp_path):
    tl = audio.Timeline(duration=20, bpm=108, beat=0.5556, bar=2.2222, beats=[], bars=[0.2 + 2.2222 * k for k in range(8)])
    p = tmp_path / "edl.csv"
    p.write_text("start,clip,in,out,transition\n0,,,,\n3:1,a.mp4,0,2,0.3\n", encoding="utf-8")
    shots = edl.load(str(p), tl)
    assert shots[1].start == pytest.approx(0.2 + 2 * 2.2222, abs=1e-3)
    assert shots[0].end == shots[1].start and shots[-1].end == 20
