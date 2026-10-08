@echo off
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
chcp 65001 > nul
title QR Stok Yonetim Sistemi - Guncelleyici

echo.
echo ============================================================
echo      QR STOK YONETIM SISTEMI - GUNCELLEME SERVISI
echo ============================================================
echo.

python guncelleme_kontrol.py

echo.
echo ============================================================
echo [TAMAMLANDI] Islem sona erdi.
echo ============================================================
echo.
echo Pencereyi kapatmak icin herhangi bir tusa basin...
pause >nul
exit
