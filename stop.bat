@echo off
setlocal
rem ---------------------------------------------------------------
rem Master Duel stats - stop collector + floating window
rem
rem Reads the pids they wrote themselves:
rem   collector : data\archive\duel_collector.pid  (json: "collector_pid")
rem   window    : data\.duel_ui.lock               (first line = pid)
rem
rem Safety: before killing, check the pid really is a python.exe,
rem so a stale pid file can never kill an unrelated process.
rem NOTE: cmd runs this file as GBK, so keep everything ASCII here.
rem ---------------------------------------------------------------

cd /d "%~dp0"

set "PIDFILE=data\archive\duel_collector.pid"
set "LOCKFILE=data\.duel_ui.lock"
set "CPID="
set "UPID="

if exist "%PIDFILE%" (
  rem file is one json line: {"collector_pid": 1234, "game_pid": 5678, "at": "..."}
  for /f "usebackq tokens=1 delims=," %%A in (`findstr /r /c:"collector_pid" "%PIDFILE%"`) do (
    for /f "tokens=2 delims=: " %%B in ("%%A") do set "CPID=%%B"
  )
)

if exist "%LOCKFILE%" (
  for /f "usebackq delims=" %%A in ("%LOCKFILE%") do if not defined UPID set "UPID=%%A"
)

if defined CPID (
  tasklist /fi "pid eq %CPID%" | findstr /i "python.exe" >nul
  if not errorlevel 1 (
    echo Stopping collector ^(pid %CPID%^) ...
    taskkill /pid %CPID% /t /f >nul
  ) else (
    echo Collector pid %CPID% is not a python.exe - skipped ^(stale pid file^)
  )
) else (
  echo No collector pid file found - nothing to stop.
)

if defined UPID (
  tasklist /fi "pid eq %UPID%" | findstr /i "python" >nul
  if not errorlevel 1 (
    echo Stopping the floating window ^(pid %UPID%^) ...
    taskkill /pid %UPID% /t /f >nul
  ) else (
    echo Window pid %UPID% is not a python.exe - skipped ^(stale lock file^)
  )
) else (
  echo No window lock file found - nothing to stop.
)

echo Done.
ping -n 3 127.0.0.1 >nul
endlocal
