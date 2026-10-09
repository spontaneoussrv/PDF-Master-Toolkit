@echo off
setlocal EnableDelayedExpansion
title Building PDF Master Toolkit

REM ===========================================================================
REM  PDF Master Toolkit - one-click Windows build
REM
REM  Produces:  dist_installer\PDF_Master_Toolkit_Setup.exe
REM
REM  Requirements on THIS machine only:
REM    * Python 3.11 - 3.14 (64-bit)
REM    * Inno Setup 6   https://jrsoftware.org/isdl.php
REM
REM  The end user needs NONE of the above - the installer is self-contained.
REM ===========================================================================

cd /d "%~dp0"

echo.
echo  ===========================================================
echo    PDF Master Toolkit  -  build
echo  ===========================================================
echo.

REM ------------------------------------------------- 1. find a usable Python
REM Several Pythons often coexist. Try them newest-first via the Windows "py"
REM launcher, then fall back to whatever "python" resolves to.
REM 3.11 - 3.14 all work; anything outside that range is rejected with a clear
REM message rather than letting pip fail confusingly further down.

echo [1/8] Looking for a supported Python...
set PYEXE=

for %%V in (3.13 3.12 3.14 3.11) do (
    if "!PYEXE!"=="" (
        py -%%V -c "import sys" >nul 2>&1
        if not errorlevel 1 (
            for /f "delims=" %%P in ('py -%%V -c "import sys;print(sys.executable)"') do set PYEXE=%%P
            set PYTAG=%%V
        )
    )
)

if "!PYEXE!"=="" (
    where python >nul 2>&1
    if not errorlevel 1 (
        for /f "delims=" %%P in ('python -c "import sys;print(sys.executable)" 2^>nul') do set PYEXE=%%P
        for /f "delims=" %%T in ('python -c "import sys;print('%%d.%%d'%%sys.version_info[:2])" 2^>nul') do set PYTAG=%%T
        REM reject anything outside 3.11 - 3.14
        python -c "import sys;sys.exit(0 if (3,11)<=sys.version_info[:2]<=(3,14) else 1)" >nul 2>&1
        if errorlevel 1 set PYEXE=
    )
)

if "!PYEXE!"=="" (
    echo.
    echo  ---------------------------------------------------------------
    echo   ERROR: no supported Python found.
    echo.
    echo   This project needs Python 3.11, 3.12, 3.13 or 3.14 ^(64-bit^).
    echo.
    echo   FIX: install Python from https://www.python.org/downloads/
    echo        and TICK "Add python.exe to PATH" on the first screen.
    echo.
    echo   Installed versions detected on this machine:
    py -0 2>nul
    echo  ---------------------------------------------------------------
    echo.
    pause
    exit /b 1
)

echo       Using Python !PYTAG!
echo       !PYEXE!

"!PYEXE!" -c "import sys; sys.exit(0 if sys.maxsize > 2**32 else 1)" >nul 2>&1
if errorlevel 1 (
    echo.
    echo  ERROR: that is 32-bit Python. Install the 64-bit build instead.
    pause
    exit /b 1
)

REM ------------------------------------------------------- 2. virtualenv
echo [2/8] Preparing the build environment...

REM If a venv exists but was built with a different Python, rebuild it.
if exist ".venv\Scripts\python.exe" (
    .venv\Scripts\python.exe -c "import sys;sys.exit(0 if (3,11)<=sys.version_info[:2]<=(3,14) else 1)" >nul 2>&1
    if errorlevel 1 (
        echo       Existing environment uses an unsupported Python - rebuilding.
        rmdir /s /q .venv
    )
)

if not exist ".venv\Scripts\python.exe" (
    echo       Creating a virtual environment ^(one-off, takes a minute^)...
    "!PYEXE!" -m venv .venv
    if errorlevel 1 (
        echo  ERROR: could not create the virtual environment.
        pause
        exit /b 1
    )
)
set PY=.venv\Scripts\python.exe

REM ------------------------------------------------------- 3. dependencies
echo [3/8] Installing dependencies ^(first run downloads ~400 MB^)...
"%PY%" -m pip install --upgrade pip --quiet --disable-pip-version-check
"%PY%" -m pip install -r requirements.txt --disable-pip-version-check
if errorlevel 1 (
    echo.
    echo  ---------------------------------------------------------------
    echo   ERROR: dependency installation failed. Scroll up for the reason.
    echo.
    echo   Common causes:
    echo     * "No matching distribution found"  -^> a package has no build
    echo       for this Python yet. Installing Python 3.13 alongside and
    echo       re-running usually clears it.
    echo     * Connection / SSL errors           -^> a proxy or firewall is
    echo       blocking pypi.org.
    echo  ---------------------------------------------------------------
    pause
    exit /b 1
)
echo       Dependencies ready.

