"""시장 모니터링 엔진.

기존 코드 대비 변경점(1순위 수정):
- 텔레그램 미설정/발송 실패 시 쿨다운이 걸리지 않아 3분마다 같은 알림이 DB에
  쌓이던 문제 → 처리 즉시 쿨다운 적용, 발송 여부는 DB ``sent`` 컬럼에 기록
- 스레드 간 공유 dict(쿨다운) 접근에 락 적용
- 종목이 0개면 ThreadPoolExecutor(max_workers=0) 로 예외 나던 문제 수정
- 설정 파일 손상 시 마지막 정상 설정 유지
- 일봉 추세를 알림마다 1년치 재다운로드 → 1시간 캐시
- 진행 중(미완성) 5분봉은 판정에서 제외 (거래량 폭증 오판 방지)
- SIGTERM(systemd stop) 시 루프를 정상 종료
"""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, Optional, Tuple

import pandas as pd

from .config import ConfigError, Settings, TickerConfig, load_ticker_configs
from .db import AlertDatabase
from .indicators import BEAR, BULL, Evaluation, daily_trend, evaluate
from .notifier import TelegramNotifier, esc

log = logging.getLogger(__name__)

Fetcher = Callable[[str, str, str], pd.DataFrame]  # (ticker, period, interval)
BAR_MINUTES = 5
TREND_CACHE_SEC = 3600
FAIL_ALERT_CYCLES = 5  # 전 종목 수집 실패가 연속 N회면 장애 알림


def yfinance_fetcher(ticker: str, period: str, interval: str) -> pd.DataFrame:
    """yfinance 로 OHLCV 를 받는다 (지연 import: 테스트 시 설치 불필요)."""
    import yfinance as yf

    df = yf.download(
        tickers=ticker, period=period, interval=interval, progress=False, auto_adjust=True
    )
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df


def drop_incomplete_bar(df: pd.DataFrame, now: Optional[datetime] = None) -> pd.DataFrame:
    """마지막 봉이 아직 마감되지 않았으면 제외한다."""
    if df.empty or not isinstance(df.index, pd.DatetimeIndex):
        return df
    now = now or datetime.now(timezone.utc)
    last = df.index[-1]
    last = last.tz_localize("UTC") if last.tzinfo is None else last.tz_convert("UTC")
    if last + timedelta(minutes=BAR_MINUTES) > pd.Timestamp(now):
        return df.iloc[:-1]
    return df


