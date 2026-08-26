@echo off
chcp 65001 >nul
cd /d "%~dp0"
title AIQYN — жүйені тексеру
python -m vision.main doctor
echo.
pause
