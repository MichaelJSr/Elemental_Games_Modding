@echo off
rem Launcher for the Azurik Modding Toolkit GUI on Windows.
rem
rem Prefers the installed azurik-gui console script, then falls back
rem to pythonw (no console window) and finally python (keeps the
rem console open for tracebacks).  Pauses with an install hint if no
rem Python is found on PATH.

title Azurik Modding Toolkit
cd /d "%~dp0"

where azurik-gui >nul 2>&1
if %ERRORLEVEL%==0 (
    start "" azurik-gui
    exit
)

where pythonw >nul 2>&1
if %ERRORLEVEL%==0 (
    start "" pythonw -m gui
    exit
)

where python >nul 2>&1
if %ERRORLEVEL%==0 (
    python -m gui
    if %ERRORLEVEL% neq 0 pause
    exit
)

echo Python 3.10 or later was not found.
echo.
echo Install it from https://www.python.org/downloads/ and make sure
echo you tick "Add Python to PATH" during installation.
echo.
pause
