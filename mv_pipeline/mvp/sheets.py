"""썸네일 시트: 클립 동작 확인용(0.5초 간격)과 완성본 검수용(소절마다 한 장)."""
from __future__ import annotations

import os
import subprocess
import tempfile

from .lyrics import Line
from .render import _font


def clip_sheet(clip: str, out: str, every: float = 0.5, cols: int = 6, thumb_w: int = 320) -> str:
    """클립을 every초 간격으로 펼쳐 시간이 적힌 한 장짜리 시트로 만든다.

    어느 순간에 어떤 동작(빛이 터짐, 착지 등)이 있는지 보고 배치표의 in/out을 정할 때 쓴다.
    """
    from PIL import Image, ImageDraw
    with tempfile.TemporaryDirectory() as td:
        subprocess.run(["ffmpeg", "-v", "error", "-i", clip, "-vf", f"fps=1/{every},scale={thumb_w}:-2",
                        os.path.join(td, "%04d.jpg")], check=True)
        files = sorted(os.listdir(td))
        if not files:
            raise RuntimeError(f"썸네일을 만들지 못함: {clip}")
        ims = [Image.open(os.path.join(td, f)).convert("RGB") for f in files]
        tw, th = ims[0].size
        rows = (len(ims) + cols - 1) // cols
        sheet = Image.new("RGB", (cols * tw, rows * (th + 28)), (20, 20, 20))
        d = ImageDraw.Draw(sheet)
        font = _font(22, None)
        for k, im in enumerate(ims):
            x, y = (k % cols) * tw, (k // cols) * (th + 28)
            sheet.paste(im, (x, y + 28))
            d.text((x + 6, y + 2), f"{k * every:.1f}s", font=font, fill=(255, 220, 0))
        sheet.save(out, quality=85)
    return out


def review_sheet(video: str, lines: list[Line], out: str, cols: int = 3, thumb_w: int = 480) -> str:
    """완성본에서 소절마다 중간 프레임을 뽑아 가사와 함께 한 장에 모은다.

    자막이 얼굴을 가리는지, 동작이 가사에 맞는지 휴대폰에서 한눈에 확인하기 위한 것.
    """
    from PIL import Image, ImageDraw
    font = _font(20, None)
    cells = []
    with tempfile.TemporaryDirectory() as td:
        for l in lines:
            if l.start < 0:
                continue
            t = (l.start + l.end) / 2
            fn = os.path.join(td, f"{l.idx:04d}.jpg")
            r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{t:.3f}", "-i", video, "-frames:v", "1",
                                "-vf", f"scale={thumb_w}:-2", fn], capture_output=True)
            if r.returncode == 0 and os.path.exists(fn):
                cells.append((l, Image.open(fn).convert("RGB")))
        if not cells:
            raise RuntimeError("검수 프레임을 하나도 뽑지 못함")
        tw, th = cells[0][1].size
        cap = 54
        rows = (len(cells) + cols - 1) // cols
        sheet = Image.new("RGB", (cols * tw, rows * (th + cap)), (15, 15, 15))
        d = ImageDraw.Draw(sheet)
        for k, (l, im) in enumerate(cells):
            x, y = (k % cols) * tw, (k // cols) * (th + cap)
            sheet.paste(im, (x, y + cap))
            m, s = divmod(l.start, 60)
            d.text((x + 6, y + 2), f"{int(m)}:{s:05.2f} [{l.section}] #{l.idx}", font=font, fill=(255, 220, 0))
            d.text((x + 6, y + 26), l.text[:38], font=font, fill=(255, 255, 255))
        sheet.save(out, quality=82)
    return out
