# ============================================
# Eskimos 2.0 - Instalator PowerShell
# Run: powershell -ExecutionPolicy Bypass -File install.ps1
# ============================================

param(
    [string]$InstallDir = "C:\eskimos",
    [switch]$CreateService,
    [switch]$Force
)

$ErrorActionPreference = "Stop"

# Colors
function Write-Step { param($msg) Write-Host "[*] $msg" -ForegroundColor Cyan }
function Write-OK { param($msg) Write-Host "[OK] $msg" -ForegroundColor Green }
function Write-Err { param($msg) Write-Host "[ERROR] $msg" -ForegroundColor Red }
function Write-Warn { param($msg) Write-Host "[WARN] $msg" -ForegroundColor Yellow }

Write-Host ""
Write-Host "=======================================" -ForegroundColor Magenta
Write-Host "  ESKIMOS 2.0 - SMS Gateway z AI" -ForegroundColor Magenta
Write-Host "  Instalator PowerShell" -ForegroundColor Magenta
Write-Host "=======================================" -ForegroundColor Magenta
Write-Host ""

# Check Python
Write-Step "Sprawdzam Python..."
try {
    $pythonVersion = python --version 2>&1
    if ($pythonVersion -match "Python (\d+)\.(\d+)") {
        $major = [int]$Matches[1]
        $minor = [int]$Matches[2]
        if ($major -lt 3 -or ($major -eq 3 -and $minor -lt 11)) {
            Write-Err "Python 3.11+ wymagany! Masz: $pythonVersion"
            Write-Host "Pobierz: https://www.python.org/downloads/"
            exit 1
        }
        Write-OK "Python $major.$minor znaleziony"
    }
} catch {
    Write-Err "Python nie jest zainstalowany!"
    Write-Host "Pobierz: https://www.python.org/downloads/"
    Write-Host "Zaznacz 'Add Python to PATH' podczas instalacji!"
    exit 1
}

# Get script directory
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectDir = Split-Path -Parent $ScriptDir

Write-Step "Katalog projektu: $ProjectDir"
Write-Step "Katalog instalacji: $InstallDir"
Write-Host ""

# Create install directory
if (-not (Test-Path $InstallDir)) {
    Write-Step "Tworzenie katalogu $InstallDir..."
    New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null
    Write-OK "Katalog utworzony"
}

# Copy project files
Write-Step "Kopiowanie plikow projektu..."
if ($Force -or -not (Test-Path "$InstallDir\pyproject.toml")) {
    Copy-Item -Path "$ProjectDir\*" -Destination $InstallDir -Recurse -Force
    Write-OK "Pliki skopiowane"
} else {
    Write-Warn "Pliki juz istnieja, pomijam (uzyj -Force aby nadpisac)"
}
Write-Host ""

# Create virtual environment
Write-Step "Tworzenie srodowiska wirtualnego..."
Set-Location $InstallDir
if (-not (Test-Path "venv")) {
    python -m venv venv
    Write-OK "venv utworzony"
} else {
    Write-Warn "venv juz istnieje, pomijam"
}
Write-Host ""

# Activate and install
Write-Step "Instalowanie zaleznosci (moze trwac kilka minut)..."
& "$InstallDir\venv\Scripts\Activate.ps1"
pip install --upgrade pip --quiet
pip install -e $InstallDir --quiet
if ($LASTEXITCODE -ne 0) {
    Write-Err "Blad instalacji!"
    exit 1
}
Write-OK "Zaleznosci zainstalowane"
Write-Host ""

# Create .env
Write-Step "Konfiguracja..."
if (-not (Test-Path "$InstallDir\.env")) {
    if (Test-Path "$InstallDir\.env.example") {
        Copy-Item "$InstallDir\.env.example" "$InstallDir\.env"
        Write-OK "Utworzono .env"
    }
} else {
    Write-Warn ".env juz istnieje"
}
Write-Host ""

# Create start script
$startBat = @"
@echo off
cd /d "$InstallDir"
call venv\Scripts\activate.bat
eskimos serve
pause
"@
$startBat | Out-File -FilePath "$InstallDir\start.bat" -Encoding ASCII
Write-OK "Utworzono start.bat"

# Create desktop shortcut
Write-Step "Tworzenie skrotu na pulpicie..."
$Desktop = [Environment]::GetFolderPath("Desktop")
$shortcutUrl = @"
[InternetShortcut]
URL=http://localhost:8000
IconIndex=0
"@
$shortcutUrl | Out-File -FilePath "$Desktop\Eskimos Dashboard.url" -Encoding ASCII
Write-OK "Skrot utworzony"
Write-Host ""

# Optional: Create Windows Service using NSSM
if ($CreateService) {
    Write-Step "Instalowanie jako Windows Service..."

    # Check for NSSM
    $nssmPath = Get-Command nssm -ErrorAction SilentlyContinue
    if (-not $nssmPath) {
        Write-Warn "NSSM nie znaleziony. Pobierz z: https://nssm.cc/"
        Write-Warn "Service nie zostanie utworzony"
    } else {
        # Install service
        nssm install EskimosAPI "$InstallDir\venv\Scripts\python.exe" "-m" "eskimos.api"
        nssm set EskimosAPI AppDirectory $InstallDir
        nssm set EskimosAPI Description "Eskimos 2.0 SMS Gateway API"
        nssm set EskimosAPI Start SERVICE_AUTO_START

        Write-OK "Service 'EskimosAPI' zainstalowany"
        Write-Host "  Start: nssm start EskimosAPI"
        Write-Host "  Stop:  nssm stop EskimosAPI"
    }
}

Write-Host ""
Write-Host "=======================================" -ForegroundColor Green
Write-Host "  INSTALACJA ZAKONCZONA!" -ForegroundColor Green
Write-Host "=======================================" -ForegroundColor Green
Write-Host ""
Write-Host "  Aby uruchomic Dashboard:" -ForegroundColor White
Write-Host ""
Write-Host "    1. Otworz: $InstallDir\start.bat" -ForegroundColor Yellow
Write-Host "    2. Przegladarka: http://localhost:8000" -ForegroundColor Yellow
Write-Host ""
Write-Host "  Lub z PowerShell:" -ForegroundColor White
Write-Host ""
Write-Host "    cd $InstallDir" -ForegroundColor Cyan
Write-Host "    .\venv\Scripts\Activate.ps1" -ForegroundColor Cyan
Write-Host "    eskimos serve" -ForegroundColor Cyan
Write-Host ""
Write-Host "=======================================" -ForegroundColor Green
