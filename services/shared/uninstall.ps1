# Orion uninstall dialog ("Uygulamalar ve ozellikler" -> Orion AI Assistant -> Kaldir, and the tray's "Orion'u kaldir").
# Asks what to remove and whether to keep the user data, then runs the safe flow of services.shared.uninstall from
# the retained bootstrap runtime. Dependency-free (Windows PowerShell + WinForms). Kept ASCII: Windows PowerShell 5.1
# reads this file as ANSI, so Turkish text is written as \uXXXX escapes and unescaped by T.
param([Parameter(Mandatory = $true)][string]$Registry, [switch]$NonInteractive, [string]$Scope, [switch]$DeleteData, [switch]$DeleteModels)
$ErrorActionPreference = 'Stop'
if ($NonInteractive) {
    function T([string]$text) { [regex]::Unescape($text) }
    Add-Type -AssemblyName System.Windows.Forms
} else {
    . (Join-Path $PSScriptRoot 'Orion-Ui.ps1')   # Orion's look (Initialize-OrionUi, New-OrionForm, ...) and T.
    Initialize-OrionUi
}
$record = Get-Content -LiteralPath $Registry -Raw -Encoding UTF8 | ConvertFrom-Json
$kinds = @($record.components | ForEach-Object { $_.kind })
$hasHub = $kinds -contains 'hub'
$hasRouter = $kinds -contains 'router'
$bootstrap = Split-Path -Parent $Registry | Join-Path -ChildPath 'bootstrap'
$pointer = Join-Path $bootstrap 'automatic-runtime.json'
$root = $null
if (Test-Path -LiteralPath $pointer) { $root = (Get-Content -LiteralPath $pointer -Raw -Encoding UTF8 | ConvertFrom-Json).root }
$python = if ($root) { Join-Path $root 'runtime/python/python.exe' } else { $null }
if (-not $python -or -not (Test-Path -LiteralPath $python -PathType Leaf)) {
    $hub = $record.components | Where-Object { $_.kind -eq 'hub' } | Select-Object -First 1
    if ($hub) { $root = $hub.code_path; $python = Join-Path $root 'runtime/python/python.exe' }
}
if (-not $python -or -not (Test-Path -LiteralPath $python -PathType Leaf)) {
    if ($NonInteractive) { Write-Output (T 'Orion kaldırma aracı bulunamadı; hiçbir şey silinmedi.') }
    else { [void](Show-OrionMessage (T 'Orion kaldırma aracı bulunamadı; hiçbir şey silinmedi.')) }
    exit 1
}

function Invoke-Uninstall([string]$scope, [bool]$deleteData, [bool]$deleteModels) {
    $arguments = @('-B', '-m', 'services.shared.uninstall', '--registry', ('"' + $Registry + '"'), '--scope', $scope)
    if ($deleteData) { $arguments += '--delete-data' }
    if ($deleteModels) { $arguments += '--delete-models' }
    $out = Join-Path $env:TEMP ('orion-uninstall-' + [guid]::NewGuid().ToString('N') + '.json')
    $err = $out + '.err'
    $process = Start-Process -FilePath $python -ArgumentList $arguments -WorkingDirectory $root -WindowStyle Hidden `
        -RedirectStandardOutput $out -RedirectStandardError $err -PassThru
    while (-not $process.HasExited) { [Windows.Forms.Application]::DoEvents(); Start-Sleep -Milliseconds 150 }
    $text = ''
    try { $text = (Get-Content -LiteralPath $out -Raw -Encoding UTF8) } catch {}
    # Keep what the uninstaller printed (traceback included) where the user and support can find it.
    try {
        $log = Join-Path $env:LOCALAPPDATA 'Orion-uninstall.log'
        $stamp = (Get-Date).ToString('s') + ' exit=' + $process.ExitCode
        $errText = ''
        try { $errText = (Get-Content -LiteralPath $err -Raw -Encoding UTF8) } catch {}
        Add-Content -LiteralPath $log -Encoding UTF8 -Value ($stamp + "`n" + $text + "`n" + $errText)
    } catch {}
    Remove-Item -LiteralPath $out, $err -Force -ErrorAction SilentlyContinue
    try { return ($text.Trim().Split("`n") | Select-Object -Last 1 | ConvertFrom-Json) } catch { return $null }
}

