@echo off
chcp 65001 >nul
cd /d "%~dp0"
title AIQYN Vision — телефон камерасы
echo.
echo  ============================================
echo    AIQYN Vision — телефон камерасынан
echo  ============================================
echo.
echo    Алдымен телефонда:
echo      1) IP Webcam ашып, "Start server" басыңыз
echo      2) USB модем (tethering) режимін қосыңыз
echo.
echo    Басқару: q - шығу, d - панель, space - кідірту
echo  ============================================
echo.
python -m vision.main run
echo.
pause
