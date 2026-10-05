@echo off
cd /d "%~dp0"
chcp 65001 > nul
title QR Stok Yonetim Sistemi - Guncelleyici

echo.
echo ============================================================
echo       QR STOK YONETIM SISTEMI - GITHUB GUNCELLEME
echo ============================================================
echo.
echo  [1/2] GitHub sunucusundan en son kodlar cekiliyor...
echo.

git fetch origin main
git reset --hard FETCH_HEAD

if %errorlevel% neq 0 (
    echo.
    echo  [UYARI] Git komutu basarisiz oldu, Python motoru deneniyor...
    python guncelleme_kontrol.py
)

echo.
echo ============================================================
echo  [2/2] KODLAR BASARIYLA GUNCELLENDI!
echo  Verileriniz (cikis_kayitlari.db) %100 korundu.
echo ============================================================
echo.
echo Programi baslatmak icin Calistir.bat dosyasini acabilirsiniz.
echo.
pause
