"""로컬 AI 대리인: 사용자의 말을 대신하는 역할만 한다.

1. brief      : 사용자의 목표를 Claude Code 가 바로 일할 수 있는 임무 지시서로 옮긴다
2. judge      : Claude Code 의 도구 사용 요청이 임무·사용자 규칙에 맞는지 승인/거절한다
3. next_step  : Claude Code 의 보고를 읽고 계속 / 완료 / 사용자 확인 필요 를 정한다

대리인은 판단을 '추가'하지 않는다. 사용자 프로필(config/proxy_profile.md)에 적힌
사용자의 의도·선호·금지사항을 기준으로 말할 뿐이다. 형식이 틀리거나 판단이 애매하면
안전한 쪽(거절 / 사용자 확인)으로 처리한다.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Callable, Dict, List

from orchestrator.commander import extract_json

log = logging.getLogger(__name__)

ChatFn = Callable[[str, str], str]  # (system, prompt) -> JSON text

SYSTEM = (
    "너는 사용자의 대리인이다. 사용자의 말을 대신 전할 뿐 스스로 작업하거나 새 목표를 만들지 않는다. "
    "사용자 프로필과 목표에 근거해서만 판단한다. 반드시 요청한 JSON 하나만 출력한다."
)

BRIEF_PROMPT = """[사용자 프로필]
{profile}

[사용자 목표]
{goal}

위 목표를 Claude Code(작업 AI)에게 전달할 임무 지시서로 옮겨라. 사용자가 말하지 않은 목표는 추가하지 않는다.
JSON: {{"mission": "무엇을 왜 하는지 (사용자 말투 그대로 요약)",
        "deliverables": ["텔레그램으로 바로 공유할 수 있는 결과물 목록 (파일 형식 포함)"],
        "constraints": ["지켜야 할 조건 (프로필의 금지사항 포함)"],
        "done_when": "완료 기준 한 문장"}}"""

JUDGE_PROMPT = """[사용자 프로필]
{profile}

[현재 임무]
{mission}

[Claude Code 의 권한 요청]
도구: {tool}
입력: {input}

이 요청이 임무 수행에 필요하고 사용자 프로필에 어긋나지 않으면 allow, 아니면 deny.
확신이 없으면 deny 하고 이유에 무엇을 대신 하라고 적는다.
JSON: {{"decision": "allow" 또는 "deny", "reason": "한 문장"}}"""

NEXT_PROMPT = """[사용자 프로필]
{profile}

[임무]
{mission}

[지금까지 라운드 요약]
{history}

[Claude Code 최신 보고]
{report}

사용자 대신 다음 지시를 정하라.
- 완료 기준을 충족했고 결과물이 공유 가능한 수준이면 "done"
- 부족한 점이 있으면 "continue" 와 구체적 다음 지시 (사용자가 요청한 범위 안에서만)
- 사용자만 결정할 수 있는 문제(돈, 계정, 공개 게시, 목표 변경)면 "ask_user" 와 질문
JSON: {{"action": "done|continue|ask_user", "instruction": "Claude Code 에게 보낼 다음 지시 또는 사용자에게 할 질문",
        "reason": "한 문장"}}"""

REPEAT_PROMPT = """[사용자 프로필]
{profile}

[반복 임무] {goal}
[직전 사이클 결과 요약]
{last}

사용자가 멈추라고 할 때까지 반복하는 임무다. 다음 사이클에서 Claude Code 에게 맡길 일을 정하라.
직전과 같은 일을 중복하지 말고 이어서 진행한다.
JSON: {{"instruction": "다음 사이클 지시", "reason": "한 문장"}}"""


def _parse(text: str) -> Dict[str, Any]:
    data = json.loads(extract_json(text))
    if not isinstance(data, dict):
        raise ValueError("JSON 객체가 아님")
    return data


@dataclass
class Decision:
    action: str
    instruction: str
    reason: str


class LocalProxy:
    """로컬 AI 대리인."""

    def __init__(self, chat: ChatFn, profile: str):
        self.chat = chat
        self.profile = profile.strip() or "(프로필 없음: 사용자 목표 문장만 따른다)"

    def _ask(self, prompt: str) -> Dict[str, Any]:
        last = ""
        for attempt in range(2):
            try:
                return _parse(self.chat(SYSTEM, prompt if attempt == 0 else prompt + "\nJSON 하나만 출력하라."))
            except (ValueError, json.JSONDecodeError) as e:
                last = str(e)
        raise ValueError(f"로컬 AI 가 JSON 형식을 지키지 못함: {last}")

    def brief(self, goal: str) -> str:
        """목표 → Claude Code 임무 지시서 (텍스트). 실패하면 목표 원문을 그대로 전달."""
        try:
            d = self._ask(BRIEF_PROMPT.format(profile=self.profile, goal=goal))
        except Exception as e:
            log.warning("지시서 작성 실패, 원문 전달: %s", e)
            return goal
        parts = [f"# 임무\n{d.get('mission') or goal}", f"(사용자 원문: {goal})"]
        for key, title in (("deliverables", "결과물"), ("constraints", "조건")):
            items = d.get(key) if isinstance(d.get(key), list) else []
            if items:
                parts.append(f"## {title}\n" + "\n".join(f"- {x}" for x in items))
        if d.get("done_when"):
            parts.append(f"## 완료 기준\n{d['done_when']}")
        return "\n\n".join(parts)

    def judge(self, mission: str, tool: str, tool_input: Dict[str, Any]) -> Dict[str, str]:
        """권한 요청 승인/거절. 실패·애매하면 거절."""
        try:
            d = self._ask(JUDGE_PROMPT.format(profile=self.profile, mission=mission[:2000], tool=tool,
                                              input=json.dumps(tool_input, ensure_ascii=False)[:2000]))
        except Exception as e:
            return {"decision": "deny", "reason": f"대리인 판단 실패로 거절: {e}"}
        decision = str(d.get("decision", "")).lower()
        return {"decision": "allow" if decision == "allow" else "deny",
                "reason": str(d.get("reason", ""))[:300]}

    def next_step(self, mission: str, report: str, history: List[str]) -> Decision:
        try:
            d = self._ask(NEXT_PROMPT.format(profile=self.profile, mission=mission[:2000],
                                             history="\n".join(history[-6:]) or "(첫 라운드)",
                                             report=report[:6000]))
        except Exception as e:
            return Decision("ask_user", "대리인이 다음 지시를 정하지 못했습니다. 어떻게 할까요?", str(e))
        action = str(d.get("action", "")).lower()
        if action not in ("done", "continue", "ask_user"):
            action = "ask_user"
        instruction = str(d.get("instruction", "")).strip()
        if action == "continue" and not instruction:
            action, instruction = "ask_user", "다음 지시가 비어 있습니다. 어떻게 할까요?"
        return Decision(action, instruction, str(d.get("reason", ""))[:300])

    def next_cycle(self, goal: str, last_summary: str) -> str:
        try:
            d = self._ask(REPEAT_PROMPT.format(profile=self.profile, goal=goal, last=last_summary[:3000]))
            return str(d.get("instruction") or goal)
        except Exception:
            return f"{goal}\n(직전 사이클에 이어서 진행)"
