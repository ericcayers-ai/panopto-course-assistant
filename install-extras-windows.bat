@echo off
REM ============================================================================
REM  Optional add-ons for Panopto Course Assistant (Windows)
REM  Installs the heavier features:
REM    - transcription engine (faster-whisper) + yt-dlp downloader
REM    - MarkItDown[all] for PDF / PowerPoint / Word / Excel -> Markdown
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

echo Installing optional add-ons ^(this can take several minutes^)...
".venv\Scripts\python.exe" -m pip install -r requirements-transcribe.txt
if errorlevel 1 ( echo Some packages failed to install. & pause & exit /b 1 )

echo.
echo   Done. Restart the app (start-windows.bat) to use transcription and
echo   full document conversion.
echo.
pause
