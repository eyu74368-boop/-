"""웹소설 작업실 테스트 (가짜 모델, 네트워크 없음)."""

import json
import threading
from datetime import datetime

import pytest

from novel.project import NovelProject, char_count, slugify
from novel.studio import NovelStudio, Stopped, StudioError, split_chunks
from novel.telegram import NovelCommands


class FakeModels:
    """프롬프트 내용을 보고 적당한 가짜 응답을 돌려준다."""

    def __init__(self, scores=(8,)):
        self.scores = list(scores)
        self.calls = []

    def planner(self, system, prompt):
        self.calls.append(("plan", prompt))
        if "시장 관련 검색 결과" in prompt:
            return json.dumps({"trends": ["회귀"], "platforms": [{"name": "A", "note": "n"}],
                               "opportunities": ["o"], "cautions": ["c"]}, ensure_ascii=False)
        if "기획안 3개" in prompt:
            return json.dumps({"concepts": [{"title": f"작품{i}", "genre": "판타지", "logline": "l",
                                             "selling_points": ["a"]} for i in (1, 2, 3)]}, ensure_ascii=False)
        if "회차별 개요" in prompt:
            start = int(prompt.split("화부터")[0].split()[-1])
            return json.dumps({"chapters": [{"no": 99, "title": f"제목{start + i}", "summary": "s",
                                             "beats": ["b1", "b2", "b3", "b4"], "hook": "h"} for i in range(5)]},
                              ensure_ascii=False)
        if "편집자로서 검토" in prompt:
            score = self.scores.pop(0) if self.scores else 8
            return json.dumps({"score": score, "issues": ["반복"], "fix": "대사 줄이기", "summary": "요약"},
                              ensure_ascii=False)
        raise AssertionError(prompt[:80])

    def writer(self, system, prompt):
        self.calls.append(("write", prompt))
        if "설정집을 작성" in prompt:
            return "## 작품 개요\n설정"
        if "고쳐 써라" in prompt:
            return "수정된 문단입니다.\n" * 60
        return "가" * 1300


def studio(tmp_path, models=None, **kw):
    m = models or FakeModels()
    st = NovelStudio(str(tmp_path), m.writer, m.planner, search_fn=lambda q: [{"title": q}], **kw)
    return st, m


def test_full_pipeline(tmp_path):
    st, m = studio(tmp_path)
    assert st.research("로판")["trends"] == ["회귀"]
    assert len(st.ideas("회귀물")) == 3
    proj = st.start(2)
    assert proj.meta()["title"] == "작품2" and "작품 개요" in proj.read("bible.md")
    assert [c["no"] for c in proj.outline()] == list(range(1, 11))  # 번호 교정
    res = st.write_chapter()
    assert res["no"] == 1 and res["chars"] >= 5000 and res["score"] == 8
    assert proj.summary(1) == "요약" and proj.next_chapter() == 2
    # 다음 회차는 앞 회차 요약·끝부분을 맥락으로 받는다
    st.write_chapter()
    scene_prompts = [p for kind, p in m.calls if kind == "write" and "[이번 회차] 2화" in p]
    assert "1화: 요약" in scene_prompts[0]


def test_low_score_triggers_one_revision(tmp_path):
    st, m = studio(tmp_path, FakeModels(scores=(4, 7)))
    st.ideas()
    st.start(1)
    res = st.write_chapter()
    assert res["score"] == 7
    assert any("고쳐 써라" in p for kind, p in m.calls if kind == "write")


def test_outline_extends_automatically(tmp_path):
    st, _ = studio(tmp_path)
    st.ideas()
    proj = st.start(1)
    st.write_chapter(12)
    assert len(proj.outline()) == 15


def test_short_output_padded(tmp_path):
    m = FakeModels()
    m.writer = lambda s, p: "## 개요" if "설정집을 작성" in p else "짧다" * 100
    st = NovelStudio(str(tmp_path), m.writer, m.planner, target_chars=5000)
    st.ideas()
    st.start(1)
    res = st.write_chapter()
    # 4장면 + 보강 2회 = 6조각
    assert res["chars"] == len("\n\n".join(["짧다" * 100] * 6))


