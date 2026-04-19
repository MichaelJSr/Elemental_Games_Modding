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

rem Pre-check that the `gui` package imports cleanly.  If it doesn't,
rem the project isn't installed and running `pythonw -m gui` would
rem flash a console and close — point the user at pip instead.
where python >nul 2>&1
if %ERRORLEVEL%==0 (
    python -c "import gui" >nul 2>&1
    if %ERRORLEVEL%==0 (
        rem Prefer pythonw so we don't leave a stray console window.
        where pythonw >nul 2>&1
        if %ERRORLEVEL%==0 (
            start "" pythonw -m gui
            exit
        )
        python -m gui
        if %ERRORLEVEL% neq 0 pause
        exit
    )
    echo Found python but could not import the `gui` package.
    echo This usually means the project hasn't been installed yet.
    echo.
    echo From this folder, run:
    echo     python -m pip install -e .
    echo.
    echo Then retry this launcher.
    pause
    exit
)

echo Python 3.10 or later was not found.
echo.
echo Install it from https://www.python.org/downloads/ and make sure
echo you tick "Add Python to PATH" during installation.
echo.
pause
