@echo off
setlocal DisableDelayedExpansion
pushd "%~dp0" || exit /b 1
if exist .venv\Scripts\python.exe (
    .venv\Scripts\python.exe -X utf8 scripts\build_portable.py %*
) else (
    py -3.12 -X utf8 scripts\build_portable.py %*
)
set "AH_EXIT=%errorlevel%"
if not "%AH_EXIT%"=="0" echo Portable build failed. See docs\portable-distribution.md.
popd
exit /b %AH_EXIT%
