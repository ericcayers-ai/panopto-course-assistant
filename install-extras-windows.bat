@echo off
REM ============================================================================
REM  Optional add-ons for Panopto Course Assistant (Windows)
REM  Installs the heavier features:
REM    - transcription engine (faster-whisper) + yt-dlp downloader
REM    - MarkItDown[all] for PDF / PowerPoint / Word / Excel -> Markdown
REM    - Kokoro-82M text-to-speech (Speech tab)
REM    - Playwright + Chromium (Moodle browser scrape / Panopto discovery)
REM  Run start-windows.bat at least once first (it creates the environment).
REM ============================================================================
setlocal enableextensions
cd /d "%~dp0"
title Panopto Course Assistant - optional add-ons

if not exist ".venv\Scripts\python.exe" (
  echo Please run start-windows.bat once first to create the environment.
  pause
  exit /b 1
)

echo Installing transcription + document conversion...
".venv\Scripts\python.exe" -m pip install -r requirements-transcribe.txt
if errorlevel 1 ( echo Some packages failed to install. & pause & exit /b 1 )

echo.
echo Installing Kokoro text-to-speech...
echo ^(Downloads packages from PyPI; the ~300 MB model fetches on first use.^)
".venv\Scripts\python.exe" -m pip install -r requirements-tts.txt
if errorlevel 1 ( echo Kokoro install failed - TTS will not be available. & echo. )

echo.
echo Installing Playwright browser scrape support...
".venv\Scripts\python.exe" -m pip install -r requirements-browser.txt
if errorlevel 1 (
  echo Playwright pip install failed - browser Moodle scrape will not be available.
  echo You can retry later: pip install -r requirements-browser.txt
  echo.
) else (
  echo Installing Chromium for Playwright ^(best-effort^)...
  ".venv\Scripts\python.exe" -m playwright install chromium
  if errorlevel 1 (
    echo WARNING: playwright install chromium failed.
    echo Browser scrape needs Chromium. Retry: .venv\Scripts\python.exe -m playwright install chromium
    echo.
  ) else (
    echo Chromium installed.
  )
)

echo.
echo   Done. Restart the app ^(start-windows.bat^) to use transcription,
echo   document conversion, Speech TTS, and Moodle browser scrape.
echo   Optional: install espeak-ng for non-English / OOV pronunciation fallback.
echo.
pause
