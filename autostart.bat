@echo off
rem Turns "start spotify-ascii when I sign in to Windows" on or off.
rem (The work is done by autostart.py, so updates can improve it.)
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    call setup.bat
    if errorlevel 1 exit /b 1
)
".venv\Scripts\python.exe" autostart.py
pause
