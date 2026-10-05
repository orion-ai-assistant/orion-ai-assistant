# Faz 6 — üç repository için ortak güvenli bağlantı sözleşmesi

4 Ekim 2026. Kaynak, kullanıcının bu çalışma için paylaştığı **Orion Faz 6 —
Güvenli Yerel Bağlantı, Pairing ve TLS** planıdır. Önceki devir belgelerindeki
yaprak sertifika DER pini kararı bu plan tarafından değiştirilmiştir.

## Ortak transport ve kimlik

- Kalıcı ECDSA P-256 özel anahtar; self-signed sertifika aynı anahtarla yenilenir.
- `fp`: DER SubjectPublicKeyInfo verisinin SHA-256 özeti, 64 küçük harf hex karakter.
  Sertifika fingerprint'i değildir.
- Kalıcı servis UUID'si ve kayıtlı SPKI pini güven kimliğidir. mDNS yalnızca adres
  bulur; keşif sonuçları kayıtlı pini değiştiremez.
- Hub mobil HTTPS varsayılan portu `8443`; Router HTTPS varsayılan portu `9443`.
  Hub web ve yönetim girişleri de HTTPS kullanır; HTTP dinleyicileri kaldırılmıştır.
- Mobil DNS-SD servisi `_orion-mobile._tcp.local.`; Router güvenli servisi
  `_orion-router-tls._tcp.local.`. SRV gerçek HTTPS portunu taşır. TXT alanları
  `id`, `name`, `v=1`, `scheme=https`.
- HTTP'ye device token veya mobil hesaba ait JWT gönderilmesi yetki sağlamaz.
  Forwarded başlıkları TLS kanıtı değildir.
- Pin doğrulaması, sırların gönderileceği bağlantıda ve HTTP/WSS isteğinden önce
  yapılır. Redirect ve HTTP fallback güvenli istemcilerde kapalıdır.

## Hub–Flutter eşleştirme

Güvenilir PC ekranından alınan QR/metin tam olarak bu alanları taşır:

```json
{"v":1,"id":"<hub-uuid>","url":"https://<adres>:8443","fp":"<spki-sha256-hex>","code":"<8-karakter>","name":"<pc-adı>"}
```

`POST /api/v1/pair/claim` yalnızca mobil HTTPS listener'da kullanılabilir.
İstek `code`, `device_name`, isteğe bağlı `platform` ve geriye uyumluluk için
isteğe bağlı `device_id` içerir. Sunucu gerçek cihaz kimliğini kendisi üretir.
Başarılı cevap `hub_id`, `hub_name`, `name`, `device_id`, `device_token`, `fp`
alanlarını içerir. `hub_name` ve `name` aynı gösterim adının alias'larıdır.
Flutter cevaptaki `hub_id` ve `fp` değerlerini QR kimliğiyle karşılaştırdıktan
sonra UUID/pin/token/ad bilgilerini güvenli depoya kaydeder.

Kod kriptografik rastgele, 8 base32 karakter ve 120 saniye ömürlüdür. Başarılı
claim atomik olarak kodu tüketir ve yalnızca hash'i depolanan 256 bit cihaz
token'ını bir kez döndürür. Yanlış kod gerçek oturumu tüketmez. Geçersiz,
tüketilmiş veya süresi dolmuş kod `403 pair_code_invalid`; hız sınırı `429`.

Eşleştirmeden sonra cihaz token'ı `X-Device-Token` başlığındadır. Kullanıcı
hesabı girişi ayrı kalır; mevcut `Authorization: Bearer <hesap-JWT>` kullanılır.
Mobil JWT eşleştirilmiş cihaza bağlanır. Device token yönetici yetkisi vermez.
Kullanıcı çıkışı eşleştirmeyi korur; cihazı unutma/iptal bağlantıları kapatır.
Mobil `DELETE /api/v1/devices/self` yalnızca başlıktaki token'ın cihazını iptal
eder ve `{ok:true}` döndürür. Tüm cihazların yönetimi PC'nin özel yönetim
arayüzünde kalır. Flutter sunucuya ulaşılamasa da yerel kaydı silebilir; bu
durumda sunucudaki cihazın PC üzerinden ayrıca kaldırılması gerektiğini gösterir.

## Hub–Router güveni

Router `GET /api/v1/tls/identity` ile HTTPS üzerinde
`{v:1,id,name,fp}` döndürür. Bu endpoint ve mDNS güvenilir pin kaynağı değildir;
ilk pin Router'ın yerel yönetim ekranından doğrulanır. Hub yönetim ekranı
Router UUID'sini ve doğrulanmış pini kaydeder. Sonraki REST, worker, sohbet,
model/ses katalogları, STT/TTS, dosya ve WebSocket çağrıları ortak pinli
transport'tan geçer. Adres değişimi pini değiştirmez, anahtar değişimi bağlantıyı
reddettirir ve açık yeniden eşleştirme gerektirir.

## Entegrasyon doğrulaması

Repository testlerine ek olarak `tests/test_phase6_cross_repository.py` gerçek
TLS soketleri üzerinden Router kimlik üreticisini, Hub pinli transport'unu ve
Flutter'ın üretim pinli istemcisini birlikte sınar. Test verileri geçici
dizinlerdedir; çalışan kullanıcı servisleri ve kalıcı kimlikleri kullanılmaz.
4 Ekim 2026 son doğrulaması: **13 testin tamamı geçti**, atlanan test yok.

| Canlı kontrol | Sonuç |
| --- | --- |
| Flutter REST doğru pin / yanlış pin; yanlış pinde sıfır HTTP isteği | Geçti |
| Flutter ilk claim: yanlış pinde kod gönderilmez ve oturum tüketilmez | Geçti |
| Flutter WSS aynı pin politikası; yanlış pinde upgrade/token gönderilmez | Geçti |
| Flutter aynı anahtarla sertifika yenilemesi ve değişmiş anahtarı reddetme | Geçti |
| Flutter self-revoke isteği yalnızca kendi cihazını iptal eder | Geçti |
| Hub claim yarışı: iki istekte tek başarı; yanlış kod doğru oturumu korur | Geçti |
| Hub kod süresi ve yalnızca hash depolama | Geçti |
| HTTP cihaz token enjeksiyonu ve sahte TLS başlıkları | Geçti |
| Cihaz iptali açık SSE/WSS'yi kapatır, yeni isteği reddeder | Geçti |
| Hub → Router REST/WSS ortak SPKI transport'u | Geçti |
| Yanlış Router pini ve aynı UUID ile anahtar değişimi: sıfır istek | Geçti |
| Router sertifika yenilemesi ve yeni IP/port, aynı UUID/pin | Geçti |
| Router güvenini kaldırma açık akışı kapatır | Geçti |

Hub kökünde PowerShell ile tekrar çalıştırma (repository ve runtime yollarını
kendi kurulumunuza uyarlayın):

```powershell
$env:PYTHONPATH = "$PWD/services/hub/src;$PWD"
$env:ORION_ROUTER_REPO = '<Router repository>'
$env:ORION_FLUTTER_REPO = '<Flutter repository>'
$env:ORION_DART_EXE = '<Flutter SDK>/bin/cache/dart-sdk/bin/dart.exe'
python -m unittest discover -s services/hub/tests -p test_phase6_cross_repository.py -v
```

Python ortamında Hub proje bağımlılıkları, Flutter uygulamasında `pub get`
tamamlanmış olmalıdır. Normal Hub testleri kardeş repository yolları verilmezse
opsiyonel canlı Router/Dart kontrollerini atlar; bu son çalıştırmada üç yol da
verildi. Fixture sunucuları gerçek uvicorn TLS soketleri ve üretim Hub pairing
middleware/endpoint'lerini kullanır. Router üretim kimlik üreticisi ve Flutter
üretim pinli transport'u doğrudan çağrılır; gerçek PostgreSQL/Redis veya model
backend'leri bu testin kapsamında değildir.

Adres değişimi testi loopback IP/port değişimi üzerinden doğrulandı. Fiziksel
telefonda QR/kamera, Android/iOS güvenli depo ve gerçek LAN multicast/firewall
koşulları ayrıca cihaz testi gerektirir. Docker bridge TCP port yayınlamak
multicast mDNS'yi LAN'a taşımaz; host advertiser/uygun host networking veya
reflector gerekir. Flutter Web tarayıcı API'leri explicit SPKI denetimi sunmadığı
için güvenli mobil eşleştirme o platformda kapalıdır; native istemci kullanılır.

## Repository doğrulamaları ve uygulama belgeleri

- Hub: ilgili regresyon setinde **84 test ve 24 subtest geçti**; Faz 6'ya özel
  **15 test geçti**. Endpoint, storage ve kurulum bilgileri `phase6-security.md`.
- Router: **54 Python testi geçti**, yeni TLS kontrollerinin sayısı **10**;
  dashboard Next.js üretim derlemesi geçti. Router belgesi
  `orion-router/docs/secure-router-tls.md`.
- Flutter: tam test seti kontrolünde **99 test geçti**, çalışma öncesinde de bulunan
  **4 Router ayar testi başarısız**. Kullanıcının mevcut anahtar alanını temizleme
  değişikliği korundu. Son SSE yeniden bağlantı değişikliğinden sonra UUID ile
  yeni adres bulma eklendi; son güvenlik setinin **7 testi** ve mevcut SSE setinin
  **3 testi geçti**, analyzer temiz. Windows native ve Web release derlemeleri
  SSE'ye son eklenen bu hook öncesinde geçti. İstemci belgesi
  `orion-ai-assistant-flutter/main_app/app/PHASE6.md`.

