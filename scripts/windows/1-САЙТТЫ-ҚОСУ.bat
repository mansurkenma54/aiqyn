@echo off
chcp 65001 >nul
cd /d "%~dp0"
title AIQYN Portal
echo.
echo  ============================================
echo    AIQYN Portal — оператор сайты
echo  ============================================
echo.
echo    Карта    : http://localhost:8000
echo    Оператор : admin / aiqyn2026
echo.
echo    Тоқтату  : Ctrl+C
echo  ============================================
echo.
start "" http://localhost:8000
python -m uvicorn portal.app:app --host 127.0.0.1 --port 8000
pause
