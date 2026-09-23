"""DAG 기반 태스크 실행 엔진.

기존 스켈레톤의 문제:
- ``Tuple`` 이 ``__main__`` 안에서 import 되어 클래스 정의 시 NameError
- 선행 작업 미완료를 경고만 하고 그대로 실행
- 병렬 그룹이 순차 그룹의 결과를 기다리지 못함 (의존성이 모드별로 갈라짐)

이 엔진은 의존성 그래프만 보고 실행 가능 시점을 판단한다.
- 선행 작업이 모두 COMPLETED → 실행
- 선행 작업 중 하나라도 FAILED/SKIPPED → SKIPPED (연쇄 차단)
- 무거운 에이전트(클라우드 AI)는 ``heavy_concurrency``(기본 1) 로 순차 처리
- 가벼운 에이전트(로컬 AI·스크립트)는 ``light_concurrency`` 만큼 병렬 처리
- 태스크 완료마다 상태를 JSON 으로 저장, ``resume=True`` 면 완료분은 건너뜀
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
from typing import Any, Dict, List, Optional

from .agents.base import Agent
from .guard import RuleGuard, RuleViolation
from .models import Task, TaskStatus, validate_graph

log = logging.getLogger(__name__)


class StateStore:
    """실행 상태 JSON 저장소 (원자적 쓰기)."""

    def __init__(self, path: Optional[str]):
        self.path = path

    def load(self) -> Dict[str, Any]:
        if not self.path or not os.path.exists(self.path):
            return {}
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            log.error("[State] 상태 파일을 읽을 수 없어 새로 시작합니다: %s", e)
            return {}

    def save(self, data: Dict[str, Any]) -> None:
        if not self.path:
            return
        directory = os.path.dirname(os.path.abspath(self.path))
        os.makedirs(directory, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=directory, prefix=".state-", suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, default=str)
        os.replace(tmp, self.path)


class TaskEngine:
    """태스크 그래프 실행기."""

    def __init__(
        self,
        agents: Dict[str, Agent],
        guard: RuleGuard,
        state_path: Optional[str] = None,
        heavy_concurrency: int = 1,
        light_concurrency: int = 4,
        retry_backoff_sec: float = 2.0,
    ):
        self.agents = agents
        self.guard = guard
        self.store = StateStore(state_path)
        self._heavy_sem = asyncio.Semaphore(heavy_concurrency)
        self._light_sem = asyncio.Semaphore(light_concurrency)
        self.retry_backoff_sec = retry_backoff_sec

    async def _run_one(self, task: Task, results: Dict[str, Any]) -> None:
        agent = self.agents[task.agent]
        sem = self._heavy_sem if agent.heavy else self._light_sem
        deps = {d: results.get(d) for d in task.depends_on}

        async with sem:
            task.status = TaskStatus.RUNNING
            for attempt in range(1, task.max_retries + 2):
                task.attempts = attempt
                try:
                    self.guard.check(task)
                    log.info("▶ %s (%s) 시도 %d", task.task_id, task.agent, attempt)
                    task.result = await asyncio.wait_for(
                        agent.run(task, deps), timeout=task.timeout_sec
                    )
                    task.status = TaskStatus.COMPLETED
                    task.error = None
                    log.info("✔ %s 완료", task.task_id)
                    return
                except RuleViolation as e:
                    task.status, task.error = TaskStatus.FAILED, f"규칙 위반: {e}"
                    log.error("⛔ %s %s", task.task_id, task.error)
                    return
                except asyncio.TimeoutError:
                    task.error = f"시간 초과 ({task.timeout_sec}s)"
                except Exception as e:  # 에이전트 오류는 재시도 대상
                    task.error = f"{type(e).__name__}: {e}"
                log.warning("✖ %s 실패 (%d회): %s", task.task_id, attempt, task.error)
                if attempt <= task.max_retries:
                    await asyncio.sleep(self.retry_backoff_sec * attempt)
            task.status = TaskStatus.FAILED

    def _snapshot(self, goal: str, tasks: List[Task]) -> Dict[str, Any]:
        return {
            "goal": goal,
            "guard": self.guard.summary(),
            "tasks": {t.task_id: t.to_dict() for t in tasks},
        }

    async def run(self, tasks: List[Task], goal: str = "", resume: bool = False) -> Dict[str, Task]:
        """모든 태스크를 실행하고 task_id → Task 맵을 반환한다."""
        validate_graph(tasks)
        missing = {t.agent for t in tasks} - set(self.agents)
        if missing:
            raise ValueError(f"등록되지 않은 에이전트: {sorted(missing)}")

        by_id = {t.task_id: t for t in tasks}
        results: Dict[str, Any] = {}

        if resume:
            prev = self.store.load().get("tasks", {})
            for tid, saved in prev.items():
                if tid in by_id and saved.get("status") == TaskStatus.COMPLETED.value:
                    by_id[tid].status = TaskStatus.COMPLETED
                    by_id[tid].result = saved.get("result")
                    results[tid] = by_id[tid].result
                    log.info("↺ %s 이전 실행 결과 재사용", tid)

        running: Dict[asyncio.Task, Task] = {}
        done_states = {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.SKIPPED}

        while True:
            # 선행 실패 → SKIPPED 전파
            changed = True
            while changed:
                changed = False
                for t in tasks:
                    if t.status is TaskStatus.PENDING and any(
                        by_id[d].status in (TaskStatus.FAILED, TaskStatus.SKIPPED)
                        for d in t.depends_on
                    ):
                        t.status = TaskStatus.SKIPPED
                        t.error = "선행 작업 실패"
                        changed = True

            for t in tasks:
                if t.status is TaskStatus.PENDING and all(
                    by_id[d].status is TaskStatus.COMPLETED for d in t.depends_on
                ):
                    t.status = TaskStatus.RUNNING  # 중복 스케줄 방지
                    running[asyncio.create_task(self._run_one(t, results))] = t

            if not running:
                break

            finished, _ = await asyncio.wait(running, return_when=asyncio.FIRST_COMPLETED)
            for fut in finished:
                t = running.pop(fut)
                if fut.exception() is not None:  # 방어: _run_one 은 예외를 삼킨다
                    t.status, t.error = TaskStatus.FAILED, str(fut.exception())
                if t.status is TaskStatus.COMPLETED:
                    results[t.task_id] = t.result
            self.store.save(self._snapshot(goal, tasks))

        undone = [t.task_id for t in tasks if t.status not in done_states]
        if undone:  # 그래프 검증을 통과했다면 도달하지 않음
            raise RuntimeError(f"실행되지 않은 태스크: {undone}")
        self.store.save(self._snapshot(goal, tasks))
        return by_id
