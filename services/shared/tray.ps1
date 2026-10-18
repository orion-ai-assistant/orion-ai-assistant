# Orion notification-area icon. Dependency-free (Windows PowerShell + WinForms).
# Started and supervised by services.shared.launcher; never started by the Hub itself.
#   left click / "Orion'u ac" : open the Orion window (starts services if they are not running)
#   "Cik"                     : stop Hub, Router and every service through the normal launcher stop
# Closing the Orion window never touches this icon or the services.
param([Parameter(Mandatory = $true)][string]$Registry)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

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

$script:icon = New-Object Windows.Forms.NotifyIcon
# Same Orion icon as the desktop/Startup entry (embedded in the retained Orion.exe next to this script).
$script:icon.Icon = [Drawing.SystemIcons]::Application
try {
    $exe = Join-Path $PSScriptRoot 'Orion.exe'
    if (Test-Path -LiteralPath $exe -PathType Leaf) { $script:icon.Icon = [Drawing.Icon]::ExtractAssociatedIcon($exe) }
} catch {}
$script:icon.Text = 'Orion'
$menu = New-Object Windows.Forms.ContextMenuStrip
$open = $menu.Items.Add("Orion'u a" + [char]0x00E7)
$quit = $menu.Items.Add([string][char]0x00C7 + [char]0x0131 + 'k')
$script:icon.ContextMenuStrip = $menu
$open.Add_Click({ Open-Orion })
$script:icon.Add_MouseClick({ if ($_.Button -eq [Windows.Forms.MouseButtons]::Left) { Open-Orion } })
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
$script:icon.Visible = $true
try { [Windows.Forms.Application]::Run() } finally { $script:mutex.ReleaseMutex() }
