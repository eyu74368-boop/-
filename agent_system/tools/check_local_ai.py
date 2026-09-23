"""로컬 AI(Ollama) 작동 점검 및 명령 테스트.

사용 예 (agent_system 폴더에서):
  python tools/check_local_ai.py                      # 연결·모델·응답 점검
  python tools/check_local_ai.py --model qwen2.5:7b   # 다른 모델 점검
  python tools/check_local_ai.py --ask "오늘 할 일 3가지를 정리해줘"   # 직접 명령
  python tools/check_local_ai.py --router             # 관제소(JSON 판단) 기능 점검

종료 코드: 0 정상, 1 점검 실패
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from orchestrator.agents.llm import OllamaAgent  # noqa: E402
from orchestrator.commander import Commander  # noqa: E402
from orchestrator.guard import Rules  # noqa: E402
from telegram_bot.bot import format_health  # noqa: E402

HINTS = {
    "연결 실패": [
        "1) Ollama 설치: https://ollama.com/download",
        "2) 서버 실행: ollama serve  (데스크톱 앱은 실행만 하면 자동 시작)",
        "3) 다른 PC 의 Ollama 를 쓰면 OLLAMA_HOST=http://IP:11434 로 지정",
    ],
    "모델 미설치": ["모델 설치: ollama pull {model}  (7B 모델 약 4.7GB)"],
}


def main() -> int:
    """점검을 실행하고 결과를 출력한다."""
    p = argparse.ArgumentParser(description="로컬 AI(Ollama) 작동 점검")
    p.add_argument("--host", default=None, help="기본: OLLAMA_HOST 또는 http://localhost:11434")
    p.add_argument("--model", default=None, help="기본: OLLAMA_MODEL 또는 qwen2.5:7b")
    p.add_argument("--ask", default=None, help="로컬 AI 에게 보낼 명령/질문")
    p.add_argument("--router", action="store_true", help="관제소 JSON 판단 기능 점검")
    args = p.parse_args()

    agent = OllamaAgent(model=args.model, host=args.host)
    print("=" * 50)
    print(" 로컬 AI 작동 점검")
    print("=" * 50)
    health = agent.health()
    print(format_health(health))

    if health.get("error"):
        for key, hints in HINTS.items():
            if key in health["error"]:
                print("\n[해결 방법]")
                for h in hints:
                    print("  " + h.format(model=agent.model))
        return 1

    if args.router:
        print("\n[관제소 판단 점검]")
        commander = Commander(Rules(goal="뉴스 3건 수집 후 요약"), agent.chat)
        d = commander.decide({"SCRAPE": {"status": "FAILED", "error": "HTTP 403"}})
        print(f"  action={d.action} | instruction={d.instruction} | reason={d.reason}")
        if d.reason.startswith("출력 형식 오류"):
            print("  ❗ 모델이 JSON 형식을 지키지 못했습니다. 더 큰 모델이나 Instruct 모델을 권장합니다.")
            return 1

    if args.ask:
        print(f"\n[명령] {args.ask}")
        try:
            print("[응답]\n" + agent.chat("너는 사용자의 업무 보조 AI다. 한국어로 간결하게 답하라.", args.ask))
        except Exception as e:
            print(f"❗ 응답 실패: {e}")
            return 1

    print("\n" + json.dumps({k: health[k] for k in ("host", "model", "latency_sec")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
