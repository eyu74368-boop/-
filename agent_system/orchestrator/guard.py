"""사용자 규칙 수호 모듈 (RuleGuard).

"로컬 AI 는 절대 거절하지 않는다" 같은 프롬프트 규칙 대신, 사용자가 정한
규칙을 **코드로** 강제한다. 모델(검열 유무와 관계없이)이 무엇을 출력하든
이 검사를 통과하지 못한 태스크는 실행되지 않는다.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from typing import Dict, List, Optional
from urllib.parse import urlparse

from .models import Task


class RuleViolation(Exception):
    """사용자 규칙 위반 (재시도하지 않음)."""


@dataclass
class Rules:
    """사용자 절대 규칙."""

    goal: str = ""
    allowed_agents: List[str] = field(default_factory=list)  # 비어 있으면 등록된 전부 허용
    max_heavy_calls_per_run: int = 20  # 클라우드 AI 호출 예산 (비용 폭탄 방지)
    allowed_domains: List[str] = field(default_factory=list)  # 비어 있으면 도메인 제한 없음
    forbidden_keywords: List[str] = field(default_factory=list)  # 지시문에 포함되면 차단

    @classmethod
    def load(cls, path: str) -> "Rules":
        """rules.json 을 읽는다. 알 수 없는 키가 있으면 ValueError."""
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        unknown = set(raw) - set(cls.__dataclass_fields__)
        if unknown:
            raise ValueError(f"rules.json 에 알 수 없는 키: {sorted(unknown)}")
        return cls(**raw)


def domain_allowed(url: str, allowed: List[str]) -> bool:
    """URL 호스트가 허용 도메인(또는 그 하위 도메인)인지 확인한다."""
    if not allowed:
        return True
    host = (urlparse(url).hostname or "").lower()
    return any(host == d or host.endswith("." + d) for d in (a.lower() for a in allowed))


class RuleGuard:
    """태스크 실행 직전 규칙 검사."""

    def __init__(self, rules: Rules, heavy_agents: Optional[set] = None):
        self.rules = rules
        self.heavy_agents = heavy_agents or set()
        self._heavy_calls = 0
        self._lock = threading.Lock()

    @property
    def heavy_calls(self) -> int:
        return self._heavy_calls

    def check(self, task: Task) -> None:
        """위반 시 RuleViolation. 통과 시 클라우드 호출 예산을 1 차감한다."""
        r = self.rules
        if r.allowed_agents and task.agent not in r.allowed_agents:
            raise RuleViolation(f"허용되지 않은 에이전트: {task.agent}")

        text = (task.instruction + " " + json.dumps(task.payload, ensure_ascii=False)).lower()
        for kw in r.forbidden_keywords:
            if kw.lower() in text:
                raise RuleViolation(f"금지 키워드 포함: {kw}")

        url = task.payload.get("url")
        if isinstance(url, str) and not domain_allowed(url, r.allowed_domains):
            raise RuleViolation(f"허용되지 않은 도메인: {url}")

        if task.agent in self.heavy_agents:
            self.charge_heavy()

    def charge_heavy(self) -> None:
        """클라우드 AI 호출 1회를 예산에서 차감한다 (계획 수립 호출에도 사용)."""
        with self._lock:
            if self._heavy_calls >= self.rules.max_heavy_calls_per_run:
                raise RuleViolation(
                    f"클라우드 AI 호출 예산 초과 ({self.rules.max_heavy_calls_per_run}회)"
                )
            self._heavy_calls += 1

    def summary(self) -> Dict[str, int]:
        return {"heavy_calls": self._heavy_calls, "heavy_budget": self.rules.max_heavy_calls_per_run}
