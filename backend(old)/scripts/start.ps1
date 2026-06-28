# start.ps1
# Backend servislerini DEVELOPMENT modunda başlatmak için PowerShell scripti
# .\scripts\start.ps1 -Mode api

param(
    # [string[]] yapılarak birden fazla seçim yapabilmenin önü açıldı
    [ValidateSet("full", "api", "orion_agent", "admin_panel")]
    [string[]]$Mode = "full"
)

# Scriptin bulunduğu klasörden bir üst klasöre (backend/) geç
Set-Location "$PSScriptRoot/.."

Write-Host "🚀 Orion AI Assistant (DEVELOPMENT) başlatılıyor..." -ForegroundColor Green

# Eğer seçilenler arasında 'full' varsa eski usul her şeyi sıfırdan kurar
if ($Mode -contains "full") {
    Write-Host "🔄 Production ortamı varsa çakışmayı önlemek için durduruluyor..." -ForegroundColor Yellow
    if (Test-Path "docker-compose.prod.yml") {
        docker compose -f docker-compose.prod.yml down 2>$null
    }

    Write-Host "🏗️ Tüm servisler (DEV) derleniyor ve başlatılıyor..." -ForegroundColor Cyan
    docker compose -f docker-compose.dev.yml up -d --build --remove-orphans
}
else {
    # 'full' seçilmediyse, sadece kullanıcının girdiği servisleri çalıştır
    Write-Host "🏗️ Seçilen servisler (DEV) derleniyor: $($Mode -join ', ')..." -ForegroundColor Cyan
    docker compose -f docker-compose.dev.yml up -d --build $Mode
}

if ($?) {
    Write-Host "`n✅ Development ortamı başarıyla ayağa kaldırıldı!" -ForegroundColor Green
    Write-Host "Çalışan/Güncellenen Servisler: $($Mode -join ', ')" -ForegroundColor Cyan
    
    # Otomatik İmaj Temizliği Bölümü
    Write-Host "`n🧹 Eski ve isimsiz (dangling) imajlar temizleniyor..." -ForegroundColor Yellow
    docker image prune -f
    Write-Host "✨ Gereksiz imajlar silindi, disk alanı açıldı!" -ForegroundColor Green

    Write-Host "`n🌐 Erişim Adresleri:"
    Write-Host "ArangoDB:      http://localhost:8529"
    Write-Host "API:           http://localhost:8000"
    Write-Host "Admin Panel:   http://localhost:3000"
    Write-Host "Agent Service: Docker internal only (http://orion_agent:8001)" -ForegroundColor Cyan
} else {
    Write-Host "`n❌ Development başlatılırken hata oluştu." -ForegroundColor Red
}