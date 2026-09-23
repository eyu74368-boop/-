"""텔레그램 대화·명령·파일 전송 봇.

- 허용된 chat_id 에서 온 메시지만 처리한다 (그 외는 무시하고 로그만 남김).
- 일반 문장 → 로컬 AI(Ollama)와 대화 (채팅방별 최근 대화 이력 유지)
- 파일/사진 전송 → ``workspace/inbox/`` 에 저장
- ``/get 경로`` → workspace 안의 파일만 전송 (경로 탈출 차단)
- ``/run 계획.json`` → 오케스트레이터 계획을 백그라운드로 실행하고 결과 보고

명령 목록은 ``HELP`` 참고.
"""

from __future__ import annotations

import logging
import os
import re
import threading
import time
from collections import defaultdict, deque
from datetime import datetime
from typing import Any, Callable, Deque, Dict, Iterable, List, Optional, Set, Tuple

from .api import TelegramClient, TelegramError

log = logging.getLogger(__name__)

HISTORY_TURNS = 10  # 채팅방별로 기억하는 최근 대화 수 (질문+답변 = 1턴)
MAX_LIST = 50

SYSTEM_PROMPT = (
    "너는 사용자의 개인 업무 보조 AI다. 항상 한국어로 결론부터 간결하게 답한다. "
    "너는 이 대화창에서 직접 명령을 실행하거나 파일을 열 수 없다. "
    "작업 실행이 필요하면 /help 에 있는 명령어를 안내하라. 모르는 것은 모른다고 답한다."
)

HELP = """사용 가능한 명령
/goal 목표 - 로컬 AI 가 도구(검색·웹·파일·다른 모델·이미지·시장·보고)를 스스로 골라 목표 수행
/stop - 진행 중인 /goal 중단
/tools - /goal 에서 쓸 수 있는 도구 목록
/status - 로컬 AI 작동 상태 점검
/reset - 대화 기록 초기화
/files [폴더] - 작업 폴더 파일 목록
/get 경로 - 작업 폴더의 파일 받기 (예: /get reports/daily.json)
/plans - 실행 가능한 계획 목록
/run 계획.json - 계획 실행 (백그라운드, 완료 시 결과 보고)
/help - 이 도움말

그 외 일반 문장은 로컬 AI 와 대화합니다.
파일·사진을 보내면 작업 폴더 inbox/ 에 저장합니다."""

ChatFn = Callable[[str, List[Dict[str, str]]], str]  # (system, messages) -> reply
HealthFn = Callable[[], Dict[str, Any]]
PlanRunner = Callable[[str], Tuple[bool, str]]  # (plan_path) -> (ok, summary)
# (goal, chat_id, on_event, stop_event) -> RunResult 유사 객체(status, answer, steps, report_path)
GoalRunner = Callable[[str, int, Callable[[str], None], threading.Event], Any]


class PathError(ValueError):
    """작업 폴더 밖 경로 접근."""


def resolve_inside(base: str, rel: str) -> str:
    """base 안의 경로만 절대경로로 돌려준다. 밖이면 PathError."""
    base = os.path.realpath(base)
    path = os.path.realpath(os.path.join(base, rel.strip().lstrip("/\\")))
    if os.path.commonpath([path, base]) != base:
        raise PathError(f"작업 폴더 밖 경로는 사용할 수 없습니다: {rel}")
    return path


def safe_filename(name: str) -> str:
    """전송받은 파일명을 안전한 이름으로 바꾼다 (경로 구분자·제어문자 제거)."""
    name = os.path.basename(name or "").strip()
    name = re.sub(r"[\x00-\x1f/\\:*?\"<>|]", "_", name)
    return name.lstrip(".") or "file"


def format_health(h: Dict[str, Any]) -> str:
    """OllamaAgent.health() 결과를 사람이 읽는 문장으로."""
    lines = [f"서버: {h.get('host')} → {'연결됨' if h.get('reachable') else '연결 실패'}"]
    if h.get("reachable"):
        lines.append(f"모델: {h.get('model')} → {'설치됨' if h.get('model_installed') else '미설치'}")
        if h.get("models"):
            lines.append("설치된 모델: " + ", ".join(h["models"][:10]))
    if h.get("reply") is not None:
        lines.append(f"테스트 응답({h.get('latency_sec')}초): {str(h['reply']).strip()[:200]}")
    if h.get("error"):
        lines.append(f"❗ {h['error']}")
    ok = h.get("reachable") and h.get("model_installed") and not h.get("error")
    return ("✅ 로컬 AI 정상\n" if ok else "⚠️ 로컬 AI 점검 필요\n") + "\n".join(lines)


