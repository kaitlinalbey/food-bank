@echo off
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" goto :start
where py >nul 2>&1
if %errorlevel%==0 (
  py -3 -m venv .venv
) else (
  python -m venv .venv
)
if errorlevel 1 goto :fail
:start
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto :fail
".venv\Scripts\python.exe" start_demo.py
pause
exit /b 0
:fail
echo Could not start. Check that Python 3.10+ is installed and internet is available for first-time Flask setup.
pause
exit /b 1
