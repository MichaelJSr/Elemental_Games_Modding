@echo off
title Azurik Randomizer
cd /d "%~dp0"

where pythonw >nul 2>&1
if %ERRORLEVEL%==0 (
    start "" pythonw azurik_gui_launcher.py
    exit
)

where python >nul 2>&1
if %ERRORLEVEL%==0 (
    python azurik_gui_launcher.py
    if %ERRORLEVEL% neq 0 pause
    exit
)

echo Python was not found. Please install Python 3.10 or later from:
echo https://www.python.org/downloads/
echo.
echo Make sure to check "Add Python to PATH" during installation.
pause
