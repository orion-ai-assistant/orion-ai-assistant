# stop.ps1
# Backend servislerini durdurmak için PowerShell scripti

# Scriptin bulunduğu klasörden bir üst klasöre (backend/) geç
Set-Location "$PSScriptRoot/.."

Write-Host "🛑 Orion AI Assistant Backend durduruluyor..." -ForegroundColor Yellow

# Docker Compose ile servisleri durdur ve containerları sil (DEV modu varsayılan)
docker compose -f docker-compose.dev.yml down

if ($?) {
    Write-Host "`n✅ Tüm servisler durduruldu ve temizlendi." -ForegroundColor Green
} else {
    Write-Host "`n❌ Servisler durdurulurken bir hata oluştu." -ForegroundColor Red
}