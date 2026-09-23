"""텔레그램 Bot API 최소 클라이언트 (requests 만 사용).

점검 스크립트와 대화 봇이 공통으로 사용한다.
토큰이 로그·예외 메시지에 노출되지 않도록 URL 대신 메서드 이름만 기록한다.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, List, Optional

import requests

log = logging.getLogger(__name__)

API = "https://api.telegram.org"
DOWNLOAD_LIMIT = 20 * 1024 * 1024  # Bot API 로 받을 수 있는 최대 파일 크기
UPLOAD_LIMIT = 50 * 1024 * 1024  # Bot API 로 보낼 수 있는 최대 파일 크기
TEXT_LIMIT = 4000  # 메시지 최대 4096자, 여유분


class TelegramError(RuntimeError):
    """텔레그램 API 오류."""


class TelegramClient:
    """텔레그램 봇 API 호출기."""

    def __init__(self, token: str, session: Optional[requests.Session] = None, retries: int = 3):
        if not token:
            raise TelegramError("TELEGRAM_BOT_TOKEN 이 비어 있습니다.")
        self.token = token
        self.session = session or requests.Session()
        self.retries = retries

    def call(self, method: str, http_timeout: float = 20, files=None, **params) -> Any:
        """API 메서드를 호출하고 result 를 반환한다. 429 는 대기 후 재시도.

        http_timeout 은 통신 제한 시간이다. (getUpdates 의 ``timeout`` 파라미터와 구분)
        """
        url = f"{API}/bot{self.token}/{method}"
        last_err = ""
        for attempt in range(1, self.retries + 1):
            try:
                if files:
                    res = self.session.post(url, data=params, files=files, timeout=http_timeout)
                else:
                    res = self.session.post(url, json=params, timeout=http_timeout)
                data = res.json()
            except (requests.RequestException, ValueError) as e:
                last_err = type(e).__name__
                log.warning("[Telegram] %s 통신 실패 (%d/%d): %s", method, attempt, self.retries, last_err)
                time.sleep(min(2 ** attempt, 10))
                continue
            if data.get("ok"):
                return data["result"]
            if res.status_code == 429:
                wait = float(data.get("parameters", {}).get("retry_after", 5))
                log.warning("[Telegram] 요청 제한, %.0f초 대기", wait)
                time.sleep(min(wait, 60))
                continue
            # 400/401/403 등은 재시도해도 같으므로 즉시 실패
            raise TelegramError(f"{method} 실패 ({res.status_code}): {data.get('description')}")
        raise TelegramError(f"{method} 재시도 초과: {last_err}")

    # ---------- 조회 ----------
    def get_me(self) -> Dict[str, Any]:
        return self.call("getMe")

    def get_updates(self, offset: Optional[int] = None, timeout: int = 30) -> List[Dict[str, Any]]:
        """롱 폴링으로 새 메시지를 받는다."""
        params: Dict[str, Any] = {"timeout": timeout, "allowed_updates": ["message"]}
        if offset is not None:
            params["offset"] = offset
        return self.call("getUpdates", http_timeout=timeout + 15, **params)

    # ---------- 발송 ----------
    def send_text(self, chat_id: int, text: str) -> None:
        """평문 메시지 발송. 길면 나눠 보낸다."""
        text = text or "(빈 응답)"
        for i in range(0, len(text), TEXT_LIMIT):
            self.call("sendMessage", chat_id=chat_id, text=text[i:i + TEXT_LIMIT],
                      disable_web_page_preview=True)

    def send_chat_action(self, chat_id: int, action: str = "typing") -> None:
        try:
            self.call("sendChatAction", chat_id=chat_id, action=action)
        except TelegramError:
            pass  # 부가 기능: 실패해도 무시

    def send_document(self, chat_id: int, path: str, caption: str = "") -> None:
        """파일 전송 (50MB 제한)."""
        size = os.path.getsize(path)
        if size > UPLOAD_LIMIT:
            raise TelegramError(f"파일이 너무 큽니다: {size / 1e6:.1f}MB (최대 50MB)")
        with open(path, "rb") as f:
            self.call("sendDocument", http_timeout=120, files={"document": (os.path.basename(path), f)},
                      chat_id=chat_id, caption=caption[:1000])

    # ---------- 수신 파일 ----------
    def download(self, file_id: str, dest_path: str) -> int:
        """file_id 의 파일을 dest_path 에 저장하고 바이트 수를 반환한다."""
        info = self.call("getFile", file_id=file_id)
        size = info.get("file_size") or 0
        if size > DOWNLOAD_LIMIT:
            raise TelegramError(f"파일이 너무 큽니다: {size / 1e6:.1f}MB (최대 20MB)")
        url = f"{API}/file/bot{self.token}/{info['file_path']}"
        tmp = dest_path + ".part"
        written = 0
        try:
            with self.session.get(url, stream=True, timeout=120) as res:
                if res.status_code != 200:
                    raise TelegramError(f"파일 다운로드 실패 ({res.status_code})")
                with open(tmp, "wb") as out:
                    for chunk in res.iter_content(64 * 1024):
                        written += len(chunk)
                        if written > DOWNLOAD_LIMIT:
                            raise TelegramError("파일 크기 제한 초과")
                        out.write(chunk)
            os.replace(tmp, dest_path)
        except requests.RequestException as e:
            raise TelegramError(f"파일 다운로드 통신 실패: {type(e).__name__}") from e
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
        return written
