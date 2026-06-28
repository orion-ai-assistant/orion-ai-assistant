# Linux Sunucu Kurulum ve Yönetim Rehberi (Production)

Bu rehber, projenin kaynak kodlarını sunucuya taşımadan, **Private (Özel)** Docker imajları kullanarak Linux sunucuda nasıl çalıştırılacağını anlatır.

---

## 🛠 1. Aşama: Docker Hub Hazırlığı (Gizlilik İçin Önemli)

Proje ticari olduğu için imajların başkaları tarafından indirilmesini engellemeliyiz.

1. [Docker Hub](https://hub.docker.com/) adresine gidin ve giriş yapın.
2. **Repositories** > **Create Repository** butonuna tıklayın.
3. Aşağıdaki 3 repo için tek tek kayıt oluşturun ve hepsinde **Private** seçeneğini işaretleyin:
   - `krstlcm/orion-langgraph`
   - `krstlcm/orion-websocket`
  - `krstlcm/orion-admin_panel`
   *(Not: Ücretsiz Docker Hub hesaplarında genellikle sadece 1 adet Private repo hakkı vardır. Eğer 3 tane Private repo açmanıza izin vermiyorsa, ya PRO üyelik almalısınız ya da imajları sunucuda build etme (kod taşıma) yöntemine geri dönmelisiniz. Bu rehber **PRO/Private** senaryosuna göre yazılmıştır.)*

---

## 💻 2. Aşama: Bilgisayarda (Windows) Yapılacaklar

Kodlarınızı paketleyip güvenli kasanıza (Docker Hub) göndereceğiz.

### 2.1. İmaj İsimlerini Hazırlama
Bilgisayarınızdaki `backend/docker-compose.prod.yml` dosyasındaki `image` satırlarının şu şekilde olduğundan emin olun (Sadece kontrol edin, değiştirmeyin):
- `krstlcm/orion-langgraph:prod`
- `krstlcm/orion-websocket:prod`
- `krstlcm/orion-admin_panel:prod`

### 2.2. Login, Build ve Push (Terminal)
PowerShell'i `backend` klasöründe açın ve sırasıyla çalıştırın:

```powershell
# 1. Docker Hub hesabına giriş yap (Private repo erişimi için şart)
docker login
# (Kullanıcı adı: krstlcm, ardından şifrenizi girin)

# 2. İmajları oluştur (Kodları paketle)
docker compose -f docker-compose.prod.yml build

# 3. İmajları gönder (İnternete yükle)
docker compose -f docker-compose.prod.yml push
```
*Bu işlem internet hızınıza bağlı olarak biraz sürebilir.*

---

## ☁️ 3. Aşama: Linux Sunucuda Yapılacaklar

Artık sunucuya kod götürmeyeceğiz. Sadece "git şu kutuları indir ve çalıştır" diyeceğiz.

### 3.1. Sunucuya Bağlanma ve Hazırlık
SSH ile sunucunuza bağlandıktan sonra:

```bash
# 1. Docker Kurulumu (Eğer kurulu değilse)
curl -fsSL https://get.docker.com -o get-docker.sh
sh get-docker.sh

# 2. Docker Hub'a Giriş (ÇOK ÖNEMLİ)
# Private repo olduğu için sunucunun indirme yetkisi olması lazım.
docker login
# (Kullanıcı adı: krstlcm ve şifrenizi girin. "Login Succeeded" yazmalı.)

# 3. Klasör oluşturma
mkdir orion-app
cd orion-app
```

### 3.2. Konfigürasyon Dosyalarını Oluşturma

Sunucuya sadece iki dosya lazım.

**A. .env Dosyası:**
```bash
nano .env
```
*Bilgisayarınızdaki `.env` içeriğini buraya yapıştırın ve kaydedin (CTRL+X -> Y -> Enter).*

**B. docker-compose.yml Dosyası:**
```bash
nano docker-compose.yml
```
*Aşağıdaki içeriği kopyalayıp yapıştırın. Bu dosya, `build` satırlarından temizlenmiş, `healthcheck` eklenmiş ve sunucuya uygun hale getirilmiştir.*

---

### 📋 Sunucu İçin Hazır `docker-compose.yml` İçeriği

```yaml
version: "3.9"

services:
  arangodb:
    image: "arangodb:3.12.7.1"
    container_name: arangodb
    restart: always
    environment:
      ARANGO_ROOT_PASSWORD: ${ARANGO_PASSWORD:-rootpassword}
    ports:
      - "8529:8529"
    volumes:
      - arango_data:/var/lib/arangodb3
    networks:
      - backend

  redis:
    image: "redis:8.4.0-alpine"
    container_name: redis
    restart: always
    ports:
      - "6379:6379"
    volumes:
      - redis_data:/data
    networks:
      - backend

  postgres:
    image: postgres:16-alpine
    container_name: postgres
    restart: always
    environment:
      POSTGRES_USER: postgres
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-postgrespassword}
      POSTGRES_DB: langgraph
    volumes:
      - postgres_data:/var/lib/postgresql/data
    # Healthcheck: Diğer servislerin veritabanını beklemesi için kritik
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U postgres -d langgraph"]
      interval: 5s
      timeout: 5s
      retries: 10
      start_period: 10s
    networks:
      - backend

  langgraph:
    # Build KISMI YOKTUR. Direkt sizin özel reponuzdan çeker.
    image: krstlcm/orion-langgraph:prod
    container_name: langgraph
    restart: always
    ports:
      - "8123:8123"
    environment:
      - PORT=8123
      - "LANGSERVE_GRAPHS={\"agent\": \"/deps/orion-agent/agent.py:graph\"}"
      - "DATABASE_URI=postgres://postgres:${POSTGRES_PASSWORD:-postgrespassword}@postgres:5432/langgraph?sslmode=disable"
      - "REDIS_URI=redis://redis:6379"
      - TAVILY_API_KEY=${TAVILY_API_KEY}
      - LANGSMITH_API_KEY=${LANGSMITH_API_KEY}
    depends_on:
      postgres:
        condition: service_healthy # Postgres tam hazır olana kadar bekle
      redis:
        condition: service_started
    networks:
      - backend

  websocket:
    image: krstlcm/orion-websocket:prod
    container_name: websocket
    restart: always
    ports:
      - "8000:8000"
    environment:
      - ARANGO_URL=http://arangodb:8529
      - REDIS_URL=redis://redis:6379
      - LANGGRAPH_URL=http://langgraph:8123
    depends_on:
      - arangodb
      - redis
      - langgraph
    networks:
      - backend

  admin_panel:
    image: krstlcm/orion-admin_panel:prod
    container_name: admin_panel
    restart: always
    ports:
      - "3000:3000"
    environment:
      - ARANGO_URL=http://arangodb:8529
      - LANGGRAPH_URL=http://langgraph:8123
      - WEBSOCKET_URL=http://websocket:8000
    depends_on:
      - arangodb
      - langgraph
      - websocket
    networks:
      - backend

networks:
  backend:
    driver: bridge

volumes:
  arango_data:
  redis_data:
  postgres_data:
```

### 3.3. Sistemi Başlatma

Dosyaları kaydettikten sonra (klasörde sadece `.env` ve `docker-compose.yml` olmalı), şu komutu çalıştırın:

```bash
docker compose up -d
```

Docker sırayla şunları yapacaktır:
1. `krstlcm` hesabına giriş yaptığınız için Private repolarınıza erişip imajları indirecek.
2. `postgres` servisini başlatacak ve `service_healthy` olmasını bekleyecek.
3. Diğer servisleri sırayla ayağa kaldıracak.

### 3.4. Güncelleme Yapmak İstediğinizde

Bilgisayarınızda (Windows) kodları değiştirdiğinizde:
1. Windows'ta: `docker compose -f docker-compose.prod.yml build` ve `push` yapın.
2. Linux sunucusunda sırasıyla:
   ```bash
   docker compose pull    # Yeni imajları indir
   docker compose up -d   # Sadece değişenleri yeniden başlat
   ```
