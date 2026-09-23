"""텔레그램 연결 점검 및 chat_id 확인.

준비: 텔레그램에서 @BotFather → /newbot 으로 봇을 만들고 토큰을 받는다.
      만든 봇에게 아무 메시지나 한 번 보낸다 (chat_id 확인용).

사용 예 (agent_system 폴더에서):
  export TELEGRAM_BOT_TOKEN=123456:ABC...
  python tools/check_telegram.py                         # 토큰 확인 + 최근 대화한 chat_id 목록
  python tools/check_telegram.py --send 987654321        # 테스트 메시지 발송
  python tools/check_telegram.py --send 987654321 --file README.md   # 테스트 파일 발송

종료 코드: 0 정상, 1 실패
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from telegram_bot.api import TelegramClient, TelegramError  # noqa: E402


def list_chats(client: TelegramClient) -> dict:
    """봇에게 최근 메시지를 보낸 대화방 목록 {chat_id: 설명}."""
    chats = {}
    for upd in client.get_updates(timeout=0):
        msg = upd.get("message") or {}
        chat = msg.get("chat") or {}
        if "id" in chat:
            who = chat.get("username") or chat.get("title") or chat.get("first_name") or ""
            chats[chat["id"]] = f"{chat.get('type')} / {who}"
    return chats


def main() -> int:
    """점검을 실행하고 결과를 출력한다."""
    p = argparse.ArgumentParser(description="텔레그램 연결 점검")
    p.add_argument("--send", type=int, default=None, help="테스트 메시지를 보낼 chat_id")
    p.add_argument("--file", default=None, help="--send 와 함께: 테스트로 보낼 파일 경로")
    args = p.parse_args()

    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    if not token:
        print("❗ TELEGRAM_BOT_TOKEN 환경변수를 설정하세요. (@BotFather → /newbot)")
        return 1

    print("=" * 50)
    print(" 텔레그램 연결 점검")
    print("=" * 50)
    try:
        client = TelegramClient(token)
        me = client.get_me()
        print(f"✅ 토큰 정상: @{me.get('username')} ({me.get('first_name')})")
    except TelegramError as e:
        print(f"❌ 토큰 확인 실패: {e}")
        print("   토큰을 다시 복사했는지, 네트워크가 api.telegram.org 에 접속 가능한지 확인하세요.")
        return 1

    try:
        chats = list_chats(client)
    except TelegramError as e:
        if "409" in str(e):
            print("⚠️ 봇이 이미 실행 중이라 메시지 목록을 읽을 수 없습니다. 봇을 잠시 끄고 다시 실행하세요.")
        else:
            print(f"⚠️ 메시지 목록 조회 실패: {e}")
        chats = {}
    if chats:
        print("\n[봇에게 메시지를 보낸 대화방]")
        for cid, desc in chats.items():
            print(f"  chat_id = {cid:<15} ({desc})")
        ids = ",".join(str(c) for c in chats)
        print(f"\n→ 설정 예: export TELEGRAM_ALLOWED_CHAT_IDS={ids}")
        print(f"→ 시장 알림용: export TELEGRAM_CHAT_ID={next(iter(chats))}")
    else:
        print("\nℹ️ 최근 메시지가 없습니다. 텔레그램에서 봇에게 아무 메시지나 보낸 뒤 다시 실행하세요.")

    if args.send is not None:
        try:
            client.send_text(args.send, "✅ 텔레그램 연결 테스트 메시지입니다.")
            print(f"\n✅ 메시지 발송 성공 → {args.send}")
            if args.file:
                client.send_document(args.send, args.file, caption="테스트 파일")
                print(f"✅ 파일 발송 성공: {args.file}")
        except (TelegramError, OSError) as e:
            print(f"\n❌ 발송 실패: {e}")
            print("   해당 chat_id 사용자가 봇에게 먼저 메시지를 보냈는지 확인하세요.")
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
