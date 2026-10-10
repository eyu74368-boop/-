# qterm_addons — QTERM 추가 데이터 모듈

블룸버그 조사(STATE v3, 2026-10-09)에서 정한 순서 중, **로그인·키 없이** 클라우드 세션에서 만들고 시험할 수 있는 것만 먼저 구현했다. QTERM 본체(사용자 PC)에는 아직 연결하지 않았다.

| 모듈 | QTERM ID | 내용 | 실제 호출 확인 (2026-10-10, 클라우드) |
|---|---|---|---|
| `liquidity.py` | N8 + N9 대체안 | TGA·역레포·실효금리·목표범위·2년물, 순유동성, 금리 기대 방향(확률 아님) | TGA ✅ · 역레포 ✅ · EFFR ✅ · 2년물 ✅ · FRED WALCL ❌(시간 초과) |
| `econ_calendar.py` | N1 | BEA·BLS 일정 ICS + FOMC 공식 날짜, 지난 일정 자동 숨김·다음 회의 자동 선택 | BEA ✅ · BLS ❌(403) · FOMC는 내장 날짜 |
| `sec_insider.py` | BB-P3 OWN | SEC 분기 내부자 거래 zip에서 P(매수)·S(매도)만 추출·종목별 합계 | 가짜 zip 시험만 (실제 zip은 연락처 User-Agent 필요) |

- 표준 라이브러리만 사용 → `requirements.txt` 변경 없음.
- 출처 하나가 실패해도 나머지 값은 채우고 `errors`에 이유를 남긴다. 화면은 `asof`(출처별 기준일)와 `errors`를 그대로 표시한다.
- 매매 지시 문구 없음. 금리 기대 방향은 "2년물 − 목표범위 상단" 차이만 보여 주고 "확률 아님"을 붙인다.

## 시험

```bash
python -m unittest discover -s tests -v      # 네트워크 없이 19건
python -m qterm_addons.liquidity              # 실제 호출
python -m qterm_addons.econ_calendar          # 실제 호출
```

`tests/fixtures/`는 2026-10-09 공식 출처 응답 사본이다.

## PC에서 확인할 것 (확인 대기)

1. **User-Agent 설정**: BLS·SEC는 연락처가 담긴 User-Agent를 요구한다. PC 환경변수 `QTERM_USER_AGENT="QTERM 본인이메일"`을 넣는다. 비밀값이 아니지만 저장소에는 적지 않는다.
2. **FRED WALCL**: PC에서 `python -m qterm_addons.liquidity`를 실행해 `walcl` 오류가 사라지는지 본다. 되면 순유동성이 계산되고, 이때부터 FRED 표시 문구(`FRED_NOTICE`) 노출이 의무다.
3. **BLS ICS**: 1번 설정 후 `econ_calendar` 실행 → `errors.bls`가 사라지고 고용·CPI·PPI가 목록에 나오는지 본다.
4. **SEC zip 주소**: `sec_insider.download_dataset(2026, 3)`이 받아지는지 본다(주소 형식 미확인).

## QTERM 본체 연결안 (PC 세션 몫, qterm-agent-step으로 진행)

1. `qterm_addons/`를 `app/addons/`로 복사한다. 상대 import라 그대로 동작한다.
2. 서버 캐시 작업에 추가: `liquidity.build_snapshot()` 1시간, `econ_calendar.build_calendar()` 6시간. 화면은 캐시만 읽는다(공통 설계 규약).
3. 라우트: `GET /api/macro/liquidity`, `GET /api/macro/calendar`.
   - **주의**: F-1(`_api_allowed` 정확 일치) 서버 반영 전에는 `/api/macro/*`가 게스트에게 자동으로 열릴 수 있다. F-1 확인 후에 라우트를 붙인다.
   - 공개 정부 데이터라 게스트 노출은 가능하나, FRED 값(WALCL·순유동성)은 표시 문구와 함께만 보여 준다.
4. 패널: 390px부터(모바일 우선). 한 줄 요약 예시 — `순유동성 5,762B · TGA 887B(7일 −6B) · RRP 0.3B · EFFR 3.88% (3.75~4.00) · 2Y 4.80% (+80bp, 확률 아님) · FOMC D-19`.
5. `fomc_list_expired`가 true이면 "FOMC 일정 갱신 필요"를 띄운다(2027 날짜 공개 후 `FOMC_2026` 옆에 추가).

## 이번 범위에서 뺀 것

- OpenDART 지분공시(N18), ECOS: 인증키(로그인) 필요 → 사용자 몫.
- 한국 공매도 잔고: 공식 API 없음. Polymarket: 국내 접속 차단(2026-08).
