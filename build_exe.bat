@echo off
rem ============================================================
rem  PlanSticky - build a single-file exe with PyInstaller
rem  Output: dist\PlanSticky.exe  (double-click to run, no Python needed)
rem
rem  Uses PlanSticky.spec so the icon data file and the QtMultimedia
rem  hidden imports (needed to play journal videos) are included.
rem ============================================================
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [PlanSticky] venv not found. Run setup.bat first.
    pause
    exit /b 1
)

call ".venv\Scripts\activate.bat"

echo [1/3] Ensuring PyInstaller is installed ...
pip install pyinstaller
if errorlevel 1 (
    echo [PlanSticky] PyInstaller install failed.
    pause
    exit /b 1
)

echo [2/3] Generating app icon (assets\icon.ico) ...
python scripts\make_icon.py
if errorlevel 1 (
    echo [PlanSticky] icon generation failed.
    pause
    exit /b 1
)

echo [3/3] Building exe (takes 1-3 minutes) ...
rem 用 PlanSticky.spec 而不是命令行参数：spec 里带了 assets/icon.ico 数据和
rem 日记视频播放所需的 PySide6.QtMultimedia hiddenimports，命令行方式会漏掉。
pyinstaller --noconfirm --clean PlanSticky.spec
if errorlevel 1 (
    echo [PlanSticky] build failed.
    pause
    exit /b 1
)

echo.
echo Done! exe is at: dist\PlanSticky.exe
echo Copy it anywhere; data still lives in %%APPDATA%%\PlanSticky.
pause
