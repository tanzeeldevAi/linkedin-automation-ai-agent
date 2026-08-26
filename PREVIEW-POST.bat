@echo off
REM ===================================================================
REM  Write a post and print it. Publishes NOTHING.
REM
REM  Use this to check the voice before you let the agent loose, and any
REM  time you change `voice:` or `content.pillars` in config.yaml.
REM ===================================================================
setlocal EnableExtensions
cd /d "%~dp0"
title LinkedIn Agent - post preview
set "LA_LOCAL=1"

echo Writing a post with your Claude subscription. This takes 20-60 seconds.
echo Nothing is published.
echo.
python run.py draft
echo.
pause
