@echo off
REM ============================================================================
REM  Optional add-ons for Panopto Course Assistant (Windows)
REM  Installs the heavier features:
REM    - transcription engine (faster-whisper) + yt-dlp downloader
REM    - MarkItDown[all] for PDF / PowerPoint / Word / Excel -> Markdown
REM    - VibeVoice-Realtime-0.5B text-to-speech (Voice TTS tab)
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
echo Installing VibeVoice text-to-speech...
echo ^(This pulls from GitHub and may take a few minutes on first run.^)
".venv\Scripts\python.exe" -m pip install -r requirements-tts.txt
if errorlevel 1 ( echo VibeVoice install failed - TTS will not be available. & echo. )

echo.
echo   Done. Restart the app ^(start-windows.bat^) to use transcription,
echo   full document conversion, and the Voice TTS tab.
echo.
pause
