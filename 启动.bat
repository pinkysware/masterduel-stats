@echo off
setlocal
rem ---------------------------------------------------------------
rem Master Duel stats - one click start
rem   1) collector : archives duel logs + writes into the DB (no window)
rem   2) floating window + dashboard : read the DB only (they do NOT collect!)
rem
rem NOTE: cmd runs this file as GBK, so keep everything ASCII here.
rem NOTE: second run is safe - both apps guard against duplicates
rem       (collector: data\archive\duel_collector.pid, window: data\.duel_ui.lock)
rem ---------------------------------------------------------------

rem locate python: py launcher (3.13) -> python on PATH
set "PY="
for /f "delims=" %%i in ('py -3.13 -c "import sys;print(sys.executable)" 2^>nul') do set "PY=%%i"
if not defined PY for /f "delims=" %%i in ('python -c "import sys;print(sys.executable)" 2^>nul') do set "PY=%%i"
if not defined PY (
  echo [ERROR] Python 3.13 not found.
  echo Install Python 3.13 ^(https://www.python.org/^) and make sure
  echo "py" or "python" works on PATH, then run this again.
  pause
  exit /b 1
)
set "PYW=%PY%"
if /i "%PYW:~-10%"=="python.exe" set "PYW=%PYW:~0,-10%pythonw.exe"
if not exist "%PYW%" set "PYW=%PY%"

cd /d "%~dp0"
if not exist "%PY%" (
  echo [ERROR] Python not found: %PY%
  echo.
  pause
  exit /b 1
)

echo Starting collector ^(no window^; stop via the Stop script^) ...
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
echo   - to stop both  : run the Stop script in this folder
endlocal
