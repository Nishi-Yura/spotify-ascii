@echo off
rem Waits for Spotify to start, opens the visualiser in its own window, and
rem closes it again after Spotify quits. Put a shortcut to this file in
rem shell:startup to have it running whenever you use Spotify.
rem (The work is done by watch.py, so updates can improve it.)
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    call setup.bat
    if errorlevel 1 exit /b 1
)
".venv\Scripts\python.exe" watch.py
