"""로컬 AI 자율 실행·도구 테스트 (네트워크·Ollama 없음)."""

import json
import os
import threading

import pandas as pd
import pytest

from orchestrator.autonomy import AutonomousAgent, parse_action
from orchestrator.guard import RuleGuard, Rules
from orchestrator.tools import BuiltinTools, Tool, ToolBox, build_toolbox, html_to_text


def scripted(*replies):
    """정해진 순서로 답하는 가짜 로컬 AI. 받은 메시지를 기록한다."""
    seen = []
    it = iter(replies)

    def chat(system, messages, json_mode):
        seen.append({"system": system, "messages": [dict(m) for m in messages], "json": json_mode})
        return next(it)
    chat.seen = seen
    return chat


def j(**kw):
    return json.dumps(kw, ensure_ascii=False)


def simple_box(rules=None, calls=None):
    calls = calls if calls is not None else []
    box = ToolBox(RuleGuard(rules or Rules()))
    box.add(Tool("echo", "받은 값을 돌려준다", lambda text: calls.append(text) or f"echo:{text}",
                 {"text": "값"}, ["text"]))
    box.add(Tool("fetch_url", "웹", lambda url: "본문", {"url": "주소"}, ["url"]))
    return box, calls


# ---------- 루프 ----------
def test_runs_tools_then_finishes_and_saves_report(tmp_path):
    chat = scripted(j(thought="확인", action="echo", args={"text": "a"}),
                    j(thought="끝", action="finish", answer="결과 A"))
    box, calls = simple_box()
    events = []
    res = AutonomousAgent(chat, box, Rules(), str(tmp_path), on_event=events.append).run("테스트 목표")
    assert res.status == "DONE" and res.answer == "결과 A"
    assert calls == ["a"] and len(res.steps) == 1 and events and "echo" in events[0]
    report = (tmp_path / res.report_path).read_text(encoding="utf-8")
    assert "테스트 목표" in report and "echo:a" in report
    # 도구 결과는 observation 으로 감싸 전달, JSON 모드 사용, 도구 설명이 시스템 프롬프트에 포함
    second = chat.seen[1]
    assert "<observation>" in second["messages"][-1]["content"]
    assert second["json"] is True and "echo" in second["system"]


def test_format_errors_stop_after_three(tmp_path):
    chat = scripted("그냥 말", "{bad", '{"no_action": 1}')
    res = AutonomousAgent(chat, simple_box()[0], Rules(), str(tmp_path)).run("g")
    assert res.status == "FAILED" and "형식" in res.answer


def test_recovers_from_one_format_error(tmp_path):
    chat = scripted("앗", j(action="finish", answer="ok"))
    res = AutonomousAgent(chat, simple_box()[0], Rules(), str(tmp_path)).run("g")
    assert res.status == "DONE"


def test_repeated_action_stops(tmp_path):
    same = j(thought="x", action="echo", args={"text": "same"})
    res = AutonomousAgent(scripted(same, same, same, same), simple_box()[0], Rules(), str(tmp_path)).run("g")
    assert res.status == "FAILED" and "반복" in res.answer and len(res.steps) == 2


def test_max_steps(tmp_path):
    replies = [j(action="echo", args={"text": str(i)}) for i in range(5)]
    res = AutonomousAgent(scripted(*replies), simple_box()[0], Rules(), str(tmp_path), max_steps=3).run("g")
    assert res.status == "MAX_STEPS" and len(res.steps) == 3


def test_stop_event(tmp_path):
    stop = threading.Event()
    stop.set()
    res = AutonomousAgent(scripted(), simple_box()[0], Rules(), str(tmp_path), stop_event=stop).run("g")
    assert res.status == "STOPPED"


def test_forbidden_goal_not_started(tmp_path):
    chat = scripted()
    res = AutonomousAgent(chat, simple_box()[0], Rules(forbidden_keywords=["결제"]), str(tmp_path)).run("쇼핑몰 결제 해줘")
    assert res.status == "FAILED" and chat.seen == []


