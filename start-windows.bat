@echo off
REM ============================================================================
REM  Panopto Course Assistant - one-click launcher (Windows)
REM  Double-click this file. On first run it sets up everything; after that it
REM  just starts the app and opens your browser.
REM ============================================================================
setlocal enableextensions
cd /d "%~dp0"
title Panopto Course Assistant

REM --- find a Python interpreter -------------------------------------------
set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY (
  where python >nul 2>nul && set "PY=python"
)
if not defined PY (
  echo.
  echo   Python was not found on this computer.
  echo   Please install Python 3.10+ from https://www.python.org/downloads/
  echo   ^(tick "Add python.exe to PATH" in the installer^), then run this again.
  echo.
  pause
  exit /b 1
)

REM --- create the virtual environment on first run -------------------------
if not exist ".venv\Scripts\python.exe" (
  echo Creating a private Python environment ^(first run, ~1 min^)...
  %PY% -m venv .venv
  if errorlevel 1 ( echo Failed to create the environment. & pause & exit /b 1 )
)

set "VENV_PY=.venv\Scripts\python.exe"

REM --- install / update dependencies ---------------------------------------
echo Installing dependencies ^(first run only, quick after that^)...
"%VENV_PY%" -m pip install --quiet --upgrade pip
"%VENV_PY%" -m pip install --quiet -r requirements.txt
if errorlevel 1 ( echo Failed to install dependencies. & pause & exit /b 1 )

REM --- launch ---------------------------------------------------------------
echo.
echo   Starting Panopto Course Assistant...
echo   Your browser will open at http://127.0.0.1:8000
echo   Leave this window open while you use the app; close it to stop.
echo.
set "OPEN_BROWSER=1"
"%VENV_PY%" run.py

pause
