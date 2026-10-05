@echo off
chcp 65001 > nul
title QR Stok Yönetim Sistemi - Başlatıcı
cd /d "%~dp0"

:: Python kontrolü
python --version > nul 2>&1
if %errorlevel% neq 0 (
    echo.
    echo [HATA] Python bulunamadı! Lütfen Python'u kurun: https://www.python.org/downloads/
    pause
    exit /b 1
)

:: Modern Python Launcher'ı başlat
python launcher.py
if %errorlevel% neq 0 (
    echo.
    echo ============================================================
    echo [HATA] Uygulama başlatılamadı!
    echo ============================================================
    pause
)
