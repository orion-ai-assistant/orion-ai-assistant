# start-prod.ps1
# Backend servislerini PRODUCTION modunda başlatmak için PowerShell scripti

param(
    # [string[]] yaparak birden fazla seçim yapabilmenin önünü açtık
    [ValidateSet("full", "api", "orion_agent", "admin_panel")]
    [string[]]$Mode = "full"
)

# Scriptin bulunduğu klasörden bir üst klasöre (backend/) geç
Set-Location "$PSScriptRoot/.."

Write-Host "🚀 Orion AI Assistant (PRODUCTION) başlatılıyor..." -ForegroundColor Cyan

# Eğer seçilenler arasında 'full' varsa eski usul her şeyi sıfırdan kurar
if ($Mode -contains "full") {
    Write-Host "🔄 Geliştirme ortamı varsa durduruluyor..." -ForegroundColor Yellow
    if (Test-Path "docker-compose.dev.yml") {
        docker compose -f docker-compose.dev.yml down 2>$null
    }

    Write-Host "🏗️ Tüm servisler derleniyor ve başlatılıyor..." -ForegroundColor Cyan
    docker compose -f docker-compose.prod.yml up -d --build --remove-orphans
}

# 'full' seçilmediyse, sadece kullanıcının girdiği servisleri günceller
# $Mode dizisi otomatik olarak aralarında boşluk bırakılarak Docker'a iletilir
if ($Mode -notcontains "full") {
    Write-Host "🏗️ Seçilen servisler derleniyor: $($Mode -join ', ')..." -ForegroundColor Cyan
    docker compose -f docker-compose.prod.yml up -d --build $Mode
}

if ($?) {
    Write-Host "`n✅ Production ortamı başarıyla ayağa kaldırıldı!" -ForegroundColor Green
    Write-Host "Güncellenen Servisler: $($Mode -join ', ')" -ForegroundColor Cyan
    
    # Otomatik İmaj Temizliği Bölümü
    Write-Host "`n🧹 Eski ve isimsiz (dangling) imajlar temizleniyor..." -ForegroundColor Yellow
    docker image prune -f
    Write-Host "✨ Gereksiz imajlar silindi, disk alanı açıldı!" -ForegroundColor Green

    Write-Host "ArangoDB: http://localhost:8529"
    Write-Host "API: http://localhost:8000"
    Write-Host "Admin Panel: http://localhost:3000"
    Write-Host "Agent Service: Docker internal only (http://orion_agent:8001)" -ForegroundColor Cyan
} else {
    Write-Host "`n❌ Production başlatılırken hata oluştu." -ForegroundColor Red
}