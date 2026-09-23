"""텔레그램 /novel 명령.

/novel research [관심사]   시장 조사
/novel ideas [요청]        기획안 3개
/novel start 번호          기획안 선택 → 설정집·개요 작성
/novel write [회차]        다음 회차(또는 지정 회차) 집필 → 원고 파일 전송
/novel auto N              다음 N개 회차 연속 집필 (최대 10)
/novel fix 회차 피드백     피드백대로 고쳐 쓰기
/novel ok 회차             연재용 확정 표시
/novel get 회차|bible|outline   파일 받기
/novel daily HH:MM | off   매일 정해진 시각에 다음 회차 자동 집필
/novel status | list | select 작품명
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime
from typing import Any, Callable, Dict, Optional

from .project import list_projects
from .studio import NovelStudio, Stopped, StudioError

log = logging.getLogger(__name__)

NOVEL_HELP = """웹소설 작업실 (/novel)
/novel research [관심 장르] - 시장·플랫폼 조사
/novel ideas [요청] - 기획안 3개
/novel start 번호 - 기획안 선택 → 설정집·개요 작성
/novel write [회차] - 다음 회차 집필 후 원고 전송
/novel auto 개수 - 여러 회차 연속 집필 (최대 10)
/novel fix 회차 피드백 - 피드백 반영해 고쳐 쓰기
/novel ok 회차 - 연재용으로 확정
/novel get 회차|bible|outline - 파일 받기
/novel daily 07:00 | off - 매일 자동 집필
/novel status · list · select 작품명"""

StudioFactory = Callable[[Callable[[str], None], threading.Event], NovelStudio]


class NovelCommands:
    """TelegramBot 에 붙는 /novel 명령 처리기."""

    def __init__(self, bot: Any, factory: StudioFactory, novels_dir: str):
        self.bot = bot
        self.factory = factory
        self.novels_dir = novels_dir
        self.schedule_path = os.path.join(novels_dir, "schedule.json")
        self._sched_thread: Optional[threading.Thread] = None

    # ---------- 공통 ----------
    def _studio(self, chat_id: int, stop: Optional[threading.Event] = None) -> NovelStudio:
        return self.factory(lambda m: self.bot.reply(chat_id, m), stop or threading.Event())

    def _send_chapter(self, chat_id: int, res: Dict[str, Any]) -> None:
        issues = "\n".join(f"- {i}" for i in (res.get("issues") or [])[:5])
        try:
            self.bot.client.send_document(chat_id, res["path"], caption=f"{res['no']}화 {res.get('title', '')}")
        except Exception as e:
            self.bot.reply(chat_id, f"⚠️ 원고 파일 전송 실패: {e}")
        return issues

    def _bg(self, chat_id: int, start_text: str, work: Callable[[NovelStudio], str]) -> None:
        def run(stop: threading.Event) -> str:
            studio = self._studio(chat_id, stop)
            try:
                return work(studio)
            except Stopped as e:
                return f"⏹ {e}"
            except StudioError as e:
                return f"⚠️ {e}"
        self.bot.run_background(chat_id, start_text, run)

    # ---------- 명령 분기 ----------
    def handle(self, chat_id: int, arg: str) -> None:
        sub, _, rest = arg.strip().partition(" ")
        rest = rest.strip()
        actions = {
            "research": self.research, "ideas": self.ideas, "start": self.start, "write": self.write,
            "auto": self.auto, "fix": self.fix, "ok": self.ok, "get": self.get, "daily": self.daily,
            "status": self.status, "list": self.list, "select": self.select,
        }
        fn = actions.get(sub.lower())
        if fn is None:
            self.bot.reply(chat_id, NOVEL_HELP)
            return
        fn(chat_id, rest)

    def research(self, chat_id: int, hint: str) -> None:
        def work(st: NovelStudio) -> str:
            r = st.research(hint)
            lines = ["📊 웹소설 시장 조사"]
            lines += ["\n[경향]"] + [f"• {t}" for t in r.get("trends", [])]
            lines += ["\n[플랫폼]"] + [f"• {p.get('name')}: {p.get('note')}" if isinstance(p, dict) else f"• {p}"
                                    for p in r.get("platforms", [])]
            lines += ["\n[기회]"] + [f"• {t}" for t in r.get("opportunities", [])]
            lines += ["\n[주의]"] + [f"• {t}" for t in r.get("cautions", [])]
            lines.append("\n※ 로컬 AI 가 정리한 내용이라 사실 확인이 필요합니다. 다음: /novel ideas [원하는 방향]")
            return "\n".join(lines)
        self._bg(chat_id, "🔎 시장 조사를 시작합니다 (수 분 소요).", work)

    def ideas(self, chat_id: int, hint: str) -> None:
        def work(st: NovelStudio) -> str:
            out = ["💡 기획안"]
            for i, c in enumerate(st.ideas(hint), 1):
                sp = ", ".join(c.get("selling_points", [])[:3]) if isinstance(c.get("selling_points"), list) else ""
                out.append(f"\n{i}. 「{c.get('title')}」 [{c.get('genre')}]\n"
                           f"   {c.get('logline')}\n   1화 훅: {c.get('hook')}\n   차별점: {sp}\n"
                           f"   독자: {c.get('target_reader')} · 추천 연재처: {c.get('platform')}")
            out.append("\n마음에 드는 번호로 시작: /novel start 번호")
            return "\n".join(out)
        self._bg(chat_id, "💡 기획안을 만듭니다.", work)

    def start(self, chat_id: int, arg: str) -> None:
        if not arg.isdigit():
            self.bot.reply(chat_id, "사용법: /novel start 번호 (예: /novel start 2)")
            return

        def work(st: NovelStudio) -> str:
            proj = st.start(int(arg))
            for rel in ("bible.md", "outline.json"):
                try:
                    self.bot.client.send_document(chat_id, proj.path(rel), caption=rel)
                except Exception as e:
                    log.error("파일 전송 실패: %s", e)
            return (f"📚 「{proj.meta()['title']}」 준비 완료: 설정집과 {len(proj.outline())}화 개요를 보냈습니다.\n"
                    "설정집을 확인하고 고칠 점이 있으면 PC 의 bible.md 를 직접 수정하세요.\n다음: /novel write")
        self._bg(chat_id, "📚 설정집과 개요를 작성합니다 (수 분 소요).", work)

    def write(self, chat_id: int, arg: str) -> None:
        no = int(arg) if arg.isdigit() else None

        def work(st: NovelStudio) -> str:
            res = st.write_chapter(no)
            issues = self._send_chapter(chat_id, res)
            return (f"✅ {res['no']}화 「{res['title']}」 {res['chars']}자 · 편집자 검토 {res['score']}점\n"
                    + (f"지적 사항:\n{issues}\n" if issues else "")
                    + f"고치기: /novel fix {res['no']} 피드백 · 확정: /novel ok {res['no']}")
        self._bg(chat_id, "✍️ 회차 집필을 시작합니다 (모델·PC 성능에 따라 5~20분).", work)

    def auto(self, chat_id: int, arg: str) -> None:
        count = min(int(arg), 10) if arg.isdigit() and int(arg) > 0 else 3

        def work(st: NovelStudio) -> str:
            results = []
            for _ in range(count):
                res = st.write_chapter()
                self._send_chapter(chat_id, res)
                self.bot.reply(chat_id, f"✅ {res['no']}화 {res['chars']}자 · 검토 {res['score']}점")
                results.append(res)
            return f"🏁 {len(results)}개 회차 집필 완료. 원고를 검토하고 /novel ok 회차 로 확정하세요."
        self._bg(chat_id, f"✍️ {count}개 회차를 연속 집필합니다. 중단: /stop", work)

    def fix(self, chat_id: int, arg: str) -> None:
        no, _, feedback = arg.partition(" ")
        if not no.isdigit() or not feedback.strip():
            self.bot.reply(chat_id, "사용법: /novel fix 회차 피드백\n예: /novel fix 3 대사를 더 짧게, 마지막 장면 긴장감 올려줘")
            return

        def work(st: NovelStudio) -> str:
            res = st.apply_feedback(int(no), feedback.strip())
            self._send_chapter(chat_id, res)
            return f"🖊 {no}화 수정 완료: {res['chars']}자 · 검토 {res['score']}점 (이전 원고는 history/ 에 보관)"
        self._bg(chat_id, f"🖊 {no}화에 피드백을 반영합니다.", work)

    def ok(self, chat_id: int, arg: str) -> None:
        try:
            self._studio(chat_id).approve(int(arg))
            self.bot.reply(chat_id, f"✅ {arg}화를 연재용으로 확정했습니다. 플랫폼 업로드는 직접 해 주세요.")
        except (ValueError, StudioError) as e:
            self.bot.reply(chat_id, f"⚠️ {e}" if isinstance(e, StudioError) else "사용법: /novel ok 회차")

    def get(self, chat_id: int, arg: str) -> None:
        try:
            proj = self._studio(chat_id).active()
        except StudioError as e:
            self.bot.reply(chat_id, str(e))
            return
        rel = {"bible": "bible.md", "outline": "outline.json"}.get(arg.lower()) or \
            (proj.chapter_rel(int(arg)) if arg.isdigit() else None)
        if rel is None or not os.path.isfile(proj.path(rel)):
            self.bot.reply(chat_id, "사용법: /novel get 회차|bible|outline (해당 파일이 없을 수도 있습니다)")
            return
        try:
            self.bot.client.send_document(chat_id, proj.path(rel), caption=rel)
        except Exception as e:
            self.bot.reply(chat_id, f"⚠️ 파일 전송 실패: {e}")

    def status(self, chat_id: int, _arg: str = "") -> None:
        self.bot.reply(chat_id, self._studio(chat_id).status() + self._schedule_line())

    def list(self, chat_id: int, _arg: str = "") -> None:
        projects = list_projects(self.novels_dir)
        self.bot.reply(chat_id, "작품 목록:\n" + "\n".join(f"• {p.slug}" for p in projects) if projects
                       else "작품이 없습니다. /novel ideas 로 시작하세요.")

    def select(self, chat_id: int, name: str) -> None:
        try:
            proj = self._studio(chat_id).select(name)
            self.bot.reply(chat_id, f"작업 작품: {proj.slug}")
        except StudioError as e:
            self.bot.reply(chat_id, str(e))

    # ---------- 매일 자동 집필 ----------
    def _load_schedule(self) -> Dict[str, Any]:
        try:
            with open(self.schedule_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            return {}

    def _save_schedule(self, data: Dict[str, Any]) -> None:
        os.makedirs(self.novels_dir, exist_ok=True)
        with open(self.schedule_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)

    def _schedule_line(self) -> str:
        s = self._load_schedule()
        return f"\n⏰ 매일 {s['time']} 자동 집필 중" if s.get("time") else ""

    def daily(self, chat_id: int, arg: str) -> None:
        if arg.lower() in ("off", "끄기"):
            self._save_schedule({})
            self.bot.reply(chat_id, "⏰ 매일 자동 집필을 껐습니다.")
            return
        try:
            datetime.strptime(arg, "%H:%M")
        except ValueError:
            self.bot.reply(chat_id, "사용법: /novel daily 07:00 (24시간제) 또는 /novel daily off")
            return
        self._save_schedule({"time": arg, "chat_id": chat_id, "last": ""})
        self.start_scheduler()
        self.bot.reply(chat_id, f"⏰ 매일 {arg} 에 다음 회차를 자동 집필해 보내드립니다. (PC 와 봇이 켜져 있어야 함)")

    def tick(self, now: Optional[datetime] = None) -> bool:
        """예약 시각이면 집필을 시작한다. 시작했으면 True."""
        now = now or datetime.now()
        s = self._load_schedule()
        if not s.get("time") or s.get("last") == now.strftime("%Y-%m-%d"):
            return False
        if now.strftime("%H:%M") < s["time"]:
            return False
        s["last"] = now.strftime("%Y-%m-%d")
        self._save_schedule(s)
        self.write(int(s["chat_id"]), "")
        return True

    def start_scheduler(self) -> None:
        if self._sched_thread and self._sched_thread.is_alive():
            return

        def loop() -> None:
            while True:
                try:
                    self.tick()
                except Exception:
                    log.exception("자동 집필 예약 확인 실패")
                time.sleep(30)

        self._sched_thread = threading.Thread(target=loop, daemon=True, name="novel-scheduler")
        self._sched_thread.start()


def build_studio_factory(workspace: str) -> StudioFactory:
    """환경변수 설정으로 NovelStudio 를 만드는 함수를 돌려준다.

    NOVEL_WRITER_MODEL  집필 모델 (기본 exaone3.5:7.8b — 한국어 문장 품질)
    NOVEL_PLANNER_MODEL 기획·검토 모델 (기본 OLLAMA_MODEL 또는 qwen2.5:7b — JSON 안정성)
    NOVEL_CHAPTER_CHARS 회차 목표 분량 (기본 5000자, 공백 포함)
    """
    from orchestrator.agents.llm import OllamaAgent
    from orchestrator.tools import BuiltinTools

    writer = OllamaAgent(model=os.getenv("NOVEL_WRITER_MODEL", "exaone3.5:7.8b"))
    planner = OllamaAgent(model=os.getenv("NOVEL_PLANNER_MODEL", os.getenv("OLLAMA_MODEL", "qwen2.5:7b")))
    tools = BuiltinTools(workspace, planner)
    target = int(os.getenv("NOVEL_CHAPTER_CHARS", "5000"))

    def write_fn(system: str, prompt: str) -> str:
        return writer.chat_messages(system, [{"role": "user", "content": prompt}],
                                    options={"temperature": 0.85, "repeat_penalty": 1.1})

    def plan_fn(system: str, prompt: str) -> str:
        return planner.chat_messages(system, [{"role": "user", "content": prompt}], json_mode=True,
                                     options={"temperature": 0.3})

    def factory(on_event, stop_event) -> NovelStudio:
        return NovelStudio(workspace, write_fn, plan_fn, search_fn=tools.news_search, target_chars=target,
                           on_event=on_event, stop_event=stop_event)

    return factory
