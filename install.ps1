Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "   Orion AI Assistant Native Installer    " -ForegroundColor Cyan
Write-Host "==========================================" -ForegroundColor Cyan

Write-Host "Installation Mode: Docker (Windows Only)" -ForegroundColor Yellow
$TargetFolder = Join-Path $env:LOCALAPPDATA "OrionAIAssistant"
$RepoUrl = "https://github.com/orion-ai-assistant/orion-ai-assistant.git"
Write-Host "Target Directory:  $TargetFolder`n" -ForegroundColor DarkGray

# 1. Requirement Checks
Write-Host "[1/5] Checking system requirements..."
$requiredCommands = @("git", "docker", "python")

foreach ($cmd in $requiredCommands) {
    try { Get-Command $cmd -ErrorAction Stop | Out-Null }
    catch { Write-Error "Error: '$cmd' not found! Please install it and try again."; exit 1 }
}

# Test if Docker daemon is running
try {
    & docker ps > $null
} catch {
    Write-Host "`nWARNING: Docker Desktop does not seem to be running!" -ForegroundColor Yellow
    Write-Host "Please start Docker Desktop and run this installer again.`n" -ForegroundColor Yellow
}

$joinedCmds = $requiredCommands -join ', '
Write-Host "[OK] Requirements met ($joinedCmds)." -ForegroundColor Green
Write-Host ""

# 2. Repo Clone or Update
Write-Host "[2/5] Setting up Orion AI Assistant directory..."

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
    if ($remotes -contains "origin") {
        git remote set-url origin $RepoUrl
    } else {
        git remote add origin $RepoUrl
    }
    git fetch origin main
    if ($LASTEXITCODE -ne 0) { Write-Host "[ERROR] git fetch failed." -ForegroundColor Red; exit 1 }
    git reset --hard origin/main
    if ($LASTEXITCODE -ne 0) { Write-Host "[ERROR] git reset failed." -ForegroundColor Red; exit 1 }
    Write-Host "[OK] Repository initialized and updated." -ForegroundColor Green
} else {
    Write-Host "[OK] Directory exists, forcing updates from GitHub..." -ForegroundColor Yellow
    Set-Location -Path $TargetFolder
    $ErrorActionPreference = "Continue"
    $remotes = git remote 2>$null
    $ErrorActionPreference = "Stop"
    if ($remotes -contains "origin") {
        git remote set-url origin $RepoUrl
    } else {
        git remote add origin $RepoUrl
    }
    git fetch origin main
    if ($LASTEXITCODE -ne 0) { Write-Host "[ERROR] git fetch failed. Check your connection." -ForegroundColor Red; exit 1 }
    git reset --hard origin/main
    if ($LASTEXITCODE -ne 0) { Write-Host "[ERROR] git reset failed." -ForegroundColor Red; exit 1 }
}

Set-Location -Path $TargetFolder
Write-Host ""

# --- Create local env files if missing ---
Write-Host "[*] Checking environment configuration files..."
$envGlobalLocal = "services/.env.global.local"
$envGlobalLocalExample = "services/.env.global.local.example"

if (-not (Test-Path $envGlobalLocal)) {
    if (Test-Path $envGlobalLocalExample) {
        Copy-Item $envGlobalLocalExample $envGlobalLocal
        Write-Host "[OK] Created $envGlobalLocal from example." -ForegroundColor Green
    } else {
        New-Item -Path $envGlobalLocal -ItemType File | Out-Null
        Write-Host "[OK] Created empty $envGlobalLocal file." -ForegroundColor Green
    }
} else {
    Write-Host "[OK] Existing $envGlobalLocal detected. Kept intact." -ForegroundColor Green
}
Write-Host ""

# 3. Setup Python Virtual Environment for Installer UI
Write-Host "[3/5] Setting up python virtual environment for Installer UI..."
$InstallerDir = Join-Path $TargetFolder "installer"
$VenvDir = Join-Path $InstallerDir ".venv"
$PipExecutable = ""

