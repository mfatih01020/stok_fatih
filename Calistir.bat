@echo off
chcp 437 > nul
title QR Stok Yonetim Sistemi

:: -- Calisma dizinini bat dosyasinin klasorune sabitle --------
cd /d "%~dp0"

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

:: -- pip guncelle ----------------------------------------------
echo  pip guncelleniyor...
python -m pip install --upgrade pip --quiet

:: -- Kutuphaneleri yukle ---------------------------------------
echo  [2/4] Gerekli kutuphaneler yukleniyor...
echo.

echo    ^> Flask 3.1.3 yukleniyor...
python -m pip install "Flask==3.1.3" --quiet
if %errorlevel% neq 0 (
    echo    [UYARI] Flask kurulamadi, son surum deneniyor...
    python -m pip install flask --quiet
)

echo    ^> Werkzeug 3.1.8 yukleniyor...
python -m pip install "Werkzeug==3.1.8" --quiet
if %errorlevel% neq 0 (
    python -m pip install werkzeug --quiet
)

echo    ^> pandas 2.2.2 yukleniyor...
python -m pip install "pandas==2.2.2" --quiet
if %errorlevel% neq 0 (
    echo    [UYARI] pandas kurulamadi, son surum deneniyor...
    python -m pip install pandas --quiet
)

echo    ^> openpyxl 3.1.2 yukleniyor...
python -m pip install "openpyxl==3.1.2" --quiet
if %errorlevel% neq 0 (
    python -m pip install openpyxl --quiet
)

echo    ^> xlrd 2.0.2 yukleniyor...
python -m pip install "xlrd==2.0.2" --quiet
if %errorlevel% neq 0 (
    python -m pip install xlrd --quiet
)

echo    ^> requests 2.31.0 yukleniyor...
python -m pip install "requests==2.31.0" --quiet
if %errorlevel% neq 0 (
    python -m pip install requests --quiet
)

echo    ^> selenium yukleniyor (Bakanlik ekrani icin)...
python -m pip install selenium --quiet
if %errorlevel% neq 0 (
    echo    [UYARI] selenium kurulamadi!
)

echo.
echo  Tum kutuphaneler hazir.
echo.

:: -- Tarayici ve WebDriver hazirligi ---------------------------
echo  [3/4] Tarayici ve WebDriver kontrol ediliyor...

set CHROME1=C:\Program Files\Google\Chrome\Application\chrome.exe
set CHROME2=C:\Program Files (x86)\Google\Chrome\Application\chrome.exe
set EDGE1=C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe
set EDGE2=C:\Program Files\Microsoft\Edge\Application\msedge.exe

:: -- Edge varsa msedgedriver.exe'yi hazirla --------------------
if exist "%EDGE1%" goto edge_found
if exist "%EDGE2%" goto edge_found
goto check_chrome

:edge_found
echo  Edge tarayicisi bulundu.

:: Edge versiyonunu registriden al (birden fazla kayit yolu denenir)
set EDGE_VER=
for /f "tokens=3" %%a in ('reg query "HKLM\SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{56EB18F8-B008-4CBD-B6D2-8C97FE7E9062}" /v pv 2^>nul') do set EDGE_VER=%%a
if "%EDGE_VER%"=="" for /f "tokens=3" %%a in ('reg query "HKCU\SOFTWARE\Microsoft\EdgeUpdate\Clients\{56EB18F8-B008-4CBD-B6D2-8C97FE7E9062}" /v pv 2^>nul') do set EDGE_VER=%%a
if "%EDGE_VER%"=="" for /f "tokens=3" %%a in ('reg query "HKLM\SOFTWARE\Microsoft\EdgeUpdate\Clients\{56EB18F8-B008-4CBD-B6D2-8C97FE7E9062}" /v pv 2^>nul') do set EDGE_VER=%%a

if "%EDGE_VER%"=="" (
    echo  [UYARI] Edge versiyonu registriden alinamadi, PowerShell ile deneniyor...
    for /f "delims=" %%a in ('powershell -NoProfile -Command "(Get-Item (Get-Command msedge.exe -ErrorAction SilentlyContinue).Source -ErrorAction SilentlyContinue).VersionInfo.ProductVersion" 2^>nul') do set EDGE_VER=%%a
)

if "%EDGE_VER%"=="" (
    echo  [UYARI] Edge versiyonu tespit edilemedi, driver olmadan devam ediliyor.
    goto browser_ok
)

echo  Edge versiyonu: %EDGE_VER%