def test_local_ai_down(tmp_path):
    def down(*a):
        raise ConnectionError("ollama down")
    res = AutonomousAgent(down, simple_box()[0], Rules(), str(tmp_path)).run("g")
    assert res.status == "FAILED" and "로컬 AI" in res.answer


def test_parse_action_variants():
    assert parse_action('```json\n{"action": "finish", "answer": "a"}\n```')["action"] == "finish"
    assert parse_action('{"action": "echo", "args": null}')["args"] == {}
    with pytest.raises(ValueError):
        parse_action('{"action": "echo", "args": "x"}')


# ---------- ToolBox 검증 ----------
def test_toolbox_validates_and_enforces_rules():
    box, calls = simple_box(Rules(forbidden_keywords=["password"], allowed_domains=["example.com"]))
    assert "없는 도구" in box.call("shell", {})
    assert "없는 인자" in box.call("echo", {"text": "a", "cmd": "rm"})
    assert "필수 인자" in box.call("echo", {})
    assert "금지 키워드" in box.call("echo", {"text": "my PASSWORD"})
    assert "허용되지 않은 도메인" in box.call("fetch_url", {"url": "https://evil.com"})
    assert box.call("fetch_url", {"url": "https://news.example.com/a"}) == "본문"
    assert calls == []


def test_heavy_tool_budget():
    box = ToolBox(RuleGuard(Rules(max_heavy_calls_per_run=1)))
    box.add(Tool("ask_cloud", "c", lambda agent, prompt: "ok", {"agent": "", "prompt": ""},
                 ["agent", "prompt"], heavy=True))
    assert box.call("ask_cloud", {"agent": "CLAUDE", "prompt": "p"}) == "ok"
    assert "예산 초과" in box.call("ask_cloud", {"agent": "CLAUDE", "prompt": "p"})


def test_observation_truncated():
    box = ToolBox(RuleGuard(Rules()))
    box.add(Tool("big", "", lambda: "x" * 10000))
    out = box.call("big", {})
    assert len(out) < 3200 and "생략" in out


# ---------- 기본 도구 ----------
class FakeResp:
    def __init__(self, data=None, content=b"", status=200):
        self._data, self.content, self.status_code = data, content, status
        self.text = content.decode() if content else json.dumps(data or {})

    def json(self):
        return self._data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


class FakeSession:
    def __init__(self, routes):
        self.routes, self.headers, self.calls = routes, {}, []

    def get(self, url, **kw):
        self.calls.append((url, kw))
        for prefix, resp in self.routes.items():
            if url.startswith(prefix):
                return resp
        raise AssertionError(f"예상 못 한 URL {url}")

    def post(self, url, **kw):
        self.calls.append((url, kw))
        return self.routes["POST"](url, kw)


class FakeOllama:
    host = "http://ollama"


def make_tools(tmp_path, routes=None, **kw):
    return BuiltinTools(str(tmp_path / "ws"), FakeOllama(), session=FakeSession(routes or {}), **kw)


def test_html_to_text():
    raw = "<html><head><title>t</title></head><script>x()</script><p>안녕&amp;하세요</p><div>둘째</div></html>"
    assert html_to_text(raw) == "안녕&하세요\n둘째"


def test_file_tools_stay_in_workspace(tmp_path):
    t = make_tools(tmp_path)
    assert "저장 완료" in t.write_file("reports/a.md", "내용")
    assert t.read_file("reports/a.md") == "내용"
    assert "reports/" in t.list_files(".")
    with pytest.raises(Exception):
        t.write_file("../escape.md", "x")
    with pytest.raises(Exception):
        t.write_file("run.bat", "x")
    with pytest.raises(Exception):
        t.read_file("../../etc/passwd")


