"""빈 장면 목록: 생성해야 할 장면을 휴대폰에서 읽기 좋은 목록과 그록용 프롬프트로 정리한다.

첫 이미지는 ComfyUI Qwen-Image-Edit 2509(Lightning LoRA, 4스텝)에 캐릭터 원본을 참조로 넣어 만들고,
영상은 그 첫 이미지 + 영어 프롬프트로 그록 이매진에서 만든다.
배운 점: 첫 이미지에 없는 부위(손 등)는 영상 모델이 지어낸다 → 첫 이미지에 미리 그려 넣고 크기를 고정하라고 적는다.
"""
from __future__ import annotations

from .edl import Shot

GUARD = ("Keep the character exactly as in the first frame: same proportions, same small hands, "
         "do not add or enlarge arms or limbs. No text, no subtitles. Smooth motion, stable camera.")


def empty_list(shots: list[Shot], style: str = "") -> str:
    """클립이 없는 장면만 골라 마크다운 목록으로 만든다."""
    out = ["# 빈 장면 목록", "", f"공통 지시: {GUARD}", ""]
    if style:
        out += [f"화풍: {style}", ""]
    n = 0
    for i, s in enumerate(shots):
        if s.clip:
            continue
        n += 1
        prompt = s.prompt or "(prompt 칸이 비어 있음: 배치표에 영어 지시문을 적으세요)"
        out += [f"## 장면 {i} · {s.start:.2f}s ~ {s.end:.2f}s ({s.length:.2f}s)",
                f"- 가사/메모: {s.note}",
                f"- 첫 이미지: scenes/scene_{i:03d}.png",
                f"- 영상 파일 이름: clips/scene_{i:03d}.mp4",
                "```", f"{prompt} {style} {GUARD}".strip(), "```", ""]
    out.insert(1, f"\n총 {n}개 장면이 비어 있습니다. 영상을 만들면 clips 폴더에 위 이름으로 넣고 배치표 clip 칸에 적으세요.\n")
    return "\n".join(out)
