"""로컬 '손과 발' 에이전트.

AI 가 생성한 임의 코드를 실행하지 않는다. 미리 등록된 도구(화이트리스트)만
``payload = {"tool": 이름, "args": {...}}`` 형태로 호출할 수 있다.
새 도구는 사람이 코드로 추가하고 ``register`` 한다.

기본 제공 도구:
- ``fetch_url``: robots.txt 확인 + 도메인별 최소 간격을 지키는 웹 수집
- ``save_json``: 작업 폴더(workspace) 안에만 파일 저장
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
import time
from typing import Any, Callable, Dict, Optional
from urllib import robotparser
from urllib.parse import urlparse

import requests

from ..models import Task
from .base import Agent

USER_AGENT = "LocalAgentBot/0.1 (+personal research)"
MAX_BYTES = 2_000_000


class ToolError(RuntimeError):
    """도구 실행 오류."""


class LocalToolAgent(Agent):
    """화이트리스트 도구 실행기."""

    name = "LOCAL_PYTHON"
    heavy = False

    def __init__(self, workspace: str = "./agent_workspace", min_interval_sec: float = 2.0,
                 session: Optional[requests.Session] = None):
        self.workspace = os.path.abspath(workspace)
        os.makedirs(self.workspace, exist_ok=True)
        self.min_interval_sec = min_interval_sec
        self.session = session or requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT
        self._tools: Dict[str, Callable[..., Any]] = {}
        self._last_hit: Dict[str, float] = {}
        self._robots: Dict[str, robotparser.RobotFileParser] = {}
        self._lock = threading.Lock()
        self.register("fetch_url", self.fetch_url)
        self.register("save_json", self.save_json)

    def register(self, name: str, fn: Callable[..., Any]) -> None:
        """사람이 검토한 도구 함수를 등록한다."""
        self._tools[name] = fn

    async def run(self, task: Task, deps: Dict[str, Any]) -> Any:
        tool = task.payload.get("tool")
        if tool not in self._tools:
            raise ToolError(f"등록되지 않은 도구: {tool} (가능: {sorted(self._tools)})")
        args = task.payload.get("args", {})
        if not isinstance(args, dict):
            raise ToolError("args 는 객체여야 합니다.")
        if tool == "fetch_url" and "url" not in args and "url" in task.payload:
            args = {**args, "url": task.payload["url"]}
        if tool == "save_json" and "data" not in args:
            args = {**args, "data": deps}  # data 미지정 시 선행 작업 결과를 저장
        return await asyncio.to_thread(self._tools[tool], **args)

    # ---------- 도구 ----------
    def _robots_allows(self, url: str) -> bool:
        parsed = urlparse(url)
        base = f"{parsed.scheme}://{parsed.netloc}"
        with self._lock:
            rp = self._robots.get(base)
        if rp is None:
            rp = robotparser.RobotFileParser()
            try:
                res = self.session.get(base + "/robots.txt", timeout=10)
                rp.parse(res.text.splitlines() if res.status_code == 200 else [])
            except requests.RequestException:
                rp.parse([])  # robots.txt 를 못 읽으면 제한 없음으로 간주
            with self._lock:
                self._robots[base] = rp
        return rp.can_fetch(USER_AGENT, url)

    def _throttle(self, host: str) -> None:
        with self._lock:
            wait = self._last_hit.get(host, 0) + self.min_interval_sec - time.monotonic()
            self._last_hit[host] = time.monotonic() + max(wait, 0)
        if wait > 0:
            time.sleep(wait)

    def fetch_url(self, url: str) -> Dict[str, Any]:
        """공개 웹페이지를 가져온다 (http/https, robots 준수, 크기 제한)."""
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise ToolError(f"허용되지 않은 URL: {url}")
        if not self._robots_allows(url):
            raise ToolError(f"robots.txt 가 수집을 금지합니다: {url}")
        self._throttle(parsed.hostname)
        res = self.session.get(url, timeout=20, stream=True)
        res.raise_for_status()
        body = res.raw.read(MAX_BYTES + 1, decode_content=True)
        truncated = len(body) > MAX_BYTES
        text = body[:MAX_BYTES].decode(res.encoding or "utf-8", errors="replace")
        return {"url": url, "status": res.status_code, "text": text, "truncated": truncated}

    def save_json(self, filename: str, data: Any) -> Dict[str, Any]:
        """workspace 내부에만 JSON 을 저장한다 (경로 탈출 차단)."""
        path = os.path.abspath(os.path.join(self.workspace, filename))
        if os.path.commonpath([path, self.workspace]) != self.workspace:
            raise ToolError(f"workspace 밖으로 저장할 수 없습니다: {filename}")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return {"saved": os.path.relpath(path, self.workspace)}
