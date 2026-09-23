"""태스크 데이터 모델 및 계획(JSON) 검증."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, Iterable, List, Optional


class TaskStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"  # 선행 작업 실패로 실행하지 않음


class PlanError(ValueError):
    """계획(태스크 그래프) 형식 오류."""


@dataclass
class Task:
    """에이전트에게 전달되는 작업 단위.

    순차/병렬 여부는 따로 지정하지 않는다. ``depends_on`` 이 있으면 선행 작업
    완료 후 실행되고, 없으면 조건이 되는 즉시 병렬로 실행된다. 무거운(클라우드)
    에이전트는 엔진이 동시 실행 수를 제한해 순차 처리한다.
    """

    task_id: str
    agent: str
    instruction: str
    payload: Dict[str, Any] = field(default_factory=dict)
    depends_on: List[str] = field(default_factory=list)
    max_retries: int = 1
    timeout_sec: float = 120.0
    status: TaskStatus = TaskStatus.PENDING
    result: Optional[Any] = None
    error: Optional[str] = None
    attempts: int = 0

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        return d


_TASK_KEYS = {"task_id", "agent", "instruction", "payload", "depends_on", "max_retries", "timeout_sec"}


def parse_plan(raw: Any, known_agents: Iterable[str]) -> List[Task]:
    """AI 또는 사람이 작성한 계획(JSON)을 검증해 Task 목록으로 만든다.

    AI 가 만든 계획은 신뢰하지 않는다: 모르는 에이전트, 중복 ID, 없는 선행 작업,
    순환 의존을 모두 거부한다.
    """
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError as e:
            raise PlanError(f"계획 JSON 파싱 실패: {e}") from e
    if isinstance(raw, dict):
        raw = raw.get("tasks")
    if not isinstance(raw, list) or not raw:
        raise PlanError("계획은 비어있지 않은 tasks 배열이어야 합니다.")

    agents = set(known_agents)
    tasks: List[Task] = []
    seen = set()
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            raise PlanError(f"{i}번째 태스크가 객체가 아닙니다.")
        unknown = set(item) - _TASK_KEYS
        if unknown:
            raise PlanError(f"{i}번째 태스크에 허용되지 않은 키: {sorted(unknown)}")
        for key in ("task_id", "agent", "instruction"):
            if not isinstance(item.get(key), str) or not item[key].strip():
                raise PlanError(f"{i}번째 태스크의 '{key}' 가 비었습니다.")
        if item["task_id"] in seen:
            raise PlanError(f"중복 task_id: {item['task_id']}")
        if item["agent"] not in agents:
            raise PlanError(f"등록되지 않은 에이전트: {item['agent']} (가능: {sorted(agents)})")
        deps = item.get("depends_on", [])
        if not isinstance(deps, list) or not all(isinstance(d, str) for d in deps):
            raise PlanError(f"{item['task_id']}: depends_on 은 문자열 배열이어야 합니다.")
        payload = item.get("payload", {})
        if not isinstance(payload, dict):
            raise PlanError(f"{item['task_id']}: payload 는 객체여야 합니다.")
        seen.add(item["task_id"])
        tasks.append(
            Task(
                task_id=item["task_id"],
                agent=item["agent"],
                instruction=item["instruction"],
                payload=payload,
                depends_on=list(deps),
                max_retries=int(item.get("max_retries", 1)),
                timeout_sec=float(item.get("timeout_sec", 120.0)),
            )
        )
    validate_graph(tasks)
    return tasks


def validate_graph(tasks: List[Task]) -> None:
    """없는 선행 작업·순환 의존을 검사한다."""
    ids = {t.task_id for t in tasks}
    for t in tasks:
        missing = [d for d in t.depends_on if d not in ids]
        if missing:
            raise PlanError(f"{t.task_id}: 존재하지 않는 선행 작업 {missing}")
        if t.task_id in t.depends_on:
            raise PlanError(f"{t.task_id}: 자기 자신에 의존할 수 없습니다.")

    graph = {t.task_id: t.depends_on for t in tasks}
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {k: WHITE for k in graph}

    def visit(node: str, path: List[str]) -> None:
        color[node] = GRAY
        for dep in graph[node]:
            if color[dep] == GRAY:
                raise PlanError(f"순환 의존: {' -> '.join(path + [node, dep])}")
            if color[dep] == WHITE:
                visit(dep, path + [node])
        color[node] = BLACK

    for node in graph:
        if color[node] == WHITE:
            visit(node, [])
