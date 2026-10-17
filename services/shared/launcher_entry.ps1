param([switch]$Startup, [switch]$Background, [switch]$Stop, [switch]$Repair, [switch]$NonInteractive)
$ErrorActionPreference = 'Stop'
$registry = Join-Path (Split-Path -Parent $PSScriptRoot) 'install.json'
function Show-OrionRepair([string]$message) {
    if ($NonInteractive) { Write-Output $message; return }
    Add-Type -AssemblyName System.Windows.Forms
    $form = New-Object Windows.Forms.Form
    $form.Text = 'Orion - Onarma / Repair'
    $form.Width = 620; $form.Height = 290
    $label = New-Object Windows.Forms.Label
    $label.Text = "Orion baslatilamadi. Kayitli disk/kurulum ve runtime'i kontrol edin.`r`n$message`r`nVeriler degistirilmedi. / Check the registered disk and runtime."
    $label.SetBounds(15, 15, 575, 150); $form.Controls.Add($label)
    $logs = New-Object Windows.Forms.Button
    $logs.Text = 'Logs'; $logs.SetBounds(15, 180, 150, 32)
    $logs.Add_Click({ Start-Process explorer.exe -ArgumentList ('"' + $PSScriptRoot + '"') })
    $form.Controls.Add($logs)
    $repairButton = New-Object Windows.Forms.Button
    $repairButton.Text = 'Installation record'; $repairButton.SetBounds(180, 180, 180, 32)
    $repairButton.Add_Click({ Start-Process explorer.exe -ArgumentList ('"' + (Split-Path -Parent $PSScriptRoot) + '"') })
    $form.Controls.Add($repairButton)
    [void]$form.ShowDialog()
}
# Phases in which the helper is switching versions right now (calm wait), and phases that allow a normal start.
$applying = @('draining','backing_up','switching','health_check','rollback_pending')
$allowedPhases = @('checking','downloading','verifying','staged','waiting_for_idle','completed','rolled_back','failed')
function T([string]$text) { [regex]::Unescape($text) }   # Keeps this file ASCII; Windows PowerShell 5.1 reads it as ANSI.
function Get-OrionUpdatePhase([string]$pending) {
    $operation = Get-Content -LiteralPath $pending -Raw -Encoding UTF8 | ConvertFrom-Json
    $operationId = [guid]::ParseExact($operation.operation_id, 'D').ToString('D')
    $journalPath = Join-Path (Split-Path -Parent $pending) "$operationId/journal.json"
    $journal = Get-Content -LiteralPath $journalPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($operation.schema -ne 1 -or $journal.schema -ne 1 -or $journal.operation_id -ne $operationId) {
        throw 'Update recovery in progress. Resume the local update helper; installation record and logs remain available.'
    }
    return $journal.phase
}
function Test-OrionUpdating([string]$pending) {
    try { return $applying -contains (Get-OrionUpdatePhase $pending) } catch { return $true }
}
function Wait-OrionUpdate([string]$pending) {
    $deadline = [DateTime]::Now.AddMinutes(30)
    if ($NonInteractive) {
        while ((Test-OrionUpdating $pending) -and [DateTime]::Now -lt $deadline) { Start-Sleep -Seconds 2 }
        return
    }
    Add-Type -AssemblyName System.Windows.Forms
    $form = New-Object Windows.Forms.Form
    $form.Text = 'Orion'
    $form.Width = 520; $form.Height = 170
    $form.StartPosition = 'CenterScreen'; $form.FormBorderStyle = 'FixedDialog'
    $form.MaximizeBox = $false; $form.MinimizeBox = $false; $form.ControlBox = $false; $form.TopMost = $true
    $label = New-Object Windows.Forms.Label
    $label.Text = (T 'Orion g\u00fcncelleniyor, birka\u00e7 dakika s\u00fcrebilir.') + "`r`n" + (T 'Bitince Orion kendili\u011finden a\u00e7\u0131lacak; bu pencereyi kapatmay\u0131n.')
    $label.SetBounds(20, 20, 470, 50); $form.Controls.Add($label)
    $bar = New-Object Windows.Forms.ProgressBar
    $bar.Style = 'Marquee'; $bar.SetBounds(20, 85, 470, 22); $form.Controls.Add($bar)
    $timer = New-Object Windows.Forms.Timer
    $timer.Interval = 2000
    $timer.Add_Tick({ if (-not (Test-OrionUpdating $pending) -or [DateTime]::Now -ge $deadline) { $timer.Stop(); $form.Close() } })
    $timer.Start()
    [void]$form.ShowDialog()
    $timer.Dispose(); $form.Dispose()
}
try {
    if ($Repair) { Show-OrionRepair 'Installation record and launcher logs are available locally.'; exit 0 }
    $record = Get-Content -LiteralPath $registry -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($Startup -and $record.preferences.start_with_windows -eq $false) { exit 0 }
    if ($record.schema_version -ne 1) { throw 'Unsupported installation registry. Repair required.' }
    $pending = Join-Path (Split-Path -Parent $PSScriptRoot) 'updates/active.json'
    if (-not $Stop -and (Test-Path -LiteralPath $pending)) {
        $phase = Get-OrionUpdatePhase $pending
        if ($applying -contains $phase) {
            # Another start is switching versions right now: wait calmly, do not show the Repair dialog.
            Wait-OrionUpdate $pending
            $phase = Get-OrionUpdatePhase $pending
            if ($phase -in @('rolled_back', 'failed') -and -not $NonInteractive) {
                [void][Windows.Forms.MessageBox]::Show((T 'G\u00fcncelleme uygulanamad\u0131; Orion \u00f6nceki s\u00fcr\u00fcmle a\u00e7\u0131l\u0131yor.'), 'Orion')
            }
        }
        if ($phase -notin $allowedPhases) {
            throw 'Update recovery in progress. Resume the local update helper; installation record and logs remain available.'
        }
    }
    $hubs = @($record.components | Where-Object { $_.kind -eq 'hub' })
    if ($hubs.Count -gt 1) { throw 'Exactly one registered Hub is required.' }
    if ($hubs.Count -eq 1) {
        $root = $hubs[0].code_path
    } else {
        $routers = @($record.components | Where-Object { $_.kind -eq 'router' })
        if ($routers.Count -ne 1) { throw 'No registered Hub or standalone Router.' }
        $runtimeRecord = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'automatic-runtime.json') -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($runtimeRecord.schema -ne 1) { throw 'Invalid retained runtime.' }
        $root = $runtimeRecord.root
    }
    if (-not [IO.Path]::IsPathRooted($root) -or -not (Test-Path -LiteralPath $root -PathType Container)) {
        throw 'Registered disk or Hub folder is missing. Reconnect the disk; no empty installation was created.'
    }
    $python = Join-Path $root 'runtime/python/python.exe'
    if (-not (Test-Path -LiteralPath $python -PathType Leaf)) { $python = Join-Path $root 'installer/.venv/Scripts/python.exe' }
    if (-not (Test-Path -LiteralPath $python -PathType Leaf)) { $python = Join-Path $root 'services/hub/.venv/Scripts/python.exe' }
    if (-not (Test-Path -LiteralPath $python -PathType Leaf)) { throw 'Orion Python runtime is missing. Repair the existing installation.' }
    $env:ORION_INSTALLATION_REGISTRY = $registry
    $env:PYTHONPATH = $root
    $arguments = @('-m', 'services.shared.launcher', '--registry', $registry)
    if ($Startup) { $arguments += '--startup' }
    if ($Background) { $arguments += '--background' }
    if ($Stop) { $arguments += 'stop' }
    Push-Location -LiteralPath $root
    try {
        $output = & $python @arguments 2>&1
        $code = $LASTEXITCODE
    } finally { Pop-Location }
    # Keep only the most recent launch result; no secrets/DB settings are written.
    $output | Out-File -LiteralPath (Join-Path $PSScriptRoot 'launcher.log') -Encoding UTF8
    if ($code -ne 0) { throw ($output -join "`r`n") }
} catch {
    try { $_.Exception.Message | Out-File -LiteralPath (Join-Path $PSScriptRoot 'launcher.log') -Encoding UTF8 } catch {}
    Show-OrionRepair $_.Exception.Message
    exit 1
}
