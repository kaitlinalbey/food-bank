@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  where py >nul 2>&1
  if %errorlevel%==0 (py -3 -m venv .venv) else (python -m venv .venv)
  if errorlevel 1 goto :fail
)
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto :fail
".venv\Scripts\python.exe" release_check.py
set "RESULT=%ERRORLEVEL%"
if not "%RESULT%"=="0" (
  echo RELEASE NOT FULLY VERIFIED - read reports\release_verification.md
) else (
  echo Automated release checks finished. Docker runtime still requires separate verification.
)
pause
exit /b %RESULT%
:fail
echo Python 3.10+ and an Internet connection for Flask installation are needed.
pause
exit /b 1
