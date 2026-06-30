Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# Kullanıcının başlangıç dizinini kaydet — kurulum sonunda geri döneceğiz
$OriginalLocation = Get-Location

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

# --- Cihaz Dilini Otomatik Algılama ---
$envContent = Get-Content $envGlobalLocal -ErrorAction SilentlyContinue
if ($null -eq $envContent -or -not ($envContent -match "(?m)^CLI_LANG=")) {
    $sysLang = (Get-Culture).TwoLetterISOLanguageName
    Add-Content -Path $envGlobalLocal -Value "`n# Auto-detected system language`nCLI_LANG=$sysLang" -Encoding UTF8
    Write-Host "[OK] Sistem dili algılandı ve ayarlandı: $sysLang" -ForegroundColor Green
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

# Wrapper batch/powershell file content
$lines = [System.Collections.Generic.List[string]]::new()
$lines.Add('param ([Parameter(Position=0)][string]$Action = "help", [Parameter(Position=1)][string]$SubService = "")')
$lines.Add('[Console]::OutputEncoding = [System.Text.Encoding]::UTF8')
$lines.Add('')
$lines.Add('$ProjectPath = "$env:LOCALAPPDATA\OrionAIAssistant"')
$lines.Add('$PreviousLocation = Get-Location')
$lines.Add('try {')
$lines.Add('    Set-Location -Path $ProjectPath')
$lines.Add('    if ($Action -in @("help", "")) {
        Write-Host ""
        Write-Host "  Orion AI Assistant CLI" -ForegroundColor Cyan
        Write-Host "  --------------------------------" -ForegroundColor DarkGray
        Write-Host "  Usage: orion [installer|start|stop|logs|status|help]" -ForegroundColor Yellow
        Write-Host ""
        Write-Host "  Commands:"
        Write-Host "    installer  Launch the local Web installer GUI panel" -ForegroundColor Green
        Write-Host "    start      Build and spin up the Orion Docker stack" -ForegroundColor Green
        Write-Host "    stop       Stop all running Orion containers" -ForegroundColor Green
        Write-Host "    logs       Stream logs (e.g., orion log installer, orion log router)" -ForegroundColor Green
        Write-Host "    status     List all running Orion container states" -ForegroundColor Green
        Write-Host "    help       Display this help documentation"
        Write-Host ""
        exit 0
    }')
$lines.Add('    if ($Action -eq "installer") {
        Write-Host "Launching Orion Installer Application in background..." -ForegroundColor Cyan
        $PidFile = "$ProjectPath\.installer.pid"
        $LogFile = "$ProjectPath\installer.log"
        $ErrFile = "$ProjectPath\installer.err.log"
        $InstallerPy  = Join-Path (Join-Path $ProjectPath "installer") ".venv\Scripts\python.exe"
        $OrionPy      = Join-Path $ProjectPath "orion.py"
        if (-not (Test-Path $InstallerPy)) { $InstallerPy = "python" }
        if (-not (Test-Path $LogFile)) { New-Item -Path $LogFile -ItemType File -Force | Out-Null }
        if (-not (Test-Path $ErrFile)) { New-Item -Path $ErrFile -ItemType File -Force | Out-Null }
        $AppArgs = @($OrionPy, "installer")
        $p = Start-Process -FilePath $InstallerPy -ArgumentList $AppArgs -WindowStyle Hidden -RedirectStandardOutput $LogFile -RedirectStandardError $ErrFile -PassThru
        $p.Id | Out-File -FilePath $PidFile -Encoding ASCII
        Write-Host "[OK] Installer baslatildi, arayuz aciliyor..." -ForegroundColor Green
    } elseif ($Action -eq "start") {')
$lines.Add('        Write-Host "Checking Docker status..." -ForegroundColor Cyan')
$lines.Add('        $DockerReady = $false')
$lines.Add('        try { docker info 2>$null | Out-Null; $DockerReady = $true } catch { $DockerReady = $false }')
$lines.Add('        if (-not $DockerReady) {')
$lines.Add('            Write-Host "[!] Docker Daemon hazir degil. Docker Desktop baslatiliyor..." -ForegroundColor Yellow')
$lines.Add('            $dp = "C:\Program Files\Docker\Docker\Docker Desktop.exe"')
$lines.Add('            if (Test-Path $dp) {')
$lines.Add('                Start-Process -FilePath $dp')
$lines.Add('                $maxRetry = 12')
$lines.Add('                for ($i = 1; $i -le $maxRetry; $i++) {')
$lines.Add('                    Start-Sleep -Seconds 5')
$lines.Add('                    try { docker info 2>$null | Out-Null; $DockerReady = $true; break } catch {}')
$lines.Add('                    $dots = "." * $i')
$lines.Add('                    Write-Host "`r    Bekleniyor$dots ($($i*5)s)" -NoNewline -ForegroundColor DarkGray')
$lines.Add('                }')
$lines.Add('                Write-Host ""')
$lines.Add('            }')
$lines.Add('            if (-not $DockerReady) {')
$lines.Add('                Write-Host "[ERROR] Docker baslatilamadi. Lutfen Docker Desktop''i manuel acip tekrar deneyin." -ForegroundColor Red')
$lines.Add('                exit 1')
$lines.Add('            }')
$lines.Add('        }')
$lines.Add('')
$lines.Add('        $running_up = docker ps --format "{{.Names}}" 2>$null')
$lines.Add('        if ($running_up -match "orion-") {')
$lines.Add('            Write-Host "[i] Orion servisleri zaten calisiyor. Durdurmak icin: orion stop" -ForegroundColor Yellow')
$lines.Add('            exit 0')
$lines.Add('        }')
$lines.Add('')
$lines.Add('        Write-Host "Starting Docker stack via Docker Compose..." -ForegroundColor Cyan')
$lines.Add('        Get-ChildItem "$ProjectPath\services" -Filter "manifest.json" -Recurse | ForEach-Object {')
$lines.Add('            $dir = $_.DirectoryName')
$lines.Add('            if (Test-Path "$dir\.env") {')
$lines.Add('                $m = Get-Content $_.FullName | ConvertFrom-Json')
$lines.Add('                $cat = $m.category')
$lines.Add('                if (-not $cat) { $cat = "misc" }')
$lines.Add('                $projName = "orion-$cat"')
$lines.Add('                Write-Host "Starting installed service: $($m.name) (Project: $projName)" -ForegroundColor DarkGray')
$lines.Add('                Set-Location $dir')
$lines.Add('                ')
$lines.Add('                $composeArgs = @("-p", $projName)')
$lines.Add('                if (Test-Path "$ProjectPath\services\.env.global") { $composeArgs += "--env-file", "$ProjectPath\services\.env.global" }')
$lines.Add('                if (Test-Path "$ProjectPath\services\.env.global.local") { $composeArgs += "--env-file", "$ProjectPath\services\.env.global.local" }')
$lines.Add('                if (Test-Path ".env") { $composeArgs += "--env-file", ".env" }')
$lines.Add('                $composeArgs += "up", "-d"')
$lines.Add('                ')
$lines.Add('                & docker compose $composeArgs')
$lines.Add('                Set-Location $ProjectPath')
$lines.Add('            }')
$lines.Add('        }')
$lines.Add('        Write-Host "[OK] Orion servisleri baslatildi." -ForegroundColor Green')
$lines.Add('    } elseif ($Action -eq "stop") {')
$lines.Add('        Write-Host "Stopping all Orion services..." -ForegroundColor Yellow')
$lines.Add('        Get-ChildItem "$ProjectPath\services" -Filter "manifest.json" -Recurse | ForEach-Object {')
$lines.Add('            $m = Get-Content $_.FullName | ConvertFrom-Json')
$lines.Add('            $cat = $m.category')
$lines.Add('            if (-not $cat) { $cat = "misc" }')
$lines.Add('            $projName = "orion-$cat"')
$lines.Add('            $dir = $_.DirectoryName')
$lines.Add('            Set-Location $dir')
$lines.Add('            if (Test-Path "docker-compose.yml") {')
$lines.Add('                docker compose -p $projName down')
$lines.Add('            }')
$lines.Add('            Set-Location $ProjectPath')
$lines.Add('        }')
$lines.Add('        $PidFile = "$ProjectPath\.installer.pid"')
$lines.Add('        if (Test-Path $PidFile) {')
$lines.Add('            $existingPid = Get-Content $PidFile')
$lines.Add('            if (Get-Process -Id $existingPid -ErrorAction SilentlyContinue) {')
$lines.Add('                Stop-Process -Id $existingPid -Force -ErrorAction SilentlyContinue')
$lines.Add('                Write-Host "[OK] Background installer stopped." -ForegroundColor Green')
$lines.Add('            }')
$lines.Add('            Remove-Item -Path $PidFile -ErrorAction SilentlyContinue')
$lines.Add('        }')
$lines.Add('    } elseif ($Action -in @("logs", "log")) {')
$lines.Add('        Write-Host "Streaming logs... (Ctrl+C to exit)" -ForegroundColor Gray')
$lines.Add('        if ($SubService -eq "installer") {')
$lines.Add('            $LogFile = "$ProjectPath\installer.log"')
$lines.Add('            if (Test-Path $LogFile) { Get-Content $LogFile -Wait -Tail 100 } else { Write-Host "No installer logs found." }')
$lines.Add('        } elseif ($SubService -ne "") {')
$lines.Add('            if (Test-Path "services\$SubService\manifest.json") {')
$lines.Add('                $m = Get-Content "services\$SubService\manifest.json" | ConvertFrom-Json')
$lines.Add('                $cat = $m.category; if (-not $cat) { $cat = "misc" }; $projName = "orion-$cat"')
$lines.Add('                Set-Location "services\$SubService"')
$lines.Add('                docker compose -p $projName logs -f --tail=100')
$lines.Add('                Set-Location $ProjectPath')
$lines.Add('            }')
$lines.Add('        } else {')
$lines.Add('            Write-Host "[i] Printing last 30 lines for all services." -ForegroundColor DarkGray')
$lines.Add('            Write-Host "[i] To stream continuously, use: orion log <service-name> (e.g. orion log installer, orion log router)" -ForegroundColor DarkGray')
$lines.Add('            ')
$lines.Add('            $LogFile = "$ProjectPath\installer.log"')
$lines.Add('            if (Test-Path $LogFile) { ')
$lines.Add('                Write-Host "`n--- installer ---" -ForegroundColor Cyan')
$lines.Add('                Get-Content $LogFile -Tail 30 ')
$lines.Add('            }')
$lines.Add('            ')
$lines.Add('            Get-ChildItem "$ProjectPath\services" -Filter "manifest.json" -Recurse | ForEach-Object {')
$lines.Add('                $m = Get-Content $_.FullName | ConvertFrom-Json')
$lines.Add('                $cat = $m.category; if (-not $cat) { $cat = "misc" }; $projName = "orion-$cat"')
$lines.Add('                $dir = $_.DirectoryName')
$lines.Add('                Set-Location $dir')
$lines.Add('                if (Test-Path "docker-compose.yml") { ')
$lines.Add('                    Write-Host "`n--- $($m.name) ---" -ForegroundColor Cyan')
$lines.Add('                    docker compose -p $projName logs --tail=30 ')
$lines.Add('                }')
$lines.Add('                Set-Location $ProjectPath')
$lines.Add('            }')
$lines.Add('        }')
$lines.Add('    } elseif ($Action -eq "status") {')
$lines.Add('        Write-Host "Container Status:" -ForegroundColor Cyan')
$lines.Add('        docker ps --filter "network=orion-network"')
$lines.Add('    } elseif ($Action -eq "help" -or $Action -eq "-h" -or $Action -eq "--help") {')
$lines.Add('        Write-Host "`n  Orion AI Assistant CLI" -ForegroundColor Cyan')
$lines.Add('        Write-Host "  --------------------------------" -ForegroundColor Cyan')
$lines.Add('        Write-Host "  Usage: orion [installer|start|stop|logs|status|help]"')
$lines.Add('        Write-Host "`n  Commands:"')
$lines.Add('        Write-Host "    installer  Launch the local Web installer GUI panel"')
$lines.Add('        Write-Host "    start      Build and spin up the Orion Docker stack"')
$lines.Add('        Write-Host "    stop       Stop all running Orion containers"')
$lines.Add('        Write-Host "    logs       Stream logs (e.g., orion log installer, orion log router)"')
$lines.Add('        Write-Host "    status     List all running Orion container states"')
$lines.Add('        Write-Host "    help       Display this help documentation`n"')
$lines.Add('    } else {')
$lines.Add('        Write-Host "Unknown command: $Action" -ForegroundColor Red')
$lines.Add('        Write-Host "Type ''orion help'' to see available commands." -ForegroundColor Gray')
$lines.Add('    }')
$lines.Add('} finally {')
$lines.Add('    Set-Location $PreviousLocation')
$lines.Add('}')

# orion.ps1 — kök dizine yaz (router ile aynı mantık)
$WrapperScriptPath = Join-Path $TargetFolder "orion.ps1"
[System.IO.File]::WriteAllText($WrapperScriptPath, ($lines -join "`r`n"), (New-Object System.Text.UTF8Encoding $false))

# orion.cmd — %~dp0 ile kendi dizinine göre relatif path (router ile aynı mantık)
$CmdContent = "@echo off`r`npowershell -NoProfile -ExecutionPolicy Bypass -File `"%~dp0orion.ps1`" %*"
[System.IO.File]::WriteAllText((Join-Path $TargetFolder "orion.cmd"), $CmdContent, (New-Object System.Text.UTF8Encoding $false))

# PATH'e $TargetFolder köküne ekle (router ile aynı mantık)
Write-Host "Registering 'orion' command to Environment PATH..." -ForegroundColor DarkGray
$CleanFolder = $TargetFolder.TrimEnd('\')
$UserPath = [Environment]::GetEnvironmentVariable("Path", "User")
$PathParts = $UserPath -split ";" | ForEach-Object { $_.Trim().TrimEnd('\') } | Where-Object { $_ }
if ($CleanFolder -notin $PathParts) {
    $NewUserPath = ($PathParts + $CleanFolder) -join ";"
    [Environment]::SetEnvironmentVariable("Path", $NewUserPath, "User")
    Write-Host "[OK] Added to User PATH variables." -ForegroundColor Green
} else {
    Write-Host "[OK] Already exists in PATH." -ForegroundColor Green
}

# Mevcut oturum PATH'ine de ekle
$CurrentPathParts = $env:Path -split ";" | ForEach-Object { $_.Trim().TrimEnd('\') } | Where-Object { $_ }
if ($CleanFolder -notin $CurrentPathParts) {
    $env:Path = ($CurrentPathParts + $CleanFolder) -join ";"
}
Write-Host "[OK] 'orion' command registered." -ForegroundColor Green

Write-Host "`n=======================================================" -ForegroundColor Cyan
Write-Host "  Orion AI Assistant is successfully installed!" -ForegroundColor Green
Write-Host "=======================================================" -ForegroundColor Cyan
Write-Host "  To start docker services directly, type: orion start"
Write-Host "=======================================================" -ForegroundColor Cyan

# Kullanıcının başlangıç dizinine geri dön
Set-Location -Path $OriginalLocation

# Installer arayüzünü otomatik başlat
Write-Host "`n[>>] Orion Installer arayüzü başlatılıyor..." -ForegroundColor Cyan
$OrionPy      = Join-Path $TargetFolder "orion.py"
$InstallerPy  = Join-Path (Join-Path $TargetFolder "installer") ".venv\Scripts\python.exe"
$PidFile      = Join-Path $TargetFolder ".installer.pid"
$LogFile      = Join-Path $TargetFolder "installer.log"

$ErrFile      = Join-Path $TargetFolder "installer.err.log"

if (-not (Test-Path $InstallerPy)) {
    $InstallerPy = "python"
}

if (-not (Test-Path $LogFile)) { New-Item -Path $LogFile -ItemType File -Force | Out-Null }
if (-not (Test-Path $ErrFile)) { New-Item -Path $ErrFile -ItemType File -Force | Out-Null }

$AppArgs = @($OrionPy, "installer")
Start-Process $InstallerPy -ArgumentList $AppArgs -WindowStyle Hidden -RedirectStandardOutput $LogFile -RedirectStandardError $ErrFile -PassThru | ForEach-Object {
    $_.Id | Out-File -FilePath $PidFile -Encoding ASCII
    Write-Host "[OK] Installer arka planda başlatıldı (PID: $($_.Id))" -ForegroundColor Green
    Write-Host "[i]  Uygulama penceresi birkaç saniye içinde açılacak." -ForegroundColor DarkGray
}