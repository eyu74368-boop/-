"""기술적 지표 계산 및 시그널 판정 모듈 (순수 pandas, 네트워크 없음).

기존 코드 대비 변경점:
- RSI 를 단순이동평균이 아닌 Wilder 방식(지수평활)으로 계산 (일반 차트 도구와 동일한 값)
- 손실 0 구간에서 RSI 가 NaN 이 되던 문제 수정 (→ 100)
- 상승/하락 방향을 분리 채점: 골든크로스와 데드크로스, 과매수와 과매도가
  같은 점수로 합산되어 '강력 시그널'로 오판되던 문제 수정
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

import pandas as pd

from .config import TickerConfig

RSI_PERIOD = 14
SHORT_MA = 5
LONG_MA = 20
BB_STD = 2.0
MIN_BARS = LONG_MA + 2

BULL, BEAR, NEUTRAL = "bull", "bear", "neutral"


@dataclass
class Signal:
    """개별 지표 시그널."""

    name: str
    direction: str
    points: int
    detail: str = ""


@dataclass
class Evaluation:
    """한 종목의 판정 결과."""

    price: float
    rsi: float
    signals: List[Signal] = field(default_factory=list)

    @property
    def bull_score(self) -> int:
        return sum(s.points for s in self.signals if s.direction == BULL)

    @property
    def bear_score(self) -> int:
        return sum(s.points for s in self.signals if s.direction == BEAR)

    @property
    def neutral_score(self) -> int:
        return sum(s.points for s in self.signals if s.direction == NEUTRAL)

    @property
    def direction(self) -> str:
        """우세 방향. 동점이면 neutral."""
        if self.bull_score > self.bear_score:
            return BULL
        if self.bear_score > self.bull_score:
            return BEAR
        return NEUTRAL

    @property
    def score(self) -> int:
        """컨플루언스 점수 = 우세 방향 점수 + 중립(거래량) 점수."""
        return max(self.bull_score, self.bear_score) + self.neutral_score

    @property
    def grade(self) -> str:
        """점수별 등급."""
        if self.score >= 60:
            return "강력 포착"
        if self.score >= 40:
            return "주요 시그널"
        return "일반 알림"


def calculate_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """RSI(Wilder), MA5/MA20, 볼린저밴드(20, 2σ)를 계산한 새 DataFrame 을 반환한다."""
    out = df.copy()
    close = out["Close"].astype(float)

    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    alpha = 1.0 / RSI_PERIOD
    avg_gain = gain.ewm(alpha=alpha, adjust=False, min_periods=RSI_PERIOD).mean()
    avg_loss = loss.ewm(alpha=alpha, adjust=False, min_periods=RSI_PERIOD).mean()
    rs = avg_gain / avg_loss
    rsi = 100 - 100 / (1 + rs)
    # 손실 0 → RSI 100, 이득·손실 모두 0 → 50
    rsi = rsi.where(avg_loss != 0, 100.0)
    rsi = rsi.where(~((avg_gain == 0) & (avg_loss == 0)), 50.0)
    out["RSI"] = rsi.where(avg_gain.notna())

    out["MA_Short"] = close.rolling(SHORT_MA).mean()
    out["MA_Long"] = close.rolling(LONG_MA).mean()
    std = close.rolling(LONG_MA).std()
    out["BB_Upper"] = out["MA_Long"] + std * BB_STD
    out["BB_Lower"] = out["MA_Long"] - std * BB_STD
    return out


def evaluate(df: pd.DataFrame, cfg: TickerConfig) -> Evaluation:
    """지표가 계산된 DataFrame 의 마지막 두 봉으로 시그널을 판정한다.

    데이터가 부족하면 ValueError 를 던진다.
    """
    if len(df) < MIN_BARS:
        raise ValueError(f"데이터 부족: {len(df)}봉 (최소 {MIN_BARS}봉)")

    data = calculate_indicators(df)
    curr, prev = data.iloc[-1], data.iloc[-2]
    price = float(curr["Close"])
    prev_price = float(prev["Close"])
    rsi = float(curr["RSI"]) if pd.notna(curr["RSI"]) else 50.0

    ev = Evaluation(price=price, rsi=rsi)
    add = ev.signals.append

    if pd.notna(prev["MA_Long"]) and pd.notna(curr["MA_Long"]):
        if prev["MA_Short"] <= prev["MA_Long"] and curr["MA_Short"] > curr["MA_Long"]:
            add(Signal("골든크로스", BULL, 30, f"MA{SHORT_MA} > MA{LONG_MA}"))
        if prev["MA_Short"] >= prev["MA_Long"] and curr["MA_Short"] < curr["MA_Long"]:
            add(Signal("데드크로스", BEAR, 30, f"MA{SHORT_MA} < MA{LONG_MA}"))

    # 하한 이탈 = 과매도 반등 후보(상승 쪽), 상한 돌파 = 과열(하락 쪽)으로 해석
    if pd.notna(curr["BB_Lower"]) and price < curr["BB_Lower"]:
        add(Signal("볼린저 하한 이탈", BULL, 25, f"{price:,.2f} < {curr['BB_Lower']:,.2f}"))
    if pd.notna(curr["BB_Upper"]) and price > curr["BB_Upper"]:
        add(Signal("볼린저 상한 돌파", BEAR, 25, f"{price:,.2f} > {curr['BB_Upper']:,.2f}"))

    if rsi <= cfg.rsi_oversold:
        add(Signal("RSI 과매도", BULL, 25, f"{rsi:.1f}"))
    if rsi >= cfg.rsi_overbought:
        add(Signal("RSI 과매수", BEAR, 25, f"{rsi:.1f}"))

    if prev_price > 0:
        change = (price - prev_price) / prev_price * 100
        if abs(change) >= cfg.price_change_thresh:
            add(Signal("주가 급등락", BULL if change > 0 else BEAR, 15, f"{change:+.2f}%"))

    # 현재 봉을 제외한 직전 20봉 평균과 비교 (현재 봉이 평균에 섞여 배수가 희석되던 문제 수정)
    base = data["Volume"].iloc[-(LONG_MA + 1):-1].astype(float).mean()
    if base and base > 0:
        ratio = float(curr["Volume"]) / base
        if ratio >= cfg.volume_spike_thresh:
            add(Signal("거래량 폭증", NEUTRAL, 15, f"{ratio:.1f}배"))

    return ev


def daily_trend(daily: pd.DataFrame) -> str:
    """일봉 MA50/MA200 기준 상위 추세 문자열을 반환한다."""
    if len(daily) < 200:
        return "정보 부족"
    close = daily["Close"].astype(float)
    ma50 = close.rolling(50).mean().iloc[-1]
    ma200 = close.rolling(200).mean().iloc[-1]
    price = close.iloc[-1]
    if price > ma50 > ma200:
        return "강한 상승 추세"
    if price < ma50 < ma200:
        return "강한 하락 추세"
    if price > ma200:
        return "완만한 상승 추세"
    return "완만한 하락 추세"
