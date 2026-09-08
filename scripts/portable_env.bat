@rem Called within the entry point's SETLOCAL; use only the bundled interpreter.
@set "AH_PYTHON=%~dp0..\runtime\python\python.exe"
@set "AH_PYTHONW=%~dp0..\runtime\python\pythonw.exe"
@if not exist "%AH_PYTHON%" goto missing
@if not exist "%AH_PYTHONW%" goto missing
@if not exist "%~dp0..\runtime\python\python312._pth" goto missing
@set "PYTHONHOME="
@set "PYTHONPATH="
@set "PYTHONUSERBASE="
@set "TCL_LIBRARY=%~dp0..\runtime\python\tcl\tcl8.6"
@set "TK_LIBRARY=%~dp0..\runtime\python\tcl\tk8.6"
@exit /b 0
:missing
@echo Bundled Python is missing. Extract the complete AssignmentHub distribution ZIP.
@echo Source checkout: run build_portable.bat on the build PC, then use its dist folder.
@exit /b 1
