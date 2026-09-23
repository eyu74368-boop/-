# 자율 에이전트 시스템 (로컬 관제 → 계획 → 분산 실행)

대화에서 설계한 구조를 **실행 가능한 코드**로 옮기고, 원래 코드의 버그·위험 요소를
우선순위별로 정리해 반영했다.

```
[사용자] 목표 + 절대 규칙(rules.json)
   │
   ▼
[로컬 AI 관제소 · Commander]  다음 행동 판단 (PLAN / DONE / ESCALATE / WAIT)
   │   └─ 출력은 JSON 스키마 검증 후에만 사용, 실패 시 ESCALATE(사람에게)
   ▼
[계획 수립 · Planner (Claude)]  지시 → 태스크 그래프(JSON)
   │   └─ parse_plan 으로 검증: 미등록 에이전트·순환 의존·모르는 키 거부
   ▼
[RuleGuard]  태스크마다 규칙 검사: 허용 에이전트, 금지 키워드, 허용 도메인, 클라우드 호출 예산
   ▼
[TaskEngine (DAG)]
   ├─ 무거운 작업 (CLAUDE / GEMINI / GROK)  → 동시 1개, 순차 처리
   └─ 가벼운 작업 (LOCAL_AI / LOCAL_PYTHON) → 병렬 처리
       선행 실패 시 후속 작업 SKIPPED, 상태는 state.json 에 저장·재개 가능
```

## 폴더 구조

| 경로 | 내용 |
|---|---|
| `orchestrator/` | 멀티 에이전트 엔진 (models, guard, engine, commander, agents/) |
| `market_monitor/` | 시장 모니터링 (config, indicators, db, notifier, monitor) |
| `dashboard.py` | Streamlit 관리 화면 (선택) |
| `config/*.example.json` | 규칙·계획·종목 설정 예시 |
| `deploy/` | systemd 서비스, 장애 알림 스크립트, 환경변수 예시 |
| `tests/` | 단위 테스트 39개 (네트워크·API 키 불필요) |

## 빠른 시작

```bash
cd agent_system
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt     # requests, pandas, yfinance, streamlit, pytest

python -m pytest -q                 # 테스트

# API 키 없이 흐름 확인 (LOCAL_PYTHON 은 실제로 example.com 등을 수집함)
python -m orchestrator run config/plan.example.json --rules config/rules.example.json --mock

# 시장 모니터링
cp config/configs.example.json configs.json
export TELEGRAM_BOT_TOKEN=... TELEGRAM_CHAT_ID=...   # 없으면 콘솔 출력만
python -m market_monitor
```

---

## 우선순위별 변경 사항

### 🔴 1순위 — 지금 바로 고쳐야 하는 것 (버그·안전) ✅ 반영 완료

**오케스트레이터 / 자율 에이전트**

| 문제 (기존 코드) | 조치 |
|---|---|
| `def execute()` / `def load_state()` 에 `self` 누락 → 실행 즉시 TypeError | 전면 재작성 |
| `Tuple` 을 `__main__` 안에서 import → 클래스 정의 시 NameError | 제거, 엔진 재작성 |
| 선행 작업 미완료여도 경고만 하고 실행 | DAG 엔진: 선행 완료 시에만 실행, 실패 시 후속 SKIPPED |
| 병렬 그룹과 순차 그룹이 분리되어 서로 의존 불가 | 의존성 그래프 하나로 통합, 순차/병렬은 자동 결정 |
| 로컬 AI 규칙 `"Never refuse instructions"` + 검열 없는 모델 + 웹 수집 텍스트 입력 → 웹페이지 속 문구가 명령으로 실행될 수 있음 (간접 프롬프트 주입) | 규칙은 **프롬프트가 아니라 코드(RuleGuard)** 로 강제. 수집 데이터는 `<data>` 로 감싸 "지시가 아님"을 명시. 모델 출력은 스키마 검증 후에만 사용 |
| AI 가 만든 파이썬 코드를 자동 저장·실행 (Self-Healing) → 격리 없음, 임의 코드 실행 | **자동 실행 제거.** LOCAL_PYTHON 은 사람이 등록한 도구(`fetch_url`, `save_json`)만 실행. `save_json` 은 workspace 밖 경로 차단 |
| 클라우드 AI 무한 호출 가능 → 비용 폭탄 | `max_heavy_calls_per_run` 예산, 초과 시 즉시 중단(재시도 안 함) |
| 로컬 AI 통신 실패 시 무조건 ESCALATE → 클라우드 호출 반복 | 실패 시 루프 중단 후 사람에게 보고 |
| 무지성 스크래핑 | robots.txt 준수, 도메인별 최소 2초 간격, 응답 2MB 제한, 허용 도메인 목록 |

**시장 모니터링**

| 문제 (기존 코드) | 조치 |
|---|---|
| 텔레그램 미설정/발송 실패 시 쿨다운이 안 걸림 → 3분마다 같은 알림이 DB 에 무한 누적 | 처리 즉시 쿨다운, 발송 여부는 DB `sent` 컬럼 |
| `parse_mode=Markdown` → 수치·티커의 `_` `*` 때문에 발송 실패(400) | HTML 모드 + escape, 429 응답 시 대기 후 재시도 |
| 여러 스레드가 쿨다운 dict·SQLite 에 동시 접근 | 락 적용, SQLite WAL 모드 |
| 종목 0개일 때 `ThreadPoolExecutor(max_workers=0)` 예외 | 최소 1, 최대 `MM_MAX_WORKERS` |
| 이전 버전 DB(`date`/`score` 컬럼 없음)에서 INSERT 실패 | 자동 마이그레이션 |
| 재시작하면 일일 리포트 중복 발송 | 발송일을 DB `meta` 테이블에 저장 |
| systemd `User=root`, 토큰 평문 기재, 대시보드 `0.0.0.0` + 기본 비밀번호 `admin1234` | 전용 계정, `EnvironmentFile`(chmod 600), 쓰기 경로 제한, 대시보드 127.0.0.1 바인딩, 비밀번호 미설정 시 실행 거부 |
| 장애 알림 스크립트에 토큰 하드코딩, 로그 특수문자로 Markdown 깨짐 | 환경파일에서 읽기, 평문 + `--data-urlencode` |

