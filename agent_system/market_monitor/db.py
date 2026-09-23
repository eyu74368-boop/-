"""알림 이력 SQLite 저장 모듈.

- 멀티스레드 수집에서 동시에 호출되므로 쓰기에 락을 건다.
- 이전 버전 DB(date/score 컬럼 없음)를 자동 마이그레이션한다.
- 일일 리포트 발송 여부를 meta 테이블에 저장해 재시작 시 중복 발송을 막는다.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from datetime import datetime
from typing import Dict, List, Optional

log = logging.getLogger(__name__)

_COLUMNS = {
    "timestamp": "TEXT",
    "date": "TEXT",
    "ticker": "TEXT",
    "price": "REAL",
    "rsi": "REAL",
    "score": "INTEGER",
    "direction": "TEXT",
    "signals": "TEXT",
    "sent": "INTEGER DEFAULT 0",
}


class AlertDatabase:
    """알림 이력 저장소."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._lock = threading.Lock()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        """테이블 생성 및 누락 컬럼 추가."""
        with self._lock, self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")  # 대시보드 동시 읽기 허용
            conn.execute(
                "CREATE TABLE IF NOT EXISTS alert_logs ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
            )
            existing = {r["name"] for r in conn.execute("PRAGMA table_info(alert_logs)")}
            for col, typ in _COLUMNS.items():
                if col not in existing:
                    conn.execute(f"ALTER TABLE alert_logs ADD COLUMN {col} {typ}")
            if "timestamp" in existing and "date" not in existing:
                conn.execute("UPDATE alert_logs SET date = substr(timestamp, 1, 10)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_alert_date ON alert_logs(date)")
            conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")

    def log_alert(
        self,
        ticker: str,
        price: float,
        rsi: float,
        score: int,
        direction: str,
        signals: List[str],
        sent: bool,
        now: Optional[datetime] = None,
    ) -> bool:
        """알림 1건을 저장한다. 실패 시 False (모니터링은 계속)."""
        now = now or datetime.now()
        try:
            with self._lock, self._connect() as conn:
                conn.execute(
                    "INSERT INTO alert_logs "
                    "(timestamp, date, ticker, price, rsi, score, direction, signals, sent) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        now.strftime("%Y-%m-%d %H:%M:%S"),
                        now.strftime("%Y-%m-%d"),
                        ticker,
                        price,
                        rsi,
                        score,
                        direction,
                        " | ".join(signals),
                        int(sent),
                    ),
                )
            return True
        except sqlite3.Error as e:
            log.error("[DB] 저장 실패 (%s): %s", ticker, e)
            return False

    def daily_summary(self, date: str) -> Dict:
        """해당 날짜의 총 건수와 종목별 집계."""
        with self._connect() as conn:
            total = conn.execute(
                "SELECT COUNT(*) AS c FROM alert_logs WHERE date = ?", (date,)
            ).fetchone()["c"]
            by_ticker = [
                dict(r)
                for r in conn.execute(
                    "SELECT ticker, COUNT(*) AS cnt, MAX(score) AS max_score "
                    "FROM alert_logs WHERE date = ? GROUP BY ticker ORDER BY cnt DESC",
                    (date,),
                )
            ]
        return {"total_count": total, "by_ticker": by_ticker}

    def recent(self, limit: int = 100) -> List[Dict]:
        """최근 알림 이력."""
        with self._connect() as conn:
            return [
                dict(r)
                for r in conn.execute(
                    "SELECT * FROM alert_logs ORDER BY id DESC LIMIT ?", (limit,)
                )
            ]

    def get_meta(self, key: str) -> Optional[str]:
        with self._connect() as conn:
            row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def set_meta(self, key: str, value: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO meta (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )
