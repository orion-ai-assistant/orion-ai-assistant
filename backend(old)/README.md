# Backend Service Management

Orion AI Assistant projesinin backend servislerini yönetmek için Docker Compose kullanıyoruz. Projede **Geliştirme (Development)** ve **Üretim (Production)** olmak üzere iki farklı yapılandırma bulunmaktadır.

## 📂 Klasör Yapısı
- **backend/**: Tüm servislerin bulunduğu ana dizin.
- **docker-compose.dev.yml**: Geliştirme ortamı yapılandırması (Hot-reload aktif).
- **docker-compose.prod.yml**: Üretim ortamı yapılandırması (Volume yok, kodlar imaj içinde).
- **scripts/**: Kolay başlatma/durdurma komut dosyaları.

## 🚀 1. Geliştirme Ortamı (Development)
Bu modda kodlar bilgisayarınızdan container içine `volume` ile bağlanır. Kodda yaptığınız değişiklikler "restart" yapmadan veya sadece servisi yeniden başlatarak anında yansır.

**Başlatmak için:**
```bash
# Otomatik Script ile:
.\scripts\start.ps1

# Veya Manuel Komut ile:
docker-compose -f docker-compose.dev.yml up -d
```

**Durdurmak için:**
```bash
.\scripts\stop.ps1
# veya
docker-compose -f docker-compose.dev.yml down
```

---

## 🏭 2. Üretim Ortamı (Production)
Bu modda kodlar container imajının içine gömülür (`COPY`). Sunucuda kodlar dışarıdan değiştirilemez, sabit ve güvenlidir. Veritabanı verileri yine kalıcıdır.

**ÖNEMLİ:** Production modunu çalıştırmadan önce `websocket/app` ve `admin_panel/app` klasörlerinizde kodlarınızın (`requirements.txt`, `main.py`, `package.json` vb.) hazır olduğundan emin olun.

**Başlatmak için (Build alarak):**
```bash
# -f ile prod dosyasını seçiyoruz ve --build ile imajları oluşturuyoruz
docker-compose -f docker-compose.prod.yml up -d --build
```

**Durdurmak için:**
```bash
docker-compose -f docker-compose.prod.yml down
```

## 🛠 Notlar
- **Veritabanları (ArangoDB, Redis):** Her iki modda da `./arangodb/data` ve `./redis/data` klasörlerini kullanarak verileri korur.
- **Langflow:** Geliştirme modunda tüm klasörü görür, Production modunda sadece veritabanını (`data` klasörünü) kalıcı tutar.

