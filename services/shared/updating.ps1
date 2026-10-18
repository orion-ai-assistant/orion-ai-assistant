# "Orion is updating" window. Dependency-free (Windows PowerShell + WinForms), ASCII only.
# Started by services.shared.update_ui while an update is applied (the Orion app window is closed then,
# because its Hub is stopped); closed again by the launcher / watcher once Orion is back, or when the
# update state disappears. It only READS the update journal; it never changes anything.
param([Parameter(Mandatory = $true)][string]$Registry)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
function T([string]$text) { [regex]::Unescape($text) }

$base = Split-Path -Parent $Registry
$bootstrap = Join-Path $base 'bootstrap'
$updates = Join-Path $base 'updates'
$marker = Join-Path $bootstrap 'updating-window.exit'
$identity = (Get-Content -LiteralPath $Registry -Raw -Encoding UTF8 | ConvertFrom-Json).installation_id
$created = $false
$script:mutex = New-Object System.Threading.Mutex($true, "Local\Orion-Updating-$identity", [ref]$created)
if (-not $created) { exit 0 }   # One window per installation.
$started = [DateTime]::Now
$deadline = $started.AddMinutes(40)

function Read-Json([string]$path) {
    try { return Get-Content -LiteralPath $path -Raw -Encoding UTF8 | ConvertFrom-Json } catch { return $null }
}
function Get-Step {
    # Returns @(text, lastActivity). apply-now phases come first, the journal gives the detail inside "applying".
    $apply = Read-Json (Join-Path $updates 'apply-now.json')
    $journal = $null
    $pointer = Read-Json (Join-Path $updates 'active.json')
    $stamp = $started
    if ($pointer -and $pointer.operation_id -match '^[0-9a-fA-F-]{36}$') {
        $path = Join-Path $updates ($pointer.operation_id + '/journal.json')
        $journal = Read-Json $path
        if (Test-Path -LiteralPath $path) { $stamp = (Get-Item -LiteralPath $path).LastWriteTime }
    }
    $apply_path = Join-Path $updates 'apply-now.json'
    if ((Test-Path -LiteralPath $apply_path) -and (Get-Item -LiteralPath $apply_path).LastWriteTime -gt $stamp) { $stamp = (Get-Item -LiteralPath $apply_path).LastWriteTime }
    $phase = $null
    if ($apply -and @('stopping', 'applying', 'restarting') -contains $apply.phase) { $phase = $apply.phase }
    if ($journal -and @('draining', 'backing_up', 'switching', 'health_check', 'rollback_pending') -contains $journal.phase -and $phase -ne 'restarting') { $phase = $journal.phase }
    if (-not $phase -and $journal) { $phase = $journal.phase }
    switch ($phase) {
        'stopping' { $text = T 'Servisler kapat\u0131l\u0131yor...' }
        'draining' { $text = T 'Ge\u00e7i\u015f haz\u0131rlan\u0131yor...' }
        'backing_up' { $text = T 'Veriler yedekleniyor...' }
        'applying' { $text = T 'Yeni s\u00fcr\u00fcm yerle\u015ftiriliyor...' }
        'switching' { $text = T 'Yeni s\u00fcr\u00fcm yerle\u015ftiriliyor...' }
        'health_check' { $text = T 'Yeni s\u00fcr\u00fcm denetleniyor...' }
        'rollback_pending' { $text = T '\u00d6nceki s\u00fcr\u00fcme d\u00f6n\u00fcl\u00fcyor...' }
        'restarting' { $text = T 'Orion yeniden ba\u015flat\u0131l\u0131yor...' }
        default { $text = T 'G\u00fcncelleme s\u00fcr\u00fcyor...' }
    }
    return @($text, $stamp)
}

$form = New-Object Windows.Forms.Form
$form.Text = 'Orion'
$form.Width = 540; $form.Height = 220
$form.StartPosition = 'CenterScreen'; $form.FormBorderStyle = 'FixedDialog'
$form.MaximizeBox = $false; $form.MinimizeBox = $false; $form.ControlBox = $false; $form.TopMost = $true
$form.ShowInTaskbar = $true
$title = New-Object Windows.Forms.Label
$title.Text = (T 'Orion g\u00fcncelleniyor, l\u00fctfen bekleyin (birka\u00e7 dakika s\u00fcrebilir).')
$title.Font = New-Object Drawing.Font($title.Font.FontFamily, 10, [Drawing.FontStyle]::Bold)
$title.SetBounds(20, 18, 490, 40); $form.Controls.Add($title)
$step = New-Object Windows.Forms.Label
$step.SetBounds(20, 66, 490, 22); $form.Controls.Add($step)
$bar = New-Object Windows.Forms.ProgressBar
$bar.Style = 'Marquee'; $bar.SetBounds(20, 96, 490, 22); $form.Controls.Add($bar)
$note = New-Object Windows.Forms.Label
$note.Text = (T 'Bitince Orion kendili\u011finden a\u00e7\u0131lacak; bu pencereyi kapatmay\u0131n.')
$note.SetBounds(20, 130, 490, 22); $form.Controls.Add($note)
$elapsed = New-Object Windows.Forms.Label
$elapsed.SetBounds(20, 155, 490, 22); $form.Controls.Add($elapsed)

$timer = New-Object Windows.Forms.Timer
$timer.Interval = 1000
$timer.Add_Tick({
    if ((Test-Path -LiteralPath $marker) -or -not (Test-Path -LiteralPath $Registry) -or [DateTime]::Now -ge $deadline) {
        Remove-Item -LiteralPath $marker -Force -ErrorAction SilentlyContinue
        $timer.Stop(); $form.Close(); return
    }
    $info = Get-Step
    $step.Text = $info[0]
    $span = [DateTime]::Now - $started
    $elapsed.Text = (T 'Ge\u00e7en s\u00fcre: ') + ('{0:00}:{1:00}' -f [int][math]::Floor($span.TotalMinutes), $span.Seconds)
    if (([DateTime]::Now - $info[1]).TotalMinutes -gt 10) {
        $elapsed.Text += '  ' + (T '(Beklenenden uzun s\u00fcr\u00fcyor; hala \u00e7al\u0131\u015f\u0131yor olabilir.)')
    }
})
$step.Text = (T 'G\u00fcncelleme s\u00fcr\u00fcyor...')
$timer.Start()
try { [void]$form.ShowDialog() } finally { $timer.Dispose(); $form.Dispose(); $script:mutex.ReleaseMutex() }
