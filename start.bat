@echo off
setlocal DisableDelayedExpansion
chcp 65001 >nul
pushd "%~dp0" || exit /b 1
call scripts\portable_env.bat
if errorlevel 1 goto failed
"%AH_PYTHON%" -X utf8 -B -m assignmenthub.portable check --gui --quick
if errorlevel 1 goto failed
if not exist logs mkdir logs
start "" "%AH_PYTHONW%" -X utf8 -B -m assignmenthub.server_manager %* >>logs\manager-startup.log 2>&1
set "AH_EXIT=%errorlevel%"
popd
exit /b %AH_EXIT%
:failed
echo.
echo 서버 관리창을 열지 못했습니다. 위 오류를 확인해 주세요.
echo 배포 ZIP 전체를 새 폴더에 압축 해제한 뒤 start.bat를 실행하세요.
if not defined AH_NO_PAUSE pause
popd
exit /b 1
