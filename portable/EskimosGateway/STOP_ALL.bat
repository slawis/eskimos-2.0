@echo off
echo Stopping all Eskimos services...

:: Stop CMD windows by title
taskkill /f /fi "WINDOWTITLE eq Eskimos*" 2>nul

:: Wait for CMD windows to close
timeout /t 2 /nobreak >nul

:: Kill ALL python processes (CMD child processes don't inherit window title)
taskkill /f /im python.exe 2>nul
taskkill /f /im pythonw.exe 2>nul

:: Remove PID file
if exist .daemon.pid del .daemon.pid

echo.
echo All services stopped.
pause
