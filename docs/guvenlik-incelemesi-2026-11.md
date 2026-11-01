# Güvenlik incelemesi — 1 Kasım 2026

Hub, Hub'ın ağa açılan kapıları, yerel model servisleri (TTS, STT, LLM, embed) ve Router (`../orion-router`) kod üzerinden incelendi. Bulguların bir kısmı canlı olarak (yalnızca loopback'te, zararsız dosyalarla) doğrulandı.

## Ağa açık kapılar (değişiklikten sonra)

| Port | Kim dinler | Kime açık | Koruma |
| --- | --- | --- | --- |
| 8443 | Hub, telefon | Ev ağı (güvenlik duvarı: Özel profil, yerel alt ağ) | Pinned TLS, eşleştirilmiş cihaz |
| 9443 | Router | Ev ağı | TLS, Hub anahtarı / hesap anahtarı / yönetici şifresi |
| 5353/UDP | Hub, Router (mDNS) | Ev ağı | Yalnızca duyuru; paylaşımlı port |
| 8909, 8910, 80, 8090, 20128, 5433, 5445, 6379 | Hub, Flutter, Router, veritabanları | Yalnızca bu PC | Host/Origin denetimi, oturum |
| 8810, 9009, 8085, 8086 | TTS, STT, LLM, embed | Yalnızca bu PC (`ORION_SERVICES_BIND_HOST`) | Tarayıcı istekleri reddedilir |

## Düzeltilenler

**Router**
- **Kritik — kimlik doğrulamasız dosya okuma.** `GET /dashboard/{path}` yolu klasör dışına çıkabiliyordu (`..%2F`, `C:/...`). Ev ağındaki bir cihaz 9443'ten `.env`, `persistent/encryption.key` ve TLS anahtarı dahil her dosyayı okuyabiliyordu. Yol artık dashboard klasörünün içinde kalmak zorunda (`api/admin.py`).
- **Kritik — varsayılan yönetici şifresi ağdan.** `is-default-password` şifreyi herkese döndürüyordu ve `orion` 9443'te kabul ediliyordu. Artık şifre yalnızca bu PC'deki panelde (20128) gösteriliyor. Varsayılan şifre ağdan ne admin ne `/v1` için kabul ediliyor; önce bu PC'den değiştirilmesi gerekiyor (`core/security.py`, `core/dependencies.py`).
- **CORS `*` ve kimlik bilgisi.** Her site 9443'e istek gönderip yanıtı okuyabiliyordu. CORS artık yalnızca geliştirme sunucusu (3001) için ve yalnızca dev modunda açık (`main.py`).
- **Şifre denetimi olay döngüsünü kilitliyordu.** PBKDF2 artık ayrı bir thread'de çalışıyor, karşılaştırma sabit zamanlı.

**Yerel model servisleri**
- **TTS/STT/LLM/embed `0.0.0.0` dinliyordu, kimlik doğrulama yoktu.** TTS ev ağından kullanılabiliyordu: ses üretme, ses klonu yükleme, motoru yeniden yükleme. STT'de `CORS *` vardı; her site son kaydı (`/v1/audio/debug/last`) okuyabiliyor ve WebSocket ile ses yazdırabiliyordu.
  - Hepsi artık tek bir ayardan yönetiliyor: `ORION_SERVICES_BIND_HOST` (varsayılan `127.0.0.1`, `services/.env.global`).
  - TTS/STT'ye `BrowserGuard` eklendi (`services/shared/service_access.py`): başka sitelerin istekleri ve DNS rebinding reddediliyor. STT'nin CORS'u kaldırıldı.
  - LLM/embed (`llama-server`) için `--cors-origins` yalnızca kendi adresine izin veriyor, `--no-slots` ayarıyla da konuşma içeriğini gösteren uç nokta kapalı.
  - Docker'da portlar host'ta aynı ayardaki adreste yayınlanıyor.
- **TTS ses adı ile dosya yolu açığı.** Ses adı doğrudan dosya yoluna ekleniyordu; `../` ile klasör dışındaki `.pt` dosyaları okunabiliyor, yazılabiliyor ve silinebiliyordu. Bu dosyalar `pickle` ile açıldığı için kod çalıştırma riski vardı. Ad artık harf, rakam, boşluk ve `. _ -` ile sınırlı (`engines.valid_voice_name`).

