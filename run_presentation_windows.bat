@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  where py >nul 2>&1
  if %errorlevel%==0 (py -3 -m venv .venv) else (python -m venv .venv)
  if errorlevel 1 goto :fail
)
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto :fail
".venv\Scripts\python.exe" start_presentation.py
pause
exit /b 0
:fail
echo Python 3.10+ and Flask are required. Check your connection for installation.
pause
exit /b 1
