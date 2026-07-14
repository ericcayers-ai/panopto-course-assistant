@echo off
REM ============================================================================
REM  Panopto Course Assistant - one-click launcher (Windows)
REM  Double-click this file. On first run it sets up everything; after that it
REM  just starts the app and opens your browser.
REM ============================================================================
setlocal enableextensions enabledelayedexpansion
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

REM --- check for app updates (best-effort; skipped if offline / no git) -----
where git >nul 2>nul && if exist ".git" (
  echo Checking for updates...
  git -C "%~dp0." fetch --quiet origin 2>nul
  if not errorlevel 1 (
    set "BEHIND=0"
    for /f %%L in ('git -C "%~dp0." rev-list --count HEAD..@{u} 2^>nul') do set "BEHIND=%%L"
    if not "!BEHIND!"=="0" (
      REM only update when there are no local changes to avoid conflicts
      git -C "%~dp0." diff --quiet && git -C "%~dp0." diff --cached --quiet && (
        echo   Update found ^(!BEHIND! new commit^(s^)^) - updating...
        git -C "%~dp0." merge --ff-only @{u} --quiet && echo   Updated to the latest version. || echo   Could not auto-update; continuing on current version.
      ) || echo   Local changes present - skipping auto-update.
    )
  )
)

REM --- install / update dependencies ---------------------------------------
echo Installing dependencies ^(first run only, quick after that^)...
"%VENV_PY%" -m pip install --quiet --upgrade pip
"%VENV_PY%" -m pip install --quiet -r requirements.txt
if errorlevel 1 ( echo Failed to install dependencies. & pause & exit /b 1 )

REM --- first-run: offer optional transcription / docs / speech / Playwright ---
REM  Marked done with a stamp file so returning users are never asked again.
if not exist ".venv\.extras_offered" (
  echo.
  echo   Optional add-ons enable lecture transcription, full document
  echo   conversion ^(PDF/PowerPoint/Word^), text-to-speech, and Moodle
  echo   browser scrape ^(Playwright/Chromium^). They are a larger download.
  echo   You can also add them later with install-extras-windows.bat.
  echo.
  choice /c YN /t 20 /d N /m "  Install the optional add-ons now"
  if errorlevel 2 ( echo   Skipping add-ons for now. )
  if errorlevel 1 if not errorlevel 2 (
    echo   Installing add-ons ^(this can take a few minutes^)...
    "%VENV_PY%" -m pip install -r requirements-transcribe.txt
    "%VENV_PY%" -m pip install -r requirements-tts.txt
    "%VENV_PY%" -m pip install -r requirements-browser.txt
    if errorlevel 1 (
      echo   WARNING: Playwright pip install failed - browser scrape unavailable.
    ) else (
      echo   Installing Chromium for Playwright ^(best-effort^)...
      "%VENV_PY%" -m playwright install chromium
      if errorlevel 1 (
        echo   WARNING: playwright install chromium failed.
        echo   Retry later: "%VENV_PY%" -m playwright install chromium
      )
    )
  )
  echo done> ".venv\.extras_offered"
)

REM --- launch ---------------------------------------------------------------
echo.
echo   Starting Panopto Course Assistant...
echo   Your browser will open at http://127.0.0.1:8000
echo   Leave this window open while you use the app; close it to stop.
echo.
set "OPEN_BROWSER=1"
"%VENV_PY%" run.py

pause
