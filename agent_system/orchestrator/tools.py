"""자율 모드에서 로컬 AI 가 호출할 수 있는 도구 모음.

지금까지 사용한 수단(웹 수집, 작업 폴더 파일, 설치된 Ollama 모델, 이미지 인식,
시장 지표, 텔레그램 보고, 클라우드 AI)을 **정해진 인자만 받는 함수**로 감싼다.
로컬 AI 는 임의 코드를 실행할 수 없고 이 목록 안에서만 고를 수 있다.
"""

from __future__ import annotations

import base64
import html
import json
import logging
import os
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import quote_plus

import requests

from .agents.llm import HttpLLMAgent, OllamaAgent
from .agents.local_tools import LocalToolAgent, ToolError
from .guard import RuleGuard, RuleViolation, domain_allowed

log = logging.getLogger(__name__)

OBS_LIMIT = 3000  # 도구 결과를 모델에 돌려줄 때 최대 글자 수
READ_LIMIT = 20_000
IMAGE_LIMIT = 10 * 1024 * 1024
TEXT_EXT = {".txt", ".md", ".json", ".csv", ".log", ".html", ".xml", ".py", ".yaml", ".yml"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}


@dataclass
class Tool:
    """도구 1개. params 는 {인자명: 설명}, required 는 필수 인자 목록."""

    name: str
    description: str
    func: Callable[..., Any]
    params: Dict[str, str] = field(default_factory=dict)
    required: List[str] = field(default_factory=list)
    heavy: bool = False  # 클라우드 호출(예산 차감) 여부


def html_to_text(raw: str) -> str:
    """HTML 에서 스크립트·태그를 제거하고 읽을 수 있는 텍스트만 남긴다."""
    raw = re.sub(r"(?is)<(script|style|noscript|svg|head)[^>]*>.*?</\1>", " ", raw)
    raw = re.sub(r"(?i)<br\s*/?>|</(p|div|li|h[1-6]|tr)>", "\n", raw)
    raw = re.sub(r"<[^>]+>", " ", raw)
    raw = html.unescape(raw)
    raw = re.sub(r"[ \t\r\f\v]+", " ", raw)
    return "\n".join(line.strip() for line in raw.split("\n") if line.strip())


def truncate(value: Any, limit: int = OBS_LIMIT) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    return text if len(text) <= limit else text[:limit] + f"\n…(이하 {len(text) - limit}자 생략)"


class ToolBox:
    """도구 등록·설명·검증·실행."""

    def __init__(self, guard: RuleGuard):
        self.guard = guard
        self.tools: Dict[str, Tool] = {}

    def add(self, tool: Tool) -> None:
        self.tools[tool.name] = tool

    def describe(self) -> str:
        """시스템 프롬프트에 넣을 도구 설명."""
        lines = []
        for t in self.tools.values():
            args = ", ".join(
                f'"{k}": {v}' + ("" if k in t.required else " (선택)") for k, v in t.params.items()
            )
            lines.append(f"- {t.name}: {t.description}\n  args: {{{args}}}")
        return "\n".join(lines)

    def _check_rules(self, tool: Tool, args: Dict[str, Any]) -> None:
        rules = self.guard.rules
        text = json.dumps(args, ensure_ascii=False).lower()
        for kw in rules.forbidden_keywords:
            if kw.lower() in text:
                raise RuleViolation(f"금지 키워드 포함: {kw}")
        url = args.get("url")
        if isinstance(url, str) and not domain_allowed(url, rules.allowed_domains):
            raise RuleViolation(f"허용되지 않은 도메인: {url}")
        if tool.heavy:
            self.guard.charge_heavy()

    def call(self, name: str, args: Dict[str, Any]) -> str:
        """도구를 실행하고 결과 문자열을 반환한다. 오류도 문자열로 돌려 모델이 대처하게 한다."""
        tool = self.tools.get(name)
        if tool is None:
            return f"오류: 없는 도구 '{name}'. 사용 가능: {', '.join(self.tools)}"
        if not isinstance(args, dict):
            return "오류: args 는 객체여야 합니다."
        unknown = set(args) - set(tool.params)
        if unknown:
            return f"오류: {name} 에 없는 인자 {sorted(unknown)}. 가능한 인자: {list(tool.params)}"
        missing = [k for k in tool.required if args.get(k) in (None, "")]
        if missing:
            return f"오류: {name} 필수 인자 누락 {missing}"
        try:
            self._check_rules(tool, args)
            return truncate(tool.func(**args))
        except RuleViolation as e:
            return f"규칙 위반으로 차단: {e}"
        except (ToolError, ValueError, OSError, requests.RequestException) as e:
            return f"도구 실행 실패: {type(e).__name__}: {e}"
        except Exception as e:  # 예상 못 한 오류도 루프는 계속
            log.exception("도구 %s 오류", name)
            return f"도구 실행 실패: {type(e).__name__}: {e}"


