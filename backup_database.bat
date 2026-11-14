@echo off
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" backup_database.py
) else (
  py -3 backup_database.py
)
pause
