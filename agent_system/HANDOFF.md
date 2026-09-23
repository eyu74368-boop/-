# 인수인계 (새 세션용)

새 Claude Code 세션은 이 문서부터 읽고 이어서 작업한다. 브랜치: `claude/modest-cori-xx7p1d`, PR #5 (draft).

## 1. 현재 동작 확인된 것 (사용자 Windows 11 PC)
- 위치: `C:\Users\USER\agent-repo\agent_system`, Python 3.14 venv, `.env` 로 설정 (토큰은 `.env` 에만, 채팅에 붙여넣지 않게 안내)
- Ollama 설치 모델: `qwen2.5:7b`(관제·JSON), `exaone3.5:7.8b`(한국어 글), `qwen3:14b`, `gpt-oss:20b`, `moondream`(이미지)
- 텔레그램 봇 `@hjkn2005my_work_ai_bot` 동작: 대화, `/status`, 파일 송수신 확인 완료
- 실행: `점검하기.bat`, `봇시작.bat` (더블클릭). 사용자는 명령어 복사 위치를 자주 혼동 → 설명은 "어느 창에 입력하는지" 명시, 예시 값(`확인한숫자` 등)을 그대로 붙여넣는 실수 잦음
- 미검증: 실제 모델로 `/goal`, `/novel` 전체 수행 (코드·테스트 86개는 통과)

## 2. 구현된 모듈 요약
| 모듈 | 내용 |
|---|---|
| `market_monitor/` | 읽기 전용 시세 모니터링·텔레그램 알림 |
| `orchestrator/` | DAG 엔진, RuleGuard, `tools.py`(자율 도구), `autonomy.py`(`/goal` 루프) |
| `telegram_bot/` | 허용 chat_id 전용 봇, `run_background()` 공용 백그라운드 실행, `extra_commands` 확장 |
| `novel/` | 웹소설 작업실 `/novel` (조사→기획→설정집→개요→집필→검토→피드백, daily 예약) |
| `claude_bridge/proxy.py` | **작업 중** — 아래 3절 |

## 3. 다음 작업: Claude Code 브리지 (사용자 요구사항 원문 정리)
- **생각과 실행은 PC 에 설치된 Claude Code** 가 한다.
- **로컬 AI 는 사용자 말을 대신하는 역할만**: 목표를 임무로 전달, 권한 요청 승인/허락, 보고를 보고 다음 지시.
- 사용자의 새 명령(또는 `/stop`)이 있을 때까지 **임무 → 실행 → 승인 → 다음 지시를 반복**.
- **Claude Code 의 보고와 결과물은 텔레그램에 공유 가능한 수준으로 전송**.

### 완료된 부분
- `claude_bridge/proxy.py`: `LocalProxy` (brief / judge / next_step / next_cycle). 형식 오류·애매하면 거절 또는 사용자 확인. 테스트 없음 → 작성 필요.

### 남은 구현 (권장 설계)
1. `claude_bridge/runner.py`: `claude -p "<지시>" --output-format json [--resume <session_id>] --max-turns N`
   를 임무 폴더(`agent_workspace/missions/<id>/`)에서 subprocess 로 실행 → JSON 의 `result`, `session_id`,
   `total_cost_usd` 파싱 (`is_error`, `num_turns` 는 문서 미확인 → 실제 출력으로 확인 후 사용). 실행 파일은
   `CLAUDE_CODE_PATH` (기본 `claude`, Windows 는 `%USERPROFILE%\.local\bin\claude.exe`).
2. **승인 방식은 PreToolUse 훅 권장** (문서화가 확실함): `--settings` 로 PreToolUse command 훅을 지정 →
   `claude_bridge/approve_hook.py` 가 stdin JSON(`tool_name`, `tool_input`, `cwd`, `session_id`)을 받아
   ① 코드 규칙(작업 폴더 밖 쓰기, `rm -rf`/`del /s`/`git push`/`pip install`/`curl|sh` 등) 즉시 거절
   ② 나머지는 `LocalProxy.judge()` 로 판단
   → stdout `{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"allow|deny","permissionDecisionReason":"..."}}`
   결정은 `missions/<id>/approvals.jsonl` 에 기록, 거절은 텔레그램에 알림.
   (`--permission-prompt-tool` MCP 방식은 입출력 계약이 문서로 확인되지 않아 보류)
   `--permission-mode` 는 `default` 유지. `bypassPermissions` / `--dangerously-skip-permissions` 는 사용 금지.
   `--allowedTools` 와 `--disallowedTools` 는 함께 쓰지 말 것 (문서상 둘 중 하나).
3. `claude_bridge/mission.py`: 라운드 루프 — brief → runner → 보고·새 파일 텔레그램 전송 → `next_step`
   (done / continue(--resume) / ask_user) → 반복 모드면 `next_cycle`. 중단 조건: `/stop`, 새 사용자 명령,
   최대 라운드, 누적 `total_cost_usd` 한도(.env `CLAUDE_MAX_COST_USD`), 하루 사이클 수 한도.
4. 텔레그램: `/mission 목표`, `/mission repeat 목표`, `/mission status` → `bot.run_background()` 사용.
5. `config/proxy_profile.md`: 사용자의 의도·선호·금지사항 (대리인 판단 기준). 사용자와 함께 작성.
6. 과금: **사용자 결정 — Pro/Max 구독 로그인만 사용.** `ANTHROPIC_API_KEY` 가 설정돼 있으면 API 과금이 우선하므로,
   runner 는 subprocess 환경에서 `ANTHROPIC_API_KEY` 를 제거하고 실행할 것. `--bare` 모드 사용 금지(구독 로그인 불가).

## 4. 운영 규칙 (CLAUDE.md 요약)
한국어 답변·결론 먼저, PEP 8 + 한국어 독스트링, 커밋 `[feat]/[fix]/[docs]` 한국어, 패키지 설치 시 명령과 이유 명시,
파괴적 변경 전 승인. 테스트: `cd agent_system && python -m pytest -q`.
