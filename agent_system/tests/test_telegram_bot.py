"""텔레그램 봇·로컬 AI 점검 테스트 (네트워크 없음)."""

import os
import threading

import pytest
import requests

from orchestrator.agents.llm import OllamaAgent
from telegram_bot.api import TelegramError
from telegram_bot.bot import PathError, TelegramBot, format_health, resolve_inside, safe_filename

OWNER = 111


class FakeClient:
    """TelegramClient 대역: 보낸 내용을 기록한다."""

    def __init__(self):
        self.texts, self.docs, self.downloads = [], [], {}

    def send_text(self, chat_id, text):
        self.texts.append((chat_id, text))

    def send_chat_action(self, chat_id, action="typing"):
        pass

    def send_document(self, chat_id, path, caption=""):
        self.docs.append((chat_id, path))

    def download(self, file_id, dest):
        data = self.downloads[file_id]
        with open(dest, "wb") as f:
            f.write(data)
        return len(data)


def make_bot(tmp_path, chat_fn=None, runner=None):
    client = FakeClient()
    calls = []

    def default_chat(system, messages):
        calls.append(messages)
        return f"답변{len(calls)}"

    bot = TelegramBot(client, [OWNER], str(tmp_path / "ws"), chat_fn or default_chat,
                      health_fn=lambda: {"host": "h", "model": "m", "reachable": True,
                                         "model_installed": True, "reply": "정상", "latency_sec": 0.1},
                      plan_runner=runner, plan_dirs=[str(tmp_path / "plans")])
    return bot, client, calls


def msg(text=None, chat=OWNER, **extra):
    m = {"chat": {"id": chat}, "from": {"id": chat}}
    if text is not None:
        m["text"] = text
    m.update(extra)
    return {"update_id": 1, "message": m}


def test_requires_allowlist(tmp_path):
    with pytest.raises(ValueError):
        TelegramBot(FakeClient(), [], str(tmp_path), lambda s, m: "", lambda: {})


def test_ignores_unknown_chat(tmp_path):
    bot, client, calls = make_bot(tmp_path)
    bot.handle_update(msg("안녕", chat=999))
    assert client.texts == [] and calls == []


def test_chat_keeps_history(tmp_path):
    bot, client, calls = make_bot(tmp_path)
    bot.handle_update(msg("첫 질문"))
    bot.handle_update(msg("두 번째"))
    assert client.texts[-1] == (OWNER, "답변2")
    assert [m["content"] for m in calls[1]] == ["첫 질문", "답변1", "두 번째"]
    bot.handle_update(msg("/reset"))
    bot.handle_update(msg("새 질문"))
    assert [m["content"] for m in calls[2]] == ["새 질문"]


def test_chat_failure_reported(tmp_path):
    def broken(system, messages):
        raise requests.ConnectionError("ollama down")
    bot, client, _ = make_bot(tmp_path, chat_fn=broken)
    bot.handle_update(msg("질문"))
    assert "로컬 AI 응답 실패" in client.texts[-1][1]


def test_status_and_unknown_command(tmp_path):
    bot, client, _ = make_bot(tmp_path)
    bot.handle_update(msg("/status"))
    assert "로컬 AI 정상" in client.texts[-1][1]
    bot.handle_update(msg("/rm -rf"))
    assert "알 수 없는 명령" in client.texts[-1][1]


def test_receive_file_saved_to_inbox_with_safe_name(tmp_path):
    bot, client, _ = make_bot(tmp_path)
    client.downloads["f1"] = b"hello"
    bot.handle_update(msg(document={"file_id": "f1", "file_name": "../../evil.txt"}))
    bot.handle_update(msg(document={"file_id": "f1", "file_name": "../../evil.txt"}))
    inbox = tmp_path / "ws" / "inbox"
    assert sorted(os.listdir(inbox)) == ["evil.txt", "evil_1.txt"]
    assert "저장 완료" in client.texts[-1][1]


