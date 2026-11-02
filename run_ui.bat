@echo off
setlocal EnableExtensions
rem =====================================================================
rem  Karaoke AI Subtitler - launcher
rem
rem  Keep this file ASCII-only. cmd.exe reads it with the OEM codepage
rem  (GBK / 950 on this machine) and any UTF-8 Chinese turns into garbage
rem  that can break parsing. All messages are in English on purpose.
rem
rem  Flow:  Python -> FFmpeg -> .venv-ui -> dependencies -> UI
rem  Full deployment guide: DEPLOY.md  (sections 1-3)
rem =====================================================================

cd /d "%~dp0"
set "ROOT=%~dp0"
set "VENV=%ROOT%.venv-ui"
set "PY=%VENV%\Scripts\python.exe"
set "LOCK=%ROOT%requirements.lock.txt"
set "STAMP=%VENV%\.installed-lock"
set "FIRST_RUN="

rem App version (same file the UI title bar and the packaging script read)
set "APPVER=dev"
if exist "%ROOT%VERSION" for /f "usebackq delims=" %%V in ("%ROOT%VERSION") do set "APPVER=%%V"

echo.
echo ==========================================================
echo   Karaoke AI Subtitler   v%APPVER%
echo ==========================================================

rem An existing-looking venv might still be broken (copied from another
rem machine). Treat it as "first run" only when python.exe is missing.
if not exist "%PY%" set "FIRST_RUN=1"

rem ---------------------------------------------------------------------
rem [1/4] Python
rem ---------------------------------------------------------------------
echo.
echo [1/4] Looking for Python 3 ...
set "PYEXE="
for /f "delims=" %%I in ('py -3 -c "import sys;print(sys.executable)" 2^>nul') do set "PYEXE=%%I"
if not defined PYEXE for /f "delims=" %%I in ('python -c "import sys;print(sys.executable)" 2^>nul') do set "PYEXE=%%I"
if not defined PYEXE goto :no_python

rem tkinter must already exist in the BASE interpreter, otherwise every
rem venv built from it will fail at UI start (Microsoft Store builds lack it).
"%PYEXE%" -c "import tkinter" >nul 2>&1
if errorlevel 1 goto :no_tkinter

"%PYEXE%" -c "import sys;print('      OK: Python', sys.version.split()[0], 'at', sys.executable)"
if errorlevel 1 goto :no_python

rem ---------------------------------------------------------------------
rem [2/4] FFmpeg  (warn only - the UI can still open and a path can be set)
rem ---------------------------------------------------------------------
echo.
echo [2/4] Looking for FFmpeg ...
where ffmpeg >nul 2>&1
if errorlevel 1 goto :no_ffmpeg
where ffprobe >nul 2>&1
if errorlevel 1 goto :no_ffmpeg
echo       OK: ffmpeg and ffprobe found on PATH
goto :venv

