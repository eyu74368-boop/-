"""모니터링 설정 로드·검증·저장 모듈.

종목별 임계값은 ``configs.json`` 에서 읽고, 비밀값(텔레그램 토큰 등)은
환경변수에서만 읽는다. 설정 파일이 손상되면 예외를 던지고, 호출 측은
마지막 정상 설정을 유지한다.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass
from typing import Dict


class ConfigError(ValueError):
    """설정 파일 형식·값 오류."""


@dataclass(frozen=True)
class TickerConfig:
    """종목별 감지 임계값."""

    rsi_oversold: float = 30.0
    rsi_overbought: float = 70.0
    price_change_thresh: float = 2.0
    volume_spike_thresh: float = 2.5

    def validate(self, ticker: str) -> None:
        """값의 범위를 검증하고 이상 시 ConfigError 를 던진다."""
        if not 0 < self.rsi_oversold < self.rsi_overbought < 100:
            raise ConfigError(
                f"[{ticker}] RSI 기준은 0 < 과매도 < 과매수 < 100 이어야 합니다."
            )
        if self.price_change_thresh <= 0 or self.volume_spike_thresh <= 0:
            raise ConfigError(f"[{ticker}] 변동률/거래량 기준은 0보다 커야 합니다.")


@dataclass(frozen=True)
class Settings:
    """프로세스 전역 설정 (환경변수 기반)."""

    config_path: str = "./configs.json"
    db_path: str = "./market_alerts.db"
    telegram_token: str = ""
    telegram_chat_id: str = ""
    check_interval_sec: int = 180
    cooldown_min: int = 15
    daily_report_hour: int = 18
    max_workers: int = 4

    @classmethod
    def from_env(cls) -> "Settings":
        """환경변수에서 설정을 읽는다. 숫자 변환 실패 시 ConfigError."""
        try:
            return cls(
                config_path=os.getenv("MM_CONFIG_PATH", cls.config_path),
                db_path=os.getenv("MM_DB_PATH", cls.db_path),
                telegram_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
                telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
                check_interval_sec=int(os.getenv("MM_CHECK_INTERVAL", "180")),
                cooldown_min=int(os.getenv("MM_COOLDOWN_MIN", "15")),
                daily_report_hour=int(os.getenv("MM_REPORT_HOUR", "18")),
                max_workers=int(os.getenv("MM_MAX_WORKERS", "4")),
            )
        except ValueError as e:
            raise ConfigError(f"환경변수 숫자 형식 오류: {e}") from e


def parse_ticker_configs(raw: dict) -> Dict[str, TickerConfig]:
    """dict 를 검증된 TickerConfig 맵으로 변환한다."""
    if not isinstance(raw, dict):
        raise ConfigError("configs.json 최상위는 {티커: {...}} 객체여야 합니다.")
    allowed = set(TickerConfig.__dataclass_fields__)
    result: Dict[str, TickerConfig] = {}
    for ticker, params in raw.items():
        if not isinstance(params, dict):
            raise ConfigError(f"[{ticker}] 설정은 객체여야 합니다.")
        unknown = set(params) - allowed
        if unknown:
            raise ConfigError(f"[{ticker}] 알 수 없는 키: {sorted(unknown)}")
        try:
            cfg = TickerConfig(**{k: float(v) for k, v in params.items()})
        except (TypeError, ValueError) as e:
            raise ConfigError(f"[{ticker}] 숫자 형식 오류: {e}") from e
        cfg.validate(ticker)
        result[ticker.strip().upper()] = cfg
    return result


def load_ticker_configs(path: str) -> Dict[str, TickerConfig]:
    """설정 파일을 읽어 검증한다. 파일 없음/손상 시 ConfigError."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except FileNotFoundError as e:
        raise ConfigError(f"설정 파일이 없습니다: {path}") from e
    except json.JSONDecodeError as e:
        raise ConfigError(f"설정 파일 JSON 손상: {e}") from e
    return parse_ticker_configs(raw)


def save_ticker_configs(path: str, configs: Dict[str, TickerConfig]) -> None:
    """임시 파일에 쓴 뒤 교체(원자적 저장)하여 읽는 쪽이 반쯤 쓰인 파일을 보지 않게 한다."""
    for ticker, cfg in configs.items():
        cfg.validate(ticker)
    data = {t: asdict(c) for t, c in configs.items()}
    directory = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".configs-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise
