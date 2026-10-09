@echo off
setlocal EnableExtensions
cd /d "%~dp0"
set "EXITCODE=0"

echo === MoyuntengControl build (tree-shake unused modules) ===
echo.

where python >nul 2>nul
if errorlevel 1 (
    echo [ERROR] python not found. Install Python 3.10+ and add it to PATH.
    set "EXITCODE=1"
    goto :end
)

python -c "import PySide6, httpx, pydantic" 2>nul
if errorlevel 1 (
    echo [INFO] Installing runtime deps...
    python -m pip install -r requirements.txt
    if errorlevel 1 (
        set "EXITCODE=1"
        goto :end
    )
)

python -c "import PyInstaller" 2>nul
if errorlevel 1 (
    echo [INFO] Installing PyInstaller...
    python -m pip install "pyinstaller>=6.0"
    if errorlevel 1 (
        set "EXITCODE=1"
        goto :end
    )
)

echo [INFO] Cleaning build\ and dist\MoyuntengControl\
if exist build rmdir /s /q build
if exist dist\MoyuntengControl rmdir /s /q dist\MoyuntengControl

echo [INFO] Building...
python -m PyInstaller --noconfirm --clean moyunteng.spec
if errorlevel 1 (
    echo.
    echo [FAIL] Build failed.
    set "EXITCODE=1"
    goto :end
)

echo.
echo [OK] Output: %~dp0dist\MoyuntengControl\
echo       EXE:    %~dp0dist\MoyuntengControl\MoyuntengControl.exe
echo DB: %%APPDATA%%\MoyuntengControl\  crash log: EXE dir\debug_log\
echo.

:end
echo.
pause
exit /b %EXITCODE%
