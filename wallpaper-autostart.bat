@echo off
rem Turns "start the wallpaper when I sign in to Windows" on or off.
rem (Adds / removes a shortcut to wallpaper.bat in your Startup folder.)
cd /d "%~dp0"
set "LNK=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\spotify-ascii wallpaper.lnk"
if exist "%LNK%" (
    del "%LNK%"
    echo  Autostart is now OFF. The wallpaper will no longer start when you sign in.
) else (
    powershell -NoProfile -ExecutionPolicy Bypass -Command "$s=(New-Object -ComObject WScript.Shell).CreateShortcut($env:LNK); $s.TargetPath='%~dp0wallpaper.bat'; $s.WorkingDirectory='%~dp0'; $s.WindowStyle=7; $s.Save()"
    if exist "%LNK%" (
        echo  Autostart is now ON. The wallpaper will start when you sign in.
    ) else (
        echo  Could not create the shortcut.
    )
)
echo  Run this file again to switch it back.
pause
