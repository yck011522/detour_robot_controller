@echo off
setlocal
cd /d "%~dp0"
if errorlevel 1 exit /b 1
".venv\Scripts\python.exe" "experiments\e14_monitor_identity\calibrate.py"
echo.
pause
