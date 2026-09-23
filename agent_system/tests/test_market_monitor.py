"""시장 모니터링 모듈 테스트 (네트워크 없음)."""

import json
import sqlite3
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from market_monitor.config import (
    ConfigError, Settings, TickerConfig, load_ticker_configs, parse_ticker_configs,
    save_ticker_configs,
)
from market_monitor.db import AlertDatabase
from market_monitor.indicators import BEAR, BULL, calculate_indicators, evaluate
from market_monitor.monitor import MarketMonitor, drop_incomplete_bar


def make_df(closes, volumes=None):
    idx = pd.date_range("2026-01-01", periods=len(closes), freq="5min", tz="UTC")
    volumes = volumes if volumes is not None else [1000] * len(closes)
    return pd.DataFrame({"Close": closes, "Volume": volumes}, index=idx)


# ---------- 지표 ----------
def test_rsi_all_gains_is_100():
    df = calculate_indicators(make_df(list(range(1, 40))))
    assert df["RSI"].iloc[-1] == pytest.approx(100.0)


def test_rsi_flat_is_50():
    df = calculate_indicators(make_df([10.0] * 40))
    assert df["RSI"].iloc[-1] == pytest.approx(50.0)


def test_rsi_in_range_for_random_walk():
    rng = np.random.default_rng(0)
    closes = 100 + rng.normal(0, 1, 200).cumsum()
    rsi = calculate_indicators(make_df(closes))["RSI"].dropna()
    assert ((rsi >= 0) & (rsi <= 100)).all()


def test_insufficient_data_raises():
    with pytest.raises(ValueError):
        evaluate(make_df([1.0] * 10), TickerConfig())


def test_crash_is_bearish_not_mixed():
    closes = [100.0] * 30 + [90.0]
    ev = evaluate(make_df(closes), TickerConfig())
    names = {s.name for s in ev.signals}
    assert "주가 급등락" in names
    assert ev.bear_score > 0


def test_volume_spike_uses_previous_bars_only():
    vols = [1000] * 30 + [3000]
    ev = evaluate(make_df([100.0] * 31, vols), TickerConfig(volume_spike_thresh=3.0))
    # 현재 봉을 평균에 넣으면 3000/1095 = 2.7배로 놓치던 케이스
    assert any(s.name == "거래량 폭증" for s in ev.signals)


def test_opposite_signals_do_not_stack():
    """과매수(하락)와 급등(상승)이 동시에 떠도 반대 방향끼리 합산되지 않는다."""
    closes = list(np.linspace(100, 130, 30)) + [135.0]
    ev = evaluate(make_df(closes), TickerConfig(price_change_thresh=1.0))
    assert ev.score == max(ev.bull_score, ev.bear_score) + ev.neutral_score
    assert ev.direction in (BULL, BEAR, "neutral")


# ---------- 설정 ----------
def test_config_validation_rejects_bad_rsi():
    with pytest.raises(ConfigError):
        parse_ticker_configs({"X": {"rsi_oversold": 80, "rsi_overbought": 70}})


def test_config_rejects_unknown_key():
    with pytest.raises(ConfigError):
        parse_ticker_configs({"X": {"rsi_low": 30}})


def test_config_roundtrip_atomic(tmp_path):
    path = tmp_path / "configs.json"
    save_ticker_configs(str(path), {"NVDA": TickerConfig(rsi_oversold=25)})
    loaded = load_ticker_configs(str(path))
    assert loaded["NVDA"].rsi_oversold == 25
    assert not [p for p in tmp_path.iterdir() if p.name.startswith(".configs-")]


def test_broken_config_keeps_previous(tmp_path):
    path = tmp_path / "configs.json"
    path.write_text("{broken", encoding="utf-8")
    m = MarketMonitor(Settings(config_path=str(path), db_path=str(tmp_path / "a.db")),
                      FakeNotifier(), AlertDatabase(str(tmp_path / "a.db")),
                      configs={"AAPL": TickerConfig()})
    m.reload_configs()
    assert list(m.configs) == ["AAPL"]


