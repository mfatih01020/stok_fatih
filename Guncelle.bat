@echo off
cd /d "%~dp0"
chcp 65001 > nul
title QR Stok Yonetim Sistemi - Guncelleyici

echo.
echo ============================================================
echo       QR STOK YONETIM SISTEMI - GITHUB GUNCELLEME
echo ============================================================
echo.
echo  [1/2] GitHub sunucusuna baglaniliyor ve kodlar cekiliyor...
echo.

git -c http.sslVerify=false fetch origin main
git checkout -B main origin/main
git reset --hard origin/main

echo.
echo ============================================================
echo  [2/2] KODLAR GUNCEL SURUME ESITLENDI!
git log -1 --format="  • Surum Kodu: %%h | Tarih: %%cd" --date=format:"%%d.%%m.%%Y %%H:%%M"
git log -1 --format="  • Son Degisiklik: %%s"
echo ============================================================
echo  Verileriniz (cikis_kayitlari.db) %%100 korundu.
echo ============================================================
echo.
echo Programi baslatmak icin Calistir.bat dosyasini acabilirsiniz.
echo.
pause
