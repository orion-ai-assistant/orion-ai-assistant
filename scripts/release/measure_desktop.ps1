# Orion acilis/kapanis olcumu. Claude uygulamasinin DISINDA, normal bir PowerShell penceresinde calistirin:
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\release\measure_desktop.ps1 -Rounds 2
# Her turda: Orion kapatilir (acik ise), Orion.exe ile acilir (masaustu kisayolu ile ayni), pencere
# gorunene kadar adimlar zamanlanir; sonra "Cik" ile ayni yol (Orion.ps1 -Stop) zamanlanir.
# Sonuc repo kokundeki .tmp\measure-desktop-<zaman>.txt dosyasina da yazilir. Hicbir veri degistirilmez.
# Adim adim ic sureler: %LOCALAPPDATA%\Orion\updates\watcher.log icindeki 'launch.timing' satirlari.
param([int]$Rounds = 2, [int]$TimeoutSeconds = 240)
$ErrorActionPreference = 'Stop'
$base = Join-Path $env:LOCALAPPDATA 'Orion'
$exe = Join-Path $base 'bootstrap\Orion.exe'
if (-not (Test-Path -LiteralPath $exe)) { throw "Orion.exe yok: $exe" }
$ports = [ordered]@{ 'router.pg' = 5433; 'router.http' = 20128; 'hub.pg' = 5445; 'redis' = 6379; 'hub.api' = 8909; 'flutter' = 8090 }
$folder = Join-Path (Split-Path -Parent (Split-Path -Parent $PSScriptRoot)) '.tmp'
New-Item -ItemType Directory -Force -Path $folder | Out-Null
$out = Join-Path $folder('measure-desktop-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.txt')

function Listening { $set = @{}; foreach ($c in [Net.NetworkInformation.IPGlobalProperties]::GetIPGlobalProperties().GetActiveTcpListeners()) { $set[$c.Port] = $true }; return $set }
function OrionWindow {
    foreach ($p in Get-Process msedge, chrome -ErrorAction SilentlyContinue) {
        if ($p.MainWindowHandle -ne 0 -and $p.MainWindowTitle -match 'Orion') { return $true }
    }
    return $false
}
function EntryRunning { [bool](Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" | Where-Object { $_.CommandLine -match 'bootstrap\\Orion\.ps1' }) }
function Say([string]$text) { Write-Host $text; Add-Content -LiteralPath $out -Value $text -Encoding UTF8 }

function Measure-Stop([string]$label) {
    $t0 = [Diagnostics.Stopwatch]::StartNew()
    $p = Start-Process -FilePath $exe -ArgumentList '-Stop' -PassThru
    $marks = [ordered]@{}
    while ($t0.Elapsed.TotalSeconds -lt $TimeoutSeconds) {
        $open = Listening
        if (-not $marks.Contains('window.closed') -and -not (OrionWindow)) { $marks['window.closed'] = $t0.Elapsed.TotalSeconds }
        foreach ($k in $ports.Keys) { if (-not $marks.Contains("$k.closed") -and -not $open[$ports[$k]]) { $marks["$k.closed"] = $t0.Elapsed.TotalSeconds } }
        if (-not $marks.Contains('entry.exited') -and -not (EntryRunning)) { $marks['entry.exited'] = $t0.Elapsed.TotalSeconds }
        if ($marks.Count -ge $ports.Count + 2) { break }
        Start-Sleep -Milliseconds 100
    }
    Say "--- $label kapanis (Orion.ps1 -Stop) ---"
    foreach ($k in $marks.Keys) { Say ('{0,-22} {1,7:N1} s' -f $k, $marks[$k]) }
}

function Measure-Launch([string]$label) {
    $t0 = [Diagnostics.Stopwatch]::StartNew()
    Start-Process -FilePath $exe | Out-Null
    $marks = [ordered]@{}
    while ($t0.Elapsed.TotalSeconds -lt $TimeoutSeconds) {
        $open = Listening
        if (-not $marks.Contains('entry.started') -and (EntryRunning)) { $marks['entry.started'] = $t0.Elapsed.TotalSeconds }
        foreach ($k in $ports.Keys) { if (-not $marks.Contains("$k.listening") -and $open[$ports[$k]]) { $marks["$k.listening"] = $t0.Elapsed.TotalSeconds } }
        if (-not $marks.Contains('window.visible') -and (OrionWindow)) { $marks['window.visible'] = $t0.Elapsed.TotalSeconds }
        if ($marks.Contains('window.visible') -and $marks.Contains('entry.started') -and -not (EntryRunning)) { $marks['entry.exited'] = $t0.Elapsed.TotalSeconds; break }
        Start-Sleep -Milliseconds 100
    }
    Say "--- $label acilis (Orion.exe) ---"
    foreach ($k in ($marks.Keys | Sort-Object { $marks[$_] })) { Say ('{0,-22} {1,7:N1} s' -f $k, $marks[$k]) }
}

Say ("Orion olcumu " + (Get-Date -Format 's') + "  makine: $env:COMPUTERNAME")
if ((Listening)[8909]) { Measure-Stop 'baslangic' ; Start-Sleep -Seconds 5 }
for ($i = 1; $i -le $Rounds; $i++) {
    Measure-Launch "tur $i"
    Start-Sleep -Seconds 10   # Pencere ve servisler otursun.
    Measure-Stop "tur $i"
    Start-Sleep -Seconds 5
}
Say "Bitti. Dosya: $out"
Say "Orion su an kapali; isterseniz normal kisayoldan acin."
