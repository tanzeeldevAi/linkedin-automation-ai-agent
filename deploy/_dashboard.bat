@echo off
REM Started by START-AGENT.bat in its own window.
setlocal EnableExtensions
cd /d "%~dp0.."
title LinkedIn Agent Dashboard
set "LA_LOCAL=1"
set "LA_NO_AUTH=1"
echo Dashboard on http://127.0.0.1:4300
echo.
python run.py dashboard
echo.
echo Dashboard stopped.
pause
