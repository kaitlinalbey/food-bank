@echo off
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" reset_demo.py
) else (
  py -3 reset_demo.py
)
pause