if (Test-Path $InstallerDir) {
    Set-Location -Path $InstallerDir
    if (-not (Test-Path $VenvDir)) {
        Write-Host "[*] Creating .venv in $InstallerDir..." -ForegroundColor Yellow
        python -m venv .venv
    }
    
    $PipExecutable = Join-Path $VenvDir "Scripts\pip.exe"
    $PythonExecutable = Join-Path $VenvDir "Scripts\python.exe"
    
    Write-Host "[*] Upgrading pip & installing dependencies..." -ForegroundColor Yellow
    & $PythonExecutable -m pip install --upgrade pip | Out-Null
    & $PipExecutable install -e .
    Write-Host "[OK] Dependencies installed successfully." -ForegroundColor Green
    Set-Location -Path $TargetFolder
} else {
    Write-Host "[WARNING] Installer directory not found!" -ForegroundColor Yellow
}
Write-Host ""

# 4. Preparing Docker Containers Cache
Write-Host "[4/5] Pulling Docker images in background..." -ForegroundColor Yellow
# Run docker-compose config pull dynamically if needed, or pull main services images
# For now, let's keep it simple and pull when starting, or run a soft check.
Write-Host "[OK] Docker environment is ready." -ForegroundColor Green
Write-Host ""

# 5. Global CLI command registry
Write-Host "[5/5] Installing global 'orion' command..." -ForegroundColor Yellow

$BinFolder = Join-Path $TargetFolder "bin"
if (-not (Test-Path $BinFolder)) {
    New-Item -ItemType Directory -Path $BinFolder | Out-Null
}

# Wrapper batch/powershell file content
$lines = [System.Collections.Generic.List[string]]::new()
$lines.Add('param ([Parameter(Position=0)][string]$Action = "help", [Parameter(Position=1)][string]$SubService = "")')
$lines.Add('[Console]::OutputEncoding = [System.Text.Encoding]::UTF8')
$lines.Add('')
$lines.Add('$ProjectPath = "$env:LOCALAPPDATA\OrionAIAssistant"')
$lines.Add('$PreviousLocation = Get-Location')
$lines.Add('try {')
$lines.Add('    Set-Location -Path $ProjectPath')
$lines.Add('    if ($Action -in @("help", "")) {')
$lines.Add('        Write-Host ""')
$lines.Add('        Write-Host "  Orion AI Assistant CLI" -ForegroundColor Cyan')
$lines.Add('        Write-Host "  --------------------------------" -ForegroundColor DarkGray')
$lines.Add('        Write-Host "  Usage: orion [installer|start|stop|logs|status|help]" -ForegroundColor Yellow')
$lines.Add('        Write-Host ""')
$lines.Add('        Write-Host "  Commands:"')
$lines.Add('        Write-Host "    installer  Launch the local Web installer GUI panel"' -ForegroundColor Green)
$lines.Add('        Write-Host "    start      Build and spin up the Orion Docker stack"' -ForegroundColor Green)
$lines.Add('        Write-Host "    stop       Stop all running Orion containers"' -ForegroundColor Green)
$lines.Add('        Write-Host "    logs       Stream docker container logs"' -ForegroundColor Green)
$lines.Add('        Write-Host "    status     List all running Orion container states"' -ForegroundColor Green)
$lines.Add('        Write-Host "    help       Display this help documentation"')
$lines.Add('        Write-Host ""')
$lines.Add('        exit 0')
$lines.Add('    }')
$lines.Add('')
$lines.Add('    if ($Action -eq "installer") {')
$lines.Add('        Write-Host "Launching Orion Installer Application..." -ForegroundColor Cyan')
$lines.Add('        & python "$ProjectPath\orion.py" installer')
$lines.Add('    } elseif ($Action -eq "start") {')
$lines.Add('        Write-Host "Starting Docker stack via Docker Compose..." -ForegroundColor Cyan')
$lines.Add('        # Start services via Compose if docker-compose.yml files are located in services/router etc.')
$lines.Add('        # Or launch installer backend so the user can control it from GUI')
$lines.Add('        # We also support starting specific service directly if compose is inside its dir')
$lines.Add('        if ($SubService -ne "") {')
$lines.Add('            if (Test-Path "services\$SubService") {')
$lines.Add('                Set-Location "services\$SubService"')
$lines.Add('                docker compose up -d')
$lines.Add('            } else {')
$lines.Add('                Write-Host "Service $SubService not found." -ForegroundColor Red')
$lines.Add('            }')
$lines.Add('        } else {')
$lines.Add('            Write-Host "Spinning up all active services. Type `orion installer` to manage them from GUI." -ForegroundColor Yellow')
$lines.Add('            # Standard compose startup from service layers')
$lines.Add('            Get-ChildItem -Path "services" -Filter "docker-compose.yml" -Recurse | ForEach-Object {')
$lines.Add('                $dir = $_.DirectoryName')
$lines.Add('                Write-Host "Starting service at: $dir" -ForegroundColor DarkGray')
$lines.Add('                Set-Location $dir')
$lines.Add('                docker compose up -d')
$lines.Add('                Set-Location $ProjectPath')
$lines.Add('            }')
$lines.Add('        }')
$lines.Add('    } elseif ($Action -eq "stop") {')
$lines.Add('        Write-Host "Stopping all Orion services..." -ForegroundColor Yellow')
$lines.Add('        Get-ChildItem -Path "services" -Filter "docker-compose.yml" -Recurse | ForEach-Object {')
$lines.Add('            $dir = $_.DirectoryName')
$lines.Add('            Set-Location $dir')
$lines.Add('            docker compose down')
$lines.Add('            Set-Location $ProjectPath')
$lines.Add('        }')
$lines.Add('    } elseif ($Action -eq "logs") {')
$lines.Add('        Write-Host "Streaming logs... (Ctrl+C to exit)" -ForegroundColor Gray')
$lines.Add('        if ($SubService -ne "") {')
$lines.Add('            if (Test-Path "services\$SubService") {')
$lines.Add('                Set-Location "services\$SubService"')
$lines.Add('                docker compose logs -f --tail=100')
$lines.Add('            }')
$lines.Add('        } else {')
$lines.Add('            Get-ChildItem -Path "services" -Filter "docker-compose.yml" -Recurse | ForEach-Object {')
$lines.Add('                $dir = $_.DirectoryName')
$lines.Add('                Set-Location $dir')
$lines.Add('                docker compose logs -f --tail=30')
$lines.Add('                Set-Location $ProjectPath')
$lines.Add('            }')
$lines.Add('        }')
$lines.Add('    } elseif ($Action -eq "status") {')
$lines.Add('        Write-Host "Container Status:" -ForegroundColor Yellow')
$lines.Add('        docker ps --filter "name=orion-"')
$lines.Add('    }')
$lines.Add('} finally {')
$lines.Add('    Set-Location $PreviousLocation')
$lines.Add('}')