class TelegramBot:
    """롱 폴링 기반 봇."""

    def __init__(
        self,
        client: TelegramClient,
        allowed_chat_ids: Iterable[int],
        workspace: str,
        chat_fn: ChatFn,
        health_fn: HealthFn,
        plan_runner: Optional[PlanRunner] = None,
        plan_dirs: Iterable[str] = (),
        goal_runner: Optional[GoalRunner] = None,
        tools_desc: str = "",
    ):
        self.client = client
        self.allowed: Set[int] = set(allowed_chat_ids)
        if not self.allowed:
            raise ValueError("허용된 chat_id 가 없습니다. TELEGRAM_ALLOWED_CHAT_IDS 를 설정하세요.")
        self.workspace = os.path.realpath(workspace)
        self.inbox = os.path.join(self.workspace, "inbox")
        os.makedirs(self.inbox, exist_ok=True)
        self.chat_fn = chat_fn
        self.health_fn = health_fn
        self.plan_runner = plan_runner
        self.plan_dirs = [os.path.realpath(d) for d in plan_dirs]
        self.offset_path = os.path.join(self.workspace, ".telegram_offset")
        self.history: Dict[int, Deque[Dict[str, str]]] = defaultdict(lambda: deque(maxlen=HISTORY_TURNS * 2))
        self.goal_runner = goal_runner
        self.tools_desc = tools_desc
        self._run_lock = threading.Lock()  # /run 과 /goal 은 동시에 하나만
        self._goal_stop = threading.Event()
        self._stop = threading.Event()
        self._warned: Set[int] = set()

    # ---------- 폴링 루프 ----------
    def _load_offset(self) -> Optional[int]:
        try:
            with open(self.offset_path, "r", encoding="utf-8") as f:
                return int(f.read().strip())
        except (OSError, ValueError):
            return None

    def _save_offset(self, offset: int) -> None:
        try:
            with open(self.offset_path, "w", encoding="utf-8") as f:
                f.write(str(offset))
        except OSError as e:
            log.error("오프셋 저장 실패: %s", e)

    def stop(self, *_args) -> None:
        self._stop.set()

    def run_forever(self) -> None:
        """새 메시지를 받아 처리한다. 네트워크 오류 시 점점 길게 대기 후 재시도."""
        offset = self._load_offset()
        backoff = 1
        log.info("봇 시작 (허용 chat_id: %s)", sorted(self.allowed))
        while not self._stop.is_set():
            try:
                updates = self.client.get_updates(offset, timeout=30)
                backoff = 1
            except TelegramError as e:
                log.error("업데이트 수신 실패: %s (%ds 후 재시도)", e, backoff)
                self._stop.wait(backoff)
                backoff = min(backoff * 2, 60)
                continue
            for upd in updates:
                offset = upd["update_id"] + 1
                self._save_offset(offset)  # 처리 전에 저장: 처리 중 죽어도 같은 명령을 반복 실행하지 않음
                try:
                    self.handle_update(upd)
                except Exception:
                    log.exception("업데이트 처리 중 오류")
        log.info("봇 정상 종료")

    # ---------- 처리 ----------
    def reply(self, chat_id: int, text: str) -> None:
        try:
            self.client.send_text(chat_id, text)
        except TelegramError as e:
            log.error("응답 발송 실패: %s", e)

    def handle_update(self, upd: Dict[str, Any]) -> None:
        msg = upd.get("message")
        if not msg:
            return
        chat_id = msg.get("chat", {}).get("id")
        if chat_id not in self.allowed:
            if chat_id not in self._warned:
                self._warned.add(chat_id)
                sender = msg.get("from", {}).get("username") or msg.get("from", {}).get("id")
                log.warning("허용되지 않은 chat_id=%s (보낸 사람: %s) 메시지 무시", chat_id, sender)
            return

        if "document" in msg:
            self.save_incoming(chat_id, msg["document"]["file_id"],
                               msg["document"].get("file_name") or "document")
            return
        if "photo" in msg:
            best = max(msg["photo"], key=lambda p: p.get("file_size", 0))
            self.save_incoming(chat_id, best["file_id"], f"photo_{datetime.now():%Y%m%d_%H%M%S}.jpg")
            return

        text = (msg.get("text") or "").strip()
        if not text:
            return
        if text.startswith("/"):
            cmd, _, arg = text.partition(" ")
            self.handle_command(chat_id, cmd.split("@")[0].lower(), arg.strip())
        else:
            self.handle_chat(chat_id, text)

    def handle_command(self, chat_id: int, cmd: str, arg: str) -> None:
        handlers = {
            "/start": lambda: self.reply(chat_id, HELP),
            "/help": lambda: self.reply(chat_id, HELP),
            "/status": lambda: self.cmd_status(chat_id),
            "/reset": lambda: self.cmd_reset(chat_id),
            "/files": lambda: self.cmd_files(chat_id, arg),
            "/get": lambda: self.cmd_get(chat_id, arg),
            "/plans": lambda: self.cmd_plans(chat_id),
            "/run": lambda: self.cmd_run(chat_id, arg),
            "/goal": lambda: self.cmd_goal(chat_id, arg),
            "/stop": lambda: self.cmd_stop(chat_id),
            "/tools": lambda: self.reply(chat_id, self.tools_desc or "도구 정보가 없습니다."),
        }
        handler = handlers.get(cmd)
        if handler is None:
            self.reply(chat_id, f"알 수 없는 명령: {cmd}\n\n{HELP}")
        else:
            handler()

    def handle_chat(self, chat_id: int, text: str) -> None:
        """로컬 AI 와 대화한다."""
        self.client.send_chat_action(chat_id, "typing")
        hist = self.history[chat_id]
        messages = list(hist) + [{"role": "user", "content": text}]
        try:
            answer = self.chat_fn(SYSTEM_PROMPT, messages).strip()
        except Exception as e:
            log.error("로컬 AI 응답 실패: %s", e)
            self.reply(chat_id, f"⚠️ 로컬 AI 응답 실패: {e}\n/status 로 상태를 확인하세요.")
            return
        hist.append({"role": "user", "content": text})
        hist.append({"role": "assistant", "content": answer})
        self.reply(chat_id, answer)

    # ---------- 명령 ----------
    def cmd_status(self, chat_id: int) -> None:
        self.client.send_chat_action(chat_id, "typing")
        self.reply(chat_id, format_health(self.health_fn()))

    def cmd_reset(self, chat_id: int) -> None:
        self.history.pop(chat_id, None)
        self.reply(chat_id, "대화 기록을 초기화했습니다.")

    def cmd_files(self, chat_id: int, arg: str) -> None:
        try:
            folder = resolve_inside(self.workspace, arg or ".")
        except PathError as e:
            self.reply(chat_id, str(e))
            return
        if not os.path.isdir(folder):
            self.reply(chat_id, f"폴더가 없습니다: {arg or '.'}")
            return
        entries = []
        for name in sorted(os.listdir(folder)):
            if name.startswith("."):
                continue
            full = os.path.join(folder, name)
            if os.path.isdir(full):
                entries.append(f"📁 {name}/")
            else:
                entries.append(f"📄 {name} ({os.path.getsize(full) / 1024:.1f}KB)")
        rel = os.path.relpath(folder, self.workspace)
        more = f"\n… 외 {len(entries) - MAX_LIST}개" if len(entries) > MAX_LIST else ""
        body = "\n".join(entries[:MAX_LIST]) or "(비어 있음)"
        self.reply(chat_id, f"[{rel}]\n{body}{more}")

    def cmd_get(self, chat_id: int, arg: str) -> None:
        if not arg:
            self.reply(chat_id, "사용법: /get 경로 (예: /get reports/daily.json)")
            return
        try:
            path = resolve_inside(self.workspace, arg)
        except PathError as e:
            self.reply(chat_id, str(e))
            return
        if not os.path.isfile(path):
            self.reply(chat_id, f"파일이 없습니다: {arg}")
            return
        self.client.send_chat_action(chat_id, "upload_document")
        try:
            self.client.send_document(chat_id, path, caption=arg)
        except (TelegramError, OSError) as e:
            self.reply(chat_id, f"⚠️ 파일 전송 실패: {e}")

    def save_incoming(self, chat_id: int, file_id: str, name: str) -> None:
        """받은 파일을 inbox 에 저장한다. 같은 이름이 있으면 번호를 붙인다."""
        name = safe_filename(name)
        base, ext = os.path.splitext(name)
        dest = os.path.join(self.inbox, name)
        n = 1
        while os.path.exists(dest):
            dest = os.path.join(self.inbox, f"{base}_{n}{ext}")
            n += 1
        try:
            size = self.client.download(file_id, dest)
        except (TelegramError, OSError) as e:
            self.reply(chat_id, f"⚠️ 파일 저장 실패: {e}")
            return
        rel = os.path.relpath(dest, self.workspace)
        self.reply(chat_id, f"✅ 저장 완료: {rel} ({size / 1024:.1f}KB)")

    def _plan_candidates(self) -> List[str]:
        found = []
        for d in self.plan_dirs:
            if os.path.isdir(d):
                found += [f for f in sorted(os.listdir(d)) if f.endswith(".json") and "plan" in f]
        return found

    def cmd_plans(self, chat_id: int) -> None:
        plans = self._plan_candidates()
        self.reply(chat_id, "실행 가능한 계획:\n" + "\n".join(f"• {p}" for p in plans) if plans
                   else "계획 파일이 없습니다. 이름에 'plan' 이 들어간 .json 을 plans 폴더에 넣으세요.")

    def _find_plan(self, name: str) -> Optional[str]:
        for d in self.plan_dirs:
            try:
                path = resolve_inside(d, name)
            except PathError:
                continue
            if os.path.isfile(path):
                return path
        return None

    def cmd_run(self, chat_id: int, arg: str) -> None:
        if self.plan_runner is None:
            self.reply(chat_id, "계획 실행 기능이 꺼져 있습니다.")
            return
        if not arg:
            self.reply(chat_id, "사용법: /run 계획.json  (/plans 로 목록 확인)")
            return
        path = self._find_plan(arg)
        if path is None:
            self.reply(chat_id, f"계획 파일을 찾을 수 없습니다: {arg}")
            return
        if not self._run_lock.acquire(blocking=False):
            self.reply(chat_id, "이미 실행 중인 계획이 있습니다. 끝난 뒤 다시 시도하세요.")
            return

        def worker() -> None:
            started = time.monotonic()
            try:
                ok, summary = self.plan_runner(path)
                head = "✅ 계획 완료" if ok else "⚠️ 계획 일부 실패"
                self.reply(chat_id, f"{head}: {arg} ({time.monotonic() - started:.0f}초)\n\n{summary}")
            except Exception as e:
                log.exception("계획 실행 실패")
                self.reply(chat_id, f"❌ 계획 실행 오류: {e}")
            finally:
                self._run_lock.release()

        self.reply(chat_id, f"▶ 계획 실행 시작: {arg}\n완료되면 결과를 보내드립니다.")
        threading.Thread(target=worker, daemon=True, name="plan-runner").start()

    # ---------- 자율 실행 ----------
    def cmd_goal(self, chat_id: int, goal: str) -> None:
        """로컬 AI 자율 실행을 백그라운드로 시작한다."""
        if self.goal_runner is None:
            self.reply(chat_id, "자율 실행 기능이 꺼져 있습니다.")
            return
        if not goal:
            self.reply(chat_id, "사용법: /goal 목표\n예: /goal 오늘 상수도 누수 탐지 관련 뉴스 5개를 찾아 요약해서 파일로 보내줘")
            return
        if not self._run_lock.acquire(blocking=False):
            self.reply(chat_id, "이미 실행 중인 작업이 있습니다. /stop 으로 중단하거나 끝날 때까지 기다려 주세요.")
            return
        self._goal_stop.clear()

        def worker() -> None:
            started = time.monotonic()
            try:
                res = self.goal_runner(goal, chat_id, lambda m: self.reply(chat_id, m), self._goal_stop)
                head = {"DONE": "✅ 목표 완료", "STOPPED": "⏹ 중단됨", "MAX_STEPS": "⚠️ 단계 한도 도달"}.get(
                    res.status, "❌ 실패")
                tail = f"\n\n📄 실행 기록: /get {res.report_path}" if res.report_path else ""
                self.reply(chat_id, f"{head} ({len(res.steps)}단계, {time.monotonic() - started:.0f}초)\n\n"
                                    f"{res.answer}{tail}")
            except Exception as e:
                log.exception("자율 실행 실패")
                self.reply(chat_id, f"❌ 자율 실행 오류: {e}")
            finally:
                self._run_lock.release()

        self.reply(chat_id, f"▶ 목표 접수: {goal}\n로컬 AI 가 단계별로 진행 상황을 보고합니다. 중단: /stop")
        threading.Thread(target=worker, daemon=True, name="goal-runner").start()

    def cmd_stop(self, chat_id: int) -> None:
        if not self._run_lock.locked():
            self.reply(chat_id, "진행 중인 작업이 없습니다.")
            return
        self._goal_stop.set()
        self.reply(chat_id, "⏹ 중단 요청을 보냈습니다. 현재 단계가 끝나면 멈춥니다.")
