@echo off
rem Turns "start spotify-ascii when I sign in to Windows" on or off.
rem (Adds / removes a shortcut to run.bat in your Startup folder; the window
rem starts minimised and the picture goes on the desktop wallpaper.)
cd /d "%~dp0"
set "STARTUP=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
set "LNK=%STARTUP%\spotify-ascii.lnk"
rem shortcut made by older versions (wallpaper.bat no longer exists)
if exist "%STARTUP%\spotify-ascii wallpaper.lnk" del "%STARTUP%\spotify-ascii wallpaper.lnk"
if exist "%LNK%" (
    del "%LNK%"
    echo  Autostart is now OFF. spotify-ascii will no longer start when you sign in.
) else (
    powershell -NoProfile -ExecutionPolicy Bypass -Command "$s=(New-Object -ComObject WScript.Shell).CreateShortcut($env:LNK); $s.TargetPath='%~dp0run.bat'; $s.WorkingDirectory='%~dp0'; $s.WindowStyle=7; $s.Save()"
    if exist "%LNK%" (
        echo  Autostart is now ON. spotify-ascii will start minimised when you sign in.
    ) else (
        echo  Could not create the shortcut.
    )
)
echo  Run this file again to switch it back.
pause
