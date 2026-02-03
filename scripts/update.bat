@echo off
REM ============================================
REM Eskimos 2.0 - Aktualizacja
REM ============================================

title Eskimos 2.0 Update
color 0E

set "INSTALL_DIR=C:\eskimos"

echo.
echo  =======================================
echo   ESKIMOS 2.0 - Aktualizacja
echo  =======================================
echo.

cd /d "%INSTALL_DIR%"
call venv\Scripts\activate.bat

echo [1/3] Sprawdzam aktualizacje...
git pull origin master 2>nul
if errorlevel 1 (
    echo [INFO] Git niedostepny, pomijam git pull
)

echo [2/3] Aktualizowanie zaleznosci...
pip install -e "%INSTALL_DIR%" --quiet --upgrade

echo [3/3] Gotowe!
echo.
echo  Uruchom ponownie Dashboard:
echo    %INSTALL_DIR%\start.bat
echo.
pause
