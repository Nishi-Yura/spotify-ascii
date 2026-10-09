@echo off
rem Waits for Spotify to start, opens the visualiser in its own window, and
rem closes it again after Spotify quits. Put a shortcut to this file in
rem shell:startup to have it running whenever you use Spotify.
cd /d "%~dp0"
:wait
tasklist /FI "IMAGENAME eq Spotify.exe" 2>nul | find /I "Spotify.exe" >nul
if errorlevel 1 (
    timeout /t 5 /nobreak >nul
    goto wait
)
where wt >nul 2>nul
if errorlevel 1 (
    start "spotify ascii" /d "%~dp0." cmd /c run.bat --quit-idle 30
) else (
    wt -w new -d "%~dp0." --title "spotify ascii" cmd /c run.bat --quit-idle 30
)
rem wait until Spotify has quit, then start watching again
:running
timeout /t 5 /nobreak >nul
tasklist /FI "IMAGENAME eq Spotify.exe" 2>nul | find /I "Spotify.exe" >nul
if not errorlevel 1 goto running
goto wait
