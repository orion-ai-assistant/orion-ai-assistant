# One-command Orion installer: downloads the signed installer bundle from this project's GitHub Release feed
# and opens the setup window, where the user picks the folder and the type (Orion center, or only the window
# for using another PC's Orion); it then installs and opens Orion.
#   PowerShell: irm https://raw.githubusercontent.com/orion-ai-assistant/orion-ai-assistant/main/get-orion.ps1 | iex
# The bundle carries the public trust root; every package is signature-verified
# by the installer before anything is extracted or run.
#
# Without the window (scripts, remote setups):
#   -Mode merkez   install the Orion center (Hub, window, Router)
#   -Mode baglan   install only the window and the light Hub that connects to another PC's Orion
#   -NoRouter      (-Mode merkez, older form) fresh install without the Router
#   -WithRouter    existing installation: also add the Router (from the Router feed) when it is missing
# Already installed? The same command ADDS what is missing and leaves everything else untouched.
param(
    [string]$Repo = 'orion-ai-assistant/orion-ai-assistant',
    [string]$Tag = 'feed',
    [ValidateSet('', 'merkez', 'baglan')]
    [string]$Mode = '',
    [switch]$NoRouter,
    [switch]$WithRouter,
    [switch]$NoStart,
    [switch]$NoStartWithWindows,
    [switch]$NoAutoUpdate,
    [switch]$NoFirewall
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12

if ($env:OS -ne 'Windows_NT' -or $env:PROCESSOR_ARCHITECTURE -notin 'AMD64') {
    throw 'Orion requires 64-bit Windows 10 (1903) or later.'
}
$orion = Join-Path $env:LOCALAPPDATA 'Orion'
$existing = Test-Path (Join-Path $orion 'install.json')
$base = "https://github.com/$Repo/releases/download/$Tag/"
$bundle = Join-Path $env:TEMP ('orion-bootstrap-' + [guid]::NewGuid().ToString('N') + '.zip')
# A first installation keeps its bundle as the retained bootstrap; an addition runs from a throw-away copy
# (the existing installation keeps its own retained bootstrap and updates it itself).
if ($existing) { $target = Join-Path $env:TEMP ('orion-installer-' + [guid]::NewGuid().ToString('N')) }
else { $target = Join-Path $orion 'Bootstrap-Installer' }
if (-not $existing -and (Test-Path -LiteralPath $target)) {
    # No registered installation: this is left over from an earlier removed or failed Orion install. Replace it; if a
    # closing process still holds it, use a fresh folder next to it (the installer removes the old one later).
    Remove-Item -LiteralPath $target -Recurse -Force -ErrorAction SilentlyContinue
    if (Test-Path -LiteralPath $target) { $target = Join-Path $orion ('Bootstrap-Installer-' + [guid]::NewGuid().ToString('N').Substring(0, 8)) }
}
function Get-OrionFile([string]$Uri, [string]$OutFile) {
    # Invoke-WebRequest shows nothing with progress off (and is very slow with it on): count the MB ourselves.
    $response = [Net.HttpWebRequest]::Create($Uri).GetResponse()
    try {
        $total = $response.ContentLength
        $in = $response.GetResponseStream()
        $out = [IO.File]::Create($OutFile)
        function Show([long]$done) {
            if ($total -gt 0) { Write-Host -NoNewline ("`r  {0:N1} / {1:N1} MB  ({2}%)   " -f ($done / 1MB), ($total / 1MB), [math]::Floor(100 * $done / $total)) }
            else { Write-Host -NoNewline ("`r  {0:N1} MB   " -f ($done / 1MB)) }
        }
        try {
            $buffer = New-Object byte[] 262144
            $done = 0
            $shown = -1
            while (($read = $in.Read($buffer, 0, $buffer.Length)) -gt 0) {
                $out.Write($buffer, 0, $read)
                $done += $read
                $step = [math]::Floor($done / 512KB)
                if ($step -ne $shown) { $shown = $step; Show $done }
            }
            Show $done  # The last piece: always ends on 100%.
        } finally { $out.Dispose(); $in.Dispose() }
    } finally { $response.Dispose() }
    Write-Host ''
}
try {
    Write-Host 'Orion kurulumu indiriliyor...'
    Get-OrionFile ($base + 'orion-bootstrap-windows-x64.zip') $bundle
    Write-Host 'Aciliyor...'
    New-Item -ItemType Directory -Force -Path $orion | Out-Null
    Expand-Archive -LiteralPath $bundle -DestinationPath $target
} finally {
    Remove-Item -LiteralPath $bundle -Force -ErrorAction SilentlyContinue
}
# No type given and no older switch: the setup window asks and installs.
$silent = $Mode -or $NoRouter -or $WithRouter -or $NoStart
if (-not $silent) {
    # The window runs on its own: this console is free (and can be closed) as soon as it opens. A throw-away
    # copy (adding to an existing install) removes itself when the window ends.
    $window = @('-B', '-m', 'scripts.release.setup_window', '--bundle', ('"' + $target + '"'))
    if ($existing) { $window += '--remove-bundle' }
    Start-Process -FilePath (Join-Path $target 'runtime/python/python.exe') -ArgumentList $window `
        -WorkingDirectory $target -WindowStyle Hidden
    Write-Host 'Orion kurulum penceresi aciliyor. Kurulumu o pencereden bitir; bu pencereyi kapatabilirsin.'
    return
}
try {
    if ($Mode) {
        $python = Join-Path $target 'runtime/python/python.exe'
        $install = @('-B', '-m', 'scripts.release.install_new', '--trusted-root', (Join-Path $target 'trusted-root.json'),
            '--release-source', (Join-Path $target 'release-source.json'), '--root', $orion,
            '--registry', (Join-Path $orion 'install.json'), '--mode', $Mode)
        if ($existing) { $install += '--add-missing' }
        if ($NoStartWithWindows) { $install += '--no-start-with-windows' }
        if ($NoAutoUpdate) { $install += '--no-auto-update' }
        if ($NoFirewall) { $install += '--no-firewall' }
        $elevated = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
        if (-not $existing -and -not $elevated) { $install += '--launch' }
        Push-Location $target
        try { & $python @install } finally { Pop-Location }
        if ($LASTEXITCODE -eq 3) { Write-Host 'Hicbir sey degistirilmedi.'; return }
        if ($LASTEXITCODE -ne 0) { throw "Orion installation failed (exit $LASTEXITCODE). Your data is kept; see $orion." }
        $shortcut = Join-Path ([Environment]::GetFolderPath('Desktop')) 'Orion.lnk'
        # explorer.exe always runs with the user's normal token, so the shortcut starts Orion de-elevated.
        if (-not $existing -and $elevated -and (Test-Path -LiteralPath $shortcut)) {
            Start-Process -FilePath 'explorer.exe' -ArgumentList ('"' + $shortcut + '"')
        }
        return
    }
    $components = @('hub', 'client')
    if ($existing) { if ($WithRouter) { $components += 'router' } }
    elseif (-not $NoRouter) { $components += 'router' }
    $arguments = @{ Components = $components; Root = $orion }
    if ($existing) { $arguments.AddMissing = $true }
    # A first start from an administrator window would run Hub/Router with an elevated token that the normal
    # desktop shortcut can no longer manage ("Process job access denied"). Install elevated, start de-elevated.
    $elevated = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    if ($elevated -and -not $NoStart -and -not $existing) {
        Write-Host 'Administrator window detected: Orion will be started through the desktop shortcut with normal rights.'
        $arguments.NoStart = $true
    }
    # Adding to an existing installation never starts or restarts anything the user is running.
    if ($NoStart -or $existing) { $arguments.NoStart = $true }
    if ($NoStartWithWindows) { $arguments.NoStartWithWindows = $true }
    if ($NoAutoUpdate) { $arguments.NoAutoUpdate = $true }
    if ($NoFirewall) { $arguments.NoFirewall = $true }
    if ($existing) { Write-Host 'Mevcut Orion kurulumu bulundu: yalnizca eksik bilesenler eklenecek, kurulu olanlara dokunulmaz.' }
    else { Write-Host 'Installing (about 320 MB download; this takes a few minutes)...' }
    & (Join-Path $target 'Install-Orion.ps1') @arguments
    if ($LASTEXITCODE -eq 3) { Write-Host 'Hicbir sey degistirilmedi.'; return }
    if ($LASTEXITCODE -ne 0) { throw "Orion installation failed (exit $LASTEXITCODE). Your data is kept; see $orion." }
} finally {
    if ($existing) { Remove-Item -LiteralPath $target -Recurse -Force -ErrorAction SilentlyContinue }
}
if ($existing) {
    Write-Host 'Eklendi. Orion''u masaustu kisayolundan acin.'
} elseif ($elevated -and -not $NoStart) {
    # explorer.exe always runs with the user's normal token, so the shortcut starts Orion de-elevated.
    $shortcut = Join-Path ([Environment]::GetFolderPath('Desktop')) 'Orion.lnk'
    if (Test-Path -LiteralPath $shortcut) { Start-Process -FilePath 'explorer.exe' -ArgumentList ('"' + $shortcut + '"') }
    else { Write-Host 'Open Orion from the Start menu or the desktop shortcut (not from an administrator window).' }
}
