@echo off
setlocal EnableDelayedExpansion
title PDF Master Toolkit - development

REM Runs the application from source, without building an installer.
REM Use this while testing changes; it starts in about two seconds.

cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Looking for a supported Python ^(3.11 - 3.14^)...
    set PYEXE=
    for %%V in (3.13 3.12 3.14 3.11) do (
        if "!PYEXE!"=="" (
            py -%%V -c "import sys" >nul 2>&1
            if not errorlevel 1 (
                for /f "delims=" %%P in ('py -%%V -c "import sys;print(sys.executable)"') do set PYEXE=%%P
            )
        )
    )
    if "!PYEXE!"=="" (
        echo.
        echo  No supported Python found. This project needs 3.11 - 3.14.
        echo.
        echo  Install Python ^(64-bit^) from python.org and try again.
        echo.
        pause
        exit /b 1
    )
    echo Creating the virtual environment...
    "!PYEXE!" -m venv .venv || goto :fail
    .venv\Scripts\python.exe -m pip install -r requirements.txt || goto :fail
)

if not exist "assets\icon.ico" (
    .venv\Scripts\python.exe tools\make_assets.py
)

.venv\Scripts\python.exe -m app %*
goto :eof

:fail
echo.
echo Setup failed. See the messages above.
pause
endlocal
