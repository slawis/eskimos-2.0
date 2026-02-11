@echo off
setlocal EnableDelayedExpansion
chcp 65001 >nul 2>&1

:: ============================================
::  ESKIMOS GATEWAY - Windows Service Installer
::  Installs as auto-start Windows Services
:: ============================================

title Eskimos Service Installer

:: Check for admin rights
net session >nul 2>&1
if %errorLevel% neq 0 (
    echo.
    echo  ========================================================
    echo   WYMAGANE UPRAWNIENIA ADMINISTRATORA
    echo  ========================================================
    echo.
    echo   Kliknij prawym przyciskiem na ten plik i wybierz:
    echo   "Uruchom jako administrator"
    echo.
    pause
    exit /b 1
)

:: Set paths
set "ROOT=%~dp0"
set "ROOT=%ROOT:~0,-1%"
set "NSSM=%ROOT%\tools\nssm.exe"
set "PYTHON=%ROOT%\python\python.exe"
set "PYTHONW=%ROOT%\python\pythonw.exe"

:: Check NSSM exists
if not exist "%NSSM%" (
    echo [ERROR] NSSM not found: %NSSM%
    echo Please ensure tools\nssm.exe exists.
    pause
    exit /b 1
)

echo.
echo  ========================================================
echo          ESKIMOS GATEWAY - SERVICE INSTALLER
echo  ========================================================
echo.
echo   Ten skrypt zainstaluje dwa serwisy Windows:
echo.
echo   1. EskimosGateway  - Glowna aplikacja (API + Dashboard)
echo   2. EskimosDaemon   - Phone-home daemon (heartbeat)
echo.
echo   Serwisy uruchomia sie automatycznie po starcie Windows.
echo.
echo  ========================================================
echo.

:: Confirm installation
set /p CONFIRM="Czy chcesz zainstalowac serwisy? (T/N): "
if /i not "%CONFIRM%"=="T" (
    echo Anulowano.
    pause
    exit /b 0
)

:: ============================================
:: Install SIMCOM modem driver (if present)
:: ============================================
echo [1/5] Instalacja sterownika modemu...
if exist "%ROOT%\drivers\simcom\simser.inf" (
    :: Remove old SIMCOM drivers silently
    for /f "tokens=*" %%L in ('pnputil /enum-drivers 2^>nul') do (
        set "LINE=%%L"
        echo !LINE! | findstr /i "simtech simcom simlteusbser" >nul 2>&1
        if !errorLevel! equ 0 (
            for /f "tokens=3" %%F in ("!LINE!") do (
                echo %%F | findstr /i "oem" >nul 2>&1
                if !errorLevel! equ 0 (
                    pnputil /delete-driver %%F /uninstall /force >nul 2>&1
                )
            )
        )
    )
    :: Stage + install new driver
    pnputil /add-driver "%ROOT%\drivers\simcom\*.inf" /subdirs /install >nul 2>&1
    pnputil /scan-devices >nul 2>&1
    echo       OK - Sterownik SIMCOM zainstalowany
) else (
    echo       SKIP - Brak sterownikow w drivers\simcom\
)
echo.

echo [2/5] Sprawdzanie istniejacych serwisow...

:: Stop existing services if running
"%NSSM%" status EskimosGateway >nul 2>&1
if %errorLevel% equ 0 (
    echo       Zatrzymywanie EskimosGateway...
    "%NSSM%" stop EskimosGateway >nul 2>&1
    "%NSSM%" remove EskimosGateway confirm >nul 2>&1
)

"%NSSM%" status EskimosDaemon >nul 2>&1
if %errorLevel% equ 0 (
    echo       Zatrzymywanie EskimosDaemon...
    "%NSSM%" stop EskimosDaemon >nul 2>&1
    "%NSSM%" remove EskimosDaemon confirm >nul 2>&1
)

echo       OK - Gotowe do instalacji
echo.

:: Create logs directory
if not exist "%ROOT%\logs" mkdir "%ROOT%\logs"

:: ============================================
:: Install EskimosGateway Service
:: ============================================
echo [3/5] Instalacja EskimosGateway...

"%NSSM%" install EskimosGateway "%PYTHON%"
"%NSSM%" set EskimosGateway AppParameters "-m eskimos.cli.main serve"
"%NSSM%" set EskimosGateway AppDirectory "%ROOT%"
"%NSSM%" set EskimosGateway AppEnvironmentExtra "PYTHONPATH=%ROOT%\eskimos"
"%NSSM%" set EskimosGateway DisplayName "Eskimos SMS Gateway"
"%NSSM%" set EskimosGateway Description "Eskimos SMS Gateway - API and Dashboard for SMS automation"
"%NSSM%" set EskimosGateway Start SERVICE_AUTO_START
"%NSSM%" set EskimosGateway AppStdout "%ROOT%\logs\gateway_service.log"
"%NSSM%" set EskimosGateway AppStderr "%ROOT%\logs\gateway_error.log"
"%NSSM%" set EskimosGateway AppRotateFiles 1
"%NSSM%" set EskimosGateway AppRotateBytes 10485760

echo       OK - EskimosGateway zainstalowany
echo.

:: ============================================
:: Install EskimosDaemon Service
:: ============================================
echo [4/5] Instalacja EskimosDaemon...

"%NSSM%" install EskimosDaemon "%PYTHONW%"
"%NSSM%" set EskimosDaemon AppParameters "-m eskimos.infrastructure.daemon start"
"%NSSM%" set EskimosDaemon AppDirectory "%ROOT%"
"%NSSM%" set EskimosDaemon AppEnvironmentExtra "PYTHONPATH=%ROOT%\eskimos"
"%NSSM%" set EskimosDaemon DisplayName "Eskimos Phone-Home Daemon"
"%NSSM%" set EskimosDaemon Description "Eskimos Daemon - Heartbeat and remote management"
"%NSSM%" set EskimosDaemon Start SERVICE_AUTO_START
"%NSSM%" set EskimosDaemon AppStdout "%ROOT%\logs\daemon_service.log"
"%NSSM%" set EskimosDaemon AppStderr "%ROOT%\logs\daemon_error.log"
"%NSSM%" set EskimosDaemon AppRotateFiles 1
"%NSSM%" set EskimosDaemon AppRotateBytes 10485760
"%NSSM%" set EskimosDaemon DependOnService EskimosGateway

echo       OK - EskimosDaemon zainstalowany
echo.

:: ============================================
:: Start services
:: ============================================
echo [5/5] Uruchamianie serwisow...

"%NSSM%" start EskimosGateway
timeout /t 3 /nobreak >nul
"%NSSM%" start EskimosDaemon

echo.
echo  ========================================================
echo              INSTALACJA ZAKONCZONA POMYSLNIE
echo  ========================================================
echo.
echo   Serwisy zostaly zainstalowane i uruchomione.
echo   Uruchomia sie automatycznie po kazdym starcie Windows.
echo.
echo   Zarzadzanie:
echo     - SERVICE_STATUS.bat    - sprawdz status
echo     - SERVICE_STOP.bat      - zatrzymaj serwisy
echo     - SERVICE_START.bat     - uruchom serwisy
echo     - UNINSTALL_SERVICE.bat - odinstaluj
echo.
echo   Lub przez Windows Services (services.msc):
echo     - Eskimos SMS Gateway
echo     - Eskimos Phone-Home Daemon
echo.
echo  ========================================================
echo.

pause
