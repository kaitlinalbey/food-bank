@echo off
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (".venv\Scripts\python.exe" run_lab.py --scenario all) else (python run_lab.py --scenario all)
pause
