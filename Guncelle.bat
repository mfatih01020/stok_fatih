@echo off
cd /d "%~dp0"
echo.
echo ============================================================
echo       QR STOK YONETIM SISTEMI - GITHUB GUNCELLEME
echo ============================================================
echo.
echo [1/2] GitHub'dan en son kod guncellemeleri cekiliyor...
git fetch origin main > nul 2>&1
git reset --hard origin/main
if %errorlevel% neq 0 (
    echo.
    echo [UYARI] 'git reset' basarisiz oldu, 'git pull' deneniyor...
    git pull origin main
)

if %errorlevel% neq 0 (
    echo.
    echo [HATA] GitHub'dan veri cekilemedi!
    echo Lutfen internet baglantinizi ve Git kurulumunu kontrol edin.
    echo.
    pause
    exit /b 1
)

echo.
echo [2/2] KODLAR BASARIYLA GUNCELLENDI!
echo ============================================================
echo Verileriniz (cikis_kayitlari.db ve Excel dosyalariniz)
echo %100 korundu ve hicbir veriniz silinmedi.
echo ============================================================
echo.
echo Programi baslatmak icin Calistir.bat dosyasini acabilirsiniz.
echo.
pause