Alt görevler yalnızca kendi repository'lerini değiştirdi. Ortak güvenlik kararları
bu belgedeki tek SPKI sözleşmesine göre koordine edildi; üç tarafın canlı testleri
üst ajan tarafından birlikte çalıştırıldı. Bu sonuçlar fiziksel cihaz/LAN testinin
yerini almaz ve mevcut dört Flutter ayar hatasını çözülmüş saymaz.


## Son yerel HTTP / ağ HTTPS ayrımı (5 Ekim 2026)

Yerel kullanım için ayrı loopback HTTP girişleri geri getirildi. LAN/mobile
trafiği ve cihaz kimlik doğrulaması mevcut HTTPS/SPKI protokolünü kullanır.
Windows sertifika güven deposu değiştirilmez.

| Kullanım | Gerçek URL | Listener bind | Canlı doğrulama |
|---|---|---|---|
| Router local dashboard/API | http://127.0.0.1:20128/dashboard/ | 127.0.0.1:20128 HTTP | HTML200; /health JSON200 |
| Router LAN/Hub API | https://192.168.1.108:9443/dashboard | 0.0.0.0:9443 HTTPS | Sertifika doğrulamalı HTML200 ve identity200 |
| Hub local management | http://127.0.0.1:8910/manage | 127.0.0.1:8910 HTTP | Doğrudan HTML200; tarayıcı giriş ekranı |
| Hub local shortcuts | http://localhost/panel | 127.0.0.1:80 HTTP | HTML200; /hub200; /orionrouter -> local Router200 |
| Hub LAN/mobile API | https://192.168.1.108:8443 | 0.0.0.0:8443 HTTPS | Sertifika doğrulamalı identity200 |
| Hub LAN browser | https://192.168.1.108:8911/ | 0.0.0.0:8911 HTTPS | Sertifika doğrulamalı HTML200 |
| Hub eski owner girişi | https://localhost:8909/ | 127.0.0.1:8909 HTTPS | Geriye uyumluluk için korunur; HTTP yönetim8910 kullanılabilir |

LAN IP geçicidir. 80,8910,20128 HTTP portlarına bu PC'nin LAN IP'sinden
bağlantı reddedildi. Host/forwarded spoof403; native cihaz token'ı yerel
HTTP'de401. İki üretim SPKI pini yeniden başlatma öncesi/sonrası aynı kaldı.
HTTPS kontrolleri mevcut sertifikaları yalnız test istemcisinin CA listesinde
kullanır; verify=False veya Windows deposuna ekleme yapılmadı.

Flutter Web, loopback HTTP8910 /api/v1/discovery/hubs üzerinden Hub'ın DNS-SD
listesini okur. Canlı self kaydı Hiro ve mevcut UUID ile HTTP8910/hub döndü;
localhost frontend origin için CORS doğrulandı. LAN kayıtları HTTPS8911,
mobile mDNS servisi HTTPS8443 ve Router mDNS servisi HTTPS9443 yayınlar.
Tarayıcı mDNS'i doğrudan taramaz. Başka PC'deki self-signed HTTPS servisi için
normal tarayıcı güven kuralları geçerlidir; native SPKI bunun yerine geçmez.

Hedefli kontroller: Hub53; Router14 TLS/loopback +12 mDNS; Flutter38
bağlantı/keşif/güvenlik testi. Üç repository ortak canlı TLS/SPKI testi15.
Fiziksel telefon veya ikinci PC'deki tarayıcı bu kontrollerin kapsamına girmez.

Ana Hub checkout'una yalnız ilgili dosyalar hash kontrolü ve yedekle aktarıldı.
Router resmi orionrouter komutuyla yeniden başlatıldı. HTTP management,
Router dashboard/logs/health ve HTTPS girişleri gerçek HTTP istemcileriyle
kontrol edildi. Kalıcı Hub/Router anahtarları, UUID ve trust kayıtları korunur.

Son Flutter Web canlı tarayıcı kontrolünde önce null cihaz başlıklarının CORS
preflight400 ürettiği görüldü. Keşif isteğinin birleşmiş header map'inden
Authorization/X-Device-Token anahtarları tamamen silindi; kalıtılan karma-case
başlıklar için regresyon testi7/7 geçti. Analyzer temiz, Web release yeniden
üretildi. Temiz loopback frontend origin'de çalışan gerçek Hub bulundu ve
giriş ekranında “Hiro cihazına bağlı” görüldü. Önceki service-worker/cache
oturumu eski derlemeyi gösterebildiğinden kullanıcı tam yenileme yapmalıdır.
Hub üretim verify_router kodu çalışan LAN Router'a gerçek SPKI kontrolüyle
bağlandı. Ortak15 TLS/SPKI testi son kaynaklarla tekrar geçti.