function Show-Result($result) {
    if ($null -eq $result) { return (T 'Kaldırma tamamlanamadı. Orion kayıtları oldukları gibi korundu. Ayrıntı: AppData/Local/Orion-uninstall.log dosyasında.') }
    if ($result.status -eq 'refused') { return $result.message }
    $lines = @((T 'Orion kaldırıldı.'))
    if ($result.data -eq 'kept') { $lines += (T 'Verileriniz ve modelleriniz korundu; Orion''u yeniden kurarsanız kaldığınız yerden devam eder.') }
    if ($result.firewall -eq 'declined') { $lines += (T 'Güvenlik duvarı kuralı için izin verilmedi; kural kaldı. Ayarlar > Güvenlik Duvarı üzerinden elle silebilirsiniz.') }
    if ($result.firewall -eq 'failed') { $lines += (T 'Güvenlik duvarı kuralı değiştirilemedi.') }
    foreach ($note in @($result.notes)) { if ($note) { $lines += $note } }
    return ($lines -join "`r`n")
}

if ($NonInteractive) {
    $result = Invoke-Uninstall $Scope $DeleteData.IsPresent $DeleteModels.IsPresent
    Write-Output (Show-Result $result)
    exit 0
}

$form = New-OrionForm (T 'Orion''u kaldır') 520 400
[void](New-OrionHeader $form (T 'Orion''u kaldır') (T 'Çalışan Orion önce kapatılır.') 520)
[void](Add-OrionControl $form (New-OrionLabel (T 'Neyi kaldırmak istiyorsunuz?') 10.5 ([Drawing.FontStyle]::Bold)) 24 88 472 24)
$options = @()
if ($hasHub -and $hasRouter) {
    $options += , @('hub', (T 'Yalnız Hub ve web istemcisi (Router kalır)'))
    $options += , @('router', (T 'Yalnız Router (Hub kalır)'))
    $options += , @('all', (T 'Her şeyi kaldır'))
} elseif ($hasRouter) {
    $options += , @('all', (T 'Orion Router''ı kaldır'))
} else {
    $options += , @('all', (T 'Orion''u kaldır'))
}
$radios = @()
$top = 118
foreach ($option in $options) {
    $radio = New-OrionCheck $option[1] ($radios.Count -eq ($options.Count - 1)) -Radio
    $radio.Tag = $option[0]
    [void](Add-OrionControl $form $radio 32 $top 460 26)
    $radios += $radio; $top += 30
}
$top += 10
[void](Add-OrionControl $form (New-OrionLabel (T 'Korunacaklar') 10.5 ([Drawing.FontStyle]::Bold)) 24 $top 472 24)
$keepData = Add-OrionControl $form (New-OrionCheck (T 'Verilerim (sohbetler, hesaplar, ayarlar, şifreler)') $true) 32 ($top + 30) 460 26
$keepModels = Add-OrionControl $form (New-OrionCheck (T 'Modellerim ve indirmelerim') $true) 32 ($top + 60) 460 26
$status = Add-OrionControl $form (New-OrionLabel '' 10 -Muted) 24 ($top + 96) 472 24
$bar = Add-OrionControl $form (New-OrionProgress) 24 ($top + 124) 472 4
$bar.Visible = $false
$cancel = Add-OrionControl $form (New-OrionButton (T 'Vazgeç')) 386 ($top + 142) 110 36
$ok = Add-OrionControl $form (New-OrionButton (T 'Kaldır') -Danger) 266 ($top + 142) 110 36
$form.ClientSize = New-Object Drawing.Size((S 520), (S ($top + 196)))
$form.CancelButton = $cancel
$cancel.Add_Click({ $form.Close() })
$ok.Add_Click({
    $scope = ($radios | Where-Object { $_.Checked }).Tag
    $deleteData = -not $keepData.Checked
    $deleteModels = ($scope -eq 'all') -and -not $keepModels.Checked
    if ($deleteData -or $deleteModels) {
        $question = (T 'Seçilen veriler KALICI olarak silinecek ve geri alınamaz. Devam edilsin mi?')
        if ((Show-OrionMessage $question (T 'Verileri sil') -YesNo -Warning -Yes (T 'Sil')) -ne 'Yes') { return }
    }
    $ok.Enabled = $false; $cancel.Enabled = $false
    $status.Text = (T 'Kaldırılıyor, lütfen bekleyin...')
    $bar.Visible = $true
    $result = Invoke-Uninstall $scope $deleteData $deleteModels
    $bar.Visible = $false
    [void](Show-OrionMessage (Show-Result $result))
    $form.Close()
})
[void]$form.ShowDialog()
