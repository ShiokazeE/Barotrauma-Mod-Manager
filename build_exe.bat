@echo off
setlocal EnableExtensions
cd /d "%~dp0" || goto fail

set "PYTHON_CMD=python"
where py >nul 2>&1
if not errorlevel 1 set "PYTHON_CMD=py -3"

echo [1/4] Checking Python 3.10+...
%PYTHON_CMD% -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)"
if errorlevel 1 goto need_python

echo.
echo [2/4] Installing runtime and build dependencies...
%PYTHON_CMD% -m pip install --upgrade pip
if errorlevel 1 goto fail
%PYTHON_CMD% -m pip install --upgrade -r requirements.txt pyinstaller
if errorlevel 1 goto fail

echo.
echo [3/4] Building BarotraumaModManager (onedir)...
%PYTHON_CMD% -m PyInstaller --noconfirm --clean --onedir --windowed --name "BarotraumaModManager" --collect-all certifi --collect-all PIL "barotrauma_mod_manager.py"
if errorlevel 1 goto fail

if not exist "dist\BarotraumaModManager\BarotraumaModManager.exe" goto fail
if not exist "dist\BarotraumaModManager\_internal" goto fail

echo.
echo =============================================
echo SUCCESS: dist\BarotraumaModManager is ready.
echo ZIP the entire folder; do not separate the EXE.
echo =============================================
if not defined CI pause
exit /b 0

:need_python
echo.
echo ERROR: Python 3.10 or newer is required.
echo Install Python and enable the PATH option, then run this file again.
if not defined CI pause
exit /b 1

:fail
echo.
echo BUILD FAILED. Check the error messages above.
if not defined CI pause
exit /b 1
