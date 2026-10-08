@echo off
cd /d "%~dp0"
chcp 65001 > nul
title QR Stok Yonetim Sistemi

:: 1. Python kontrolu
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo ============================================================
    echo [HATA] Python bu bilgisayarda bulunamadi!
    echo.
    echo Lutfen https://www.python.org adresinden Python'u indirin.
    echo KURULUM SIRASINDA EN ALTTAKI "Add Python to PATH" KUTUCUGUNU
    echo MUTLAKA ISARETLEYIN!
    echo ============================================================
    echo.
    pause
    exit /b 1
)

:: 2. Gerekli kutuphaneler kontrolu
python -c "import flask, waitress, pandas, openpyxl, requests" >nul 2>&1
if %errorlevel% neq 0 (
    echo ============================================================
    echo [BILGI] Ilk calisma icin gerekli paketler kuruluyor...
    echo (waitress, flask, pandas, openpyxl, requests vb.)
    echo Lutfen bekleyin, bu islem sadece bir kez yapilacaktir...
    echo ============================================================
    echo.
    python -m pip install --upgrade pip >nul 2>&1
    python -m pip install -r requirements.txt
    if %errorlevel% neq 0 (
        echo.
        echo [BILGI] requirements.txt tam yuklenemedi, temel kutuphaneler kuruluyor...
        python -m pip install flask waitress pandas openpyxl requests xlrd
    )
    echo.
    echo [BASARILI] Tum paketler kuruldu, program baslatiliyor...
    timeout /t 2 >nul
)

:: 3. Calistir
if exist "%~dp0Calistir.exe" (
    start "" "%~dp0Calistir.exe"
) else (
    start "" pythonw launcher.py
)
exit
