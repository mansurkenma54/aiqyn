@echo off
chcp 65001 >nul
cd /d "%~dp0"
title AIQYN

rem Сайт қосулы ма — жоқ болса, фонда іске қосамыз
curl -s -m 2 -o nul http://127.0.0.1:8000/api/health 2>nul
if errorlevel 1 (
  echo  Сайт іске қосылуда...
  start "AIQYN Portal" /min cmd /c "chcp 65001 >nul && python -m uvicorn portal.app:app --host 127.0.0.1 --port 8000"
  timeout /t 4 >nul
)

start "" pythonw app.py
exit
