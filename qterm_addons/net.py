"""외부 출처 공용 HTTP 도우미.

시간 제한·재시도·User-Agent를 한곳에서 관리한다. 실패는 예외로 올려서
호출하는 쪽이 "갱신 실패"를 화면에 그대로 표시할 수 있게 한다.
"""

import os
import time
import urllib.error
import urllib.request

DEFAULT_TIMEOUT = 20
DEFAULT_RETRIES = 2

# SEC·BLS는 연락처가 담긴 User-Agent를 요구한다. 값은 PC 환경변수로 넣는다.
USER_AGENT = os.environ.get("QTERM_USER_AGENT", "QTERM personal terminal")


class FetchError(RuntimeError):
    """외부 출처 호출 실패. url과 원인을 함께 담는다."""

    def __init__(self, url, reason):
        super().__init__(f"{url}: {reason}")
        self.url = url
        self.reason = reason


def fetch_bytes(url, timeout=DEFAULT_TIMEOUT, retries=DEFAULT_RETRIES, user_agent=None):
    """URL 본문을 bytes로 받는다.

    Args:
        url: 요청 주소.
        timeout: 요청 1회 시간 제한(초).
        retries: 실패 시 추가 시도 횟수. 4xx(429 제외)는 재시도하지 않는다.
        user_agent: 기본값 대신 쓸 User-Agent.

    Returns:
        응답 본문 bytes.

    Raises:
        FetchError: 모든 시도가 실패했을 때.
    """
    headers = {"User-Agent": user_agent or USER_AGENT, "Accept-Encoding": "identity"}
    last_reason = "unknown"
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            last_reason = f"HTTP {e.code}"
            if 400 <= e.code < 500 and e.code != 429:
                break
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
            last_reason = str(getattr(e, "reason", e))
        if attempt < retries:
            time.sleep(2 ** attempt)
    raise FetchError(url, last_reason)


def fetch_text(url, encoding="utf-8", **kwargs):
    """URL 본문을 문자열로 받는다. 인자는 fetch_bytes와 같다."""
    return fetch_bytes(url, **kwargs).decode(encoding, errors="replace")
