"""합성: 배치표대로 클립을 1920×1080·24fps로 풀고, NumPy로 장면 전환을 섞어 H.264로 굽는다.

클립 소리는 버리고 원곡만 AAC로 넣는다. 자막은 ASS를 그대로 입힌다.
클립이 비어 있는 장면은 '빈 장면' 화면(구역 색 + 박자에 맞춰 밝아짐)으로 채워, 생성 전에도 전체 흐름을 볼 수 있다.
"""
from __future__ import annotations

import colorsys
import hashlib
import os
import shutil
import subprocess
from typing import Iterator

import numpy as np

from .audio import Timeline
from .edl import Shot

FPS = 24


def probe_duration(path: str) -> float:
    """클립 길이(초)."""
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
                             capture_output=True, check=True, text=True).stdout
        return float(out.strip())
    except (subprocess.CalledProcessError, ValueError) as e:
        raise RuntimeError(f"클립 길이를 읽지 못함: {path}") from e


def clip_frames(shot: Shot, n: int, w: int, h: int) -> Iterator[np.ndarray]:
    """클립에서 n장의 RGB 프레임을 꺼낸다. in~out을 자리 길이에 맞춰 속도를 바꾸고, 모자라면 마지막 장을 늘린다."""
    cin = shot.cin
    cout = shot.cout if shot.cout > cin else probe_duration(shot.clip)
    span = max(cout - cin, 1 / FPS)
    slot = n / FPS
    speed = shot.speed if shot.speed > 0 else span / slot
    vf = (f"setpts=(PTS-STARTPTS)/{speed:.5f},fps={FPS},"
          f"scale={w}:{h}:force_original_aspect_ratio=increase:flags=lanczos,crop={w}:{h},format=rgb24")
    cmd = ["ffmpeg", "-v", "error", "-ss", f"{cin:.3f}", "-t", f"{span:.3f}", "-i", shot.clip,
           "-an", "-vf", vf, "-f", "rawvideo", "-"]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    size = w * h * 3
    last = None
    got = 0
    try:
        while got < n:
            buf = p.stdout.read(size)
            if len(buf) < size:
                break
            last = np.frombuffer(buf, np.uint8).reshape(h, w, 3)
            got += 1
            yield last
    finally:
        p.stdout.close()
        p.kill()
        p.wait()
    if last is None:
        raise RuntimeError(f"클립에서 프레임을 못 읽음: {shot.clip} ({p.stderr.read().decode(errors='ignore')[:300]})")
    for _ in range(n - got):
        yield last


def _font(size: int, font_path: str | None):
    from PIL import ImageFont
    cands = [font_path] if font_path else []
    cands += [r"C:\Windows\Fonts\malgunbd.ttf", r"C:\Windows\Fonts\malgun.ttf",
              "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc", "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"]
    for c in cands:
        if c and os.path.exists(c):
            return ImageFont.truetype(c, size)
    return ImageFont.load_default()


