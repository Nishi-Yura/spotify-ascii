@echo off
chcp 65001 >nul
cd /d "%~dp0"
rem First run: set up the Python environment (.venv) automatically.
if not exist ".venv\Scripts\python.exe" (
    call setup.bat
    if errorlevel 1 exit /b 1
)
:start
".venv\Scripts\python.exe" main.py %*
rem exit code 10: updated itself from the newest GitHub release - start the new version
if %errorlevel%==10 goto start
if errorlevel 1 pause