def test_feedback_keeps_history_and_approve(tmp_path):
    st, _ = studio(tmp_path)
    st.ideas()
    proj = st.start(1)
    st.write_chapter()
    old = proj.chapter(1)
    st.apply_feedback(1, "대사 줄여줘")
    assert proj.chapter(1) != old
    history = list((tmp_path / "novels" / proj.slug / "history").iterdir())
    assert history and history[0].read_text(encoding="utf-8") == old
    st.approve(1)
    assert "✅ 1화" in st.status()
    with pytest.raises(StudioError):
        st.approve(5)


def test_stop_between_scenes(tmp_path):
    stop = threading.Event()
    st, m = studio(tmp_path, stop_event=stop)
    st.ideas()
    st.start(1)
    orig = m.writer

    def writer(system, prompt):
        stop.set()
        return orig(system, prompt)
    st.writer_fn = writer
    with pytest.raises(Stopped):
        st.write_chapter()


def test_errors_are_friendly(tmp_path):
    st, _ = studio(tmp_path)
    with pytest.raises(StudioError, match="진행 중인 작품"):
        st.write_chapter()
    with pytest.raises(StudioError, match="기획안"):
        st.start(1)
    bad = NovelStudio(str(tmp_path), lambda s, p: "x", lambda s, p: "형식 무시")
    with pytest.raises(StudioError, match="JSON"):
        bad.ideas()


def test_helpers():
    assert slugify('나 혼자 "회귀"/한다') == "나_혼자_회귀한다"
    assert char_count("  가 나  ") == 3
    chunks = split_chunks("\n".join(f"문단{i}" for i in range(10)), 4)
    assert len(chunks) == 4 and "\n".join(chunks) == "\n".join(f"문단{i}" for i in range(10))


# ---------- 텔레그램 ----------
class FakeBot:
    def __init__(self):
        self.texts, self.docs = [], []
        self.client = self

    def reply(self, chat_id, text):
        self.texts.append(text)

    def send_document(self, chat_id, path, caption=""):
        self.docs.append(caption)

    def run_background(self, chat_id, start_text, work):
        self.reply(chat_id, start_text)
        self.reply(chat_id, work(threading.Event()))
        return True


def commands(tmp_path):
    m = FakeModels()
    bot = FakeBot()
    cmd = NovelCommands(bot, lambda ev, stop: NovelStudio(str(tmp_path), m.writer, m.planner,
                                                          on_event=ev, stop_event=stop),
                        str(tmp_path / "novels"))
    return cmd, bot


def test_telegram_flow(tmp_path):
    cmd, bot = commands(tmp_path)
    cmd.handle(1, "")
    assert "/novel ideas" in bot.texts[-1]
    cmd.handle(1, "write")
    assert "진행 중인 작품" in bot.texts[-1]
    cmd.handle(1, "ideas 회귀 판타지")
    assert "1. 「작품1」" in bot.texts[-1]
    cmd.handle(1, "start 1")
    assert "준비 완료" in bot.texts[-1] and "bible.md" in bot.docs
    cmd.handle(1, "write")
    assert "1화" in bot.texts[-1] and "/novel ok 1" in bot.texts[-1] and bot.docs[-1].startswith("1화")
    cmd.handle(1, "fix 1 더 짧게")
    assert "수정 완료" in bot.texts[-1]
    cmd.handle(1, "ok 1")
    assert "확정" in bot.texts[-1]
    cmd.handle(1, "status")
    assert "작품1" in bot.texts[-1]


def test_daily_schedule(tmp_path):
    cmd, bot = commands(tmp_path)
    cmd.start_scheduler = lambda: None
    cmd.handle(1, "daily 25:00")
    assert "사용법" in bot.texts[-1]
    cmd.handle(1, "daily 07:00")
    calls = []
    cmd.write = lambda chat_id, arg: calls.append(chat_id)
    assert not cmd.tick(datetime(2026, 9, 24, 6, 59))
    assert cmd.tick(datetime(2026, 9, 24, 7, 0)) and calls == [1]
    assert not cmd.tick(datetime(2026, 9, 24, 8, 0))  # 하루 한 번
    cmd.handle(1, "daily off")
    assert not cmd.tick(datetime(2026, 9, 25, 7, 0))
