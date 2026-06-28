# Docker & Docker Compose Best Practices: Orion Projeleri Rehberi

Bu kılavuz, Orion Router ve Orion Hub gibi projelerde Docker Compose, GitHub Container Registry (GHCR) entegrasyonu ve ortam değişkenlerinin yönetimi sırasında karşılaşılan kritik sorunları ve bunların çözümlerini açıklar.

---

## 1. Hazır Docker Image'larında Güncelleme Sorunu (`pull_policy: always`)

### Sorun
GitHub Actions veya benzeri bir CI/CD aracıyla otomatik derlenen imajları (örn: `ghcr.io/username/repo:latest`) Docker Compose ile kullanırken, yeni bir güncelleme yayınlandığında `docker compose up -d` komutu **yeni sürümü çekmez**. Docker, lokal önbellekte (cache) `latest` etiketli bir imaj gördüğü sürece internete gidip yenisi var mı diye kontrol etmez.

### Çözüm
Hazır imajı kullanan Docker Compose dosyalarında (`docker-compose.ghcr.yml` gibi) servis tanımının altına `pull_policy: always` eklenmelidir. Bu sayede her `up -d` komutunda Docker önce registry'deki imajın güncel olup olmadığını kontrol eder.

```yaml
services:
  app:
    image: ghcr.io/krstalacam/orion-hub:latest
    pull_policy: always  # <-- Her zaman yeni imajı sorgular ve çeker
    container_name: orion-hub
    # ...diğer ayarlar
```

---

## 2. Yerel Build Alırken Registry Yetki/Bulunamadı Uyarıları (`pull_policy: build`)

### Sorun
Hem yerel kaynak koddan derleme (`build: .`) yapıp hem de oluşturulan imaja isim vermek için `image: repo:latest` tanımını kullandığınızda, Docker Compose önce bu imajı uzak registry'den çekmeye çalışır. Imaj lokal bir isim olduğundan veya registry'de bulunmadığından terminalde şu uyarıyı alırsınız:
`! app Warning pull access denied for orion-hub, repository does not exist...`
Ardından Docker uyarının ardından yerel build işlemine geçer. Bu durum hata olmasa da gereksiz zaman kaybı ve uyarı kalabalığı yaratır.

### Çözüm
Yerel geliştirme ve derleme için kullanılan `docker-compose.yml` dosyasında `pull_policy: build` kuralı kullanılmalıdır. Bu kural Docker'a uzak registry'yi sorgulamamasını, doğrudan yerel koddan build etmesini söyler.

```yaml
services:
  app:
    image: orion-hub:latest
    build:
      context: .
    pull_policy: build   # <-- Uzak sunucudan çekmeyi denemez, doğrudan yerel build yapar
    container_name: orion-hub
    # ...diğer ayarlar
```

---

## 3. `.env` Dosyalarının Dockerfile Ortam Değişkenlerini Ezmesi (Path/Klasör Çakışmaları)

### Sorun
Dockerfile içinde Next.js statik dosyaları gibi derleme çıktılarını bağımsız bir klasöre koyup bunu ortam değişkeniyle tanımlayabilirsiniz:
`ENV DASHBOARD_OUT_DIR=/dashboard_out`

Ancak, `docker-compose.yml` içinde `.env` dosyasını konteynere bağladığınızda (`env_file: - .env` veya `env_file: .env`), host makinenizdeki yerel `.env` dosyası konteynerin içindeki `DASHBOARD_OUT_DIR` değerini ezer. Yerel `.env` dosyanızda `DASHBOARD_OUT_DIR=dashboard/out` yazıyorsa, Docker konteynerinin içi de bu değeri alır ve bu klasör konteyner içinde var olmadığından uygulama **404 Not Found** hatası verir.

### Çözüm
Kod seviyesinde (örneğin Python/Node.js tarafında konfigürasyon okunurken) akıllı bir denetim ve fallback mekanizması kurulmalıdır.

**Python Örneği (Orion Hub'da da uygulanabilir):**
```python
import os
import pathlib

_ROOT = pathlib.Path(__file__).parent.parent
_dash_val = os.getenv("DASHBOARD_OUT_DIR")

if _dash_val:
    if pathlib.Path(_dash_val).is_absolute():
        DASHBOARD_OUT_DIR = _dash_val
    else:
        # 1. Yerel geliştirme ortamındaki göreceli yolu kontrol et
        _rel_path = _ROOT / _dash_val
        if _rel_path.exists():
            DASHBOARD_OUT_DIR = str(_rel_path)
        # 2. Docker içindeysek ve yerel yol yoksa, Docker built-in klasörüne fallback yap
        elif os.path.exists("/dashboard_out"):
            DASHBOARD_OUT_DIR = "/dashboard_out"
        else:
            DASHBOARD_OUT_DIR = str(_rel_path)
else:
    # Hiç tanımlanmadıysa Docker varsayılanına veya yerel varsayılana düş
    DASHBOARD_OUT_DIR = "/dashboard_out" if os.path.exists("/dashboard_out") else str(_ROOT / "dashboard/out")
```

Bu yöntem sayesinde:
1. Lokal bilgisayarda native çalışırken host'taki `dashboard/out` klasörü kullanılır.
2. Docker compose ile ayağa kalktığında, `.env` dosyasından gelen `dashboard/out` klasörü lokalde olmadığından otomatik olarak Docker'ın kendi içindeki `/dashboard_out` klasörü seçilir ve **404 hataları önlenir**.
