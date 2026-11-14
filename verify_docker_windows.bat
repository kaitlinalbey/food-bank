@echo off
cd /d "%~dp0"
where py >nul 2>&1
if %errorlevel%==0 (
  py -3 verify_docker.py
) else (
  python verify_docker.py
)
if errorlevel 1 echo Docker verification failed or could not run; see output.
pause
