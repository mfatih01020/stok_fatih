@echo off
cd /d "%~dp0"
chcp 65001 > nul
title QR Stok Yonetim Sistemi - Kapatici

echo.
echo ============================================================
echo   QR STOK YÖNETİM SİSTEMİ ARKA PLAN SUNUCUSU KAPATILIYOR...
echo ============================================================
echo.

powershell -Command "Get-Process python -ErrorAction SilentlyContinue | Stop-Process -Force"
taskkill /F /IM python.exe > nul 2>&1

echo.
echo 🟢 Arka plandaki tüm sunucu süreçleri başarıyla kapatıldı.
echo.
timeout /t 3 > nul
exit
