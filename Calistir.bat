@echo off
cd /d "%~dp0"
python launcher.py
if %errorlevel% neq 0 (
    echo.
    echo ============================================================
    echo [HATA] Uygulama baslatilamadi!
    echo ============================================================
    pause
)
