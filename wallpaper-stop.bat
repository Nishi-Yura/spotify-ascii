@echo off
rem Stops the spotify-ascii desktop wallpaper and restores your normal wallpaper.
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" exit /b 0
".venv\Scripts\python.exe" wallpaper.py --stop
timeout /t 3 >nul