# ---------- DB ----------
def test_db_migrates_old_schema(tmp_path):
    path = str(tmp_path / "old.db")
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE alert_logs (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT NOT NULL, "
            "ticker TEXT NOT NULL, price REAL NOT NULL, rsi REAL, signals TEXT NOT NULL, "
            "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
        )
        conn.execute("INSERT INTO alert_logs (timestamp, ticker, price, rsi, signals) "
                     "VALUES ('2026-01-02 10:00:00', 'AAPL', 1, 50, 'x')")
    db = AlertDatabase(path)
    assert db.daily_summary("2026-01-02")["total_count"] == 1
    assert db.log_alert("NVDA", 1.0, 50.0, 30, BULL, ["a"], sent=False)


# ---------- 모니터 ----------
class FakeNotifier:
    def __init__(self):
        self.sent = []

    def send(self, text):
        self.sent.append(text)
        return False  # 미설정 상황 재현


def crash_fetcher(ticker, period, interval):
    if interval == "1d":
        return make_df([100.0] * 10)
    idx = pd.date_range("2026-01-01", periods=31, freq="5min", tz="UTC")
    return pd.DataFrame({"Close": [100.0] * 30 + [90.0], "Volume": [1000] * 31}, index=idx)


def test_cooldown_applies_even_when_send_fails(tmp_path):
    db = AlertDatabase(str(tmp_path / "m.db"))
    m = MarketMonitor(Settings(db_path=str(tmp_path / "m.db")), FakeNotifier(), db,
                      fetcher=crash_fetcher, configs={"TSLA": TickerConfig()})
    now = datetime(2026, 1, 2, 10, 0)
    m.analyze("TSLA", now=now)
    m.analyze("TSLA", now=now + timedelta(minutes=3))
    rows = db.recent()
    assert len(rows) == 1 and rows[0]["sent"] == 0
    m.analyze("TSLA", now=now + timedelta(minutes=20))
    assert len(db.recent()) == 2


def test_alert_message_escapes_html(tmp_path):
    n = FakeNotifier()
    m = MarketMonitor(Settings(db_path=str(tmp_path / "m.db")), n, AlertDatabase(str(tmp_path / "m.db")),
                      fetcher=crash_fetcher, configs={"A<B": TickerConfig()})
    m.analyze("A<B", now=datetime(2026, 1, 2))
    assert "A&lt;B" in n.sent[0]


def test_run_cycle_with_no_tickers(tmp_path):
    path = tmp_path / "c.json"
    path.write_text(json.dumps({}), encoding="utf-8")
    m = MarketMonitor(Settings(config_path=str(path), db_path=str(tmp_path / "m.db")),
                      FakeNotifier(), AlertDatabase(str(tmp_path / "m.db")), fetcher=crash_fetcher)
    assert m.run_cycle() == 0


def test_daily_report_sent_once_per_day(tmp_path):
    n = FakeNotifier()
    db = AlertDatabase(str(tmp_path / "m.db"))
    m = MarketMonitor(Settings(db_path=str(tmp_path / "m.db"), daily_report_hour=18), n, db)
    assert not m.maybe_send_daily_report(datetime(2026, 1, 2, 17, 0))
    assert m.maybe_send_daily_report(datetime(2026, 1, 2, 18, 5))
    # 재시작(새 인스턴스)해도 중복 발송 없음
    m2 = MarketMonitor(Settings(db_path=str(tmp_path / "m.db"), daily_report_hour=18), n, db)
    assert not m2.maybe_send_daily_report(datetime(2026, 1, 2, 19, 0))


def test_drop_incomplete_bar():
    df = make_df([1.0, 2.0, 3.0])
    last = df.index[-1].to_pydatetime()
    assert len(drop_incomplete_bar(df, now=last + timedelta(minutes=2))) == 2
    assert len(drop_incomplete_bar(df, now=last + timedelta(minutes=6))) == 3
