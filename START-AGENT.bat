@echo off
REM ===================================================================
REM  LinkedIn Agent -- start everything.
REM
REM  Double-click this. It installs what it needs the first time, signs
REM  you in once, learns your profile, then runs the agent and opens the
REM  dashboard. Every run after the first is fully automatic.
REM ===================================================================
setlocal EnableExtensions
cd /d "%~dp0"
title LinkedIn Agent - starting

REM Local mode: dashboard binds to this machine only, so no password prompt.
set "LA_LOCAL=1"
set "LA_NO_AUTH=1"

echo.
echo  ==========================================
echo   LinkedIn Agent
echo  ==========================================
echo.

REM ---------- python ------------------------------------------------
where python >nul 2>&1
if errorlevel 1 (
  echo  [X] Python is not on PATH.
  echo      Install Python 3.11+ from python.org and tick "Add to PATH".
  goto :die
)

REM ---------- the claude CLI is the writing engine -------------------
where claude >nul 2>&1
if errorlevel 1 (
  echo  [X] The 'claude' command is not on PATH.
  echo      The agent writes through your Claude subscription, so it needs
  echo      Claude Code installed and signed in. Then reopen this window.
  goto :die
)

REM ---------- one-time setup ---------------------------------------
if not exist ".setup-done" (
  echo  [1/5] First run: installing dependencies. This takes a few minutes.
  python -m pip install --quiet --disable-pip-version-check -r requirements.txt
  if errorlevel 1 goto :die
  echo        Downloading the browser Playwright drives...
  python -m playwright install chromium
  if errorlevel 1 goto :die
  echo done > ".setup-done"
) else (
  echo  [1/5] Dependencies already installed.
)

REM ---------- database ---------------------------------------------
echo  [2/5] Preparing the database...
python run.py init
if errorlevel 1 goto :die

REM ---------- what still needs doing? ------------------------------
set "STATE="
for /f "usebackq delims=" %%S in (`python run.py ready`) do set "STATE=%%S"

if "%STATE%"=="NEED_LOGIN" (
  echo.
  echo  [3/5] You need to sign in to LinkedIn once.
  echo        A Chrome window will open. Sign in there, finish any
  echo        verification code, wait for your feed, then press Enter here.
  echo.
  python run.py login
  if errorlevel 1 (
    echo  [X] Sign-in did not complete. Run this file again when ready.
    goto :die
  )
  for /f "usebackq delims=" %%S in (`python run.py ready`) do set "STATE=%%S"
) else (
  echo  [3/5] Already signed in to LinkedIn.
)

if "%STATE%"=="NEED_LEARN" (
  echo.
  echo  [4/5] Reading your profile to learn your niche and services...
  python run.py learn
  if errorlevel 1 echo  [!] Could not read the profile. The agent will use config.yaml as-is.
) else (
  echo  [4/5] Profile already learned.
)

REM ---------- go ---------------------------------------------------
echo.
echo  [5/5] Starting the agent and the dashboard...
start "LinkedIn Agent Worker" cmd /k "%~dp0deploy\_worker.bat"
start "LinkedIn Agent Dashboard" cmd /k "%~dp0deploy\_dashboard.bat"

REM Give uvicorn a moment to bind before the browser asks for the page.
timeout /t 5 /nobreak >nul
start "" "http://127.0.0.1:4300"

echo.
echo  Running. Two windows have opened: the worker and the dashboard.
echo.
echo   Dashboard : http://127.0.0.1:4300
echo   To stop   : run STOP-AGENT.bat, or press Stop on the dashboard.
echo.
echo  The agent works between 08:00 and 21:00 Dubai time and stays quiet
echo  outside those hours. It starts at 25%% of full volume and ramps up
echo  over two weeks.
echo.
pause
exit /b 0

:die
echo.
pause
exit /b 1
