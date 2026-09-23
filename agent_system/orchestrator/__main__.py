"""오케스트레이터 CLI.

사용 예:
  # 1) 고정 계획 실행 (API 키 없이 흐름 확인)
  python -m orchestrator run config/plan.example.json --rules config/rules.example.json --mock

  # 2) 실제 에이전트로 고정 계획 실행 (키가 있는 에이전트만 활성화)
  python -m orchestrator run config/plan.example.json --rules config/rules.example.json

  # 3) 자율 루프: 로컬 AI 판단 → Claude 계획 → 실행 반복 (Ollama + ANTHROPIC_API_KEY 필요)
  python -m orchestrator loop --rules config/rules.example.json --max-rounds 3
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from typing import Dict

from .agents.base import Agent, MockAgent
from .agents.llm import AgentConfigError, ClaudeAgent, GeminiAgent, GrokAgent, OllamaAgent
from .agents.local_tools import LocalToolAgent
from .commander import Commander, Planner, summarize
from .engine import TaskEngine
from .guard import RuleGuard, Rules
from .models import PlanError, TaskStatus, parse_plan

log = logging.getLogger("orchestrator")

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


def print_report(tasks) -> None:
    for t in tasks:
        mark = {"COMPLETED": "✔", "FAILED": "✖", "SKIPPED": "⤼"}.get(t.status.value, "?")
        print(f"{mark} {t.task_id:<24} {t.agent:<13} {t.status.value:<10} {t.error or ''}")


def cmd_run(args, rules: Rules) -> int:
    agents = build_agents(args.mock, args.workspace)
    with open(args.plan, "r", encoding="utf-8") as f:
        tasks = parse_plan(json.load(f), agents)
    guard = RuleGuard(rules, heavy_agents={n for n, a in agents.items() if a.heavy})
    engine = TaskEngine(agents, guard, state_path=args.state, light_concurrency=args.parallel)
    result = asyncio.run(engine.run(tasks, goal=rules.goal, resume=args.resume))
    print_report(result.values())
    print("클라우드 호출:", guard.summary())
    return 0 if all(t.status is TaskStatus.COMPLETED for t in result.values()) else 1


def cmd_loop(args, rules: Rules) -> int:
    agents = build_agents(False, args.workspace)
    if "CLAUDE" not in agents:
        log.error("loop 모드는 계획 수립용 ANTHROPIC_API_KEY 가 필요합니다.")
        return 2
    local: OllamaAgent = agents["LOCAL_AI"]  # type: ignore[assignment]
    claude: ClaudeAgent = agents["CLAUDE"]  # type: ignore[assignment]
    guard = RuleGuard(rules, heavy_agents={n for n, a in agents.items() if a.heavy})
    commander = Commander(rules, local.chat)
    planner = Planner(claude._call, {k: v for k, v in AGENT_DESC.items() if k in agents})

    summary: Dict = {}
    for rnd in range(1, args.max_rounds + 1):
        decision = commander.decide(summary)
        log.info("[라운드 %d] 관제소 판단: %s (%s)", rnd, decision.action, decision.reason)
        if decision.action == "DONE":
            print("🎉 목표 달성으로 판단되어 종료합니다.")
            return 0
        if decision.action in ("ESCALATE", "WAIT"):
            print(f"⏸ 사람 확인 필요: {decision.reason}\n최근 결과: {json.dumps(summary, ensure_ascii=False)[:2000]}")
            return 3
        try:
            guard.charge_heavy()
            tasks = planner.plan(rules.goal, decision.instruction)
        except Exception as e:
            log.error("계획 수립 실패 → 사람 확인 필요: %s", e)
            return 3
        engine = TaskEngine(agents, guard, state_path=args.state, light_concurrency=args.parallel)
        result = asyncio.run(engine.run(tasks, goal=rules.goal))
        print_report(result.values())
        summary = summarize(result.values())
    print(f"⚠️ 최대 라운드({args.max_rounds}) 도달. 사람 확인 후 재실행하세요.")
    return 4


def main() -> int:
    """CLI 진입점."""
    logging.basicConfig(level=logging.INFO, format="[%(asctime)s][%(levelname)s] %(message)s")
    p = argparse.ArgumentParser(prog="orchestrator")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("run", "loop"):
        sp = sub.add_parser(name)
        sp.add_argument("--rules", required=True, help="사용자 규칙 JSON")
        sp.add_argument("--workspace", default="./agent_workspace")
        sp.add_argument("--state", default="./agent_workspace/state.json")
        sp.add_argument("--parallel", type=int, default=4, help="가벼운 작업 동시 실행 수")
        if name == "run":
            sp.add_argument("plan", help="태스크 계획 JSON")
            sp.add_argument("--mock", action="store_true", help="API 없이 가짜 에이전트로 실행")
            sp.add_argument("--resume", action="store_true", help="이전 완료 태스크 건너뛰기")
        else:
            sp.add_argument("--max-rounds", type=int, default=3)
    args = p.parse_args()

    try:
        rules = Rules.load(args.rules)
    except (OSError, ValueError) as e:
        log.error("규칙 파일 오류: %s", e)
        return 2
    try:
        return cmd_run(args, rules) if args.cmd == "run" else cmd_loop(args, rules)
    except PlanError as e:
        log.error("계획 오류: %s", e)
        return 2


if __name__ == "__main__":
    sys.exit(main())