class MarketMonitor:
    """종목 감시 → 판정 → 알림/DB 기록."""

    def __init__(
        self,
        settings: Settings,
        notifier: TelegramNotifier,
        db: AlertDatabase,
        fetcher: Fetcher = yfinance_fetcher,
        configs: Optional[Dict[str, TickerConfig]] = None,
    ):
        self.settings = settings
        self.notifier = notifier
        self.db = db
        self.fetcher = fetcher
        self.configs: Dict[str, TickerConfig] = configs or {}
        self._last_alert: Dict[str, datetime] = {}
        self._trend_cache: Dict[str, Tuple[float, str]] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._fail_cycles = 0

    # ---------- 설정 ----------
    def reload_configs(self) -> None:
        """설정을 다시 읽는다. 실패하면 이전 설정을 유지한다."""
        try:
            self.configs = load_ticker_configs(self.settings.config_path)
        except ConfigError as e:
            if self.configs:
                log.error("[Config] %s → 이전 설정 유지", e)
            else:
                log.error("[Config] %s → 감시 종목 없음", e)

    # ---------- 쿨다운 ----------
    def _try_acquire_cooldown(self, ticker: str, now: datetime) -> bool:
        """쿨다운이 아니면 즉시 쿨다운을 걸고 True (중복 처리 방지)."""
        with self._lock:
            last = self._last_alert.get(ticker)
            if last and now - last < timedelta(minutes=self.settings.cooldown_min):
                return False
            self._last_alert[ticker] = now
            return True

    # ---------- 추세 캐시 ----------
    def _trend(self, ticker: str) -> str:
        now = time.monotonic()
        with self._lock:
            cached = self._trend_cache.get(ticker)
        if cached and now - cached[0] < TREND_CACHE_SEC:
            return cached[1]
        try:
            trend = daily_trend(self.fetcher(ticker, "1y", "1d"))
        except Exception as e:  # 추세는 부가정보: 실패해도 알림은 보낸다
            log.warning("[%s] 일봉 추세 조회 실패: %s", ticker, e)
            return "측정 불가"
        with self._lock:
            self._trend_cache[ticker] = (now, trend)
        return trend

    # ---------- 분석 ----------
    def analyze(self, ticker: str, now: Optional[datetime] = None) -> Optional[Evaluation]:
        """한 종목을 분석하고 필요 시 알림을 보낸다. 수집 실패 시 예외를 던진다."""
        cfg = self.configs.get(ticker, TickerConfig())
        df = self.fetcher(ticker, "5d", f"{BAR_MINUTES}m")
        if df is None or df.empty:
            raise ValueError("빈 데이터")
        df = drop_incomplete_bar(df)
        try:
            ev = evaluate(df, cfg)
        except ValueError as e:
            log.info("[%s] %s", ticker, e)
            return None

        if not ev.signals:
            return ev
        now = now or datetime.now()
        if not self._try_acquire_cooldown(ticker, now):
            return ev

        trend = self._trend(ticker)
        sent = self.notifier.send(format_alert(ticker, ev, trend, now))
        self.db.log_alert(
            ticker, ev.price, ev.rsi, ev.score, ev.direction,
            [f"{s.name}({s.detail})" for s in ev.signals], sent, now,
        )
        log.info("[%s] 시그널 %d점 (%s) 발송=%s", ticker, ev.score, ev.direction, sent)
        return ev

    def _safe_analyze(self, ticker: str) -> bool:
        try:
            self.analyze(ticker)
            return True
        except Exception as e:
            log.error("[%s] 수집/분석 실패: %s", ticker, e)
            return False

    def run_cycle(self) -> int:
        """전 종목 1회 검사. 성공 종목 수를 반환한다."""
        self.reload_configs()
        tickers = list(self.configs)
        if not tickers:
            return 0
        workers = max(1, min(self.settings.max_workers, len(tickers)))
        with ThreadPoolExecutor(max_workers=workers) as ex:
            ok = sum(ex.map(self._safe_analyze, tickers))

        self._fail_cycles = 0 if ok else self._fail_cycles + 1
        if self._fail_cycles == FAIL_ALERT_CYCLES:
            self.notifier.send(
                f"⚠️ <b>[모니터 경고]</b> {FAIL_ALERT_CYCLES}회 연속 전 종목 수집 실패. "
                "네트워크/데이터 소스를 확인하세요."
            )
        return ok

    def maybe_send_daily_report(self, now: Optional[datetime] = None) -> bool:
        """설정 시각 이후 당일 리포트를 1회 발송한다 (재시작해도 중복 없음)."""
        now = now or datetime.now()
        today = now.strftime("%Y-%m-%d")
        if now.hour < self.settings.daily_report_hour:
            return False
        if self.db.get_meta("last_report_date") == today:
            return False
        data = self.db.daily_summary(today)
        if data["total_count"] == 0:
            body = "금일 발생한 시그널이 없습니다."
        else:
            lines = [
                f"• {esc(r['ticker'])}: {r['cnt']}회 (최고 {r['max_score']}점)"
                for r in data["by_ticker"]
            ]
            body = f"총 {data['total_count']}건\n" + "\n".join(lines)
        self.notifier.send(f"📊 <b>[일일 리포트] {today}</b>\n\n{body}")
        self.db.set_meta("last_report_date", today)
        return True

    def stop(self, *_args) -> None:
        """루프 종료 요청 (SIGTERM 핸들러로도 사용)."""
        self._stop.set()

    def run_forever(self) -> None:
        """메인 루프."""
        log.info("모니터링 시작 (주기 %ss)", self.settings.check_interval_sec)
        while not self._stop.is_set():
            started = time.monotonic()
            ok = self.run_cycle()
            log.info("사이클 완료: %d/%d 종목 성공", ok, len(self.configs))
            try:
                self.maybe_send_daily_report()
            except Exception as e:
                log.error("[리포트] 실패: %s", e)
            elapsed = time.monotonic() - started
            self._stop.wait(max(1.0, self.settings.check_interval_sec - elapsed))
        log.info("모니터링 정상 종료")


def format_alert(ticker: str, ev: Evaluation, trend: str, now: datetime) -> str:
    """알림 메시지(HTML)를 만든다."""
    arrow = {BULL: "🟢 상승 우세", BEAR: "🔴 하락 우세"}.get(ev.direction, "⚪ 방향 혼조")
    badge = {"강력 포착": "🔥", "주요 시그널": "⚡"}.get(ev.grade, "🔔")
    sig = "\n".join(f"• {esc(s.name)} ({esc(s.detail)}) +{s.points}" for s in ev.signals)
    symbol = ticker.replace("-", "")
    return (
        f"{badge} <b>[{esc(ev.grade)}] {esc(ticker)}</b> | {ev.score}점 | {arrow}\n\n"
        f"<b>상위 일봉 추세:</b> {esc(trend)}\n\n"
        f"<b>시그널</b>\n{sig}\n\n"
        f"현재가 {ev.price:,.2f} | RSI {ev.rsi:.1f}\n"
        f"{now:%Y-%m-%d %H:%M:%S}\n"
        f'<a href="https://www.tradingview.com/symbols/{esc(symbol)}">차트 보기</a>\n'
        "<i>※ 참고용 정보이며 매매 판단은 본인이 합니다.</i>"
    )