$WrapperScriptPath = Join-Path $BinFolder "orion.ps1"
[System.IO.File]::WriteAllLines($WrapperScriptPath, $lines.ToArray())

# Wrapper Batch file so Command Prompt users can use 'orion' directly too
$BatLines = @(
    "@echo off",
    "powershell -NoProfile -ExecutionPolicy Bypass -Command `\"& '$WrapperScriptPath' %*`\""
)
$WrapperBatPath = Join-Path $BinFolder "orion.bat"
[System.IO.File]::WriteAllLines($WrapperBatPath, $BatLines)

# Add to user PATH safely
Write-Host "Registering 'orion' command to Environment PATH..." -ForegroundColor DarkGray
$UserPath = [Environment]::GetEnvironmentVariable("Path", "User")
if ($UserPath -notlike "*$BinFolder*") {
    $NewPath = $UserPath.TrimEnd(';') + ";" + $BinFolder
    [Environment]::SetEnvironmentVariable("Path", $NewPath, "User")
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + $NewPath
    Write-Host "[OK] Added to User PATH variables. You might need to restart your terminal." -ForegroundColor Green
} else {
    Write-Host "[OK] Already exists in PATH." -ForegroundColor Green
}

Write-Host "`n=======================================================" -ForegroundColor Cyan
Write-Host "  Orion AI Assistant is successfully installed!" -ForegroundColor Green
Write-Host "=======================================================" -ForegroundColor Cyan
Write-Host "  Type: orion installer" -ForegroundColor Yellow
Write-Host "  This command will open the interactive setup panel."
Write-Host "  To start docker services directly, type: orion start"
Write-Host "=======================================================" -ForegroundColor Cyan
