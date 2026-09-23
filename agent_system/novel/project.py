"""웹소설 작품 저장소.

작품 하나는 ``workspace/novels/<slug>/`` 폴더에 저장된다.

- project.json      제목·장르·목표 분량·회차별 상태
- concept.json      선택한 기획안
- bible.md          설정집 (인물·세계관·문체 규칙)
- outline.json      회차별 개요 [{no, title, summary, beats[], hook}]
- chapters/NNN.md   원고
- summaries/NNN.md  회차 요약 (다음 회차 집필 시 맥락으로 사용)
- reviews/NNN.md    자동 검토 결과
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import datetime
from typing import Any, Dict, List, Optional


class ProjectError(ValueError):
    """작품 데이터 오류."""


def slugify(title: str) -> str:
    """폴더 이름으로 쓸 수 있게 제목을 정리한다 (한글 유지)."""
    s = re.sub(r"[\\/:*?\"<>|\x00-\x1f]", "", title).strip()
    s = re.sub(r"\s+", "_", s)
    return s[:40] or f"novel_{datetime.now():%Y%m%d%H%M%S}"


def _atomic_write(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".tmp-")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def char_count(text: str) -> int:
    """웹소설 분량 기준(공백 포함 글자 수)."""
    return len(text.strip())


class NovelProject:
    """작품 1개의 파일 입출력."""

    def __init__(self, root: str):
        self.root = os.path.realpath(root)
        self.meta_path = os.path.join(self.root, "project.json")

    # ---------- 생성·조회 ----------
    @classmethod
    def create(cls, novels_dir: str, concept: Dict[str, Any], target_chars: int) -> "NovelProject":
        title = str(concept.get("title") or "무제")
        root = os.path.join(novels_dir, slugify(title))
        n = 2
        while os.path.exists(root):
            root = os.path.join(novels_dir, f"{slugify(title)}_{n}")
            n += 1
        proj = cls(root)
        proj.save_meta({
            "title": title,
            "genre": concept.get("genre", ""),
            "logline": concept.get("logline", ""),
            "target_chars": target_chars,
            "created": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "chapters": {},
        })
        proj.write_json("concept.json", concept)
        return proj

    @property
    def slug(self) -> str:
        return os.path.basename(self.root)

    def meta(self) -> Dict[str, Any]:
        try:
            with open(self.meta_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            raise ProjectError(f"작품 정보를 읽을 수 없습니다 ({self.slug}): {e}") from e

    def save_meta(self, meta: Dict[str, Any]) -> None:
        _atomic_write(self.meta_path, json.dumps(meta, ensure_ascii=False, indent=2))

    # ---------- 일반 파일 ----------
    def path(self, rel: str) -> str:
        return os.path.join(self.root, rel)

    def read(self, rel: str, default: str = "") -> str:
        try:
            with open(self.path(rel), "r", encoding="utf-8") as f:
                return f.read()
        except OSError:
            return default

    def write(self, rel: str, text: str) -> str:
        _atomic_write(self.path(rel), text)
        return self.path(rel)

    def read_json(self, rel: str, default: Any = None) -> Any:
        try:
            return json.loads(self.read(rel)) if self.read(rel) else default
        except json.JSONDecodeError:
            return default

    def write_json(self, rel: str, data: Any) -> None:
        self.write(rel, json.dumps(data, ensure_ascii=False, indent=2))

    # ---------- 개요·원고 ----------
    def outline(self) -> List[Dict[str, Any]]:
        return self.read_json("outline.json", []) or []

    def outline_for(self, no: int) -> Optional[Dict[str, Any]]:
        return next((c for c in self.outline() if int(c.get("no", 0)) == no), None)

    def chapter_rel(self, no: int) -> str:
        return f"chapters/{no:03d}.md"

    def chapter(self, no: int) -> str:
        return self.read(self.chapter_rel(no))

    def summary(self, no: int) -> str:
        return self.read(f"summaries/{no:03d}.md")

    def written(self) -> List[int]:
        return sorted(int(k) for k in self.meta().get("chapters", {}))

    def next_chapter(self) -> int:
        done = self.written()
        return (max(done) + 1) if done else 1

    def record_chapter(self, no: int, text: str, summary: str, review: Dict[str, Any], status: str = "draft") -> None:
        """원고·요약·검토 결과를 저장하고 회차 상태를 갱신한다."""
        self.write(self.chapter_rel(no), text)
        self.write(f"summaries/{no:03d}.md", summary)
        self.write(f"reviews/{no:03d}.md", json.dumps(review, ensure_ascii=False, indent=2))
        meta = self.meta()
        meta.setdefault("chapters", {})[str(no)] = {
            "chars": char_count(text),
            "score": review.get("score"),
            "status": status,
            "updated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        }
        self.save_meta(meta)


def list_projects(novels_dir: str) -> List[NovelProject]:
    if not os.path.isdir(novels_dir):
        return []
    return [NovelProject(os.path.join(novels_dir, d)) for d in sorted(os.listdir(novels_dir))
            if os.path.isfile(os.path.join(novels_dir, d, "project.json"))]