def placeholder_frames(shot: Shot, idx: int, n: int, w: int, h: int, tl: Timeline,
                       font_path: str | None = None) -> Iterator[np.ndarray]:
    """빈 장면 화면: 구역마다 다른 색, 박마다 밝아졌다 어두워짐, 장면 번호와 메모 표시."""
    from PIL import Image, ImageDraw
    sec = shot.note.split("|")[0].strip() or "간주"
    hue = int(hashlib.md5(sec.encode()).hexdigest()[:4], 16) / 65535
    c1 = np.array(colorsys.hsv_to_rgb(hue, 0.6, 0.55)) * 255
    c2 = np.array(colorsys.hsv_to_rgb((hue + 0.12) % 1, 0.7, 0.18)) * 255
    yy = np.linspace(0, 1, h)[:, None, None]
    base = (c1 * (1 - yy) + c2 * yy).astype(np.float32) * np.ones((1, w, 1), np.float32)
    img = Image.new("RGB", (w, h))
    d = ImageDraw.Draw(img)
    f1, f2 = _font(h // 12, font_path), _font(h // 30, font_path)
    d.text((w * 0.05, h * 0.06), f"빈 장면 #{idx}", font=f1, fill=(255, 255, 255))
    d.text((w * 0.05, h * 0.18), shot.note[:70], font=f2, fill=(230, 230, 230))
    if shot.prompt:
        d.text((w * 0.05, h * 0.24), shot.prompt[:90], font=f2, fill=(200, 200, 200))
    text = np.asarray(img, np.float32)
    mask = (text.max(axis=2, keepdims=True) > 30).astype(np.float32)
    beats = np.asarray(tl.beats)
    for k in range(n):
        t = shot.start + k / FPS
        j = np.searchsorted(beats, t) - 1
        since = t - beats[j] if j >= 0 else 1.0
        g = 1.0 + 0.35 * np.exp(-since / 0.12)
        frame = np.clip(base * g, 0, 255) * (1 - mask) + text * mask
        yield frame.astype(np.uint8)


def _crossfade(prev: list[np.ndarray], cur: np.ndarray, k: int) -> np.ndarray:
    a = (k + 1) / (len(prev) + 1)
    return (prev[k].astype(np.float32) * (1 - a) + cur.astype(np.float32) * a).astype(np.uint8)


def render(shots: list[Shot], tl: Timeline, song: str, ass: str | None, out: str, workdir: str,
           preview: bool = False, fontsdir: str | None = None, font_path: str | None = None, log=print) -> str:
    """배치표 전체를 한 번에 렌더링한다.

    Args:
        preview: True면 960×540·빠른 인코딩(확인용).
        fontsdir: 자막 글꼴 파일(.ttf)이 있는 폴더. 지정하면 작업 폴더 안 fonts로 복사해 쓴다.
    """
    w, h = (960, 540) if preview else (1920, 1080)
    total = int(round(tl.duration * FPS))
    vf = []
    if ass:
        rel_ass = "lyrics_render.ass"
        shutil.copy(ass, os.path.join(workdir, rel_ass))
        opt = f"ass={rel_ass}"
        if fontsdir and os.path.isdir(fontsdir):
            dst = os.path.join(workdir, "fonts")
            os.makedirs(dst, exist_ok=True)
            for fn in os.listdir(fontsdir):
                if fn.lower().endswith((".ttf", ".otf", ".ttc")):
                    shutil.copy(os.path.join(fontsdir, fn), dst)
            opt += ":fontsdir=fonts"
        vf = ["-vf", opt]
    enc = ["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}", "-r", str(FPS),
           "-i", "-", "-i", os.path.abspath(song), "-map", "0:v", "-map", "1:a", *vf,
           "-c:v", "libx264", "-preset", "veryfast" if preview else "medium", "-crf", "23" if preview else "18",
           "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart",
           os.path.abspath(out)]
    p = subprocess.Popen(enc, stdin=subprocess.PIPE, stderr=subprocess.PIPE, cwd=workdir)
    written = 0
    pending: list[np.ndarray] = []
    try:
        for i, s in enumerate(shots):
            f0 = int(round(s.start * FPS))
            f1 = total if i == len(shots) - 1 else int(round(s.end * FPS))
            n = max(f1 - f0, 0)
            if n == 0:
                continue
            nxt_tr = shots[i + 1].transition if i + 1 < len(shots) else 0.0
            extra = min(int(round(nxt_tr * FPS)), n)
            has_clip = bool(s.clip) and os.path.exists(s.clip)
            if has_clip:
                src = clip_frames(s, n + extra, w, h)
            else:
                if s.clip:
                    log(f"  장면 {i}: 클립 없음 → 빈 장면으로 대체 ({s.clip})")
                src = placeholder_frames(s, i, n + extra, w, h, tl, font_path)
            new_pending = []
            for k, fr in enumerate(src):
                if k < n:
                    if k < len(pending):
                        fr = _crossfade(pending, fr, k)
                    p.stdin.write(fr.tobytes())
                    written += 1
                else:
                    new_pending.append(fr.copy())
            pending = new_pending
            log(f"  장면 {i + 1}/{len(shots)} {s.start:7.2f}s ~ {s.end:7.2f}s {'클립' if has_clip else '빈 장면'}")
        while written < total:  # 끝 부분 보충
            p.stdin.write(np.zeros((h, w, 3), np.uint8).tobytes())
            written += 1
        p.stdin.close()
        err = p.stderr.read().decode(errors="ignore")
        if p.wait() != 0:
            raise RuntimeError(f"인코딩 실패:\n{err[-1500:]}")
    except BrokenPipeError as e:
        raise RuntimeError(f"인코더가 멈춤:\n{p.stderr.read().decode(errors='ignore')[-1500:]}") from e
    return out