**Hub**
- **Tarayıcı aracı yerel adreslere gidebiliyordu.** Yapay zekâ, kullanıcının oturum açık tarayıcısında `127.0.0.1:8910/panel`, Router paneli veya ev ağındaki cihazları açabiliyordu. Artık yalnızca genel internet adresleri açılabiliyor; DNS cevabı da kontrol ediliyor (`worker/tools/web_browser/tools.py`). Tam liste: [yapay-zeka-tarayici-sinirlari.md](../services/hub/docs/yapay-zeka-tarayici-sinirlari.md).
- **Yeni sahip şifresi en az 8 karakter.** Varsayılan `orion` kullanıcı kararıyla kalıyor; değiştirmek kullanıcıya bırakıldı. Eşleşmiş ve hesabı olan bir telefon bu şifreyle QR üretebilir. Bu bilinçli bir kolaylık: Flutter önce `orion`'u dener.
- **Süresi çok uzun ayarlanmış eski tip oturum anahtarları.** JWT gizli anahtarını ele geçiren biri süresiz anahtar üretebiliyordu. Oturumsuz anahtarlar en fazla 24 saat geçerli.
- **Yönetim sayfaları çerçeve içinde açılabiliyordu.** Artık `X-Frame-Options: DENY` ve `frame-ancestors 'none'` gönderiliyor.

## Açık kalanlar (önem sırasıyla)

1. **Varsayılan şifreler `orion` kalıyor (kullanıcı kararı).** Router bunu yalnızca kendi bilgisayarındaki panelde (20128) kabul eder; ağdan (9443) panele girmek için önce şifre değiştirilmeli. Panel bunu telefonda açıkça yazıyor. Hub, eşleşmiş telefonlardan kabul eder.
2. **Bu PC'deki her program hesap açıp tarayıcıyı yönetebilir.** Planlanan çözüm (kullanıcının fikri, ileride): **ana kullanıcı / misafir** ayrımı. Misafir hesaplar PC'yi kontrol eden işlevleri çağıramaz: tarayıcı yönetimi, PC sesi, güncelleme, Windows ile başlatma. Tarayıcı bağlanırken hangi hesapların kontrol edebileceği seçilebilir. Şimdilik yapılmadı.
   Durum: 8910'da davetsiz yerel kayıt mümkün ve hesap hemen aktif oluyor. Her hesap PC'deki asıl tarayıcıyı (oturumlarıyla birlikte) yönetebiliyor. Tek kullanıcılı bir PC'de risk düşük; başka bir Windows kullanıcısı veya localhost'a erişen bir tarayıcı eklentisi için yüksek.
3. **Kopyalanan eşleştirme bağlantısı ev ağında ele geçirilebilir.** Sertifika kendinden imzalı olduğu için telefon her seferinde uyarı veriyor; araya giren biri kendi sertifikasını aynı uyarıyla gösterebilir. QR okutmak bu riski taşımaz. Öneri: bağlantıyı yalnızca gerektiğinde kullan; daha kısa geçerlilik.
4. **Kaynak klasöründe gizli dosyalar herkese açık (bu PC).** D: sürücüsünden gelen izinler yüzünden aşağıdaki dosyalar diğer Windows kullanıcılarınca okunabilir ve değiştirilebilir:
   - `files/security/` (Hub TLS anahtarı, sahip şifresi özeti)
   - `services/.env.global.local` (JWT anahtarı)
   - Router `persistent/` ve `tls/`
   Kurulu sürümün klasörleri korumalı. Tek kullanıcılı PC'de pratik risk düşük.
5. ~~Hub (5445) ve Router (5433) Postgres `trust` ile açılıyordu~~ — **düzeltildi** (aşağıdaki bölüm).
6. **Router'da eski düz metin sağlayıcı anahtarı satırı.** `router_configs` içinde şifrelenmiş havuza taşındıktan sonra silinmiyor.
7. **LLM/embed'de Host denetimi yok.** `llama-server`'a ara katman eklenemiyor. Tarayıcı yanıtı okuyamıyor, ama DNS rebinding ile model kullanılabilir. Tam çözüm `--api-key` ile Router'ın anahtar göndermesi (iki depoda değişiklik).
8. **Daha düşük önemdekiler:**
   - Hesap şifresi en az 1 karakter. Hesap başına günde yaklaşık 14 bin deneme yapılabiliyor ve giriş süresinden kullanıcı adının var olup olmadığı anlaşılabiliyor.
   - Kullanıcı adları doğrulanmıyor; `lock:alice` gibi bir adla Redis anahtarı çakışması ve DoS mümkün.
   - Devre dışı bırakılan hesabın açık soketleri kapanmıyor.
   - Yüklemelerde boyut/kota sınırı yok.
   - Redis şifresiz (yalnızca loopback).
   - Eşleştirme ve sahip girişi DoS'a açık; sahip giriş sınırı 127.0.0.1'e göre tutuluyor.
   - 8090 klasör listeliyor.
   - Router playground'da hata metni HTML olarak basılıyor (dashboard yeniden derlenmeli).
   - Bir Hub sınırsız hesap açabiliyor.
