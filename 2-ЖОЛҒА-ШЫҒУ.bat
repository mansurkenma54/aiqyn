@echo off
chcp 65001 >nul
cd /d "%~dp0"
title AIQYN Vision — далалық тест

echo.
echo  ============================================================
echo    AIQYN Vision — жол ақауын анықтау
echo  ============================================================
echo.
echo    Басқару:  [q] шығу   [d] панель   [Space] кідірту
echo.
echo    Телефон дайын ба?
echo      1) IP Webcam ашық, "Start server" басылған
echo      2) USB модем ҚОСУЛЫ
echo      3) GPS ҚОСУЛЫ
echo.
echo  ============================================================
echo.

python -m vision.main run --crop-bottom 0.30

echo.
echo  Тест аяқталды. Нәтижені сайттан қараңыз: http://localhost:8000/portal
echo.
pause
