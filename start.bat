@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1
if exist .venv\Scripts\python.exe goto launch
echo AssignmentHub 첫 설치를 준비합니다. 인터넷 연결이 필요합니다.
call setup.bat
if errorlevel 1 goto failed
:launch
.venv\Scripts\python.exe -c "import tkinter, fastapi, psutil; from assignmenthub.server_manager import ServerManager"
if errorlevel 1 goto failed
if not exist tools\caddy.exe (
    call setup.bat
    if errorlevel 1 goto failed
)
start "" ".venv\Scripts\pythonw.exe" -m assignmenthub.server_manager %* >nul 2>&1
exit /b 0
:failed
echo.
echo 서버 관리창을 열지 못했습니다. 위 오류 내용을 확인해 주세요.
echo Tcl/Tk가 포함된 64비트 Python 3.12 설치 후 setup.bat를 다시 실행하세요.
pause
exit /b 1
