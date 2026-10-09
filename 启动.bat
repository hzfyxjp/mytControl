@echo off
cd /d "%~dp0"
python "%~dp0main.py"
if errorlevel 1 (
    echo.
    echo Start failed. Run: pip install -r requirements.txt
    pause
)