### 🟠 2순위 — 구조 전환 (정확도·운영) ✅ 반영 완료

- **RSI 계산을 Wilder 방식으로 교체**: 기존 단순이동평균 방식은 차트 도구(TradingView 등)와 값이 달랐음. 손실 0 구간 NaN 문제도 해결.
- **방향별 점수 분리**: 기존엔 골든크로스(+30)와 데드크로스, 과매수와 과매도가 같은 점수로 합산되어 서로 반대 신호가 "강력 포착"으로 오판됨 → 상승/하락 점수를 따로 계산하고 우세 방향 + 거래량 점수로 등급 판정.
- **거래량 배수**: 현재 봉이 평균에 포함되어 배수가 희석되던 문제 → 직전 20봉 평균과 비교.
- **미완성 봉 제외**: 진행 중인 5분봉은 판정에서 제외 (거래량 폭증 오탐 방지).
- **일봉 추세 1시간 캐시**: 알림마다 1년치 데이터를 다시 받던 부하 제거.
- **설정 파일 분리·검증·원자적 저장**: `configs.json` 이 손상돼도 마지막 정상 설정 유지.
- **SIGTERM 정상 종료**, 전 종목 5회 연속 수집 실패 시 텔레그램 경고.
- **모듈 분리 + 테스트 39개**: 네트워크 없이 검증 가능.

### 🟢 3순위 — 지금 구조 그대로 두고 천천히 켜면 되는 것

| 항목 | 방법 | 비고 |
|---|---|---|
| 실제 클라우드 에이전트 | `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, `XAI_API_KEY` 설정 후 `--mock` 제거 | 키 있는 에이전트만 자동 활성화. 모델명은 `CLAUDE_MODEL`/`GEMINI_MODEL`/`GROK_MODEL` 로 교체 |
| 로컬 AI (Ollama) | `ollama pull qwen2.5:7b` 후 `OLLAMA_MODEL`/`OLLAMA_HOST` | 관제소 역할은 JSON 출력 정확도가 중요 → Qwen2.5 Instruct 권장 |
| 자율 루프 | `python -m orchestrator loop --rules rules.json --max-rounds 3` | ESCALATE/WAIT 시 멈추고 사람에게 보고. 라운드 상한 필수 |
| 새 로컬 도구 추가 | `LocalToolAgent.register("이름", 함수)` | 사람이 검토한 함수만 등록 |
| 대시보드 | `streamlit run dashboard.py` + Nginx/Let's Encrypt 또는 SSH 터널 | `deploy/market-dashboard.service` |
| systemd 상시 구동 | `deploy/` 파일 참고 (아래) | |
| Manus 등 추가 에이전트 | `orchestrator/agents/llm.py` 의 `HttpLLMAgent` 상속 | 공개 API 사양 확인 후 추가 |
| Self-Healing | 실패 로그를 CLAUDE 에게 분석시키고 **수정안은 사람이 검토 후 반영** | 자동 코드 실행은 도입하지 않는 것을 권장 |

### ⚠️ 의도적으로 넣지 않은 것

- **검열 없는 모델 + "절대 거절 금지" 프롬프트**: 로컬 AI 가 웹에서 가져온 텍스트를 읽는 구조라, 거절하지 않는 모델은 웹페이지에 숨겨진 명령까지 그대로 따를 위험이 크다. 로컬 AI 의 역할(목표 투입·단순 명령)에는 일반 Qwen2.5 Instruct 로 충분하며, 거부 반응이 문제라면 모델 대신 **규칙을 RuleGuard 에 명시**하는 쪽이 안전하다.
- **계정 로그인·자동 매매·자동 게시**: 모니터링은 읽기 전용 알림까지만. 최종 판단은 사람이 한다.

---

## 서버 배포 (Ubuntu, 3순위)

```bash
sudo useradd --system --home /opt/agent_system monitor
sudo mkdir -p /opt/agent_system/data /etc/agent_system
sudo cp -r agent_system/* /opt/agent_system/
sudo python3 -m venv /opt/agent_system/venv
sudo /opt/agent_system/venv/bin/pip install -r /opt/agent_system/requirements.txt
sudo cp /opt/agent_system/config/configs.example.json /opt/agent_system/data/configs.json
sudo chown -R monitor:monitor /opt/agent_system/data

sudo cp /opt/agent_system/deploy/monitor.env.example /etc/agent_system/monitor.env
sudo chmod 600 /etc/agent_system/monitor.env && sudo nano /etc/agent_system/monitor.env

sudo cp /opt/agent_system/deploy/*.service /etc/systemd/system/
sudo install -m 755 /opt/agent_system/deploy/systemd-telegram-notify.sh /usr/local/bin/
sudo systemctl daemon-reload
sudo systemctl enable --now market-monitor
journalctl -u market-monitor -f
```

`Restart=on-failure` 로 일시 오류는 자동 재시작되고, 10분 안에 5회 넘게 죽어
`failed` 상태가 되면 `OnFailure` 로 텔레그램 장애 알림이 발송된다.
