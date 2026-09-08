@echo off
setlocal DisableDelayedExpansion
pushd "%~dp0" || exit /b 1
set PYTHONUTF8=1
if exist .venv\Scripts\python.exe goto install
py -3.12 scripts\check_python.py >nul 2>&1
if not errorlevel 1 (
    py -3.12 -m venv .venv
) else (
    python scripts\check_python.py
    if errorlevel 1 goto failed
    python -m venv .venv
)
if errorlevel 1 goto failed
:install
.venv\Scripts\python.exe scripts\check_python.py
if errorlevel 1 goto failed
.venv\Scripts\python.exe -m pip install --require-hashes -r requirements.lock
if errorlevel 1 goto failed
.venv\Scripts\python.exe scripts\install_proxy.py
set "AH_EXIT=%errorlevel%"
popd
exit /b %AH_EXIT%
:failed
popd
exit /b 1
