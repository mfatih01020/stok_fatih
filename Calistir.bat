@echo off
chcp 437 > nul
title QR Stok Yonetim Sistemi

:: -- Calisma dizinini bat dosyasinin klasorune sabitle --------
cd /d "%~dp0"

:: -- Otomatik Sablon Kontrolu (Giris bilgileri txt olusturma) --
if not exist "bakanlik_giris_bilgileri.txt" (
    if exist "bakanlik_giris_bilgileri.template.txt" (
        copy "bakanlik_giris_bilgileri.template.txt" "bakanlik_giris_bilgileri.txt" > nul
    )
)

echo.
echo  ============================================================
echo        QR STOK YONETIM SISTEMI - BASLATICI
echo  ============================================================
echo.

:: -- Python kontrolu -------------------------------------------
echo  [1/4] Python kontrol ediliyor...
python --version > nul 2>&1
if %errorlevel% neq 0 (
    echo.
    echo  [HATA] Python bulunamadi!
    echo.
    echo  Lutfen Python'u kurun:
    echo  https://www.python.org/downloads/
    echo.
    echo  Kurulum sirasinda "Add Python to PATH" secenegini
    echo  isaretlemeyi UNUTMAYIN!
    echo.
    pause
    start https://www.python.org/downloads/
    exit /b 1
)
python --version
echo  Python bulundu.
echo.

:: -- Kutuphaneleri yukle (Zaten kuruluysa aninda atlar) -----
echo  [2/5] Kutuphane kontrolu yapiliyor...
python -c "import flask, pandas, selenium, openpyxl, requests" > nul 2>&1
if %errorlevel% neq 0 (
    echo  Eksik kutuphaneler yukleniyor, lutfen bekleyin...
    python -m pip install flask pandas openpyxl xlrd requests selenium
) else (
    echo  Kutuphaneler hazir.
)
echo.

:: -- Tarayici ve WebDriver hazirligi ---------------------------
echo  [3/5] Tarayici kontrol ediliyor...

set CHROME1=C:\Program Files\Google\Chrome\Application\chrome.exe
set CHROME2=C:\Program Files (x86)\Google\Chrome\Application\chrome.exe
set EDGE1=C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe
set EDGE2=C:\Program Files\Microsoft\Edge\Application\msedge.exe

if exist "%EDGE1%" (
    echo  Microsoft Edge tarayicisi hazir.
    goto browser_ok
)
if exist "%EDGE2%" (
    echo  Microsoft Edge tarayicisi hazir.
    goto browser_ok
)
if exist "%CHROME1%" (
    echo  Google Chrome tarayicisi hazir.
    goto browser_ok
)
if exist "%CHROME2%" (
    echo  Google Chrome tarayicisi hazir.
    goto browser_ok
)

echo  Tarayici kontrol edildi.

:browser_ok
echo  Tarayici hazir.
echo.

:: -- GitHub Otomatik Guncelleme Kontrolu -----------------------
python guncelleme_kontrol.py

:: -- Uygulamayi baslat ----------------------------------------
echo.
echo  [5/5] Uygulama baslatiliyor...
echo.
echo  +--------------------------------------------------+
echo  ^|  Adres: http://localhost:5000                    ^|
echo  ^|  Kapatmak icin bu pencereyi kapatin.             ^|
echo  +--------------------------------------------------+
echo.

:: Tarayiciyi ac ve Flask'i baslat
start http://127.0.0.1:5000

:: Flask'i baslat
python app.py
if %errorlevel% neq 0 (
    echo.
    echo  ============================================================
    echo  [HATA] Uygulama baslatilamadi!
    echo  Lutfen yukaridaki hata mesajini kontrol edin.
    echo  ============================================================
    echo.
    pause
)
