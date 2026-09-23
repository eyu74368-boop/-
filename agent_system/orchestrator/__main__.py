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

from envfile import load_env

from .agents.llm import ClaudeAgent, OllamaAgent
from .commander import Commander, Planner, summarize
from .engine import TaskEngine
from .guard import RuleGuard, Rules
from .models import PlanError
from .registry import AGENT_DESC, build_agents, format_report, run_plan_file

log = logging.getLogger("orchestrator")


def print_report(tasks) -> None:
    print(format_report(tasks))


def cmd_run(args, rules: Rules) -> int:
    ok, summary = run_plan_file(args.plan, rules, args.workspace, args.state,
                                mock=args.mock, parallel=args.parallel, resume=args.resume)
    print(summary)
    return 0 if ok else 1


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
    load_env()
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
