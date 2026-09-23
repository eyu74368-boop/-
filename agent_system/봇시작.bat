@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist venv\Scripts\python.exe (
  echo [오류] venv 가 없습니다. 먼저 설치를 진행하세요: python -m venv venv
  pause
  exit /b 1
)
echo 텔레그램 봇을 시작합니다. 이 창을 닫으면 봇이 꺼집니다. (종료: Ctrl+C)
echo.
venv\Scripts\python.exe -m telegram_bot
echo.
echo 봇이 종료되었습니다.
pause