2026-10-05 kurulum ve oturum düzeltmesi: native Router kurulumu yalnız kamusal
sertifika/UUID konumunu kullanıcıya ait kurulum kaydına yazar. Hub aynı PC'deki
bu kaydı okuyup canlı HTTPS kimliğini doğrulayarak güveni kurar; boş güven
veritabanıyla test edildi. Eksik Router başlangıcı engellemez, sonradan yeniden
denenir. Güveni kaldırma ve değişen anahtar otomatik olarak geçersiz kılınmaz.
9 bootstrap, 3 Router seçim güvenliği ve 38 yerel Hub/kurulum testi geçti.
Router kurulum kaydı 4, TLS keşif hedefleri 3 ve ortak canlı TLS/SPKI 15 testi
geçti. Windows test taşıması kapanırken bir ConnectionResetError günlüğü üretti;
15 testin sonuçları başarılıdır.

Canlı Hub'da /hub200, /manage HTML200, /deneme404 ve /8910 açıklayıcı404
doğrulandı. Mevcut a hesabının settings ve schema istekleri200/CORS döndü;
yönetici çerezi ile hesap JWT'sinin birlikte bulunması için regresyon koruması
eklendi. Kayıtlı Router yeniden seçilirken kaydedilmiş SPKI kullanılır ve canlı
TLS kontrolü yine zorunludur. Yeni Router için parmak izi doğrulaması gerekir.
Router Ayarlar → Ağdaki Router'lar canlı mDNS kaydından Hiro'yu buldu.
Güncel Flutter Web tarayıcıda Hiro'ya bağlı göründü.

Bu değişiklikler yerel kaynaklarda ve bu PC'deki servislerde uygulanmıştır.
GitHub'a yayınlama/release veya başka PC'ye kurulum yapılmadı. İnternetten
indirilen installer'ın bu davranışı sunması için güncel kaynakların yayımlanması
gerekir. Fiziksel telefon ve ikinci PC üzerinde uçtan uca deneme yapılmadı.

2026-10-05 sade giriş akışı: Hub, Router ve Flutter giriş formunun altında kısa
cihaz adı, seçim kutusu ve yenileme ikonu gösterir. Ayrı arama ekranı yoktur.
Liste 10 saniyede yenilenir; kayıtlı UUID korunur. Kayıtlı cihaz yoksa veya kimlik
çakışıyorsa başka cihaza sessizce geçilmez. Seçici girişten sonra gizlenir.

Aynı PC'deki native Flutter masaüstünün ilk bağlantısı, Hub launcher'ın kullanıcı
profilinde oluşturduğu hub-installation.json kaydından kurulum içindeki kamusal
sertifikayı ve özel yerel kanıtı okur. Sertifikadan türetilen SPKI ile yalnız
HTTPS loopback /api/v1/pair/local çağrılır. Endpoint gerçek loopback peer, TLS,
yerel gizli kanıt ve Origin/forwarded başlıklarının bulunmamasını zorunlu kılar.
Uzak cihazlara ve tarayıcıya bu yetki verilmez; iptal edilmiş/unutulmuş eşleştirme
otomatik yenilenmez. Canlı yerel claim 200, geçici kontrol cihazı iptali 200.

Telefonun ilk bağlantısında PC yönetimindeki tek QR/link normal cihaz eşleştirme
ve isteğe bağlı hesap davetini birlikte taşır. Kullanıcı mevcut hesabıyla giriş
yapabilir veya izin verilmişse yeni hesap açabilir. Yeni hesap açıldığında tekrar
şifre girmeden oturum açılır. QR/link kısa ömürlüdür; sonraki açılışlarda kalıcı
Hub UUID/pin ve cihaz yetkisi kullanılır. Tarayıcı daveti cihaz pinlemesinin yerine
geçmez. Local HTTP yönetim korunur; LAN API HTTPS/WSS kullanır.

Son yenileme düzeltmesi: otomatik liste taraması hata metni göstermez ve oturum
yeniden kurulmaz. Hub web giriş sırasında hedef değişimini ve mükerrer girişi
engeller; tarama bekleme sayacını veya giriş kilidini açmaz. Router Ayarlar ve
Hub Güvenli bağlantılar ekranları 'Bu PC' / 'Diğer cihazlar' adreslerini açılabilir
ve kopyalanabilir olarak sunar. Port ayrımı korunur: HTTP yalnız loopback'te,
TLS ağ arayüzlerinde wildcard listener ile IP değişikliğine uyumlu çalışır.
Güncel canlı owner adresleri 200, Router dashboard 200 ve seçili Router SPKI
kontrolü 200 olarak doğrulandı. Telefonun native app_links eklentisi yeni APK
veya tamamen durdurulup yeniden flutter run ile yüklenir; hot restart native
eklenti kaydını güncelleyemez.
