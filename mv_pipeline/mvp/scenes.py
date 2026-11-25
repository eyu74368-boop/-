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
    """영상 파일이 아직 없는 장면을 클립 이름별로 묶어 마크다운 목록으로 만든다.

    같은 클립을 여러 자리(후렴 반복 등)에 쓰면 한 번만 만들면 되므로 한 항목으로 묶고, 필요한 최대 길이를 적는다.
    """
    import os
    groups: dict[str, list[tuple[int, Shot]]] = {}
    for i, s in enumerate(shots):
        if s.clip and os.path.exists(s.clip):
            continue
        key = os.path.basename(s.clip) if s.clip else f"scene_{i:03d}.mp4"
        groups.setdefault(key, []).append((i, s))
    out = ["# 빈 장면 목록", "", f"만들 영상 {len(groups)}개. 만든 영상은 clips 폴더에 아래 이름으로 넣으면 다음 렌더링에 자동으로 들어갑니다.", "",
           f"공통 지시: {GUARD}", ""]
    if style:
        out += [f"화풍: {style}", ""]
    for key, items in groups.items():
        first = next((s for _, s in items if s.prompt), items[0][1])
        need = max(s.length for _, s in items)
        slots = ", ".join(f"{s.start:.1f}s" for _, s in items)
        prompt = first.prompt or "(prompt 칸이 비어 있음: 배치표에 영어 지시문을 적으세요)"
        out += [f"## {key} (필요 길이 {need:.1f}초)",
                f"- 쓰는 자리: {slots}",
                f"- 가사/메모: {first.note}",
                f"- 첫 이미지: scenes/{os.path.splitext(key)[0]}.png",
                "```", f"{prompt} {style} {GUARD}".strip(), "```", ""]
    return "\n".join(out)
