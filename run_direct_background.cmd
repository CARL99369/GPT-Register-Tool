@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
  call "%~dp0start_standalone.cmd"
  exit /b %ERRORLEVEL%
)
start "" /b ".venv\Scripts\pythonw.exe" "serve_direct.py"
endlocal
