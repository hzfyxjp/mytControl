@echo off
setlocal EnableExtensions
cd /d "%~dp0"
set "EXITCODE=0"
set "OUT=%~dp0dist\MoyuntengControl"

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

rem 运行时从 EXE 旁读 webplayer：play + js + 加载图（不拷 dashboard / adb / c）
echo [INFO] Staging webplayer next to EXE
if exist "%OUT%\webplayer" rmdir /s /q "%OUT%\webplayer"
mkdir "%OUT%\webplayer\js" >nul 2>nul
mkdir "%OUT%\webplayer\public\images" >nul 2>nul
copy /y "%~dp0webplayer\play.html" "%OUT%\webplayer\play.html" >nul
copy /y "%~dp0webplayer\js\adapter-latest.js" "%OUT%\webplayer\js\" >nul
copy /y "%~dp0webplayer\js\jquery-3.2.1.min.js" "%OUT%\webplayer\js\" >nul
copy /y "%~dp0webplayer\js\h5.lgair.wt.all.min-2.2.js" "%OUT%\webplayer\js\" >nul
copy /y "%~dp0webplayer\js\unicode-input-compat.js" "%OUT%\webplayer\js\" >nul
copy /y "%~dp0webplayer\public\images\loading.png" "%OUT%\webplayer\public\images\" >nul
copy /y "%~dp0webplayer\public\images\108.png" "%OUT%\webplayer\public\images\" >nul
copy /y "%~dp0webplayer\public\images\start.jpg" "%OUT%\webplayer\public\images\" >nul
if not exist "%OUT%\webplayer\play.html" (
    echo [FAIL] webplayer\play.html missing after staging.
    set "EXITCODE=1"
    goto :end
)
if not exist "%OUT%\webplayer\public\images\loading.png" (
    echo [FAIL] webplayer loading images missing after staging.
    set "EXITCODE=1"
    goto :end
)

rem 去掉未用副本与误拷的厂商目录
if exist "%OUT%\_internal\webplayer" (
    echo [INFO] Removing duplicate _internal\webplayer
    rmdir /s /q "%OUT%\_internal\webplayer"
)
if exist "%OUT%\adb" (
    echo [INFO] Removing unused adb\
    rmdir /s /q "%OUT%\adb"
)
if exist "%OUT%\c" (
    echo [INFO] Removing unused official Win SDK c\
    rmdir /s /q "%OUT%\c"
)
if exist "%OUT%\_internal\adb" rmdir /s /q "%OUT%\_internal\adb"
if exist "%OUT%\_internal\c" rmdir /s /q "%OUT%\_internal\c"

echo.
echo [OK] Output: %OUT%\
echo       EXE:    %OUT%\MoyuntengControl.exe
echo       Pack:   webplayer play+js+images; no dashboard/adb/c
echo DB: %%APPDATA%%\MoyuntengControl\  crash log: EXE dir\debug_log\
echo.

:end
echo.
pause
exit /b %EXITCODE%
