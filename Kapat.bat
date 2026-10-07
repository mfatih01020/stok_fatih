@echo off
cd /d "%~dp0"
chcp 65001 > nul
title QR Stok Yonetim Sistemi - Kapatici

echo.
echo ============================================================
echo   QR STOK YÖNETİM SİSTEMİ ARKA PLAN SUNUCUSU KAPATILIYOR...
echo ============================================================
echo.

powershell -Command "Get-Process python,pythonw,py -ErrorAction SilentlyContinue | Stop-Process -Force" > nul 2>&1
taskkill /F /IM python.exe > nul 2>&1
taskkill /F /IM pythonw.exe > nul 2>&1
taskkill /F /IM py.exe > nul 2>&1

for /f "tokens=5" %%a in ('netstat -aon ^| findstr :5000 ^| findstr LISTENING') do (
    taskkill /F /PID %%a > nul 2>&1
)

echo.
echo 🟢 Arka plandaki tüm sunucu süreçleri başarıyla kapatıldı.
echo.
timeout /t 2 > nul
exit
