@echo off
REM ============================================
REM Eskimos 2.0 - Instalator Windows
REM Double-click to install
REM ============================================

title Eskimos 2.0 Installer
color 0A

echo.
echo  =======================================
echo   ESKIMOS 2.0 - SMS Gateway z AI
echo   Instalator dla Windows
echo  =======================================
echo.

REM Check Python
echo [1/6] Sprawdzam Python...
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python nie jest zainstalowany!
    echo.
    echo Pobierz Python 3.11+ z: https://www.python.org/downloads/
    echo Zaznacz "Add Python to PATH" podczas instalacji!
    echo.
    pause
    exit /b 1
)
python --version
echo [OK] Python znaleziony
echo.

REM Get script directory
set "SCRIPT_DIR=%~dp0"
set "PROJECT_DIR=%SCRIPT_DIR%.."
set "INSTALL_DIR=C:\eskimos"

echo [2/6] Katalog projektu: %PROJECT_DIR%
echo [2/6] Katalog instalacji: %INSTALL_DIR%
echo.

REM Create install directory
if not exist "%INSTALL_DIR%" (
    echo [3/6] Tworzenie katalogu %INSTALL_DIR%...
    mkdir "%INSTALL_DIR%"
)

REM Copy project files
echo [3/6] Kopiowanie plikow projektu...
xcopy /E /I /Y "%PROJECT_DIR%\*" "%INSTALL_DIR%\" >nul 2>&1
echo [OK] Pliki skopiowane
echo.

REM Create virtual environment
echo [4/6] Tworzenie srodowiska wirtualnego...
cd /d "%INSTALL_DIR%"
if exist "venv" (
    echo [INFO] venv juz istnieje, pomijam...
) else (
    python -m venv venv
    echo [OK] venv utworzony
)
echo.

REM Activate and install
echo [5/6] Instalowanie zaleznosci (moze trwac kilka minut)...
call "%INSTALL_DIR%\venv\Scripts\activate.bat"
pip install --upgrade pip >nul 2>&1
pip install -e "%INSTALL_DIR%" >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Blad instalacji!
    pause
    exit /b 1
)
echo [OK] Zaleznosci zainstalowane
echo.

REM Create .env if not exists
echo [6/6] Konfiguracja...
if not exist "%INSTALL_DIR%\.env" (
    if exist "%INSTALL_DIR%\.env.example" (
        copy "%INSTALL_DIR%\.env.example" "%INSTALL_DIR%\.env" >nul
        echo [OK] Utworzono plik .env
    )
)
echo.

REM Create start script
echo @echo off > "%INSTALL_DIR%\start.bat"
echo cd /d "%INSTALL_DIR%" >> "%INSTALL_DIR%\start.bat"
echo call venv\Scripts\activate.bat >> "%INSTALL_DIR%\start.bat"
echo eskimos serve >> "%INSTALL_DIR%\start.bat"
echo pause >> "%INSTALL_DIR%\start.bat"
echo [OK] Utworzono start.bat
echo.

REM Create desktop shortcut (optional)
echo Tworzenie skrotu na pulpicie...
set "DESKTOP=%USERPROFILE%\Desktop"
echo [InternetShortcut] > "%DESKTOP%\Eskimos Dashboard.url"
echo URL=http://localhost:8000 >> "%DESKTOP%\Eskimos Dashboard.url"
echo IconIndex=0 >> "%DESKTOP%\Eskimos Dashboard.url"
echo [OK] Skrot utworzony
echo.

echo  =======================================
echo   INSTALACJA ZAKONCZONA!
echo  =======================================
echo.
echo  Aby uruchomic Dashboard:
echo.
echo    1. Otworz: %INSTALL_DIR%\start.bat
echo    2. Przegladarka: http://localhost:8000
echo.
echo  Lub z linii polecen:
echo.
echo    cd %INSTALL_DIR%
echo    venv\Scripts\activate
echo    eskimos serve
echo.
echo  =======================================
echo.
pause
