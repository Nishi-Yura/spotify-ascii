@echo off
rem Shows spotify-ascii as your desktop wallpaper (behind the desktop icons).
rem Stop it with wallpaper-stop.bat. Options are passed through, e.g.
rem   wallpaper.bat --monitor 2 --rows 45 --fps 15 --no-hud
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
    call setup.bat
    if errorlevel 1 exit /b 1
)
start "" ".venv\Scripts\pythonw.exe" wallpaper.py %*
echo.
echo  spotify-ascii wallpaper started.
echo  Play a song in Spotify. To stop it, run wallpaper-stop.bat.
echo.
timeout /t 4 >nul
