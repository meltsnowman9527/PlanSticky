@echo off
rem ============================================================
rem  PlanSticky - debug launch (keeps console open for logs)
rem ============================================================
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [PlanSticky] venv not found. Run setup.bat first.
    pause
    exit /b 1
)

call ".venv\Scripts\activate.bat"
python main.py
if errorlevel 1 (
    echo.
    echo [PlanSticky] Exited with error code %errorlevel%
    pause
)
endlocal
