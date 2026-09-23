"""``.env`` 파일에서 환경변수를 읽는 모듈 (외부 라이브러리 없음).

토큰·API 키를 매번 PowerShell 에 입력하지 않도록, ``agent_system/.env`` 에
``이름=값`` 형식으로 한 번 저장해 두면 각 프로그램이 시작할 때 자동으로 읽는다.
이미 설정된 환경변수는 덮어쓰지 않는다. ``.env`` 는 .gitignore 에 포함되어
GitHub 에 올라가지 않는다.
"""

import os
from typing import Dict

DEFAULT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")


def parse_env(text: str) -> Dict[str, str]:
    """``이름=값`` 줄을 dict 로 변환한다. 주석(#)·빈 줄·따옴표를 처리한다."""
    result: Dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.lower().startswith("export "):
            line = line[7:].strip()
        if line.startswith("$env:"):  # PowerShell 형식으로 적어도 허용
            line = line[5:]
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key:
            result[key] = value
    return result


def load_env(path: str = DEFAULT_PATH) -> Dict[str, str]:
    """``.env`` 를 읽어 아직 없는 환경변수만 설정하고, 설정한 항목을 반환한다.

    파일이 없거나 읽을 수 없으면 아무것도 하지 않는다.
    """
    try:
        # 메모장이 붙이는 BOM 을 제거하기 위해 utf-8-sig 사용
        with open(path, "r", encoding="utf-8-sig") as f:
            values = parse_env(f.read())
    except (OSError, UnicodeDecodeError):
        return {}
    applied = {}
    for key, value in values.items():
        if value and not os.environ.get(key):
            os.environ[key] = value
            applied[key] = value
    return applied
