@echo off
setlocal
if not exist "%~dp0.runtime.cmd" (
    >&2 echo Run setup.ps1 to configure TELEMAC first.
    exit /b 1
)
call "%~dp0.runtime.cmd"
set "SYSTELCFG=%HOMETEL%\systel.cfg"
set "PYTHONPATH=%HOMETEL%\scripts\python3"
set "PYTHONHOME="
set "PYTHONNOUSERSITE=1"
set "PYTHONUTF8=1"
set "TEMP=%~dp0.tmp"
set "TMP=%TEMP%"
"%~dp0.venv\Scripts\python.exe" %*
exit /b %errorlevel%
