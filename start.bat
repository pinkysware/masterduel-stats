@echo off
setlocal
rem ---------------------------------------------------------------
rem Master Duel Stats - one click start
rem   1) collector : archives duel logs + writes into the DB (no window)
rem   2) floating window + dashboard : read the DB only (they do NOT collect!)
rem
rem Python resolution order:
rem   1. python_path.txt  (repo root, one line: full path to python.exe)
rem   2. "py -3.13" launcher
rem   3. "python" on PATH (PySide6 is auto-installed into it if missing)
rem NOTE: cmd runs this file as GBK, so keep everything ASCII here.
rem ---------------------------------------------------------------

cd /d "%~dp0"

set "PY="

rem --- 1) explicit override (recommended when several Pythons are installed)
if exist "python_path.txt" (
  for /f "usebackq delims=" %%i in ("python_path.txt") do (
    if exist "%%i" set "PY=%%i"
  )
)

rem --- 2) py launcher with 3.13
if not defined PY (
  for /f "delims=" %%i in ('py -3.13 -c "import sys;print(sys.executable)" 2^>nul') do set "PY=%%i"
)

rem --- 3) python on PATH
if not defined PY (
  for /f "delims=" %%i in ('python -c "import sys;print(sys.executable)" 2^>nul') do set "PY=%%i"
)

if not defined PY (
  echo [ERROR] Python 3.13 not found.
  echo Install Python 3.13 from https://www.python.org/downloads/
  echo ^(or create python_path.txt in this folder with the full path
  echo  to your python.exe^), then run start.bat again.
  pause
  exit /b 1
)

set "PYW=%PY%"
if /i "%PYW:~-10%"=="python.exe" set "PYW=%PYW:~0,-10%pythonw.exe"
if not exist "%PYW%" set "PYW=%PY%"

rem --- dependency check: PySide6 must be importable; install once if missing
"%PY%" -c "import PySide6" >nul 2>&1
if errorlevel 1 (
  echo [SETUP] PySide6 not found for this Python - installing dependencies
  echo         ^(one-time, needs internet^)...
  "%PY%" -m pip install -r requirements.txt
  "%PY%" -c "import PySide6" >nul 2>&1
  if errorlevel 1 (
    echo [ERROR] Dependency install failed. Run manually:
    echo         "%PY%" -m pip install -r requirements.txt
    pause
    exit /b 1
  )
)

echo Starting collector ^(no window^; to stop, close the window or use the tray^) ...
start "" "%PYW%" tools\duel_collector.py --minutes 0

rem give the collector a moment to grab the single-instance pid file
rem (NOTE: `timeout` dies when stdin is redirected - use ping as the sleeper)
ping -n 3 127.0.0.1 >nul

echo Starting the floating window ...
start "MD Stats Window" "%PYW%" tools\duel_ui.py

echo Done.
echo   - collector log : data\archive\duel_collector.log
echo   - dashboard opens automatically
echo   - floating window shows/hides per its own page (or the tray menu)
echo   - to stop both  : close the window ^(X^), or right-click tray -> exit
endlocal
