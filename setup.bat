@echo off
setlocal DisableDelayedExpansion
chcp 65001 >nul
pushd "%~dp0" || exit /b 1
call scripts\portable_env.bat
if errorlevel 1 goto failed
"%AH_PYTHON%" -X utf8 -B -m assignmenthub.portable check --gui --verify
if errorlevel 1 goto failed
echo 배포 파일 점검을 통과했습니다. start.bat를 실행하세요.
popd
exit /b 0
:failed
echo 배포 ZIP 전체를 새 폴더에 다시 압축 해제해 주세요.
if not defined AH_NO_PAUSE pause
popd
exit /b 1