def test_get_file_and_block_escape(tmp_path):
    bot, client, _ = make_bot(tmp_path)
    (tmp_path / "ws" / "reports").mkdir(parents=True)
    (tmp_path / "ws" / "reports" / "a.json").write_text("{}")
    (tmp_path / "secret.txt").write_text("x")
    bot.handle_update(msg("/get reports/a.json"))
    assert client.docs and client.docs[0][1].endswith("a.json")
    bot.handle_update(msg("/get ../secret.txt"))
    assert "작업 폴더 밖" in client.texts[-1][1]
    assert len(client.docs) == 1


def test_files_listing(tmp_path):
    bot, client, _ = make_bot(tmp_path)
    (tmp_path / "ws" / "x.txt").write_text("abc")
    bot.handle_update(msg("/files"))
    assert "x.txt" in client.texts[-1][1] and "inbox/" in client.texts[-1][1]


def test_run_plan_in_background_and_single_flight(tmp_path):
    (tmp_path / "plans").mkdir()
    (tmp_path / "plans" / "daily_plan.json").write_text("{}")
    gate, finished = threading.Event(), threading.Event()

    def runner(path):
        gate.wait(2)
        finished.set()
        return True, "요약"

    bot, client, _ = make_bot(tmp_path, runner=runner)
    bot.handle_update(msg("/plans"))
    assert "daily_plan.json" in client.texts[-1][1]
    bot.handle_update(msg("/run daily_plan.json"))
    bot.handle_update(msg("/run daily_plan.json"))
    assert "이미 실행 중" in client.texts[-1][1]
    gate.set()
    assert finished.wait(2)
    for _ in range(50):
        if any("계획 완료" in t for _, t in client.texts):
            break
        threading.Event().wait(0.02)
    assert any("계획 완료" in t for _, t in client.texts)
    bot.handle_update(msg("/run ../../etc/passwd"))
    assert "찾을 수 없습니다" in client.texts[-1][1]


def test_offset_persisted(tmp_path):
    bot, client, _ = make_bot(tmp_path)

    class OnceClient(FakeClient):
        def __init__(self):
            super().__init__()
            self.calls = 0

        def get_updates(self, offset=None, timeout=30):
            self.calls += 1
            if self.calls == 1:
                return [{"update_id": 41, "message": {"chat": {"id": OWNER}, "text": "/help"}}]
            bot.stop()
            raise TelegramError("stop")

    bot.client = OnceClient()
    bot.run_forever()
    assert bot._load_offset() == 42


def test_path_helpers(tmp_path):
    assert resolve_inside(str(tmp_path), "a/b.txt").endswith(os.path.join("a", "b.txt"))
    with pytest.raises(PathError):
        resolve_inside(str(tmp_path), "../x")
    assert safe_filename("../a:b?.txt") == "a_b_.txt"
    assert safe_filename("..") == "file"


# ---------- 로컬 AI 점검 ----------
class FakeResp:
    def __init__(self, status, data):
        self.status_code, self._data, self.text = status, data, str(data)

    def json(self):
        return self._data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))


class FakeSession:
    def __init__(self, models=None, down=False):
        self.models, self.down = models or [], down

    def get(self, url, timeout=None):
        if self.down:
            raise requests.ConnectionError("refused")
        return FakeResp(200, {"models": [{"name": m} for m in self.models]})

    def post(self, url, timeout=None, **kw):
        return FakeResp(200, {"message": {"content": "정상"}})


def test_health_server_down():
    h = OllamaAgent(model="qwen2.5:7b", host="http://x", session=FakeSession(down=True)).health()
    assert not h["reachable"] and "연결 실패" in h["error"]
    assert "점검 필요" in format_health(h)