9. **Güvenlik duvarı (elle, yönetici PowerShell):**
   - Orion kuralından 8911'i çıkar.
   - Eski "Orion Hub HTTP shortcuts" kuralını sil.
   - `.orion-lifecycle-tests` altındaki test kurulumlarından kalan yaklaşık 250 `python.exe` iznini sil. Bunlar her porta, ortak ağlar dahil izin veriyor.

## Veritabanı erişimi (düzeltildi)

Önceden Hub (5445) ve Router (5433) PostgreSQL'i `initdb --auth=trust` ile açılıyordu. Bu PC'deki her program veya Windows hesabı şifresiz süper kullanıcı olarak girebiliyordu. Bu yolla bütün sohbetleri okuyabilir ve `COPY ... FROM PROGRAM` ile Orion'un hesabıyla komut çalıştırabilirdi. Router'ın şifresi her kurulumda aynıydı ve koda gömülüydü (`router_pass`); Hub'ınki `orion` idi.

Şimdi (`services/shared/postgres_auth.py`; Router'da aynısı `core/postgres_auth.py`):
- **Yalnızca şifreyle giriş:** `pg_hba.conf` yalnızca bu PC'den (`127.0.0.1`, `::1`) ve yalnızca SCRAM şifresiyle girişe izin verir. Sunucu her zaman `listen_addresses=localhost` ile başlar.
- **Her kurulumun kendi rastgele şifreleri:** biri süper kullanıcı (yalnızca kurulum ve hazır olma kontrolü için), biri uygulama için. Saklandıkları yerler:
  - Hub: `<veri>/services/.env.global.local` (`POSTGRES_PASSWORD`, `POSTGRES_ADMIN_PASSWORD`)
  - Router: `<veri>/persistent/postgres.json`
  
  İkisi de kurulu sürümde yalnızca kullanıcının okuyabildiği klasörde. Şifreler bir kez üretilir; güncellemeler bunlara dokunmaz.
- **Uygulama kullanıcısı süper kullanıcı değil** (Hub: `orion`, Router: yeni `router_app`). Kendi veritabanının ve tablolarının sahibi, ama program çalıştıramaz (`COPY ... FROM PROGRAM`), sunucu dosyası okuyamaz (`pg_read_file`) ve kendine yetki veremez. Router'ın mevcut tabloları ilk açılışta `router_app`'e devredilir.
- **Kilitlenme yok:** Şifre dosyası kaybolur ya da elle değişirse `prepare()` sunucuyu bir kez kurulum erişimiyle (yine yalnızca bu PC'den) açar, `secure()` yeni şifreleri uygular. Geçiş başarısız olursa eski açık erişim yerinde kalır, uygulama çalışmaya devam eder ve günlüğe uyarı yazılır.
- **Doğrulanan:**
  - Gerçek PostgreSQL ile testler: `scripts/lifecycle/test_postgres_auth.py`. Kapsam: geçişte veri korunuyor; şifresiz ve yanlış şifreli girişler reddediliyor; uygulama kullanıcısı program çalıştıramıyor ve dosya okuyamıyor; şifre dosyası kaybolunca erişim geri kuruluyor.
  - Bu PC'deki gerçek Hub ve Router veritabanlarının kopyalarında geçiş başarılı. Router'ın 24 tablosu `router_app`'e geçti ve Router şema kurulumu bu kullanıcıyla çalıştı.
- **Sınırlar:**
  - Geçişten sonra Router eski bir sürüme geri döndürülürse bağlanamaz, çünkü eski kod sabit şifreyi kullanır.
  - Kaynak klasöründe (D:) şifre dosyaları diğer Windows hesaplarınca okunabilir (açık konu 4).
  - Docker'daki veritabanı dışarı açık değildir ama varsayılan şifreyle kurulur.
  - Router geliştirme modu (`bin/dev.py`) değişmedi.

## Doğrulama

- Tüm değişiklikler için testler eklendi:
  - `scripts/lifecycle/test_service_access.py`
  - Router `dev/test-orion-router/test_network_exposure.py`
  - Hub `test_browser_tools.py`, `test_remote_pairing.py`, `test_account_sessions.py`, `test_panel_mount.py`
- TTS ses adı denetimi TTS ortamında elle denendi: traversal engellendi, mevcut sesler bulunuyor.
- `llama-server` (CPU ve CUDA, aynı sürüm) yeni bayrakları kabul ediyor.
- Çalışan servisler eski kodu kullanıyor. Değişiklikler Router, TTS ve STT yeniden başlatılınca geçerli olur.
