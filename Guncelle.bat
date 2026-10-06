@echo off
cd /d "%~dp0"
chcp 65001 > nul
title QR Stok Yonetim Sistemi - Guncelleyici

python guncelleme_kontrol.py
if %errorlevel% neq 0 (
    echo.
    echo ============================================================
    echo [HATA] Guncelleme islemi basarisiz oldu!
    echo ============================================================
)

echo.
echo Pencere 10 saniye icinde otomatik kapatilacaktir...
timeout /t 10 > nul
