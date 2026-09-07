@echo off
rem ============================================================
rem  PlanSticky - first-time setup: create venv + install deps
rem  Double-click once after cloning. Then use run.bat.
rem ============================================================
setlocal
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
    echo [PlanSticky] Python not found on PATH.
    echo Install Python 3.10+ from https://www.python.org/downloads/
    echo and check "Add python.exe to PATH" during setup.
    pause
    exit /b 1
)

echo [1/3] Creating virtual environment .venv ...
python -m venv .venv
if errorlevel 1 (
    echo [PlanSticky] venv creation failed.
    pause
    exit /b 1
)

echo [2/3] Upgrading pip ...
call ".venv\Scripts\activate.bat"
python -m pip install --upgrade pip

echo [3/3] Installing PySide6 (downloads ~200 MB, first run only) ...
pip install -r requirements.txt
if errorlevel 1 (
    echo [PlanSticky] dependency install failed. Check network and retry.
    pause
    exit /b 1
)

echo.
echo Done! Start the app with run.bat (or run_debug.bat for console logs).
pause
