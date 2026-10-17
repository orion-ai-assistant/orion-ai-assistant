# One-command Orion installer: downloads the signed installer bundle from this
# project's GitHub Release feed, then installs Hub, Router and the web client.
#   PowerShell: irm https://raw.githubusercontent.com/orion-ai-assistant/orion-ai-assistant/main/get-orion.ps1 | iex
# The bundle carries the public trust root; every package is signature-verified
# by the installer before anything is extracted or run.
param(
    [string]$Repo = 'orion-ai-assistant/orion-ai-assistant',
    [string]$Tag = 'feed',
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
if (Test-Path (Join-Path $orion 'install.json')) {
    throw "Orion is already installed in $orion. Use the Orion desktop shortcut; updates run from its Settings."
}
$base = "https://github.com/$Repo/releases/download/$Tag/"
$bundle = Join-Path $env:TEMP ('orion-bootstrap-' + [guid]::NewGuid().ToString('N') + '.zip')
$target = Join-Path $orion 'Bootstrap-Installer'
if (Test-Path $target) { throw "$target already exists from an incomplete install. Remove that folder and retry." }
try {
    Write-Host 'Downloading the Orion installer...'
    Invoke-WebRequest -UseBasicParsing -Uri ($base + 'orion-bootstrap-windows-x64.zip') -OutFile $bundle
    New-Item -ItemType Directory -Force -Path $orion | Out-Null
    Expand-Archive -LiteralPath $bundle -DestinationPath $target
} finally {
    Remove-Item -LiteralPath $bundle -Force -ErrorAction SilentlyContinue
}
$source = Get-Content -LiteralPath (Join-Path $target 'release-source.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$names = @($source.targets.hub, $source.targets.router, $source.targets.client) | Where-Object { $_ }
$arguments = @{ MetadataUrl = $source.metadata_url; TargetsUrl = $source.targets_url; Targets = $names; Root = $orion }
if ($NoStart) { $arguments.NoStart = $true }
if ($NoStartWithWindows) { $arguments.NoStartWithWindows = $true }
if ($NoAutoUpdate) { $arguments.NoAutoUpdate = $true }
if ($NoFirewall) { $arguments.NoFirewall = $true }
Write-Host 'Installing (about 320 MB download; this takes a few minutes)...'
& (Join-Path $target 'Install-Orion.ps1') @arguments
if ($LASTEXITCODE -ne 0) { throw "Orion installation failed (exit $LASTEXITCODE). Your data is kept; see $orion." }
