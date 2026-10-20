# "Orion is updating" window. Dependency-free (Windows PowerShell + WinForms), ASCII only.
# Started by services.shared.update_ui while an update is applied (the Orion app window is closed then,
# because its Hub is stopped); closed again by the launcher / watcher once Orion is back, or when the
# update state disappears. It only READS the update journal; it never changes anything.
# -Mode quitting: a launch waits for the previous quit ("Cik") to finish, then Orion opens by itself.
param([Parameter(Mandatory = $true)][string]$Registry, [ValidateSet('updating', 'quitting')][string]$Mode = 'updating')
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'Orion-Ui.ps1')   # Orion's look (Initialize-OrionUi, New-OrionForm, ...) and T.

$base = Split-Path -Parent $Registry
$bootstrap = Join-Path $base 'bootstrap'
$updates = Join-Path $base 'updates'
$marker = Join-Path $bootstrap 'updating-window.exit'
$identity = (Get-Content -LiteralPath $Registry -Raw -Encoding UTF8 | ConvertFrom-Json).installation_id
$created = $false
$script:mutex = New-Object System.Threading.Mutex($true, "Local\Orion-Updating-$identity", [ref]$created)
if (-not $created) { exit 0 }   # One window per installation.
$started = [DateTime]::Now
$startedEpoch = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds() - 5
$script:shown = $false
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
        'applying' { $text = T 'G\u00fcncelleme haz\u0131rlan\u0131yor...' }
        'switching' { $text = T 'Yeni s\u00fcr\u00fcm yerle\u015ftiriliyor...' }
        'health_check' { $text = T 'Yeni s\u00fcr\u00fcm denetleniyor...' }
        'rollback_pending' { $text = T '\u00d6nceki s\u00fcr\u00fcme d\u00f6n\u00fcl\u00fcyor...' }
        'restarting' { $text = T 'Orion yeniden ba\u015flat\u0131l\u0131yor...' }
        default { $text = T 'G\u00fcncelleme s\u00fcr\u00fcyor...' }
    }
    # Real progress: the helper writes percent (0-100 of the whole apply) into the journal while it works.
    # Before the helper starts and after it ends only the apply-now phase is known.
    $percent = $null
    if ($journal -and $phase -eq $journal.phase -and $journal.progress -and $null -ne $journal.progress.percent) { $percent = [int]$journal.progress.percent }
    elseif ($phase -eq 'stopping') { $percent = 3 }
    elseif ($phase -eq 'applying') { $percent = 5 }
    # A phase that has not reported its own percent yet starts at the low end of its share (the bar never waits at 38%).
    elseif (@{ draining = 8; backing_up = 10; switching = 45; health_check = 55 }.ContainsKey([string]$phase)) { $percent = @{ draining = 8; backing_up = 10; switching = 45; health_check = 55 }[[string]$phase] }
    elseif ($phase -eq 'restarting') { $percent = 97 }
    return @($text, $stamp, $percent)
}

Initialize-OrionUi
$form = New-OrionForm 'Orion' 520 236
$form.ControlBox = $false; $form.TopMost = $true
$header = New-OrionHeader $form (T 'Orion g\u00fcncelleniyor') (T 'L\u00fctfen bekleyin; birka\u00e7 dakika s\u00fcrebilir.') 520
$title = $header[0]
$step = Add-OrionControl $form (New-OrionLabel '' 10.5) 24 96 380 24
$percent = Add-OrionControl $form (New-OrionLabel '' 10.5 -Muted) 404 96 92 24
$percent.TextAlign = 'MiddleRight'
$bar = Add-OrionControl $form (New-OrionProgress) 24 128 472 6
$note = Add-OrionControl $form (New-OrionLabel (T 'Bitince Orion kendili\u011finden a\u00e7\u0131lacak; bu pencereyi kapatmay\u0131n.') 9.5 -Muted) 24 156 472 22
$elapsed = Add-OrionControl $form (New-OrionLabel '' 9.5 -Muted) 24 182 472 44
$elapsed.AutoEllipsis = $false

$timer = New-Object Windows.Forms.Timer
$timer.Interval = 1000
$timer.Add_Tick({
    if ((Test-Path -LiteralPath $marker) -or -not (Test-Path -LiteralPath $Registry) -or [DateTime]::Now -ge $deadline) {
        Remove-Item -LiteralPath $marker -Force -ErrorAction SilentlyContinue
        $timer.Stop(); $form.Close(); return
    }
    # The outcome is recorded before Orion restarts; show it here too, so the end is visible (not just a closing window).
    $result = Read-Json (Join-Path $updates 'last-result.json')
    if ($result -and $result.at -ge $startedEpoch -and -not $script:shown) {
        $script:shown = $true
        $title.Text = (T 'G\u00fcncelleme bitti')
        $step.Text = ''
        $elapsed.Text = [string]$result.message   # Wraps over two lines; the title has room for one.
        Set-OrionProgress $bar 100; $percent.Text = '100%'
        $note.Text = (T 'Orion birazdan yeniden a\u00e7\u0131lacak.')
    }
    if ($script:shown) { return }
    $info = Get-Step
    $step.Text = $info[0]
    if ($null -ne $info[2]) {
        Set-OrionProgress $bar $info[2]  # Never moves backwards.
        $percent.Text = ('{0}%' -f [int]$bar.Tag.Value)
    }
    $span = [DateTime]::Now - $started
    $elapsed.Text = (T 'Ge\u00e7en s\u00fcre: ') + ('{0:00}:{1:00}' -f [int][math]::Floor($span.TotalMinutes), $span.Seconds)
    if (([DateTime]::Now - $info[1]).TotalMinutes -gt 10) {
        $elapsed.Text += "`r`n" +(T '(Beklenenden uzun s\u00fcr\u00fcyor; hala \u00e7al\u0131\u015f\u0131yor olabilir.)')
    }
})
$step.Text = (T 'G\u00fcncelleme s\u00fcr\u00fcyor...')
if ($Mode -eq 'quitting') {
    $title.Text = (T 'Orion kapan\u0131yor')
    $header[1].Text = (T 'Birazdan kendili\u011finden a\u00e7\u0131lacak.')
    $step.Text = (T '\u00d6nceki oturum kapat\u0131l\u0131yor...')
    $note.Text = (T 'Bitince Orion kendili\u011finden a\u00e7\u0131lacak.')
    $deadline = $started.AddMinutes(3)
    $script:shown = $true   # No update steps or outcome here: only the elapsed time moves.
    $timer.Add_Tick({
        $span = [DateTime]::Now - $started
        $elapsed.Text = (T 'Ge\u00e7en s\u00fcre: ') + ('{0:00}:{1:00}' -f [int][math]::Floor($span.TotalMinutes), $span.Seconds)
    })
}
$timer.Start()
try { [void]$form.ShowDialog() } finally { $timer.Dispose(); $form.Dispose(); $script:mutex.ReleaseMutex() }
