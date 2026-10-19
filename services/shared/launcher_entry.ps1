param([switch]$Startup, [switch]$Background, [switch]$Stop, [switch]$Repair, [switch]$NonInteractive)
$ErrorActionPreference = 'Stop'
$registry = Join-Path (Split-Path -Parent $PSScriptRoot) 'install.json'
# Orion's look for this script's few windows (Orion-Ui.ps1, written next to it). Loaded only when a window is
# needed - never on the normal launch path - and dot-sourced inside each dialog function, so its functions live
# there. Without it (an older bootstrap folder) the plain WinForms dialogs are used.
$script:OrionUiPath = Join-Path $PSScriptRoot 'Orion-Ui.ps1'
function Show-OrionText([string]$text, [switch]$Warning) {
    if (Test-Path -LiteralPath $script:OrionUiPath) {
        try { . $script:OrionUiPath; Initialize-OrionUi; [void](Show-OrionMessage $text 'Orion' -Warning:$Warning); return } catch {}
    }
    Add-Type -AssemblyName System.Windows.Forms
    [void][Windows.Forms.MessageBox]::Show($text, 'Orion')
}
function Show-OrionRepair([string]$message) {
    if ($NonInteractive) { Write-Output $message; return }
    $logsFolder = $PSScriptRoot
    $recordFolder = Split-Path -Parent $PSScriptRoot
    if (Test-Path -LiteralPath $script:OrionUiPath) {
        try {
            . $script:OrionUiPath
            Initialize-OrionUi
            $form = New-OrionForm (T 'Orion - Onarma') 560 330
            [void](New-OrionHeader $form (T 'Orion ba\u015flat\u0131lamad\u0131') (T 'Verileriniz de\u011fi\u015ftirilmedi.') 560)
            $hint = New-OrionLabel (T 'Kay\u0131tl\u0131 diski ve kurulumu kontrol edin; sorun s\u00fcrerse a\u015fa\u011f\u0131daki ayr\u0131nt\u0131y\u0131 ve g\u00fcnl\u00fckleri iletin.') 10
            $hint.AutoEllipsis = $false
            [void](Add-OrionControl $form $hint 24 84 512 40)
            $details = New-Object Windows.Forms.TextBox
            $details.Multiline = $true; $details.ReadOnly = $true; $details.ScrollBars = 'Vertical'; $details.WordWrap = $true
            $details.BorderStyle = 'FixedSingle'; $details.Text = $message
            $details.BackColor = $script:OrionTheme.Surface; $details.ForeColor = $script:OrionTheme.Muted
            $details.Font = Get-OrionFont 9 -family 'Consolas'
            [void](Add-OrionControl $form $details 24 130 512 120)
            $logs = New-OrionButton (T 'G\u00fcnl\u00fckler')
            $logs.Add_Click({ Start-Process explorer.exe -ArgumentList ('"' + $logsFolder + '"') }.GetNewClosure())
            [void](Add-OrionControl $form $logs 24 272 130 36)
            $recordButton = New-OrionButton (T 'Kurulum kayd\u0131')
            $recordButton.Add_Click({ Start-Process explorer.exe -ArgumentList ('"' + $recordFolder + '"') }.GetNewClosure())
            [void](Add-OrionControl $form $recordButton 164 272 150 36)
            $close = New-OrionButton (T 'Kapat') -Primary
            $close.DialogResult = 'OK'
            [void](Add-OrionControl $form $close 416 272 120 36)
            $form.AcceptButton = $close; $form.CancelButton = $close
            $form.Add_Shown({ $close.Focus() }.GetNewClosure())
            [void]$form.ShowDialog()
            return
        } catch {}
    }
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
function Show-OrionStopFailure([string]$message) {
    # A stop that could not finish must be visible: services may still be running.
    if ($NonInteractive) { Write-Output $message; return }
    $text = (T 'Orion tamamen kapat\u0131lamad\u0131; baz\u0131 servisler \u00e7al\u0131\u015fmaya devam ediyor olabilir. Biraz sonra \u00c7\u0131k komutunu yeniden deneyin.')
    Show-OrionText ($text + "`r`n`r`n" + $message) -Warning
}
function Show-OrionElevated([string]$message) {
    # Orion and the shortcut run at different integrity levels (administrator vs normal): explain, no Repair dialog.
    if ($NonInteractive) { Write-Output $message; return }
    if ($message -match 'ORION-ELEVATED-SELF') {
        $text = (T 'Orion y\u00f6netici olarak ba\u015flat\u0131lamaz. Orion''u normal (y\u00f6netici olmayan) bir pencereden a\u00e7\u0131n.')
    } else {
        $text = (T 'Orion y\u00f6netici olarak \u00e7al\u0131\u015f\u0131yor (veya ba\u015fka bir yetki d\u00fczeyinde). Kapatmak i\u00e7in tepsiden \u00c7\u0131k''a bas\u0131n, sonra Orion''u normal (y\u00f6netici olmadan) a\u00e7\u0131n.')
    }
    Show-OrionText $text -Warning
}
function Test-OrionElevated {
    try { return ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator) } catch { return $false }
}
function Start-OrionDeElevated {
    # Start Orion again with the normal (limited) token. Explorer is the user's de-elevated shell; runas /trustlevel
    # gives a limited-token process; neither prompts. A stamp stops a loop when de-elevation does not work (UAC off).
    $stamp = Join-Path (Split-Path -Parent $PSScriptRoot) 'bootstrap/deelevate.stamp'
    try {
        if (Test-Path -LiteralPath $stamp) {
            $age = ([DateTime]::UtcNow - [DateTime]::Parse((Get-Content -LiteralPath $stamp -Raw).Trim(), $null, 'RoundtripKind')).TotalSeconds
            if ($age -lt 60) { return $false }
        }
        [IO.File]::WriteAllText($stamp, [DateTime]::UtcNow.ToString('o'))
    } catch {}
    $exe = Join-Path $PSScriptRoot 'Orion.exe'
    try {
        if (-not $Startup -and -not $Background -and (Test-Path -LiteralPath $exe -PathType Leaf)) {
            Start-Process -FilePath 'explorer.exe' -ArgumentList ('"' + $exe + '"')
        } else {
            $flags = ''
            if ($Startup) { $flags += ' -Startup' }
            if ($Background) { $flags += ' -Background' }
            $inner = 'powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File \"' + $PSCommandPath + '\"' + $flags
            Start-Process -FilePath 'runas.exe' -ArgumentList ('/trustlevel:0x20000 "' + $inner + '"') -WindowStyle Hidden
        }
        return $true
    } catch { return $false }
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
function Test-OrionApplyNow([string]$pending) {
    # "Update now" runs in the retained watcher (stop -> switch -> restart); the journal alone stays 'staged' for a while.
    try {
        $path = Join-Path (Split-Path -Parent $pending) 'apply-now.json'
        if (-not (Test-Path -LiteralPath $path)) { return $false }
        $state = Get-Content -LiteralPath $path -Raw -Encoding UTF8 | ConvertFrom-Json
        $fresh = ([DateTime]::Now - (Get-Item -LiteralPath $path).LastWriteTime).TotalMinutes -lt 10
        return $fresh -and (@('stopping', 'applying', 'restarting') -contains $state.phase)
    } catch { return $false }
}
function Invoke-OrionRecovery {
    # An apply cut off by power loss / Task Manager / a closed window stays in a switching phase with nobody
    # working on it. The retained bootstrap (not the installed Hub, which may predate this) finishes it or rolls
    # it back; when a live helper still owns the operation it does nothing and the calm wait below applies.
    try {
        $runtimeRecord = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'automatic-runtime.json') -Raw -Encoding UTF8 | ConvertFrom-Json
        $retained = [string]$runtimeRecord.root
        $helperPython = Join-Path $retained 'runtime/python/python.exe'
        $helperScript = Join-Path $retained 'scripts/release/update_automatic.py'
        if ($runtimeRecord.schema -ne 1 -or -not (Test-Path -LiteralPath $helperPython -PathType Leaf) -or -not (Test-Path -LiteralPath $helperScript -PathType Leaf)) { return }
        $env:ORION_INSTALLATION_REGISTRY = $registry
        $previousPreference = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        try { $null = & $helperPython -B $helperScript --registry $registry startup 2>&1 } finally { $ErrorActionPreference = $previousPreference }
    } catch {}
}
function Test-OrionUpdating([string]$pending) {
    try { return ($applying -contains (Get-OrionUpdatePhase $pending)) -or (Test-OrionApplyNow $pending) } catch { return $true }
}
function Test-OrionUpdatingWindow {
    # The updater's own "Orion is updating" window (Orion-Updating.ps1) holds this mutex while it is open.
    try {
        $id = (Get-Content -LiteralPath $registry -Raw -Encoding UTF8 | ConvertFrom-Json).installation_id
        $existing = $null
        if ([System.Threading.Mutex]::TryOpenExisting("Local\Orion-Updating-$id", [ref]$existing)) { $existing.Dispose(); return $true }
    } catch {}
    return $false
}
function Wait-OrionUpdate([string]$pending) {
    $deadline = [DateTime]::Now.AddMinutes(30)
    # A second launch during an update: the updater already shows its progress window; a second one is confusing.
    if ($NonInteractive -or (Test-OrionUpdatingWindow)) {
        while ((Test-OrionUpdating $pending) -and [DateTime]::Now -lt $deadline) { Start-Sleep -Seconds 2 }
        return
    }
    $form = $null
    if (Test-Path -LiteralPath $script:OrionUiPath) {
        try {
            . $script:OrionUiPath
            Initialize-OrionUi
            $form = New-OrionForm 'Orion' 500 150
            $form.ControlBox = $false; $form.TopMost = $true
            [void](New-OrionHeader $form (T 'Orion g\u00fcncelleniyor') (T 'Bitince Orion kendili\u011finden a\u00e7\u0131lacak.') 500)
            [void](Add-OrionControl $form (New-OrionProgress) 24 96 452 6)
            [void](Add-OrionControl $form (New-OrionLabel (T 'Birka\u00e7 dakika s\u00fcrebilir; bu pencereyi kapatmay\u0131n.') 9.5 -Muted) 24 114 452 22)
        } catch { $form = $null }
    }
    if (-not $form) {
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
    }
    $timer = New-Object Windows.Forms.Timer
    $timer.Interval = 2000
    $timer.Add_Tick({ if (-not (Test-OrionUpdating $pending) -or [DateTime]::Now -ge $deadline) { $timer.Stop(); $form.Close() } })
    $timer.Start()
    [void]$form.ShowDialog()
    $timer.Dispose(); $form.Dispose()
}
try {
    if (-not $Stop -and -not $Repair -and (Test-OrionElevated)) {
        # Orion never runs elevated: re-open it with normal rights and leave; the message is only the fallback.
        if (Start-OrionDeElevated) { exit 0 }
        throw 'ORION-ELEVATED-SELF'
    }
    if ($Repair) { Show-OrionRepair 'Installation record and launcher logs are available locally.'; exit 0 }
    $record = Get-Content -LiteralPath $registry -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($Startup -and $record.preferences.start_with_windows -eq $false) { exit 0 }
    if ($record.schema_version -ne 1) { throw 'Unsupported installation registry. Repair required.' }
    $pending = Join-Path (Split-Path -Parent $PSScriptRoot) 'updates/active.json'
    if (-not $Stop -and (Test-Path -LiteralPath $pending)) {
        $phase = Get-OrionUpdatePhase $pending
        if ($applying -contains $phase) {
            Invoke-OrionRecovery
            $phase = Get-OrionUpdatePhase $pending
        }
        if (($applying -contains $phase) -or (Test-OrionApplyNow $pending)) {
            # Another start is switching versions right now: wait calmly, do not show the Repair dialog.
            Wait-OrionUpdate $pending
            $phase = Get-OrionUpdatePhase $pending
            if ($phase -in @('rolled_back', 'failed') -and -not $NonInteractive) {
                Show-OrionText (T 'G\u00fcncelleme uygulanamad\u0131; Orion \u00f6nceki s\u00fcr\u00fcmle a\u00e7\u0131l\u0131yor.')
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
    # Native stderr must not become a terminating PowerShell error (that aborted the stop half way, with
    # the services still running). The launcher's own exit code decides.
    $previousPreference = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $output = & $python @arguments 2>&1
        $code = $LASTEXITCODE
    } finally { Pop-Location; $ErrorActionPreference = $previousPreference }
    # Keep only the most recent launch result; no secrets/DB settings are written.
    $output | Out-File -LiteralPath (Join-Path $PSScriptRoot 'launcher.log') -Encoding UTF8
    if ($code -ne 0) { throw ($output -join "`r`n") }
} catch {
    try { $_.Exception.Message | Out-File -LiteralPath (Join-Path $PSScriptRoot 'launcher.log') -Encoding UTF8 } catch {}
    if ($_.Exception.Message -match 'ORION-ELEVATED') { Show-OrionElevated $_.Exception.Message }
    elseif ($Stop) { Show-OrionStopFailure $_.Exception.Message } else { Show-OrionRepair $_.Exception.Message }
    exit 1
}
