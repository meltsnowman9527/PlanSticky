@echo off
rem ============================================================
rem  PlanSticky - normal launch (no console window)
rem  Data is saved under %%APPDATA%%\PlanSticky automatically.
rem ============================================================
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\pythonw.exe" (
    echo [PlanSticky] venv not found. Run setup.bat first.
    pause
    exit /b 1
)

start "" ".venv\Scripts\pythonw.exe" main.py
endlocal
