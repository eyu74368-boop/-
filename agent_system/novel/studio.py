"""웹소설 작업실: 시장 조사 → 기획 → 설정집 → 개요 → 회차 집필 → 자동 검토·수정.

로컬 모델(7~8B)은 한 번에 긴 글을 안정적으로 쓰지 못하므로, 회차를 장면(beat) 단위로
나눠 이어 쓰고, 편집자 역할의 모델이 점수를 매겨 기준 미달이면 한 번 고쳐 쓴다.
연재 플랫폼 업로드·계약·정산은 사람이 한다 (자동 로그인·게시는 하지 않음).
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from orchestrator.commander import extract_json

from . import prompts as P
from .project import NovelProject, char_count, list_projects

log = logging.getLogger(__name__)

TextFn = Callable[[str, str], str]  # (system, prompt) -> text
SearchFn = Callable[[str], Any]  # (query) -> results
EventFn = Callable[[str], None]

BIBLE_LIMIT = 2500
OUTLINE_BATCH = 5
RESEARCH_QUERIES = ["웹소설 인기 장르 트렌드", "웹소설 공모전", "웹소설 플랫폼 신인 작가 연재"]


class StudioError(RuntimeError):
    """작업 실패 (사용자에게 그대로 보여줄 메시지)."""


class Stopped(StudioError):
    """사용자 중단."""


def parse_json(text: str) -> Dict[str, Any]:
    data = json.loads(extract_json(text))
    if not isinstance(data, dict):
        raise ValueError("JSON 객체가 아님")
    return data


def split_chunks(text: str, n: int) -> List[str]:
    """문단 경계를 지키며 원고를 대략 n 등분한다."""
    paras = [p for p in text.split("\n") if p.strip()]
    if n <= 1 or len(paras) <= n:
        return ["\n".join(paras)] if paras else []
    target = sum(len(p) for p in paras) / n
    chunks, cur, size = [], [], 0
    for p in paras:
        cur.append(p)
        size += len(p)
        if size >= target and len(chunks) < n - 1:
            chunks.append("\n".join(cur))
            cur, size = [], 0
    if cur:
        chunks.append("\n".join(cur))
    return chunks


class NovelStudio:
    """웹소설 자동 작업 파이프라인."""

    def __init__(
        self,
        workspace: str,
        writer_fn: TextFn,
        planner_fn: TextFn,
        search_fn: Optional[SearchFn] = None,
        target_chars: int = 5000,
        beats: int = 4,
        min_score: int = 7,
        on_event: Optional[EventFn] = None,
        stop_event: Optional[threading.Event] = None,
    ):
        self.novels_dir = os.path.join(os.path.realpath(workspace), "novels")
        os.makedirs(self.novels_dir, exist_ok=True)
        self.writer_fn = writer_fn
        self.planner_fn = planner_fn
        self.search_fn = search_fn
        self.target_chars = target_chars
        self.beats = beats
        self.min_score = min_score
        self.on_event = on_event or (lambda m: None)
        self.stop_event = stop_event or threading.Event()

    # ---------- 공통 ----------
    def _emit(self, msg: str) -> None:
        try:
            self.on_event(msg)
        except Exception as e:
            log.error("진행 보고 실패: %s", e)

    def _check_stop(self) -> None:
        if self.stop_event.is_set():
            raise Stopped("사용자 요청으로 중단했습니다. 완료된 회차는 저장되어 있습니다.")

    def _plan_json(self, prompt: str) -> Dict[str, Any]:
        """편집자 모델에게 JSON 을 받는다. 형식이 틀리면 한 번 더 요청한다."""
        last = ""
        for attempt in range(2):
            raw = self.planner_fn(P.PLANNER_SYSTEM, prompt if attempt == 0 else
                                  prompt + "\n\n반드시 지정한 JSON 형식 하나만 출력하라.")
            try:
                return parse_json(raw)
            except (ValueError, json.JSONDecodeError) as e:
                last = str(e)
        raise StudioError(f"편집자 모델이 JSON 형식을 지키지 못했습니다: {last}")

    def _write(self, prompt: str) -> str:
        text = self.writer_fn(P.WRITER_SYSTEM, prompt).strip()
        if not text:
            raise StudioError("작가 모델이 빈 응답을 돌려줬습니다.")
        return text

    # ---------- 작품 선택 ----------
    def _active_path(self) -> str:
        return os.path.join(self.novels_dir, "active.txt")

    def set_active(self, proj: NovelProject) -> None:
        with open(self._active_path(), "w", encoding="utf-8") as f:
            f.write(proj.slug)

    def active(self) -> NovelProject:
        try:
            with open(self._active_path(), "r", encoding="utf-8") as f:
                slug = f.read().strip()
        except OSError:
            slug = ""
        proj = NovelProject(os.path.join(self.novels_dir, slug)) if slug else None
        if proj is None or not os.path.isfile(proj.meta_path):
            raise StudioError("진행 중인 작품이 없습니다. /novel ideas → /novel start 번호 로 시작하세요.")
        return proj

    def select(self, name: str) -> NovelProject:
        for p in list_projects(self.novels_dir):
            if name in (p.slug, p.meta().get("title")) or p.slug.startswith(name):
                self.set_active(p)
                return p
        raise StudioError(f"작품을 찾을 수 없습니다: {name}")

    # ---------- 1. 시장 조사 ----------
    def research(self, hint: str = "") -> Dict[str, Any]:
        found: List[Any] = []
        if self.search_fn:
            queries = RESEARCH_QUERIES + ([f"웹소설 {hint}"] if hint else [])
            for q in queries:
                self._check_stop()
                try:
                    found.append({"query": q, "results": self.search_fn(q)})
                except Exception as e:
                    found.append({"query": q, "error": str(e)})
        data = json.dumps(found, ensure_ascii=False)[:6000] if found else "(검색 도구 없음)"
        self._emit("📊 시장 조사 결과 정리 중...")
        result = self._plan_json(P.RESEARCH_PROMPT.format(
            data=data, hint_line=f"[작가 관심사] {hint}" if hint else ""))
        with open(os.path.join(self.novels_dir, "market_research.json"), "w", encoding="utf-8") as f:
            json.dump({"hint": hint, "result": result, "sources": found}, f, ensure_ascii=False, indent=2)
        return result

    # ---------- 2. 기획안 ----------
    def ideas(self, hint: str = "") -> List[Dict[str, Any]]:
        research_path = os.path.join(self.novels_dir, "market_research.json")
        try:
            with open(research_path, "r", encoding="utf-8") as f:
                research = json.dumps(json.load(f).get("result", {}), ensure_ascii=False)[:3000]
        except (OSError, json.JSONDecodeError):
            research = "(시장 조사 없음 — 일반적 경향 기준)"
        self._emit("💡 기획안 3개 작성 중...")
        concepts = self._plan_json(P.CONCEPTS_PROMPT.format(research=research, hint=hint or "자유"))
        items = concepts.get("concepts")
        if not isinstance(items, list) or not items:
            raise StudioError("기획안을 만들지 못했습니다. 다시 시도해 주세요.")
        items = [c for c in items if isinstance(c, dict) and c.get("title")][:3]
        with open(os.path.join(self.novels_dir, "concepts.json"), "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False, indent=2)
        return items

    # ---------- 3. 작품 시작: 설정집 + 개요 ----------
    def start(self, index: int) -> NovelProject:
        try:
            with open(os.path.join(self.novels_dir, "concepts.json"), "r", encoding="utf-8") as f:
                concepts = json.load(f)
            concept = concepts[index - 1]
        except (OSError, json.JSONDecodeError, IndexError):
            raise StudioError("해당 번호의 기획안이 없습니다. /novel ideas 로 먼저 기획안을 만드세요.")
        proj = NovelProject.create(self.novels_dir, concept, self.target_chars)
        self.set_active(proj)
        self._emit(f"📚 「{proj.meta()['title']}」 설정집 작성 중...")
        proj.write("bible.md", self._write(P.BIBLE_PROMPT.format(
            concept=json.dumps(concept, ensure_ascii=False, indent=1))))
        self.ensure_outline(proj, 2 * OUTLINE_BATCH)
        return proj

    def ensure_outline(self, proj: NovelProject, upto: int) -> None:
        """upto 화까지 개요가 없으면 5화 단위로 이어서 만든다."""
        outline = proj.outline()
        while (max((int(c.get("no", 0)) for c in outline), default=0)) < upto:
            self._check_stop()
            start = max((int(c.get("no", 0)) for c in outline), default=0) + 1
            end = start + OUTLINE_BATCH - 1
            self._emit(f"🗂 {start}~{end}화 개요 작성 중...")
            prev = "\n".join(f"{c['no']}화: {c.get('summary', '')}" for c in outline[-10:]) or "(처음)"
            data = self._plan_json(P.OUTLINE_PROMPT.format(
                start=start, end=end, bible=proj.read("bible.md")[:BIBLE_LIMIT], previous=prev))
            new = []
            for i, c in enumerate(data.get("chapters", [])):
                if not isinstance(c, dict):
                    continue
                c["no"] = start + i  # 모델이 번호를 틀려도 순서대로 교정
                beats = c.get("beats") if isinstance(c.get("beats"), list) else []
                c["beats"] = [str(b) for b in beats if str(b).strip()][:6] or [c.get("summary", "")]
                new.append(c)
                if c["no"] >= end:
                    break
            if not new:
                raise StudioError("회차 개요를 만들지 못했습니다.")
            outline += new
            proj.write_json("outline.json", outline)

    # ---------- 4. 회차 집필 ----------
    def _story_so_far(self, proj: NovelProject, no: int) -> str:
        parts = []
        for n in range(max(1, no - 5), no):
            s = proj.summary(n)
            if s:
                parts.append(f"{n}화: {s[:400]}")
        return "\n".join(parts) or "(1화 — 이전 줄거리 없음)"

    def write_chapter(self, no: Optional[int] = None, proj: Optional[NovelProject] = None) -> Dict[str, Any]:
        """한 회차를 장면 단위로 쓰고, 검토 후 필요하면 고쳐 쓴 뒤 저장한다."""
        proj = proj or self.active()
        no = no or proj.next_chapter()
        self.ensure_outline(proj, no)
        plan = proj.outline_for(no)
        if plan is None:
            raise StudioError(f"{no}화 개요가 없습니다.")
        bible = proj.read("bible.md")[:BIBLE_LIMIT]
        story = self._story_so_far(proj, no)
        tail = proj.chapter(no - 1)[-800:] if no > 1 else "(작품 시작)"
        beats = plan["beats"]
        per = max(600, self.target_chars // max(1, len(beats)))
        pieces: List[str] = []

        for i, beat in enumerate(beats, 1):
            self._check_stop()
            self._emit(f"✍️ {no}화 장면 {i}/{len(beats)} 집필 중...")
            hook_line = f"[이 장면은 회차의 마지막이다. 다음 장면으로 끊어라] {plan.get('hook', '')}" \
                if i == len(beats) else ""
            pieces.append(self._write(P.SCENE_PROMPT.format(
                bible=bible, story_so_far=story, tail=(pieces[-1][-800:] if pieces else tail),
                no=no, title=plan.get("title", ""), summary=plan.get("summary", ""),
                part=i, total=len(beats), beat=beat, hook_line=hook_line, chars=per)))

        # 분량이 모자라면 절단 장면 쪽으로 이어 쓴다 (최대 2회)
        extra = 0
        while char_count("\n\n".join(pieces)) < self.target_chars * 0.85 and extra < 2:
            self._check_stop()
            extra += 1
            self._emit(f"➕ {no}화 분량 보강 {extra}...")
            pieces.append(self._write(P.SCENE_PROMPT.format(
                bible=bible, story_so_far=story, tail=pieces[-1][-800:], no=no,
                title=plan.get("title", ""), summary=plan.get("summary", ""), part="추가", total="-",
                beat="앞 장면을 자연스럽게 이어 긴장을 높인다", chars=per,
                hook_line=f"[마지막은 이 장면으로 끊어라] {plan.get('hook', '')}")))

        text = "\n\n".join(pieces)
        review = self._review(proj, no, plan, bible, text)
        if isinstance(review.get("score"), int) and review["score"] < self.min_score and review.get("fix"):
            self._check_stop()
            self._emit(f"🛠 {no}화 검토 {review['score']}점 → 고쳐 쓰는 중...")
            text = self._rewrite(text, P.REVISE_PROMPT, fix=review["fix"])
            review = self._review(proj, no, plan, bible, text)

        proj.record_chapter(no, text, str(review.get("summary") or plan.get("summary", "")), review)
        return {"no": no, "title": plan.get("title", ""), "chars": char_count(text),
                "score": review.get("score"), "issues": review.get("issues", []),
                "path": proj.path(proj.chapter_rel(no))}

    def _review(self, proj, no, plan, bible, text) -> Dict[str, Any]:
        self._emit(f"🔍 {no}화 편집자 검토 중...")
        try:
            r = self._plan_json(P.REVIEW_PROMPT.format(
                bible=bible, no=no, title=plan.get("title", ""), summary=plan.get("summary", ""),
                hook=plan.get("hook", ""), text=text[:9000]))
        except StudioError as e:
            return {"score": None, "issues": [str(e)], "fix": "", "summary": plan.get("summary", "")}
        try:
            r["score"] = max(0, min(10, int(r.get("score"))))
        except (TypeError, ValueError):
            r["score"] = None
        if not isinstance(r.get("issues"), list):
            r["issues"] = [str(r.get("issues"))] if r.get("issues") else []
        return r

    def _rewrite(self, text: str, template: str, **kw) -> str:
        chunks = split_chunks(text, self.beats)
        out = []
        for i, chunk in enumerate(chunks, 1):
            self._check_stop()
            out.append(self._write(template.format(text=chunk, **kw)))
        return "\n\n".join(out)

    def write_many(self, count: int) -> List[Dict[str, Any]]:
        results = []
        for _ in range(max(1, count)):
            self._check_stop()
            res = self.write_chapter()
            results.append(res)
            self._emit(f"✅ {res['no']}화 완료: {res['chars']}자, 검토 {res['score']}점")
        return results

    # ---------- 5. 작가 피드백 반영 ----------
    def apply_feedback(self, no: int, feedback: str) -> Dict[str, Any]:
        proj = self.active()
        text = proj.chapter(no)
        if not text:
            raise StudioError(f"{no}화 원고가 없습니다.")
        self._emit(f"🖊 {no}화 피드백 반영 중...")
        new = self._rewrite(text, P.FEEDBACK_PROMPT, feedback=feedback)
        proj.write(f"history/{no:03d}_{datetime.now():%Y%m%d_%H%M%S}.md", text)  # 이전 원고 보관
        plan = proj.outline_for(no) or {}
        review = self._review(proj, no, plan, proj.read("bible.md")[:BIBLE_LIMIT], new)
        proj.record_chapter(no, new, str(review.get("summary") or proj.summary(no)), review)
        return {"no": no, "chars": char_count(new), "score": review.get("score"),
                "path": proj.path(proj.chapter_rel(no))}

    def approve(self, no: int) -> None:
        proj = self.active()
        meta = proj.meta()
        if str(no) not in meta.get("chapters", {}):
            raise StudioError(f"{no}화 원고가 없습니다.")
        meta["chapters"][str(no)]["status"] = "approved"
        proj.save_meta(meta)

    # ---------- 상태 ----------
    def status(self) -> str:
        try:
            proj = self.active()
        except StudioError as e:
            return str(e)
        meta = proj.meta()
        chs = meta.get("chapters", {})
        lines = [f"📖 「{meta['title']}」 ({meta.get('genre', '')})", f"로그라인: {meta.get('logline', '')}",
                 f"회차 목표 분량: {meta.get('target_chars')}자 · 개요 {len(proj.outline())}화 · 원고 {len(chs)}화"]
        for n in sorted(chs, key=int)[-10:]:
            c = chs[n]
            mark = "✅" if c.get("status") == "approved" else "📝"
            lines.append(f"{mark} {n}화 {c.get('chars')}자 · 검토 {c.get('score')}점")
        others = [p.slug for p in list_projects(self.novels_dir) if p.slug != proj.slug]
        if others:
            lines.append("다른 작품: " + ", ".join(others))
        return "\n".join(lines)
