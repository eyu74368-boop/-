"""Streamlit 관리 대시보드 (3순위, 선택 도입).

실행: ``streamlit run dashboard.py``

기존 안내 대비 변경:
- 기본 비밀번호(admin1234) 제거: DASHBOARD_PASSWORD 미설정 시 실행 거부
- 비밀번호 비교는 hmac.compare_digest (타이밍 공격 방지)
- 설정 저장 전 검증 + 원자적 저장 (모니터가 반쯤 쓰인 파일을 읽지 않음)
- 삭제 버튼이 '전체 저장'을 눌러야만 반영되던 혼란 → 즉시 반영
"""

import hmac
import os

import pandas as pd
import streamlit as st

from market_monitor.config import (
    ConfigError, Settings, TickerConfig, load_ticker_configs, save_ticker_configs,
)
from market_monitor.db import AlertDatabase

settings = Settings.from_env()
st.set_page_config(page_title="시장 모니터링 대시보드", page_icon="📈", layout="wide")


def require_login() -> None:
    """비밀번호 인증. 실패 시 이후 렌더링을 중단한다."""
    expected = os.getenv("DASHBOARD_PASSWORD", "")
    if not expected:
        st.error("DASHBOARD_PASSWORD 환경변수가 설정되지 않아 실행을 중단합니다.")
        st.stop()
    if st.session_state.get("authed"):
        return
    st.title("🔒 대시보드 로그인")
    with st.form("login"):
        pw = st.text_input("비밀번호", type="password")
        ok = st.form_submit_button("로그인")
    if ok:
        if hmac.compare_digest(pw.encode(), expected.encode()):
            st.session_state["authed"] = True
            st.rerun()
        st.error("비밀번호가 올바르지 않습니다.")
    st.stop()


def load_or_empty() -> dict:
    try:
        return load_ticker_configs(settings.config_path)
    except ConfigError as e:
        st.warning(f"설정 파일을 읽지 못했습니다: {e}")
        return {}


require_login()
with st.sidebar:
    st.write("👤 인증 완료")
    if st.button("로그아웃"):
        st.session_state.clear()
        st.rerun()

st.title("📈 시장 모니터링 관리")
tab_cfg, tab_log = st.tabs(["⚙️ 종목·임계값", "📜 알림 이력"])

with tab_cfg:
    configs = load_or_empty()
    with st.expander("➕ 종목 추가"):
        new = st.text_input("티커 (예: AAPL, ETH-USD)").strip().upper()
        if st.button("추가") and new:
            if new in configs:
                st.info("이미 있는 종목입니다.")
            else:
                configs[new] = TickerConfig()
                save_ticker_configs(settings.config_path, configs)
                st.rerun()

    edited = {}
    for ticker, c in configs.items():
        st.markdown(f"**{ticker}**")
        c1, c2, c3, c4, c5 = st.columns([2, 2, 2, 2, 1])
        vals = dict(
            rsi_oversold=c1.number_input("RSI 과매도", value=c.rsi_oversold, key=f"{ticker}_lo"),
            rsi_overbought=c2.number_input("RSI 과매수", value=c.rsi_overbought, key=f"{ticker}_hi"),
            price_change_thresh=c3.number_input("변동률(%)", value=c.price_change_thresh, key=f"{ticker}_p"),
            volume_spike_thresh=c4.number_input("거래량 배수", value=c.volume_spike_thresh, key=f"{ticker}_v"),
        )
        if c5.button("삭제", key=f"{ticker}_del"):
            del configs[ticker]
            save_ticker_configs(settings.config_path, configs)
            st.rerun()
        edited[ticker] = TickerConfig(**vals)

    if st.button("💾 저장", type="primary"):
        try:
            save_ticker_configs(settings.config_path, edited)
            st.success("저장 완료. 다음 감시 주기부터 반영됩니다.")
        except ConfigError as e:
            st.error(f"저장 거부: {e}")

with tab_log:
    if os.path.exists(settings.db_path):
        rows = AlertDatabase(settings.db_path).recent(200)
        if rows:
            st.dataframe(pd.DataFrame(rows), use_container_width=True)
        else:
            st.info("저장된 알림이 없습니다.")
    else:
        st.warning("DB 파일이 아직 없습니다.")
