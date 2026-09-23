#!/usr/bin/env bash
# systemd 장애 시 텔레그램 알림.
# 기존 안내 대비 변경: 토큰을 스크립트에 하드코딩하지 않고 EnvironmentFile 에서 읽음,
# 로그 속 특수문자로 Markdown 파싱이 깨지던 문제 → parse_mode 없이 평문 전송,
# curl 의 -d 대신 --data-urlencode 사용 (로그의 & 등이 파라미터를 깨뜨리던 문제).
set -euo pipefail

UNIT="${1:?unit name required}"
: "${TELEGRAM_BOT_TOKEN:?}" "${TELEGRAM_CHAT_ID:?}"

LOGS="$(journalctl -u "$UNIT" -n 10 --no-pager 2>&1 | tail -c 3000 || true)"
TEXT="⚠️ [SYSTEMD 장애] $(hostname)
서비스: ${UNIT}
시각: $(date '+%Y-%m-%d %H:%M:%S')

최근 로그:
${LOGS}"

curl -sS --max-time 15 -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
  --data-urlencode "chat_id=${TELEGRAM_CHAT_ID}" \
  --data-urlencode "text=${TEXT}" > /dev/null
