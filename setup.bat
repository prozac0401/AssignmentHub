@echo off
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1
if exist .venv\Scripts\python.exe goto install
py -3.12 scripts\check_python.py >nul 2>&1
if not errorlevel 1 (
    py -3.12 -m venv .venv
) else (
    python scripts\check_python.py
    if errorlevel 1 exit /b 1
    python -m venv .venv
)
if errorlevel 1 exit /b 1
:install
.venv\Scripts\python.exe scripts\check_python.py
if errorlevel 1 exit /b 1
.venv\Scripts\python.exe -m pip install --require-hashes -r requirements.lock
if errorlevel 1 exit /b 1
.venv\Scripts\python.exe scripts\install_proxy.py
if errorlevel 1 exit /b 1
echo Setup complete. Double-click start.bat to open the server manager.
exit /b 0
