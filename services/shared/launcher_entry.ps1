param([switch]$Startup, [switch]$Background, [switch]$Repair, [switch]$NonInteractive)
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
try {
    if ($Repair) { Show-OrionRepair 'Installation record and launcher logs are available locally.'; exit 0 }
    $record = Get-Content -LiteralPath $registry -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($Startup -and $record.preferences.start_with_windows -eq $false) { exit 0 }
    if ($record.schema_version -ne 1) { throw 'Unsupported installation registry. Repair required.' }
    $hubs = @($record.components | Where-Object { $_.kind -eq 'hub' })
    if ($hubs.Count -ne 1) { throw 'Exactly one registered Hub is required.' }
    $root = $hubs[0].code_path
    if (-not [IO.Path]::IsPathRooted($root) -or -not (Test-Path -LiteralPath $root -PathType Container)) {
        throw 'Registered disk or Hub folder is missing. Reconnect the disk; no empty installation was created.'
    }
    $python = Join-Path $root 'installer/.venv/Scripts/python.exe'
    if (-not (Test-Path -LiteralPath $python -PathType Leaf)) { $python = Join-Path $root 'services/hub/.venv/Scripts/python.exe' }
    if (-not (Test-Path -LiteralPath $python -PathType Leaf)) { throw 'Orion Python runtime is missing. Repair the existing installation.' }
    $env:ORION_INSTALLATION_REGISTRY = $registry
    $env:PYTHONPATH = $root
    $arguments = @('-m', 'services.shared.launcher', '--registry', $registry)
    if ($Startup) { $arguments += '--startup' }
    if ($Background) { $arguments += '--background' }
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
