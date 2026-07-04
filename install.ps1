param (
    [string]$Mode = "docker"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "   Orion AI Assistant Native Installer    " -ForegroundColor Cyan
Write-Host "==========================================" -ForegroundColor Cyan

if ($Mode -notin @("local", "docker")) {
    $Mode = "docker"
}

Write-Host "Installation Mode: $Mode (Windows Only)" -ForegroundColor Yellow
$TargetFolder = Join-Path $env:LOCALAPPDATA "OrionAIAssistant"
$RepoUrl = "https://github.com/orion-ai-assistant/orion-ai-assistant.git"
Write-Host "Target Directory:  $TargetFolder`n" -ForegroundColor DarkGray

# 1. Requirement Checks
Write-Host "[1/3] Checking system requirements..."
$requiredCommands = @("git", "python")
foreach ($cmd in $requiredCommands) {
    try { Get-Command $cmd -ErrorAction Stop | Out-Null }
    catch { Write-Error "Error: '$cmd' not found! Please install it and try again."; exit 1 }
}
Write-Host "[OK] Requirements met." -ForegroundColor Green

# 2. Repo Clone or Update
Write-Host "`n[2/3] Setting up Orion AI Assistant directory..."
$GitPath = Join-Path $TargetFolder ".git"

if (-not (Test-Path $TargetFolder)) {
    Write-Host "[OK] Cloning fresh copy from GitHub..." -ForegroundColor Yellow
    git clone $RepoUrl $TargetFolder
    if ($LASTEXITCODE -ne 0) { Write-Host "[ERROR] Clone failed." -ForegroundColor Red; exit 1 }
} elseif (-not (Test-Path $GitPath)) {
    Write-Host "[!] Folder exists but has no git repository. Initializing in-place..." -ForegroundColor Yellow
    Set-Location -Path $TargetFolder
    git init | Out-Null
    $ErrorActionPreference = "Continue"
    $remotes = git remote 2>$null
    $ErrorActionPreference = "Stop"
    if ($remotes -contains "origin") { git remote set-url origin $RepoUrl } else { git remote add origin $RepoUrl }
    git fetch origin main
    git reset --hard origin/main
    Write-Host "[OK] Repository initialized and updated." -ForegroundColor Green
} else {
    Write-Host "[OK] Directory exists, forcing updates from GitHub..." -ForegroundColor Yellow
    Set-Location -Path $TargetFolder
    $ErrorActionPreference = "Continue"
    $remotes = git remote 2>$null
    $ErrorActionPreference = "Stop"
    if ($remotes -contains "origin") { git remote set-url origin $RepoUrl } else { git remote add origin $RepoUrl }
    git fetch origin main
    git reset --hard origin/main
}

Set-Location -Path $TargetFolder
Write-Host ""

# 3. Call Unified Python Setup
Write-Host "[3/4] Handing over to Unified Python Setup..." -ForegroundColor Yellow
python orion.py setup $Mode

if ($LASTEXITCODE -ne 0) {
    Write-Host "`n[ERROR] Setup failed." -ForegroundColor Red
    exit 1
}

# 4. Start Installer automatically
Write-Host "`n[4/4] Starting Orion Installer..." -ForegroundColor Yellow
python orion.py installer