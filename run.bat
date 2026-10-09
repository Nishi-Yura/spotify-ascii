@echo off
chcp 65001 >nul
cd /d "%~dp0"
rem First run: set up the Python environment (.venv) automatically.
if not exist ".venv\Scripts\python.exe" (
    call setup.bat
    if errorlevel 1 exit /b 1
)
".venv\Scripts\python.exe" main.py %*
if errorlevel 1 pause
