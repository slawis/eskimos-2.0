@echo off
setlocal EnableDelayedExpansion
chcp 65001 >nul 2>&1
title SIMCOM SIM7600 Driver Installer

:: ============================================
::  SIMCOM SIM7600 USB Driver - Auto Installer
::  Installs serial port driver for SIM7600G-H
:: ============================================

:: Admin check + auto-elevate
net session >nul 2>&1
if %errorLevel% neq 0 (
    echo.
    echo  Wymagane uprawnienia Administratora...
    echo  Podnoszenie uprawnien...
    powershell -Command "Start-Process cmd -ArgumentList '/c \"%~f0\"' -Verb RunAs"
    exit /b
)

set "DRIVERS=%~dp0drivers\simcom"

:: Check driver files exist
if not exist "%DRIVERS%\simser.inf" (
    echo.
    echo  [ERROR] Brak plikow sterownika w: %DRIVERS%
    echo  Upewnij sie ze folder drivers\simcom\ zawiera pliki .inf
    echo.
    pause
    exit /b 1
)

echo.
echo  ========================================================
echo      SIMCOM SIM7600 - Instalacja sterownika USB
echo  ========================================================
echo.

:: Step 1: Remove old SIMCOM drivers (if any)
echo  [1/3] Szukanie starych sterownikow SIMCOM...

set "FOUND_OLD=0"
for /f "tokens=*" %%L in ('pnputil /enum-drivers 2^>nul') do (
    set "LINE=%%L"
    echo !LINE! | findstr /i "simtech simcom simlteusbser" >nul 2>&1
    if !errorLevel! equ 0 (
        for /f "tokens=3" %%F in ("!LINE!") do (
            echo %%F | findstr /i "oem" >nul 2>&1
            if !errorLevel! equ 0 (
                echo        Usuwanie: %%F
                pnputil /delete-driver %%F /uninstall /force >nul 2>&1
                set "FOUND_OLD=1"
            )
        )
    )
)

if "!FOUND_OLD!"=="0" (
    echo        Brak starych sterownikow - OK
) else (
    echo        Stare sterowniki usuniete
)
echo.

:: Step 2: Stage + install new drivers
echo  [2/3] Instalacja nowego sterownika...
pnputil /add-driver "%DRIVERS%\*.inf" /subdirs /install 2>&1
echo.

:: Step 3: Trigger device rescan
echo  [3/3] Skanowanie urzadzen USB...
pnputil /scan-devices >nul 2>&1
echo        OK
echo.

echo  ========================================================
echo      INSTALACJA ZAKONCZONA
echo  ========================================================
echo.
echo   Podlacz modem SIM7600G-H przez USB.
echo   W Device Manager (devmgmt.msc) powinny pojawic sie:
echo.
echo     Ports (COM):
echo       - SimTech HS-USB AT Port (COMx)     <- ten uzywa daemon
echo       - SimTech HS-USB Diagnostics (COMx)
echo       - SimTech HS-USB NMEA (COMx)
echo.
echo     Modems:
echo       - SimTech HS-USB Modem
echo.
echo   Daemon auto-wykryje port AT po restarcie serwisu.
echo.
pause
