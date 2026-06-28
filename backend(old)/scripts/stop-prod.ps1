# stop-prod.ps1
# Production servislerini durdurmak için

Set-Location "$PSScriptRoot/.."

Write-Host "🛑 Orion AI Assistant (PRODUCTION) durduruluyor..." -ForegroundColor Yellow

docker compose -f docker-compose.prod.yml down

if ($?) {
    Write-Host "`n✅ Production servisleri durduruldu." -ForegroundColor Green
} else {
    Write-Host "`n❌ Durdurma sırasında hata oluştu." -ForegroundColor Red
}
