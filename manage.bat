@echo off
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1
if "%~1"=="" (
    call start.bat
    exit /b
)
if not exist .venv\Scripts\python.exe (
    echo Run setup.bat first.
    exit /b 1
)
.venv\Scripts\python.exe -m assignmenthub.cli %*
exit /b %errorlevel%