class BuiltinTools:
    """기본 도구 구현."""

    def __init__(
        self,
        workspace: str,
        ollama: OllamaAgent,
        notify: Optional[Callable[[str], None]] = None,
        send_file: Optional[Callable[[str, str], None]] = None,
        cloud: Optional[Dict[str, HttpLLMAgent]] = None,
        session: Optional[requests.Session] = None,
    ):
        self.local = LocalToolAgent(workspace, session=session)
        self.workspace = self.local.workspace
        self.ollama = ollama
        self.notify_fn = notify
        self.send_file_fn = send_file
        self.cloud = cloud or {}
        self.session = session or requests.Session()
        self._models: Optional[List[str]] = None

    # ---------- 경로 ----------
    def _path(self, rel: str) -> str:
        path = os.path.realpath(os.path.join(self.workspace, str(rel).strip().lstrip("/\\")))
        if os.path.commonpath([path, os.path.realpath(self.workspace)]) != os.path.realpath(self.workspace):
            raise ToolError(f"작업 폴더 밖 경로는 사용할 수 없습니다: {rel}")
        return path

    # ---------- 웹 ----------
    def web_search(self, query: str, max_results: int = 5) -> List[Dict[str, str]]:
        """네이버 검색 API(키 있으면) → 없으면 Bing RSS."""
        n = max(1, min(int(max_results), 10))
        cid, secret = os.getenv("NAVER_CLIENT_ID", ""), os.getenv("NAVER_CLIENT_SECRET", "")
        if cid and secret:
            res = self.session.get(
                "https://openapi.naver.com/v1/search/webkr.json",
                params={"query": query, "display": n},
                headers={"X-Naver-Client-Id": cid, "X-Naver-Client-Secret": secret},
                timeout=15,
            )
            res.raise_for_status()
            return [
                {"title": html_to_text(i.get("title", "")), "url": i.get("link", ""),
                 "snippet": html_to_text(i.get("description", ""))}
                for i in res.json().get("items", [])
            ]
        return self._bing_rss(f"https://www.bing.com/search?q={quote_plus(query)}&format=rss", n)

    def news_search(self, query: str, max_results: int = 5) -> List[Dict[str, str]]:
        """네이버 뉴스 API(키 있으면) → 없으면 Bing 뉴스 RSS."""
        n = max(1, min(int(max_results), 10))
        cid, secret = os.getenv("NAVER_CLIENT_ID", ""), os.getenv("NAVER_CLIENT_SECRET", "")
        if cid and secret:
            res = self.session.get(
                "https://openapi.naver.com/v1/search/news.json",
                params={"query": query, "display": n, "sort": "date"},
                headers={"X-Naver-Client-Id": cid, "X-Naver-Client-Secret": secret},
                timeout=15,
            )
            res.raise_for_status()
            return [
                {"title": html_to_text(i.get("title", "")), "url": i.get("originallink") or i.get("link", ""),
                 "date": i.get("pubDate", ""), "snippet": html_to_text(i.get("description", ""))}
                for i in res.json().get("items", [])
            ]
        return self._bing_rss(f"https://www.bing.com/news/search?q={quote_plus(query)}&format=rss", n)

    def _bing_rss(self, url: str, n: int) -> List[Dict[str, str]]:
        res = self.session.get(url, timeout=15, headers={"User-Agent": self.local.session.headers["User-Agent"]})
        res.raise_for_status()
        items = ET.fromstring(res.content).findall("./channel/item")[:n]
        results = [
            {"title": i.findtext("title") or "", "url": i.findtext("link") or "",
             "date": i.findtext("pubDate") or "", "snippet": html_to_text(i.findtext("description") or "")}
            for i in items
        ]
        if not results:
            return [{"note": "검색 결과 없음. 영어 키워드로 바꾸거나, .env 에 NAVER_CLIENT_ID/SECRET 을 설정하면 한국어 검색이 좋아집니다."}]
        return results

    def fetch_url(self, url: str) -> Dict[str, Any]:
        """웹페이지 본문 텍스트 (robots.txt 준수, 요청 간격 제한)."""
        page = self.local.fetch_url(url)
        text = html_to_text(page["text"])
        return {"url": url, "status": page["status"], "text": truncate(text, OBS_LIMIT - 200)}

    # ---------- 파일 ----------
    def list_files(self, folder: str = ".") -> List[str]:
        path = self._path(folder)
        if not os.path.isdir(path):
            raise ToolError(f"폴더가 없습니다: {folder}")
        out = []
        for name in sorted(os.listdir(path))[:100]:
            if name.startswith("."):
                continue
            full = os.path.join(path, name)
            out.append(name + "/" if os.path.isdir(full) else f"{name} ({os.path.getsize(full)}B)")
        return out

    def read_file(self, path: str) -> str:
        full = self._path(path)
        if os.path.splitext(full)[1].lower() not in TEXT_EXT:
            raise ToolError("텍스트 파일만 읽을 수 있습니다. 이미지는 describe_image 를 사용하세요.")
        with open(full, "r", encoding="utf-8", errors="replace") as f:
            return f.read(READ_LIMIT)

    def write_file(self, path: str, content: str) -> str:
        full = self._path(path)
        if os.path.splitext(full)[1].lower() not in TEXT_EXT:
            raise ToolError(f"허용된 확장자만 저장 가능: {sorted(TEXT_EXT)}")
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as f:
            f.write(str(content))
        return f"저장 완료: {os.path.relpath(full, self.workspace)} ({len(str(content))}자)"

    # ---------- 로컬 모델 ----------
    def installed_models(self) -> List[str]:
        if self._models is None:
            res = self.session.get(f"{self.ollama.host}/api/tags", timeout=5)
            res.raise_for_status()
            self._models = [m.get("name", "") for m in res.json().get("models", [])]
        return self._models

    def _resolve_model(self, model: str) -> str:
        models = self.installed_models()
        if model in models:
            return model
        if model + ":latest" in models:
            return model + ":latest"
        raise ToolError(f"설치되지 않은 모델: {model}. 설치된 모델: {models}")

    def ask_model(self, model: str, prompt: str) -> str:
        """다른 로컬 모델에게 질문 (예: exaone3.5 로 한국어 글쓰기, qwen3 로 깊은 추론)."""
        agent = OllamaAgent(model=self._resolve_model(model), host=self.ollama.host)
        return agent.chat("너는 요청받은 작업만 한국어로 수행한다.", prompt)

    def describe_image(self, path: str, question: str = "이 이미지에 무엇이 있는지 자세히 설명해줘.") -> str:
        """작업 폴더의 이미지를 비전 모델로 분석."""
        full = self._path(path)
        if os.path.splitext(full)[1].lower() not in IMAGE_EXT:
            raise ToolError("이미지 파일(jpg, png, webp, bmp)만 분석할 수 있습니다.")
        if os.path.getsize(full) > IMAGE_LIMIT:
            raise ToolError("이미지가 너무 큽니다 (최대 10MB).")
        with open(full, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        model = self._resolve_model(os.getenv("VISION_MODEL", "moondream"))
        agent = OllamaAgent(model=model, host=self.ollama.host)
        return agent.chat_messages("Describe images accurately.",
                                   [{"role": "user", "content": question, "images": [b64]}])

    # ---------- 시장 ----------
    def market_check(self, ticker: str) -> Dict[str, Any]:
        """종목의 5분봉 지표 시그널과 일봉 추세 (읽기 전용)."""
        from market_monitor.config import TickerConfig
        from market_monitor.indicators import daily_trend, evaluate
        from market_monitor.monitor import drop_incomplete_bar, yfinance_fetcher

        ticker = ticker.strip().upper()
        df = drop_incomplete_bar(yfinance_fetcher(ticker, "5d", "5m"))
        if df is None or df.empty:
            raise ToolError(f"시세 데이터를 받을 수 없습니다: {ticker}")
        ev = evaluate(df, TickerConfig())
        return {
            "ticker": ticker, "price": round(ev.price, 4), "rsi": round(ev.rsi, 1),
            "score": ev.score, "direction": ev.direction, "grade": ev.grade,
            "signals": [f"{s.name}({s.detail})" for s in ev.signals],
            "daily_trend": daily_trend(yfinance_fetcher(ticker, "1y", "1d")),
        }

    # ---------- 보고 ----------
    def notify_user(self, text: str) -> str:
        if self.notify_fn is None:
            print(f"[알림] {text}")
        else:
            self.notify_fn(str(text))
        return "사용자에게 전송함"

    def send_file(self, path: str, caption: str = "") -> str:
        full = self._path(path)
        if not os.path.isfile(full):
            raise ToolError(f"파일이 없습니다: {path}")
        if self.send_file_fn is None:
            print(f"[파일 전송] {full}")
        else:
            self.send_file_fn(full, caption)
        return "파일 전송함"

    # ---------- 클라우드 ----------
    def ask_cloud(self, agent: str, prompt: str) -> str:
        key = agent.strip().upper()
        if key not in self.cloud:
            raise ToolError(f"사용 가능한 클라우드 AI 가 아닙니다: {agent} (가능: {sorted(self.cloud)})")
        return self.cloud[key]._call(prompt)


def build_toolbox(tools: BuiltinTools, guard: RuleGuard, enable_market: bool = True) -> ToolBox:
    """기본 도구를 등록한 ToolBox 를 만든다."""
    box = ToolBox(guard)
    add = box.add
    add(Tool("web_search", "웹 검색. 결과 목록(title, url, snippet)을 돌려준다.", tools.web_search,
             {"query": "검색어", "max_results": "결과 수 1~10"}, ["query"]))
    add(Tool("news_search", "최신 뉴스 검색.", tools.news_search,
             {"query": "검색어", "max_results": "결과 수 1~10"}, ["query"]))
    add(Tool("fetch_url", "웹페이지 본문 텍스트를 읽는다.", tools.fetch_url, {"url": "http(s) 주소"}, ["url"]))
    add(Tool("list_files", "작업 폴더의 파일 목록.", tools.list_files, {"folder": "폴더 경로 (기본 .)"}))
    add(Tool("read_file", "작업 폴더의 텍스트 파일을 읽는다.", tools.read_file, {"path": "파일 경로"}, ["path"]))
    add(Tool("write_file", "작업 폴더에 텍스트 파일(.md .txt .json .csv 등)을 저장한다.", tools.write_file,
             {"path": "파일 경로 (예: reports/요약.md)", "content": "내용"}, ["path", "content"]))
    add(Tool("ask_model", "설치된 다른 로컬 모델에게 작업을 맡긴다 (예: exaone3.5:7.8b 한국어 글쓰기, "
             "qwen3:14b 깊은 추론).", tools.ask_model, {"model": "모델 이름", "prompt": "지시문"}, ["model", "prompt"]))
    add(Tool("describe_image", "작업 폴더의 이미지를 비전 모델로 분석한다 (텔레그램으로 받은 사진은 inbox/ 에 있음).",
             tools.describe_image, {"path": "이미지 경로", "question": "질문"}, ["path"]))
    if enable_market:
        add(Tool("market_check", "주식·코인 티커의 RSI·이동평균·볼린저 시그널과 일봉 추세를 조회한다 (읽기 전용).",
                 tools.market_check, {"ticker": "예: NVDA, AAPL, BTC-USD, 005930.KS"}, ["ticker"]))
    add(Tool("notify_user", "작업 도중 사용자에게 텔레그램 메시지를 보낸다 (중간 보고용).", tools.notify_user,
             {"text": "보낼 내용"}, ["text"]))
    add(Tool("send_file", "작업 폴더의 파일을 사용자에게 텔레그램으로 보낸다.", tools.send_file,
             {"path": "파일 경로", "caption": "설명"}, ["path"]))
    if tools.cloud:
        add(Tool("ask_cloud", f"클라우드 AI 에게 어려운 분석을 맡긴다 (유료, 호출 한도 있음). "
                 f"가능: {', '.join(sorted(tools.cloud))}", tools.ask_cloud,
                 {"agent": "클라우드 AI 이름", "prompt": "지시문"}, ["agent", "prompt"], heavy=True))
    return box
