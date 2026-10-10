"""QTERM 추가 데이터 모듈 모음.

QTERM 본체(사용자 PC)에 붙이기 전 단계의 독립 모듈이다.
모든 모듈은 표준 라이브러리만 쓰고, 키·로그인이 필요 없는 공식 출처만 호출한다.

- liquidity: 미국 유동성·금리 스냅샷 (TGA, 역레포, 실효금리·목표범위, 2년물)
- econ_calendar: 미국 경제지표·FOMC 일정 (BEA/BLS ICS, 연준 공식 날짜)
- sec_insider: SEC 분기 내부자 거래 데이터셋에서 장내 매수·매도(P/S)만 추출
"""

__all__ = ["liquidity", "econ_calendar", "sec_insider"]