:no_ffmpeg
echo       WARN: ffmpeg / ffprobe not found on PATH.
echo.
echo   FFmpeg is required to extract audio and to burn subtitles.
echo   Install it, then CLOSE this window and run run_ui.bat again:
echo.
echo       winget install -e --id Gyan.FFmpeg
echo.
echo   (or download from https://ffmpeg.org and add its bin folder to PATH)
echo   You can also point to ffmpeg.exe by hand later:
echo       UI -^> Settings -^> Tool paths -^> FFmpeg / FFprobe
echo.
if defined FIRST_RUN (
    echo   Press any key to continue installing the app now, or close this
    echo   window, install FFmpeg first, then run run_ui.bat again.
    pause >nul
) else (
    ping -n 5 127.0.0.1 >nul
)
goto :venv

rem ---------------------------------------------------------------------
rem [3/4] Virtual environment
rem ---------------------------------------------------------------------
:venv
echo.
if not defined FIRST_RUN goto :venv_check

echo [3/4] Creating .venv-ui (first run, takes 1-3 minutes) ...
"%PYEXE%" -m venv "%VENV%"
if errorlevel 1 goto :venv_failed
if not exist "%PY%" goto :venv_failed
echo       OK: .venv-ui created
goto :deps

:venv_check
"%PY%" -c "import sys" >nul 2>&1
if errorlevel 1 goto :venv_broken
echo [3/4] Virtual environment OK

rem ---------------------------------------------------------------------
rem [4/4] Dependencies
rem ---------------------------------------------------------------------
:deps
echo.
echo [4/4] Checking dependencies ...
if defined FIRST_RUN goto :install
if not exist "%LOCK%" goto :deps_skip
fc /b "%LOCK%" "%STAMP%" >nul 2>&1
if errorlevel 1 goto :install
echo       OK: already installed and unchanged
goto :launch

:deps_skip
echo       OK: no requirements.lock.txt (developer checkout, assuming venv is ready)
goto :launch

:install
if not exist "%LOCK%" goto :install_loose
echo       Installing pinned dependencies from requirements.lock.txt ...
"%PY%" -m pip install --disable-pip-version-check --no-input -r "%LOCK%"
if errorlevel 1 goto :pip_failed
copy /y "%LOCK%" "%STAMP%" >nul 2>&1
echo       OK: dependencies installed
goto :launch

:install_loose
echo       requirements.lock.txt not found, falling back to requirements*.txt
if exist "%ROOT%requirements-ui.txt" (
    "%PY%" -m pip install --disable-pip-version-check --no-input -r "%ROOT%requirements-ui.txt"
    if errorlevel 1 goto :pip_failed
)
if exist "%ROOT%requirements.txt" (
    "%PY%" -m pip install --disable-pip-version-check --no-input -r "%ROOT%requirements.txt"
    if errorlevel 1 goto :pip_failed
)
echo       OK: dependencies installed
goto :launch

rem ---------------------------------------------------------------------
rem Launch
rem ---------------------------------------------------------------------
:launch
echo.
echo Starting UI ...
"%PY%" -m ui %*
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" (
    echo.
    echo [ERROR] The UI exited with code %RC% - see the messages above.
    pause
)
exit /b %RC%

rem ---------------------------------------------------------------------
rem Errors
rem ---------------------------------------------------------------------
:no_python
echo       ERROR: Python 3 not found.
echo.
echo   Install Python 3.13 first, then run this file again:
echo.
echo       winget install -e --id Python.Python.3.13
echo.
echo   Or use the official installer from:
echo       https://www.python.org/downloads/windows/
echo   During setup keep "py launcher" enabled and tick "Add python.exe to PATH".
echo.
echo   Close this window after installing, then double-click run_ui.bat again.
echo.
pause
exit /b 1

:no_tkinter
echo       ERROR: this Python has no tkinter (Tcl/Tk) support.
echo.
echo   The UI is built with tkinter. Microsoft Store builds of Python usually
echo   ship without it. Install the official build from:
echo       https://www.python.org/downloads/windows/
echo   then close this window and run run_ui.bat again.
echo.
pause
exit /b 1

:venv_broken
echo       ERROR: .venv-ui exists but its python.exe cannot run.
echo.
echo   A virtual environment is NOT portable - it stores absolute paths and
echo   breaks when copied from another computer or another folder.
echo.
echo   Fix: rename (or delete) the .venv-ui folder and run this file again;
echo   it will be rebuilt in a few minutes.
echo.
pause
exit /b 1

:venv_failed
echo       ERROR: failed to create .venv-ui.
echo.
echo   Check that this folder is writable and that there is at least 1 GB free.
echo.
pause
exit /b 1

:pip_failed
echo       ERROR: dependency installation failed.
echo.
echo   The most common cause is a corporate proxy or SSL inspection blocking
echo   PyPI (look for SSL / Proxy / timeout errors above).
echo   Retry once; if it still fails, copy this whole window and send it to the
echo   person who gave you this folder.
echo.
pause
exit /b 1