:: Kayitli versiyonla karsilastir - ayni versiyon ise driver zaten dogru
set SAVED_VER=
if exist "driver_version.txt" set /p SAVED_VER=<driver_version.txt

if "%SAVED_VER%"=="%EDGE_VER%" (
    if exist "msedgedriver.exe" (
        echo  msedgedriver.exe bu Edge versiyonu icin zaten mevcut, atlaniyor.
        goto browser_ok
    )
)

:: Driver yok veya versiyon degisti - yeniden indir
echo  msedgedriver %EDGE_VER% indiriliyor (bu biraz surebilir)...

powershell -NoProfile -Command ^
    "$v='%EDGE_VER%';" ^
    "$zip=$env:TEMP+'\edgedriver.zip';" ^
    "$dst=$env:TEMP+'\edgedriver_tmp';" ^
    "$urls=@('https://msedgedriver.microsoft.com/'+$v+'/edgedriver_win64.zip','https://msedgedriver.azureedge.net/'+$v+'/edgedriver_win64.zip');" ^
    "foreach($url in $urls){" ^
    "  try{ Write-Host ('Deneniyor: '+$url); Invoke-WebRequest -Uri $url -OutFile $zip -UseBasicParsing -TimeoutSec 90; Write-Host 'Indirildi.'; break }" ^
    "  catch{ Write-Host ('Basarisiz: '+$_.Exception.Message) }" ^
    "};" ^
    "if(Test-Path $zip){" ^
    "  if(Test-Path $dst){Remove-Item $dst -Recurse -Force};" ^
    "  Expand-Archive -Path $zip -DestinationPath $dst -Force;" ^
    "  Copy-Item ($dst+'\msedgedriver.exe') '.\msedgedriver.exe' -Force;" ^
    "  Write-Host 'msedgedriver.exe klasore kopyalandi.'" ^
    "} else { Write-Host 'HATA: zip dosyasi indirilemedi.' }"

if exist "msedgedriver.exe" (
    :: Versiyonu kaydet - bir daha indirme gerekmesin
    echo %EDGE_VER%>driver_version.txt
    echo  msedgedriver.exe basariyla hazir!
) else (
    echo  [UYARI] msedgedriver indirilemedi. Selenium kendi halledecek.
)
goto browser_ok

:check_chrome
:: Chrome varsa chromedriver gerekmiyor - Selenium Manager otomatik halleder
if exist "%CHROME1%" (
    echo  Chrome tarayicisi bulundu.
    goto browser_ok
)
if exist "%CHROME2%" (
    echo  Chrome tarayicisi bulundu.
    goto browser_ok
)

:: Hicbir tarayici yok - Chrome indir ve kur
echo  Tarayici bulunamadi. Chrome indiriliyor...
echo  (Internet baglantisi gereklidir)
powershell -NoProfile -Command "Invoke-WebRequest -Uri 'https://dl.google.com/chrome/install/latest/chrome_installer.exe' -OutFile ($env:TEMP+'\chrome_setup.exe') -UseBasicParsing -TimeoutSec 120"
if %errorlevel% neq 0 (
    echo  [HATA] Chrome indirilemedi. Internet baglantinizi kontrol edin.
    echo  Manuel kurulum: https://www.google.com/intl/tr/chrome/
    start https://www.google.com/intl/tr/chrome/
    pause
    goto browser_ok
)
echo  Chrome kuruluyor... (Yonetici izni penceresi acilabilir, onaylayin)
"%TEMP%\chrome_setup.exe" /silent /install
ping -n 6 127.0.0.1 > nul
if exist "%CHROME1%" (
    echo  Chrome basariyla kuruldu!
) else (
    echo  [UYARI] Chrome otomatik kurulamadi. Manuel kurun: https://www.google.com/intl/tr/chrome/
    start https://www.google.com/intl/tr/chrome/
    pause
)

:browser_ok
echo  Tarayici hazir.
echo.

:: -- Uygulamayi baslat ----------------------------------------
echo  [4/4] Uygulama baslatiliyor...
echo.
echo  +--------------------------------------------------+
echo  ^|  Adres: http://localhost:5000                    ^|
echo  ^|  Kapatmak icin bu pencereyi kapatin.             ^|
echo  +--------------------------------------------------+
echo.

:: Tarayiciyi Flask basladiktan 3 saniye sonra ac
start /b powershell -WindowStyle Hidden -Command "Start-Sleep 3; Start-Process 'http://localhost:5000'"

:: Flask'i baslat
python app.py

pause
