@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist venv\Scripts\python.exe (
  echo [오류] venv 가 없습니다. 먼저 설치를 진행하세요: python -m venv venv
  pause
  exit /b 1
)
echo.
echo ===== 1. 로컬 AI 점검 =====
venv\Scripts\python.exe tools\check_local_ai.py --router
echo.
echo ===== 2. 텔레그램 점검 (텔레그램으로 테스트 메시지 발송) =====
venv\Scripts\python.exe tools\check_telegram.py --send-me
echo.
pause
