"""텔레그램 알림 모듈.

기존 코드는 parse_mode=Markdown 을 사용해 티커/수치에 ``_`` ``*`` 가 섞이면
400 에러로 발송이 실패했다. HTML 모드 + escape 로 변경하고,
429(요청 과다) 응답 시 retry_after 만큼 기다렸다 재시도한다.
"""

from __future__ import annotations

import html
import logging
import time

import requests

log = logging.getLogger(__name__)

MAX_LEN = 4000  # 텔레그램 메시지 한도(4096) 여유분


def esc(text: object) -> str:
    """HTML 파싱 모드용 이스케이프."""
    return html.escape(str(text), quote=False)


class TelegramNotifier:
    """텔레그램 봇 발송기. 토큰이 없으면 콘솔 출력만 한다."""

    def __init__(self, token: str, chat_id: str, retries: int = 3, session=None):
        self.token = token
        self.chat_id = chat_id
        self.retries = retries
        self.session = session or requests.Session()

    @property
    def configured(self) -> bool:
        return bool(self.token and self.chat_id)

    def send(self, text_html: str) -> bool:
        """HTML 형식 메시지를 발송한다. 성공 여부를 반환한다."""
        if not self.configured:
            log.warning("[Telegram] 토큰/Chat ID 미설정 → 콘솔 출력만 합니다.\n%s", text_html)
            return False

        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": text_html[:MAX_LEN],
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        for attempt in range(1, self.retries + 1):
            try:
                res = self.session.post(url, json=payload, timeout=10)
                if res.status_code == 429:
                    wait = res.json().get("parameters", {}).get("retry_after", 5)
                    log.warning("[Telegram] 요청 제한, %s초 후 재시도", wait)
                    time.sleep(min(float(wait), 60))
                    continue
                res.raise_for_status()
                return True
            except (requests.RequestException, ValueError) as e:
                # 토큰이 로그에 남지 않도록 URL 대신 예외 종류만 기록
                log.error("[Telegram] 발송 실패 (%d/%d): %s", attempt, self.retries, type(e).__name__)
                time.sleep(2 ** attempt)
        return False
