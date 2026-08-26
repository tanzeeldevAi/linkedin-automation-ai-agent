@echo off
REM ===================================================================
REM  Stop the agent.
REM
REM  Sets the pause flag first, so that even if a work block is mid-action
REM  nothing new starts, then closes the worker and dashboard windows.
REM ===================================================================
setlocal EnableExtensions
cd /d "%~dp0"
title LinkedIn Agent - stopping

echo Pausing the agent...
python run.py pause "stopped from STOP-AGENT.bat" 2>nul

echo Closing the worker and dashboard windows...
taskkill /F /T /FI "WINDOWTITLE eq LinkedIn Agent Worker*" >nul 2>&1
taskkill /F /T /FI "WINDOWTITLE eq LinkedIn Agent Dashboard*" >nul 2>&1

echo.
echo Stopped. Nothing will be posted until you run START-AGENT.bat again.
echo.
echo Note: the pause flag stays set, so START-AGENT.bat will bring it back
echo up but the agent stays paused until you press Start on the dashboard.
echo.
pause
