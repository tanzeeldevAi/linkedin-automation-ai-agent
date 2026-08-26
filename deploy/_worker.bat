@echo off
REM Started by START-AGENT.bat in its own window so the log is visible.
REM Kept as a separate file purely to avoid nested-quote hell in `start cmd /k`.
setlocal EnableExtensions
cd /d "%~dp0.."
title LinkedIn Agent Worker
set "LA_LOCAL=1"
REM Hold one Chrome open across work blocks instead of reopening it every time.
REM You can minimise that window; do not close it while the worker is running.
set "LA_KEEP_BROWSER=1"
echo Worker running. Closing this window stops the agent.
echo A Chrome window will open and stay open - you can minimise it.
echo.
python run.py worker
echo.
echo Worker stopped.
pause
