<#
.SYNOPSIS
    OPTIONAL, developer machines only: Microsoft Defender exclusions for Orion's build and release folders.

.DESCRIPTION
    Real-time scanning inspects every file a build or an acceptance run writes and then opens. A release
    round writes tens of thousands of files (Flutter/dart2js output, node_modules, three ~400 MB packages
    extracted into a fresh sandbox), so the scanner adds a lot of wall-clock time. The release tooling is
    already tuned to be fast without this script (parallel hashing and extraction overlap the scan
    latency); exclusions make it faster still.

    This affects ONLY the machine you run it on. It is never run automatically, the release tooling never
    calls it, and nothing here is shipped: end users' installations and their Defender settings are untouched.
    Excluded folders are not scanned on access, so only exclude folders whose contents you trust (your own
    checkouts and caches). Run it from an elevated PowerShell:

        powershell -ExecutionPolicy Bypass -File scripts\dev\setup-defender-exclusions.ps1           # add
        powershell -ExecutionPolicy Bypass -File scripts\dev\setup-defender-exclusions.ps1 -WhatIf   # show only
        powershell -ExecutionPolicy Bypass -File scripts\dev\setup-defender-exclusions.ps1 -Remove   # undo

.PARAMETER Remove
    Remove the exclusions this script adds (same list).
#>
[CmdletBinding(SupportsShouldProcess = $true)]
param([switch]$Remove)

$ErrorActionPreference = 'Stop'

$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
# In a git worktree (.claude\worktrees\<name>) the peer repositories sit next to the main checkout.
$common = (& git -C $repo rev-parse --path-format=absolute --git-common-dir 2>$null)
$main = if ($common) { Split-Path -Parent $common } else { $repo }
$peers = Split-Path -Parent $main

$pubCache = if ($env:PUB_CACHE) { $env:PUB_CACHE } else { Join-Path $env:LOCALAPPDATA 'Pub\Cache' }
$npmCache = (& npm config get cache 2>$null)
if (-not $npmCache) { $npmCache = Join-Path $env:LOCALAPPDATA 'npm-cache' }

$paths = @(
    $main                                              # Hub repository, its .tmp (build cache, candidates) and worktrees
    (Join-Path $peers 'orion-router')                  # Router repository (dashboard node_modules, .next, out)
    (Join-Path $peers 'orion-ai-assistant-flutter')    # Flutter repository (.dart_tool, build\web)
    (Join-Path $HOME '.orion-lifecycle-tests')         # acceptance sandboxes (scripts\release\release.py SCRATCH)
    $pubCache                                          # Dart/Flutter package cache
    $npmCache                                          # npm package cache
) | Where-Object { $_ } | ForEach-Object { [IO.Path]::GetFullPath($_.Trim()) } | Select-Object -Unique

$identity = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
if (-not $WhatIfPreference -and -not $identity.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Run this from an elevated (Administrator) PowerShell; nothing was changed.'
}

$action = if ($Remove) { 'scanned again' } else { 'excluded from real-time scanning' }
Write-Host "Developer machine only: these folders will be ${action}:"
$existing = @()
if (-not $WhatIfPreference) { $existing = @((Get-MpPreference).ExclusionPath) }
foreach ($path in $paths) {
    $present = $existing -contains $path
    if ($Remove) {
        if ($PSCmdlet.ShouldProcess($path, 'Remove Defender exclusion')) {
            if ($present) { Remove-MpPreference -ExclusionPath $path; Write-Host "  removed  $path" }
            else { Write-Host "  absent   $path" }
        }
    } elseif ($PSCmdlet.ShouldProcess($path, 'Add Defender exclusion')) {
        if ($present) { Write-Host "  present  $path" }
        else { Add-MpPreference -ExclusionPath $path; Write-Host "  added    $path" }
    }
}
Write-Host 'End-user installations are not affected. Undo with -Remove.'
