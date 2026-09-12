@echo off
chcp 65001 >nul
title Stewie Quiz - Server
REM File nay nam trong scripts/, nhung app.py nam o THU MUC GOC du an.
REM "%~dp0.." = thu muc cha cua scripts/ -> phai cd len do roi moi chay app.py.
cd /d "%~dp0.."

echo ============================================
echo   STEWIE QUIZ - Dang khoi dong server...
echo   (Nho bat MySQL trong XAMPP truoc)
echo ============================================
echo.

REM Chay bang Python 3.11 (ban co Flask). Neu khong co py -3.11 thi thu python.
py -3.11 app.py
if errorlevel 1 (
    echo.
    echo [!] py -3.11 khong chay duoc, thu "python"...
    python app.py
)

echo.
echo ============================================
echo   Server da dung. Cua so nay co the dong.
echo ============================================
pause