def test_bing_fallback_and_naver(tmp_path, monkeypatch):
    rss = (b'<?xml version="1.0"?><rss><channel><item><title>T1</title><link>https://a.com</link>'
           b'<description>&lt;b&gt;S1&lt;/b&gt;</description></item></channel></rss>')
    monkeypatch.delenv("NAVER_CLIENT_ID", raising=False)
    t = make_tools(tmp_path, {"https://www.bing.com/search": FakeResp(content=rss)})
    assert t.web_search("상수도") == [{"title": "T1", "url": "https://a.com", "date": "", "snippet": "S1"}]

    monkeypatch.setenv("NAVER_CLIENT_ID", "id")
    monkeypatch.setenv("NAVER_CLIENT_SECRET", "sec")
    naver = FakeResp({"items": [{"title": "<b>누수</b> 탐지", "originallink": "https://n.com", "pubDate": "d",
                                 "description": "설명"}]})
    t = make_tools(tmp_path, {"https://openapi.naver.com/v1/search/news.json": naver})
    res = t.news_search("누수", 3)
    assert res[0]["title"] == "누수 탐지" and res[0]["url"] == "https://n.com"
    assert t.session.calls[0][1]["headers"]["X-Naver-Client-Id"] == "id"


def test_ask_model_and_describe_image(tmp_path, monkeypatch):
    posted = []

    def post(url, kw):
        posted.append(kw["json"])
        return FakeResp({"message": {"content": "답"}})

    tags = FakeResp({"models": [{"name": "exaone3.5:7.8b"}, {"name": "moondream:latest"}]})
    t = make_tools(tmp_path, {"http://ollama/api/tags": tags, "POST": post})
    monkeypatch.setattr("orchestrator.agents.llm.requests.Session", lambda: t.session)
    assert t.ask_model("exaone3.5:7.8b", "써줘") == "답"
    with pytest.raises(Exception):
        t.ask_model("없는모델", "x")
    img = tmp_path / "ws" / "inbox"
    img.mkdir(parents=True)
    (img / "p.jpg").write_bytes(b"\xff\xd8fake")
    assert t.describe_image("inbox/p.jpg") == "답"
    assert posted[-1]["model"] == "moondream:latest" and posted[-1]["messages"][-1]["images"]


def test_market_check_uses_indicators(tmp_path, monkeypatch):
    idx = pd.date_range("2026-01-01", periods=40, freq="5min", tz="UTC")
    df = pd.DataFrame({"Close": [100.0] * 39 + [90.0], "Volume": [1000] * 40}, index=idx)
    monkeypatch.setattr("market_monitor.monitor.yfinance_fetcher", lambda t, p, i: df)
    res = make_tools(tmp_path).market_check("tsla")
    assert res["ticker"] == "TSLA" and res["daily_trend"] == "정보 부족"
    assert any(s.startswith("주가 급등락") for s in res["signals"]) and res["score"] > 0


def test_notify_and_send_file_callbacks(tmp_path):
    sent = []
    t = make_tools(tmp_path, notify=lambda m: sent.append(("msg", m)),
                   send_file=lambda p, c: sent.append(("file", os.path.basename(p), c)))
    t.write_file("r.md", "x")
    t.notify_user("중간 보고")
    t.send_file("r.md", "결과")
    assert sent == [("msg", "중간 보고"), ("file", "r.md", "결과")]


def test_build_toolbox_cloud_only_when_available(tmp_path):
    guard = RuleGuard(Rules())
    names = set(build_toolbox(make_tools(tmp_path), guard).tools)
    assert "ask_cloud" not in names and {"web_search", "fetch_url", "describe_image", "market_check"} <= names

    class Cloud:
        def _call(self, prompt):
            return "cloud"
    box = build_toolbox(make_tools(tmp_path, cloud={"CLAUDE": Cloud()}), guard)
    assert box.call("ask_cloud", {"agent": "claude", "prompt": "p"}) == "cloud"
