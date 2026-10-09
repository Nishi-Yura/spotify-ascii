@echo off
setlocal
cd /d "%~dp0"

echo.
echo  === spotify-ascii setup ===
echo.

rem ---- find a usable Python (3.9 - 3.12; winsdk has no wheels for newer) ----
set "PY="
for %%V in (3.12 3.11 3.10 3.9) do call :trypy %%V
if defined PY goto :havepy
python -c "import sys; sys.exit(0 if (3,9) <= sys.version_info[:2] <= (3,12) else 1)" >nul 2>nul
if not errorlevel 1 set "PY=python"
if defined PY goto :havepy

echo  Python 3.9 - 3.12 was not found.
echo.
where winget >nul 2>nul
if errorlevel 1 goto :nowinget
set "ANS="
set /p ANS=" Install Python 3.12 now with winget? [Y/N] "
if /i not "%ANS%"=="Y" goto :fail
winget install -e --id Python.Python.3.12 --scope user
if errorlevel 1 goto :fail
py -3.12 -c "import sys" >nul 2>nul
if errorlevel 1 goto :reopen
set "PY=py -3.12"
goto :havepy

:nowinget
echo  Please install Python 3.12 from https://www.python.org/downloads/
echo  and run run.bat again.
goto :fail

:reopen
echo.
echo  Python was installed. Close this window and open run.bat again.
goto :fail

:havepy
echo  Using: %PY%
%PY% --version

rem ---- create the virtual environment and install the libraries ----
if exist ".venv\Scripts\python.exe" goto :install
echo  Creating virtual environment .venv ...
%PY% -m venv .venv
if errorlevel 1 goto :fail

:install
echo  Installing libraries (the first time takes a few minutes) ...
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q --upgrade pip
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q -r requirements.txt
if errorlevel 1 goto :fail

echo.
echo  Setup complete!
echo.
endlocal
exit /b 0

:trypy
if defined PY exit /b 0
py -%1 -c "import sys" >nul 2>nul
if not errorlevel 1 set "PY=py -%1"
exit /b 0

:fail
echo.
echo  Setup did not finish. Please check the messages above.
pause
endlocal
exit /b 1