def test_health_model_missing_and_ok():
    h = OllamaAgent(model="qwen2.5:7b", host="http://x", session=FakeSession(["llama3:latest"])).health()
    assert h["reachable"] and not h["model_installed"] and "ollama pull" in h["error"]
    h = OllamaAgent(model="qwen2.5", host="http://x", session=FakeSession(["qwen2.5:latest"])).health()
    assert h["model_installed"] and h["reply"] == "정상" and h["error"] is None
    assert "로컬 AI 정상" in format_health(h)


# ---------- .env 로드 ----------
def test_env_file_parsing_and_no_override(tmp_path, monkeypatch):
    from envfile import load_env, parse_env
    assert parse_env('﻿# 주석\nA=1\nexport B="2"\n$env:C = \'3\'\nD=\n') == {"A": "1", "B": "2", "C": "3", "D": ""}
    path = tmp_path / ".env"
    path.write_text("﻿TELEGRAM_X=new\nKEEP=file\n", encoding="utf-8")
    monkeypatch.delenv("TELEGRAM_X", raising=False)
    monkeypatch.setenv("KEEP", "shell")
    applied = load_env(str(path))
    assert os.environ["TELEGRAM_X"] == "new" and os.environ["KEEP"] == "shell"
    assert applied == {"TELEGRAM_X": "new"}
    assert load_env(str(tmp_path / "missing.env")) == {}


# ---------- 실제 TelegramClient (가짜 HTTP 세션) ----------
class RecordingSession:
    def __init__(self):
        self.posts = []

    def post(self, url, timeout=None, json=None, data=None, files=None):
        self.posts.append({"method": url.rsplit("/", 1)[-1], "timeout": timeout, "json": json})
        return FakeResp(200, {"ok": True, "result": []})


def test_client_get_updates_separates_timeouts():
    from telegram_bot.api import TelegramClient
    s = RecordingSession()
    assert TelegramClient("1:abc", session=s).get_updates(offset=5, timeout=0) == []
    call = s.posts[0]
    assert call["method"] == "getUpdates"
    assert call["json"]["timeout"] == 0 and call["json"]["offset"] == 5
    assert call["timeout"] == 15


def test_client_send_document_uses_long_timeout(tmp_path):
    from telegram_bot.api import TelegramClient
    s = RecordingSession()
    f = tmp_path / "a.txt"
    f.write_text("x")
    TelegramClient("1:abc", session=s).send_document(1, str(f), caption="c")
    assert s.posts[0]["method"] == "sendDocument" and s.posts[0]["timeout"] == 120


# ---------- /goal, /stop ----------
def test_goal_runs_in_background_and_can_stop(tmp_path):
    from types import SimpleNamespace
    started, got_stop = threading.Event(), threading.Event()

    def goal_runner(goal, chat_id, on_event, stop_event):
        on_event("🔧 1/12 web_search")
        started.set()
        stop_event.wait(2)
        got_stop.set()
        return SimpleNamespace(status="STOPPED", answer="중단", steps=[1], report_path="reports/g.md")

    bot, client, _ = make_bot(tmp_path)
    bot.goal_runner = goal_runner
    bot.handle_update(msg("/goal"))
    assert "사용법" in client.texts[-1][1]
    bot.handle_update(msg("/goal 뉴스 요약"))
    assert started.wait(2)
    bot.handle_update(msg("/goal 또 다른 목표"))
    assert "이미 실행 중" in client.texts[-1][1]
    bot.handle_update(msg("/stop"))
    assert got_stop.wait(2)
    for _ in range(50):
        if any("중단됨" in t for _, t in client.texts):
            break
        threading.Event().wait(0.02)
    texts = "\n".join(t for _, t in client.texts)
    assert "목표 접수" in texts and "🔧 1/12" in texts and "/get reports/g.md" in texts
    bot.handle_update(msg("/stop"))
    assert "진행 중인 작업이 없습니다" in client.texts[-1][1]


def test_tools_command(tmp_path):
    bot, client, _ = make_bot(tmp_path)
    bot.tools_desc = "- web_search: 웹 검색"
    bot.handle_update(msg("/tools"))
    assert "web_search" in client.texts[-1][1]
