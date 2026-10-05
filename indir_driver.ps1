$edgePath = "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
if (-not (Test-Path $edgePath)) {
    $edgePath = "C:\Program Files\Microsoft\Edge\Application\msedge.exe"
}
if (-not (Test-Path $edgePath)) {
    $edgePath = "$env:LOCALAPPDATA\Microsoft\Edge\Application\msedge.exe"
}

if (Test-Path $edgePath) {
    $edgeVer = (Get-Item $edgePath).VersionInfo.FileVersion
    Write-Host "Bilgisayardaki Microsoft Edge Sürümü: $edgeVer"
} else {
    $edgeVer = "152.0.4191.53"
    Write-Host "Edge yolu bulunamadı, varsayılan sürüm deneniyor: $edgeVer"
}

$zip = "$env:TEMP\edgedriver.zip"
$dst = "$env:TEMP\edgedriver_tmp"
$url = "https://msedgedriver.microsoft.com/$edgeVer/edgedriver_win64.zip"

try {
    Write-Host "msedgedriver $edgeVer indiriliyor..."
    Invoke-WebRequest -Uri $url -OutFile $zip -UseBasicParsing -TimeoutSec 60

    if (Test-Path $dst) { Remove-Item $dst -Recurse -Force }
    Expand-Archive -Path $zip -DestinationPath $dst -Force
    Copy-Item "$dst\msedgedriver.exe" ".\msedgedriver.exe" -Force
    Set-Content -Path ".\driver_version.txt" -Value $edgeVer -Force
    Write-Host "msedgedriver.exe ($edgeVer) basariyla indirildi ve guncellendi!"
} catch {
    Write-Host "HATA: $($_.Exception.Message)"
}
