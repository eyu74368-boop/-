"""노래 분석: 박자(BPM)·박·마디·후렴 반복 간격·보컬 구간을 구한다.

외부 라이브러리 없이 NumPy와 FFmpeg만 쓴다(사용자 PC·클라우드 어디서나 같은 결과).
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass, field

import numpy as np

SR = 22050
HOP = 256  # 약 11.6ms


class AudioError(RuntimeError):
    """오디오를 읽거나 분석하지 못했을 때 낸다."""


def load_mono(path: str, sr: int = SR) -> np.ndarray:
    """FFmpeg로 파일을 모노 float32 배열로 읽는다.

    Args:
        path: mp3/flac/wav 등 FFmpeg가 읽는 파일.
        sr: 샘플레이트.
    Returns:
        -1~1 범위의 1차원 배열.
    """
    cmd = ["ffmpeg", "-v", "error", "-i", path, "-ac", "1", "-ar", str(sr), "-f", "f32le", "-"]
    try:
        raw = subprocess.run(cmd, capture_output=True, check=True).stdout
    except FileNotFoundError as e:
        raise AudioError("ffmpeg가 설치되어 있지 않습니다") from e
    except subprocess.CalledProcessError as e:
        raise AudioError(f"오디오를 읽지 못함: {path}\n{e.stderr.decode(errors='ignore')}") from e
    y = np.frombuffer(raw, dtype=np.float32)
    if y.size < sr:
        raise AudioError(f"오디오가 1초보다 짧습니다: {path}")
    return y


def _stft_mag(y: np.ndarray, n_fft: int = 2048, hop: int = HOP) -> np.ndarray:
    """크기 스펙트로그램(프레임 × 주파수)."""
    pad = n_fft // 2
    y = np.pad(y, (pad, pad))
    n = 1 + (len(y) - n_fft) // hop
    idx = np.arange(n_fft)[None, :] + hop * np.arange(n)[:, None]
    win = np.hanning(n_fft).astype(np.float32)
    frames = y[idx] * win
    return np.abs(np.fft.rfft(frames, axis=1)).astype(np.float32)


def _band_matrix(n_bins: int, sr: int, n_bands: int = 40, fmin: float = 40.0) -> np.ndarray:
    """로그 간격 주파수 묶음 행렬(멜 필터 대용)."""
    freqs = np.linspace(0, sr / 2, n_bins)
    edges = np.geomspace(fmin, sr / 2, n_bands + 2)
    m = np.zeros((n_bins, n_bands), np.float32)
    for b in range(n_bands):
        lo, c, hi = edges[b], edges[b + 1], edges[b + 2]
        up = (freqs - lo) / (c - lo)
        down = (hi - freqs) / (hi - c)
        m[:, b] = np.clip(np.minimum(up, down), 0, None)
    return m


def onset_envelope(y: np.ndarray, sr: int = SR) -> tuple[np.ndarray, np.ndarray]:
    """소리가 새로 시작되는 세기(스펙트럼 증가량)와 대역 에너지를 구한다."""
    mag = _stft_mag(y, n_fft=1024)
    bands = np.log1p(10 * (mag @ _band_matrix(mag.shape[1], sr)))
    flux = np.diff(bands, axis=0, prepend=bands[:1])
    env = np.clip(flux, 0, None).sum(axis=1)
    env -= np.convolve(env, np.ones(32) / 32, mode="same")  # 느린 변화 제거
    env = np.clip(env, 0, None)
    if env.max() > 0:
        env /= env.max()
    return env.astype(np.float32), bands


def estimate_bpm(env: np.ndarray, sr: int = SR, lo: float = 70, hi: float = 180) -> float:
    """온셋 세기의 자기상관으로 BPM을 구한다(70~180 사이, 소수 둘째 자리)."""
    fps = sr / HOP
    x = env - env.mean()
    ac = np.fft.irfft(np.abs(np.fft.rfft(x, n=2 * len(x))) ** 2)[: len(x)]
    bpms = np.arange(lo, hi, 0.05)
    lags = 60 * fps / bpms
    score = np.interp(lags, np.arange(len(ac)), ac)
    # 배수 박자(2배·절반)도 함께 보아 한 박 길이를 안정적으로 고른다
    score2 = np.interp(lags * 2, np.arange(len(ac)), ac)
    score4 = np.interp(lags * 4, np.arange(len(ac)), ac)
    total = score + 0.5 * score2 + 0.25 * score4
    # 사람이 느끼는 템포(약 110 BPM) 부근을 약하게 우대
    prior = np.exp(-0.5 * (np.log2(bpms / 110.0) / 0.9) ** 2)
    return float(round(bpms[np.argmax(total * prior)], 2))


def beat_phase(env: np.ndarray, bpm: float, sr: int = SR) -> float:
    """박 격자의 시작 위치(초)를 온셋 세기가 가장 크게 겹치는 곳으로 정한다."""
    fps = sr / HOP
    period = 60 / bpm
    t = np.arange(len(env)) / fps
    best, best_off = -1.0, 0.0
    for off in np.arange(0, period, 0.005):
        grid = np.arange(off, t[-1], period)
        s = np.interp(grid, t, env).sum()
        if s > best:
            best, best_off = s, off
    return float(best_off)


def refine_grid(env: np.ndarray, bpm: float, off: float, dur: float, sr: int = SR, iters: int = 3) -> tuple[float, float]:
    """박 격자 근처의 실제 온셋 봉우리에 직선을 맞춰 BPM과 시작 위치를 다듬는다(긴 곡에서 밀림 방지)."""
    fps = sr / HOP
    period = 60 / bpm
    for _ in range(iters):
        ks, ts = [], []
        w = int(period / 4 * fps)
        thr = np.percentile(env, 90)
        for k, b in enumerate(np.arange(off, dur, period)):
            c = int(b * fps)
            lo, hi = max(c - w, 0), min(c + w + 1, len(env))
            if hi <= lo:
                continue
            j = lo + int(np.argmax(env[lo:hi]))
            if env[j] >= thr:
                ks.append(k)
                ts.append(j / fps)
        if len(ks) < 8:
            break
        a, b0 = np.polyfit(ks, ts, 1)
        if abs(a - period) / period > 0.03:
            break
        period, off = float(a), float(b0 % a)
    return round(60 / period, 3), off


def downbeat_offset(low_env: np.ndarray, beats: np.ndarray, sr: int = SR) -> int:
    """마디 첫 박(0~3번째 박)을 저음(킥) 세기가 가장 큰 박 위치로 정한다."""
    fps = sr / HOP
    t = np.arange(len(low_env)) / fps
    vals = np.interp(beats, t, low_env)
    sums = [vals[k::4].mean() if len(vals[k::4]) else 0 for k in range(4)]
    return int(np.argmax(sums))


@dataclass
class Timeline:
    """곡의 박·마디 시간표."""

    duration: float
    bpm: float
    beat: float
    bar: float
    beats: list[float]
    bars: list[float]
    repeat_lag_bars: int = 0
    repeat_score: float = 0.0
    repeats: list[list[float]] = field(default_factory=list)

    def grid8(self) -> np.ndarray:
        """8분음표 격자(초)."""
        b = np.asarray(self.beats)
        half = (b[1:] + b[:-1]) / 2
        return np.sort(np.concatenate([b, half]))


def bar_features(bands: np.ndarray, bars: list[float], sr: int = SR) -> np.ndarray:
    """마디마다 대역 에너지 평균(정규화)을 구한다."""
    fps = sr / HOP
    feats = []
    for a, b in zip(bars[:-1], bars[1:]):
        i, j = int(a * fps), max(int(b * fps), int(a * fps) + 1)
        v = bands[i:j].mean(axis=0)
        v = v - v.mean()
        n = np.linalg.norm(v)
        feats.append(v / n if n > 0 else v)
    return np.asarray(feats)


def repetition(feats: np.ndarray, min_lag: int = 8, max_lag: int = 48) -> tuple[int, float, list[list[int]]]:
    """마디 특징이 가장 잘 반복되는 간격(마디 수)과 반복 구간을 찾는다.

    후렴은 같은 소리가 반복되므로, 두 후렴 간격(예: 16마디)이 이 값과 맞는지 교차 확인할 수 있다.
    """
    n = len(feats)
    if n < min_lag + 4:
        return 0, 0.0, []
    sim = feats @ feats.T
    best_lag, best = 0, -1.0
    for lag in range(min_lag, min(max_lag, n - 4) + 1):
        d = np.diagonal(sim, offset=lag)
        s = float(np.sort(d)[-max(4, len(d) // 3):].mean())  # 잘 맞는 상위 1/3 평균
        if lag % 4 == 0:
            s += 0.02  # 4마디 단위 우대
        if s > best:
            best, best_lag = s, lag
    d = np.diagonal(sim, offset=best_lag)
    thr = np.percentile(d, 70)
    runs, start = [], None
    for i, v in enumerate(np.append(d, -9)):
        if v >= thr and start is None:
            start = i
        elif v < thr and start is not None:
            if i - start >= 4:
                runs.append([start, start + best_lag, i - start])
            start = None
    return best_lag, round(best, 3), runs


def analyze(path: str, bpm: float | None = None) -> Timeline:
    """곡 전체를 분석해 시간표를 만든다.

    Args:
        path: 원곡 파일.
        bpm: 알고 있으면 지정(자동 추정을 덮어씀).
    """
    y = load_mono(path)
    dur = len(y) / SR
    env, bands = onset_envelope(y)
    tempo = bpm or estimate_bpm(env)
    period = 60 / tempo
    off = beat_phase(env, tempo)
    if not bpm:
        tempo, off = refine_grid(env, tempo, off, dur)
    beats = np.arange(off, dur, period)
    low = np.clip(np.diff(bands[:, :8].sum(axis=1), prepend=0), 0, None)
    k = downbeat_offset(low, beats)
    bar0 = beats[k] - 4 * period * (beats[k] // (4 * period))  # 0초 이전 없이 첫 마디까지 되돌림
    bars = np.arange(bar0, dur, 4 * period)
    beats_all = np.arange(bar0 - period * int(bar0 // period), dur, period)
    tl = Timeline(duration=round(dur, 3), bpm=tempo, beat=round(period, 4), bar=round(4 * period, 4),
                  beats=[round(float(b), 3) for b in beats_all], bars=[round(float(b), 3) for b in bars])
    feats = bar_features(bands, tl.bars + [dur])
    lag, score, runs = repetition(feats)
    tl.repeat_lag_bars, tl.repeat_score = lag, score
    tl.repeats = [[tl.bars[a], tl.bars[b] if b < len(tl.bars) else dur, n] for a, b, n in runs]
    return tl


def vocal_db(path: str, hop_s: float = 0.01) -> tuple[np.ndarray, float]:
    """보컬 분리본의 음량 곡선(dB, 10ms 간격)."""
    y = load_mono(path, sr=16000)
    hop = int(16000 * hop_s)
    n = len(y) // hop
    rms = np.sqrt((y[: n * hop].reshape(n, hop) ** 2).mean(axis=1) + 1e-12)
    return 20 * np.log10(rms), hop_s


def vocal_activity(path: str, hop_s: float = 0.01, rel_db: float = -20.0) -> tuple[np.ndarray, float]:
    """목소리가 나는 구간(불리언)과 dB 곡선. 기준은 상위 2% 음량에서 rel_db 아래."""
    db, hop_s = vocal_db(path, hop_s)
    ref = np.percentile(db, 98)
    act = db > ref + rel_db
    act = np.convolve(act.astype(float), np.ones(5) / 5, mode="same") > 0.5
    return np.stack([act.astype(float), db]), hop_s


def phrases(act_db: np.ndarray, hop_s: float, min_gap: float = 0.06, min_len: float = 0.12,
            dip_db: float = 7.0, dip_win: float = 0.35) -> list[tuple[float, float]]:
    """목소리 구간을 쉼과 '음량이 푹 꺼지는 곳'으로 끊어 소절 후보(시작, 끝) 목록을 만든다.

    랩처럼 쉼 없이 이어 부르는 곳도 음량 골짜기(dip_db 이상 떨어진 곳)에서 나눈다.
    """
    act, db = act_db[0] > 0.5, act_db[1]
    sm = np.convolve(db, np.ones(5) / 5, mode="same")
    w = int(dip_win / hop_s)
    cut = np.zeros(len(act), bool)
    for i in range(w, len(sm) - w):
        if not act[i]:
            continue
        v = sm[i]
        if v == sm[i - 3:i + 4].min():
            if sm[i - w:i].max() - v >= dip_db and sm[i + 1:i + w].max() - v >= dip_db:
                cut[i] = True
    act = act & ~cut
    segs, start = [], None
    for i, v in enumerate(np.append(act, False)):
        if v and start is None:
            start = i
        elif not v and start is not None:
            segs.append([start * hop_s, i * hop_s])
            start = None
    merged: list[list[float]] = []
    for s in segs:
        if merged and s[0] - merged[-1][1] < min_gap and not cut[int(merged[-1][1] / hop_s)]:
            merged[-1][1] = s[1]
        else:
            merged.append(s)
    return [(round(a, 3), round(b, 3)) for a, b in merged if b - a >= min_len]
