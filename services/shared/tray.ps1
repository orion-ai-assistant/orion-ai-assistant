# Orion notification-area icon. Dependency-free (Windows PowerShell + WinForms).
# Started and supervised by services.shared.launcher; never started by the Hub itself.
#   left click / "Orion'u ac" : open the Orion window (starts services if they are not running)
#   "Cik"                     : stop Hub, Router and every service through the normal launcher stop
#   "Orion'u kaldir"          : the uninstall dialog (bootstrap/Orion-Uninstall.ps1; asks what to remove / keep)
#   Windows sign-out / shutdown             : the same stop, so PostgreSQL shuts down cleanly (no crash recovery
#                                             at the next start); Windows is held back only while it runs
# Closing the Orion window never touches this icon or the services.
param([Parameter(Mandatory = $true)][string]$Registry)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'Orion-Ui.ps1')   # Orion's look (dark menu, Orion icon) and T.

$bootstrap = Split-Path -Parent $PSCommandPath
$entry = Join-Path $bootstrap 'Orion.ps1'
$marker = Join-Path $bootstrap 'tray.exit'
$identity = (Get-Content -LiteralPath $Registry -Raw -Encoding UTF8 | ConvertFrom-Json).installation_id
$created = $false
$script:mutex = New-Object System.Threading.Mutex($true, "Local\Orion-Tray-$identity", [ref]$created)
if (-not $created) { exit 0 }   # One icon per installation.
Remove-Item -LiteralPath $marker -Force -ErrorAction SilentlyContinue

$script:last = [DateTime]::MinValue
function Invoke-Orion([string[]]$extra) {
    $arguments = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-WindowStyle', 'Hidden', '-File', ('"' + $entry + '"')) + $extra
    Start-Process -FilePath 'powershell.exe' -WindowStyle Hidden -ArgumentList $arguments | Out-Null
}
function Open-Orion {
    # Ignore double clicks: one launch at a time.
    if (([DateTime]::Now - $script:last).TotalSeconds -lt 3) { return }
    $script:last = [DateTime]::Now
    Invoke-Orion @()
}
function Close-Tray {
    $script:timer.Stop()
    $script:icon.Visible = $false   # Hide first, or Windows keeps a ghost icon until the next hover.
    $script:icon.Dispose()
    [Windows.Forms.Application]::ExitThread()
}

Initialize-OrionUi
$script:icon = New-Object Windows.Forms.NotifyIcon
# Same Orion icon as the desktop/Startup entry (embedded in the retained Orion.exe next to this script), at the
# size the notification area uses on this screen (ExtractAssociatedIcon only had 32 px, scaled down blurry).
$script:icon.Icon = Get-OrionIcon ([Windows.Forms.SystemInformation]::SmallIconSize.Width)
$script:icon.Text = 'Orion'
$menu = New-Object Windows.Forms.ContextMenuStrip
Set-OrionMenuStyle $menu
$title = $menu.Items.Add('Orion AI Assistant')
$title.Image = (Get-OrionIcon (S 20)).ToBitmap()
$title.Font = Get-OrionFont 10 ([Drawing.FontStyle]::Bold)
$title.ForeColor = $script:OrionTheme.Accent
$title.Add_Click({ Open-Orion })
[void]$menu.Items.Add((New-Object Windows.Forms.ToolStripSeparator))
$open = $menu.Items.Add("Orion'u a" + [char]0x00E7)
$remove = $menu.Items.Add("Orion'u kald" + [char]0x0131 + 'r' + [char]0x2026)
[void]$menu.Items.Add((New-Object Windows.Forms.ToolStripSeparator))
$quit = $menu.Items.Add([string][char]0x00C7 + [char]0x0131 + 'k')
foreach ($item in @($open, $remove, $quit)) { $item.ForeColor = $script:OrionTheme.Text; $item.Padding = New-Object Windows.Forms.Padding(0, 3, 0, 3) }
$script:icon.ContextMenuStrip = $menu
$open.Add_Click({ Open-Orion })
$script:icon.Add_MouseClick({ if ($_.Button -eq [Windows.Forms.MouseButtons]::Left) { Open-Orion } })
$remove.Add_Click({
    # The dialog stops Orion itself after the user confirmed; this icon ends when the registry is gone.
    $script = Join-Path $bootstrap 'Orion-Uninstall.ps1'
    if (Test-Path -LiteralPath $script) {
        Start-Process -FilePath 'powershell.exe' -WindowStyle Hidden -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass',
            '-WindowStyle', 'Hidden', '-File', ('"' + $script + '"'), '-Registry', ('"' + $Registry + '"')) | Out-Null
    }
})
$quit.Add_Click({
    # The launcher stop is the single shutdown path. Do not wait for it: this icon is already gone.
    $script:icon.Visible = $false
    Invoke-Orion @('-Stop')
    Close-Tray
})

# The launcher asks for a clean exit with a marker file; losing the registry also ends the icon.
$script:timer = New-Object Windows.Forms.Timer
$script:timer.Interval = 1000
$script:timer.Add_Tick({
    if ((Test-Path -LiteralPath $marker) -or -not (Test-Path -LiteralPath $Registry)) {
        Remove-Item -LiteralPath $marker -Force -ErrorAction SilentlyContinue
        Close-Tray
    }
})
$script:timer.Start()

# Windows sign-out / shutdown. Without this, Windows ended PostgreSQL and the Hub abruptly: the next start ran
# crash recovery. A hidden window receives WM_QUERYENDSESSION as FormClosing(WindowsShutDown); while it runs the
# normal stop (seconds), Windows shows "Orion kapat\u0131l\u0131yor" and waits. The stop ends this icon itself.
$script:session = New-Object Windows.Forms.Form
$script:session.ShowInTaskbar = $false; $script:session.FormBorderStyle = 'None'; $script:session.Opacity = 0
$script:session.Size = New-Object Drawing.Size(1, 1); $script:session.StartPosition = 'Manual'
$script:session.Location = New-Object Drawing.Point(-32000, -32000)
$script:session.Add_FormClosing({
    param($sender, $e)
    if ($e.CloseReason -ne [Windows.Forms.CloseReason]::WindowsShutDown -or $script:ending) { return }
    $script:ending = $true
    try { if ($script:OrionNative) { [void][OrionUiNative]::ShutdownBlockReasonCreate($sender.Handle, (T 'Orion kapat\u0131l\u0131yor...')) } } catch {}
    $script:icon.Visible = $false
    try {
        $arguments = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-WindowStyle', 'Hidden', '-File', ('"' + $entry + '"'), '-Stop', '-NonInteractive')
        $stop = Start-Process -FilePath 'powershell.exe' -WindowStyle Hidden -ArgumentList $arguments -PassThru
        # Until the stop is done, or it asks this icon to leave (services are down by then), at most 25 s.
        $deadline = [DateTime]::Now.AddSeconds(25)
        while (-not $stop.HasExited -and -not (Test-Path -LiteralPath $marker) -and [DateTime]::Now -lt $deadline) { Start-Sleep -Milliseconds 200 }
    } catch {}
    try { if ($script:OrionNative) { [void][OrionUiNative]::ShutdownBlockReasonDestroy($sender.Handle) } } catch {}
})
$script:session.Show(); $script:session.Hide()   # A real (hidden) top-level window: Windows asks it before ending.
$script:icon.Visible = $true
try { [Windows.Forms.Application]::Run() } finally { $script:mutex.ReleaseMutex() }
