"""텔레그램 대화·파일 전송 봇 실행: ``python -m telegram_bot``

필수 환경변수
  TELEGRAM_BOT_TOKEN          봇 토큰 (@BotFather 에서 발급)
  TELEGRAM_ALLOWED_CHAT_IDS   명령을 받을 chat_id (쉼표로 여러 개) - tools/check_telegram.py 로 확인

선택 환경변수
  BOT_WORKSPACE     작업 폴더 (기본 ./agent_workspace)
  BOT_RULES_PATH    /run 에 적용할 규칙 파일 (기본 config/rules.example.json)
  OLLAMA_HOST / OLLAMA_MODEL   로컬 AI 설정
"""

import logging
import os
import signal
import sys

from envfile import load_env
from orchestrator.agents.llm import OllamaAgent
from orchestrator.guard import Rules
from orchestrator.registry import build_autonomous_agent, run_plan_file

from .api import TelegramClient, TelegramError
from .bot import TelegramBot


def parse_chat_ids(raw: str) -> list:
    """'123, -456' → [123, -456]. 숫자가 아니면 ValueError."""
    return [int(x) for x in raw.replace(" ", "").split(",") if x]


def main() -> int:
    """환경변수를 읽고 봇을 실행한다."""
    logging.basicConfig(level=logging.INFO, format="[%(asctime)s][%(levelname)s] %(message)s")
    load_env()
    try:
        chat_ids = parse_chat_ids(os.getenv("TELEGRAM_ALLOWED_CHAT_IDS", ""))
    except ValueError:
        logging.error("TELEGRAM_ALLOWED_CHAT_IDS 는 숫자를 쉼표로 구분해 입력하세요.")
        return 2
    if not chat_ids:
        logging.error("TELEGRAM_ALLOWED_CHAT_IDS 가 비어 있습니다. "
                      "python tools/check_telegram.py 로 chat_id 를 먼저 확인하세요.")
        return 2

    workspace = os.path.abspath(os.getenv("BOT_WORKSPACE", "./agent_workspace"))
    rules_path = os.getenv("BOT_RULES_PATH", "config/rules.example.json")
    autonomy_rules_path = os.getenv("BOT_AUTONOMY_RULES", "config/rules.autonomy.json")
    try:
        max_steps = int(os.getenv("BOT_MAX_STEPS", "12"))
        Rules.load(autonomy_rules_path)
    except (OSError, ValueError) as e:
        logging.error("자율 실행 설정 오류: %s", e)
        return 2
    try:
        client = TelegramClient(os.getenv("TELEGRAM_BOT_TOKEN", ""))
        me = client.get_me()
    except TelegramError as e:
        logging.error("텔레그램 연결 실패: %s", e)
        return 2
    logging.info("봇 계정 확인: @%s", me.get("username"))

    local_ai = OllamaAgent()

    def plan_runner(plan_path: str):
        rules = Rules.load(rules_path)  # 실행 때마다 읽어 규칙 수정을 즉시 반영
        return run_plan_file(plan_path, rules, workspace, os.path.join(workspace, "state.json"))

    def goal_runner(goal: str, chat_id: int, on_event, stop_event):
        rules = Rules.load(autonomy_rules_path)
        agent = build_autonomous_agent(
            workspace, rules,
            notify=lambda text: client.send_text(chat_id, "📨 " + text),
            send_file=lambda path, caption: client.send_document(chat_id, path, caption),
            on_event=on_event, stop_event=stop_event, max_steps=max_steps,
        )
        return agent.run(goal)

    tools_desc = build_autonomous_agent(workspace, Rules.load(autonomy_rules_path)).toolbox.describe()

    bot = TelegramBot(
        client,
        allowed_chat_ids=chat_ids,
        workspace=workspace,
        chat_fn=local_ai.chat_messages,
        health_fn=local_ai.health,
        plan_runner=plan_runner,
        plan_dirs=[os.path.join(workspace, "plans"), "config"],
        goal_runner=goal_runner,
        tools_desc="/goal 에서 로컬 AI 가 쓸 수 있는 도구\n\n" + tools_desc,
    )
    signal.signal(signal.SIGTERM, bot.stop)
    signal.signal(signal.SIGINT, bot.stop)
    bot.run_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
