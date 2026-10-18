# One-command Orion installer: downloads the signed installer bundle from this
# project's GitHub Release feed, then installs Hub, the web client and (from the Router's own feed) the Router.
#   PowerShell: irm https://raw.githubusercontent.com/orion-ai-assistant/orion-ai-assistant/main/get-orion.ps1 | iex
# The bundle carries the public trust root; every package is signature-verified
# by the installer before anything is extracted or run.
#
# Already installed? The same command ADDS what is missing and leaves everything else untouched
# (for example Hub + web client next to a Router-only installation; a Router with -WithRouter).
#   -NoRouter    fresh install without the Router
#   -WithRouter  existing installation: also add the Router (from the Router feed) when it is missing
param(
    [string]$Repo = 'orion-ai-assistant/orion-ai-assistant',
    [string]$Tag = 'feed',
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
try {
    Write-Host 'Downloading the Orion installer...'
    Invoke-WebRequest -UseBasicParsing -Uri ($base + 'orion-bootstrap-windows-x64.zip') -OutFile $bundle
    New-Item -ItemType Directory -Force -Path $orion | Out-Null
    Expand-Archive -LiteralPath $bundle -DestinationPath $target
} finally {
    Remove-Item -LiteralPath $bundle -Force -ErrorAction SilentlyContinue
}
try {
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
