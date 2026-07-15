@echo off
REM Install dependencies (first run) and launch Course Assistant.
REM Works from a release ZIP (root next to CourseAssistant\) or a git checkout.
setlocal
set "ROOT=%~dp0"

echo Python 3.10 or newer is required.
if exist "%ROOT%CourseAssistant\start-windows.bat" (
    cd /d "%ROOT%CourseAssistant"
) else if exist "%ROOT%start-windows.bat" (
    cd /d "%ROOT%"
) else (
    echo Could not find start-windows.bat.
    exit /b 1
)

call start-windows.bat
exit /b %errorlevel%
