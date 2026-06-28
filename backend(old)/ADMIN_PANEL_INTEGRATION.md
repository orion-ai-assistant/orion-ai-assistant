# 🎉 Admin Panel ↔ API Entegrasyonu Tamamlandı!

## 🔄 Mimari Değişiklik

**Eskiden**: Admin panel → JSON dosyası (sadece kendi okuyordu)
**Şimdi**: Admin panel → HTTP API → Ana API in-memory config

## ✅ Nasıl Çalışıyor

```
┌─────────────────┐         ┌──────────────┐         ┌────────────┐
│  Admin Panel    │  HTTP   │   Ana API    │ Memory  │   Agent    │
│  (Port 3000)    ├────────►│  (Port 8000) ├────────►│  (Graph)   │
│                 │         │              │         │            │
└─────────────────┘         └──────────────┘         └────────────┘
     localhost                   :8000                  reads config
```

### 1️⃣ Admin Panel Toggle Yapar
```javascript
// Dashboard'da thinking mode toggle
POST http://localhost:3000/api/config/thinking/toggle?enabled=false
```

### 2️⃣ Admin Panel → Ana API'ye İstek Gönderir
```python
# admin_panel/app/services/config_service.py
async def toggle_thinking(enabled, admin_name):
    response = await httpx.post(
        f"{API_BACKEND_URL}/admin/config/thinking/toggle",
        params={"enabled": enabled}
    )
```

### 3️⃣ Ana API Config'i Günceller (In-Memory)
```python
# api/app/services/config_service.py
class ConfigManager:
    _config: AIConfig  # Singleton in-memory state
    
    def toggle_thinking(enabled):
        self._config.thinking_enabled = enabled
        self._save_to_file()  # Persistence (optional)
```

### 4️⃣ Agent Gerçek Zamanlı Okur
```python
# agent/graph.py
config = get_config_manager().get_config()  # Her çağrıda güncel config okunur
# thinking/model/tool değişiklikleri yeni isteklerde otomatik etkili olur
```

## 🐳 Docker'da Çalışma

### docker-compose.yml örneği:
```yaml
services:
  api:
    build: ./backend/api
    ports:
      - "8000:8000"
    networks:
      - orion-net
  
  admin_panel:
    build: ./backend/admin_panel
    ports:
      - "3000:3000"
    environment:
      - API_BACKEND_URL=http://api:8000  # Container adı
    networks:
      - orion-net
    # Sadece localhost'tan erişilebilir (production'da)
    # profiles: ["dev"]  # Sadece development'ta çalışır

networks:
  orion-net:
    driver: bridge
```

## 🔧 Eklenen Dosyalar

### Ana API (Port 8000)
- `api/app/models/__init__.py` - Config modelleri
- `api/app/services/config_service.py` - ConfigManager (singleton, in-memory)
- `api/app/routes/admin_routes.py` - `/admin/config/*` endpoints
- `api/app/main.py` - Admin router eklendi

### Admin Panel (Port 3000)
- `admin_panel/app/services/config_service.py` - HTTP client (API'yi çağırır)
- `admin_panel/app/routes/config_routes.py` - Async endpoints

### Agent
- `agent/graph.py` - `_get_config()` fonksiyonu ekledim

## 📡 Yeni API Endpoints (Port 8000)

```
GET    /admin/config/                     # Config'i al
PUT    /admin/config/                     # Config'i güncelle
POST   /admin/config/reset                # Default'a dön
POST   /admin/config/thinking/toggle      # Thinking mode aç/kapa
POST   /admin/config/model/{name}/default # Default model seç
POST   /admin/config/model/{name}/toggle  # Model aktif/pasif
POST   /admin/config/tool/{name}/toggle   # Tool aktif/pasif
```

## 🚀 Kullanım

### 1. Ana API'yi Başlat
```bash
cd backend
python -m api.app.main
# http://localhost:8000 çalışıyor
```

### 2. Admin Panel'i Başlat
```bash
cd backend/admin_panel/app
python main.py
# http://localhost:3000 çalışıyor
```

### 3. Dashboard'da Ayarları Değiştir
- Thinking mode toggle → Gerçek zamanlı ana API'de değişir
- Model enable/disable → Agent yeni modeli kullanır
- Tool enable/disable → Agent tool listesi güncellenir

## 🎯 Persistence

Config şurada saklanır:
```
backend/api/app/data/ai_config.json
```

- Admin panel ayar değiştirince → API günceller → JSON'a yazar
- API restart olunca → JSON'dan okur
- Container silininceJSON volume'a mount edilebilir:

```yaml
volumes:
  - ./data:/app/api/app/data
```

## ✨ Özellikler

✅ **Gerçek zamanlı güncelleme** - API restart gerektirmez
✅ **Docker uyumlu** - Container'lar arası HTTP üzerinden haberleşme
✅ **Singleton pattern** - Her zaman tek config instance
✅ **Persistence** - Ayarlar JSON dosyasına kaydedilir
✅ **Hata yönetimi** - API erişilemezse fallback config
## 🧪 Test

```bash
# Ana API'de config endpoint'ini test et
curl http://localhost:8000/admin/config/
```

## 🔍 Troubleshooting

### Admin panel API'ye erişemiyor
```python
# admin_panel/app/settings.py kontrol et
API_BACKEND_URL = "http://localhost:8000"  # Local
# API_BACKEND_URL = "http://api:8000"  # Docker
```

### Config JSON oluşmuyor
```python
# Klasör yok mu?
mkdir -p backend/api/app/data
```

### Agent config'i okumuyor
```python
# API context'inde çalıştığına emin ol
# agent graph API process içinde import edilmeli
```

---

**Artık admin panelden yapılan her değişiklik gerçek zamanlı olarak agent'a yansıyor! 🚀**
