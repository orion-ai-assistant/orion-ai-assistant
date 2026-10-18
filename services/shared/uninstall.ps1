# Orion uninstall dialog ("Uygulamalar ve ozellikler" -> Orion AI Assistant -> Kaldir, and the tray's "Orion'u kaldir").
# Asks what to remove and whether to keep the user data, then runs the safe flow of services.shared.uninstall from
# the retained bootstrap runtime. Dependency-free (Windows PowerShell + WinForms). Kept ASCII: Windows PowerShell 5.1
# reads this file as ANSI, so Turkish text is written as \uXXXX escapes and unescaped by T.
param([Parameter(Mandatory = $true)][string]$Registry, [switch]$NonInteractive, [string]$Scope, [switch]$DeleteData, [switch]$DeleteModels)
$ErrorActionPreference = 'Stop'
function T([string]$text) { [regex]::Unescape($text) }
Add-Type -AssemblyName System.Windows.Forms
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
    [void][Windows.Forms.MessageBox]::Show((T 'Orion kaldırma aracı bulunamadı; hiçbir şey silinmedi.'), 'Orion')
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

$form = New-Object Windows.Forms.Form
$form.Text = (T 'Orion''u kaldır'); $form.Width = 520; $form.Height = 330
$form.StartPosition = 'CenterScreen'; $form.FormBorderStyle = 'FixedDialog'; $form.MaximizeBox = $false; $form.MinimizeBox = $false
$label = New-Object Windows.Forms.Label
$label.Text = (T 'Neyi kaldırmak istiyorsunuz? Çalışan Orion kapatılır.')
$label.SetBounds(20, 15, 470, 22); $form.Controls.Add($label)
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
$top = 45
foreach ($option in $options) {
    $radio = New-Object Windows.Forms.RadioButton
    $radio.Text = $option[1]; $radio.Tag = $option[0]; $radio.SetBounds(30, $top, 450, 24)
    if ($radios.Count -eq ($options.Count - 1)) { $radio.Checked = $true }
    $form.Controls.Add($radio); $radios += $radio; $top += 28
}
$keepData = New-Object Windows.Forms.CheckBox
$keepData.Text = (T 'Verilerimi koru (sohbetler, hesaplar, ayarlar, şifreler)'); $keepData.Checked = $true
$keepData.SetBounds(30, $top + 10, 450, 24); $form.Controls.Add($keepData)
$keepModels = New-Object Windows.Forms.CheckBox
$keepModels.Text = (T 'Modellerimi ve indirmelerimi koru'); $keepModels.Checked = $true
$keepModels.SetBounds(30, $top + 38, 450, 24); $form.Controls.Add($keepModels)
$status = New-Object Windows.Forms.Label
$status.SetBounds(20, $top + 72, 470, 40); $form.Controls.Add($status)
$ok = New-Object Windows.Forms.Button
$ok.Text = (T 'Kaldır'); $ok.SetBounds(290, $top + 118, 100, 30); $form.Controls.Add($ok)
$cancel = New-Object Windows.Forms.Button
$cancel.Text = (T 'Vazgeç'); $cancel.SetBounds(400, $top + 118, 90, 30); $form.Controls.Add($cancel)
$form.Height = $top + 205
$cancel.Add_Click({ $form.Close() })
$ok.Add_Click({
    $scope = ($radios | Where-Object { $_.Checked }).Tag
    $deleteData = -not $keepData.Checked
    $deleteModels = ($scope -eq 'all') -and -not $keepModels.Checked
    if ($deleteData -or $deleteModels) {
        $question = (T 'Seçilen veriler KALICI olarak silinecek ve geri alınamaz. Devam edilsin mi?')
        if ([Windows.Forms.MessageBox]::Show($question, 'Orion', 'YesNo', 'Warning') -ne 'Yes') { return }
    }
    $ok.Enabled = $false; $cancel.Enabled = $false
    $status.Text = (T 'Kaldırılıyor, lütfen bekleyin...')
    $result = Invoke-Uninstall $scope $deleteData $deleteModels
    [void][Windows.Forms.MessageBox]::Show((Show-Result $result), 'Orion')
    $form.Close()
})
[void]$form.ShowDialog()
