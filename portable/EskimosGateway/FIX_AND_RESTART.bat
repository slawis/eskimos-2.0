@echo off
cd /d "%~dp0"
chcp 65001 >nul 2>&1
title Eskimos - Fix and Restart
color 0A

echo ============================================
echo   ESKIMOS - FIX AND RESTART
echo   Aktualizacja do najnowszej wersji
echo ============================================
echo.
echo   Folder: %CD%
echo.

:: 1. Kill ALL Python processes (not just by window title!)
echo [1/5] Zatrzymywanie WSZYSTKICH procesow Python...
taskkill /f /fi "WINDOWTITLE eq Eskimos*" 2>nul
timeout /t 2 /nobreak >nul
taskkill /f /im python.exe 2>nul
taskkill /f /im pythonw.exe 2>nul
timeout /t 3 /nobreak >nul
echo       OK - procesy zatrzymane
echo.

:: 2. Download latest from GitHub
echo [2/5] Pobieranie najnowszej wersji z GitHub...
if exist eskimos-latest.zip del eskimos-latest.zip
curl -L -o eskimos-latest.zip https://github.com/slawis/eskimos-2.0/archive/refs/heads/master.zip
if %errorlevel% neq 0 (
    echo BLAD: Nie udalo sie pobrac! Sprawdz internet.
    pause
    exit /b 1
)
echo       OK - pobrano
echo.

:: 3. Extract and replace
echo [3/5] Rozpakowywanie i zamiana plikow...

:: Remove backup if exists
if exist eskimos.bak rd /s /q eskimos.bak

:: Rename current to backup
if exist eskimos rename eskimos eskimos.bak

:: Extract archive
tar -xf eskimos-latest.zip
if %errorlevel% neq 0 (
    echo BLAD: Nie udalo sie rozpakowac!
    if exist eskimos.bak rename eskimos.bak eskimos
    pause
    exit /b 1
)

:: Move new code
if exist "eskimos-2.0-master\src\eskimos" (
    move "eskimos-2.0-master\src\eskimos" eskimos >nul
    echo       OK - folder eskimos zaktualizowany
) else (
    echo BLAD: Nie znaleziono eskimos w archiwum!
    if exist eskimos.bak rename eskimos.bak eskimos
    pause
    exit /b 1
)

:: Also update STOP_ALL.bat from repo (to fix the kill bug)
if exist "eskimos-2.0-master\portable\EskimosGateway\STOP_ALL.bat" (
    copy /y "eskimos-2.0-master\portable\EskimosGateway\STOP_ALL.bat" STOP_ALL.bat >nul
    echo       OK - STOP_ALL.bat zaktualizowany
)

:: Cleanup
del eskimos-latest.zip 2>nul
rd /s /q eskimos-2.0-master 2>nul
rd /s /q eskimos.bak 2>nul
echo.

:: 4. Ensure config has MODEM_TYPE=serial
echo [4/5] Sprawdzanie konfiguracji...
if not exist config mkdir config
if not exist config\.env (
    echo # Eskimos Gateway Configuration> config\.env
    echo MODEM_TYPE=serial>> config\.env
    echo ESKIMOS_MODEM_PHONE=WPISZ_NUMER>> config\.env
    echo SERIAL_PORT=auto>> config\.env
    echo SERIAL_BAUDRATE=115200>> config\.env
    echo ESKIMOS_DEBUG=false>> config\.env
    echo       Utworzono nowy config\.env
) else (
    findstr /c:"MODEM_TYPE" config\.env >nul 2>&1
    if %errorlevel% neq 0 (
        echo MODEM_TYPE=serial>> config\.env
        echo SERIAL_PORT=auto>> config\.env
        echo SERIAL_BAUDRATE=115200>> config\.env
        echo       Dodano MODEM_TYPE=serial do config\.env
    ) else (
        echo       OK - config\.env juz zawiera MODEM_TYPE
    )
)
echo.

:: 5. Start everything
echo [5/5] Uruchamianie...
if exist START_ALL.bat (
    call START_ALL.bat
) else (
    echo BLAD: Brak START_ALL.bat!
    pause
    exit /b 1
)

echo.
echo ============================================
echo   GOTOWE! Eskimos zaktualizowany.
echo ============================================
echo.
pause
