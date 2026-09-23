"""에이전트 레지스트리 구성 및 계획 실행 헬퍼 (CLI·텔레그램 봇 공용)."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Dict, Iterable, Tuple

from .agents.base import Agent, MockAgent
from .agents.llm import AgentConfigError, ClaudeAgent, GeminiAgent, GrokAgent, OllamaAgent
from .agents.local_tools import LocalToolAgent
from .engine import TaskEngine
from .guard import RuleGuard, Rules
from .models import Task, TaskStatus, parse_plan

log = logging.getLogger(__name__)

AGENT_DESC = {
    "LOCAL_AI": "로컬 Ollama. 단순 분류·파싱·짧은 요약 (무료, 병렬)",
    "LOCAL_PYTHON": "화이트리스트 도구 실행: fetch_url, save_json (병렬)",
    "CLAUDE": "계획 수립, 코드/오류 분석, 종합 판단 (유료, 순차)",
    "GEMINI": "대용량 문서·멀티모달 분석 (유료, 순차)",
    "GROK": "실시간 트렌드·소셜 탐색 (유료, 순차)",
}
HEAVY = {"CLAUDE", "GEMINI", "GROK"}


def build_agents(mock: bool, workspace: str) -> Dict[str, Agent]:
    """에이전트 레지스트리를 만든다. 실제 모드에선 키가 있는 것만 활성화한다."""
    if mock:
        agents: Dict[str, Agent] = {n: MockAgent(n, heavy=n in HEAVY) for n in AGENT_DESC}
        agents["LOCAL_PYTHON"] = LocalToolAgent(workspace)
        return agents

    agents = {"LOCAL_AI": OllamaAgent(), "LOCAL_PYTHON": LocalToolAgent(workspace)}
    for cls in (ClaudeAgent, GeminiAgent, GrokAgent):
        try:
            agents[cls.name] = cls()
        except AgentConfigError as e:
            log.warning("%s 비활성화: %s", cls.name, e)
    return agents


def format_report(tasks: Iterable[Task]) -> str:
    """태스크 결과를 한 줄씩 요약한다."""
    lines = []
    for t in tasks:
        mark = {"COMPLETED": "✔", "FAILED": "✖", "SKIPPED": "⤼"}.get(t.status.value, "?")
        lines.append(f"{mark} {t.task_id:<24} {t.agent:<13} {t.status.value:<10} {t.error or ''}".rstrip())
    return "\n".join(lines)


def run_plan_file(plan_path: str, rules: Rules, workspace: str, state_path: str,
                  mock: bool = False, parallel: int = 4, resume: bool = False) -> Tuple[bool, str]:
    """계획 파일을 실행하고 (전부 성공 여부, 요약 문자열)을 반환한다."""
    agents = build_agents(mock, workspace)
    with open(plan_path, "r", encoding="utf-8") as f:
        tasks = parse_plan(json.load(f), agents)
    guard = RuleGuard(rules, heavy_agents={n for n, a in agents.items() if a.heavy})
    engine = TaskEngine(agents, guard, state_path=state_path, light_concurrency=parallel)
    result = asyncio.run(engine.run(tasks, goal=rules.goal, resume=resume))
    ok = all(t.status is TaskStatus.COMPLETED for t in result.values())
    summary = format_report(result.values()) + f"\n클라우드 호출: {guard.summary()}"
    return ok, summary
