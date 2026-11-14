@echo off
cd /d "%~dp0"
echo Add fictional sample data to the EXISTING local database.
echo Existing inventory rows are preserved; a timestamped database backup is made first.
echo Stop your Food Bank servers before continuing.
echo.
set /p "answer=Type FILL to add demo data (anything else cancels): "
if not "%answer%"=="FILL" (echo Canceled. & pause & exit /b 0)
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" seed_more_demo_data.py --apply
) else (
  where py >nul 2>&1
  if not errorlevel 1 (py -3 seed_more_demo_data.py --apply) else (python seed_more_demo_data.py --apply)
)
if errorlevel 1 (echo Failed. Check the error above. & pause & exit /b 1)
echo.
echo Done. Restart the Food Bank application to see the expanded inventory.
pause
