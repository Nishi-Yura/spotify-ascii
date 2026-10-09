@echo off
rem Optional: adds mp4 / webm / mov playback for clips in the media folder
rem (installs OpenCV into this app's .venv). GIF and PNG work without it.
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    call setup.bat
    if errorlevel 1 exit /b 1
)
echo  Installing video support (OpenCV, about 45 MB) ...
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q -r requirements-video.txt
if errorlevel 1 (
    echo  Installation failed. Please check the messages above.
    pause
    exit /b 1
)
echo  Done. mp4 files in the media folder will now play.
pause
