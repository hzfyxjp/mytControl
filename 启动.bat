@echo off
chcp 65001 >nul
cd /d "%~dp0"

rem 启动 python main.py；投屏仅官方 WebRTC webplayer
python "%~dp0main.py"
if errorlevel 1 (
    echo.
    echo Start failed. Run: pip install -r requirements.txt
    pause
)
