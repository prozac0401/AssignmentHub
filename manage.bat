@echo off
setlocal DisableDelayedExpansion
chcp 65001 >nul
if "%~1"=="" (
    call "%~dp0start.bat"
    exit /b
)
pushd "%~dp0" || exit /b 1
call scripts\portable_env.bat
if errorlevel 1 (
    popd
    exit /b 1
)
"%AH_PYTHON%" -X utf8 -B -m assignmenthub.cli %*
set "AH_EXIT=%errorlevel%"
popd
exit /b %AH_EXIT%