REM ------------------------------------------------------- 4. brand assets
echo [4/8] Generating brand assets and version info...
"%PY%" tools\make_assets.py
"%PY%" tools\make_version_info.py
"%PY%" tools\export_branding.py
if errorlevel 1 (
    echo  ERROR: asset generation failed.
    pause
    exit /b 1
)

REM ------------------------------------------------------- 5. vendor staging
REM Automatically bundle any OCR engine already installed on this machine.
REM Without this the shipped app reports "OCR engine not found" on every
REM customer's PC, which is the single most common packaging mistake here.
echo [5/8] Bundling external components...
"%PY%" tools\fetch_vendor.py --auto

echo.
if exist "vendor\tesseract\tesseract.exe" (
    echo       Tesseract OCR   : BUNDLED  ^- OCR will work on every PC you install to
) else (
    echo       Tesseract OCR   : NOT BUNDLED
    echo.
    echo       ^>^> OCR will be unavailable for everyone who installs this build.
    echo       ^>^> To fix it permanently:
    echo       ^>^>   1. Install Tesseract on THIS machine:
    echo       ^>^>      https://github.com/UB-Mannheim/tesseract/wiki
    echo       ^>^>      Tick "Additional language data" during its setup.
    echo       ^>^>   2. Run build.bat again - it is bundled automatically.
    echo.
    choice /C YN /N /T 20 /D Y /M "       Continue building without OCR? [Y/N] "
    if errorlevel 2 (
        echo       Build cancelled. Install Tesseract and run build.bat again.
        pause
        exit /b 0
    )
)
if exist "vendor\gs\gswin64c.exe" (
    echo       Ghostscript     : bundled
) else (
    echo       Ghostscript     : not bundled  ^(built-in compressor is used^)
)

REM ------------------------------------------------------- 6. clean
echo [6/8] Cleaning previous build...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
if exist dist_installer rmdir /s /q dist_installer

REM ------------------------------------------------------- 7. PyInstaller
echo [7/8] Building the application ^(3-8 minutes^)...
"%PY%" -m PyInstaller PDF_Master_Toolkit.spec --noconfirm --log-level WARN
if errorlevel 1 (
    echo.
    echo  ERROR: PyInstaller failed. Scroll up for the reason.
    pause
    exit /b 1
)
if not exist "dist\PDF Master Toolkit\PDF Master Toolkit.exe" (
    echo.
    echo  ERROR: the executable was not produced.
    pause
    exit /b 1
)
echo       Application built.

REM ------------------------------------------------------- 8. Inno Setup
echo [8/8] Building the installer...
set ISCC=
for %%p in (
    "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
    "%ProgramFiles%\Inno Setup 6\ISCC.exe"
    "%LocalAppData%\Programs\Inno Setup 6\ISCC.exe"
) do (
    if exist %%p set ISCC=%%p
)

if "!ISCC!"=="" (
    echo.
    echo  ---------------------------------------------------------------
    echo   Inno Setup was not found, so the installer was NOT built.
    echo.
    echo   The application itself is ready and runnable at:
    echo     dist\PDF Master Toolkit\PDF Master Toolkit.exe
    echo.
    echo   To produce PDF_Master_Toolkit_Setup.exe, install Inno Setup 6
    echo   from https://jrsoftware.org/isdl.php and run this script again.
    echo  ---------------------------------------------------------------
    echo.
    if exist "dist\PDF Master Toolkit" explorer "dist\PDF Master Toolkit"
    pause
    exit /b 0
)

!ISCC! /Q "installer\PDFMasterToolkit.iss"
if errorlevel 1 (
    echo.
    echo  ERROR: Inno Setup failed. Scroll up for the reason.
    pause
    exit /b 1
)

echo.
echo  ===========================================================
echo    BUILD COMPLETE
echo  ===========================================================
echo.
echo    Installer :  dist_installer\PDF_Master_Toolkit_Setup.exe
echo    Portable  :  dist\PDF Master Toolkit\
echo.
for %%F in ("dist_installer\PDF_Master_Toolkit_Setup.exe") do (
    set /a MB=%%~zF/1048576
    echo    Size      :  !MB! MB
)
echo.
echo    The installer needs no Python, no pip and no admin rights.
echo.

if exist dist_installer explorer dist_installer
pause
endlocal
