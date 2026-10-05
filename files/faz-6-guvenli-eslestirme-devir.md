# Faz 6 — Güvenli eşleştirme: inceleme ve yeni oturum için devir

Tarih: 2026-10-04. Bu dosya uygulama değişikliği değildir; mevcut kodun incelenmesi
ve kullanıcının Hub–Flutter / Hub–Router HTTPS planı için devir notudur.
TLS, QR eşleştirme ve cihaz token'ları henüz uygulanmadı.
Faz 6 revizyonu kesin sözleşmedir; Hub, Flutter ve Router uygulamaları
[ortak sözleşmeyi](orion-yerel-giris-kesif-sozlesmesi.md) kullanır.


## Faz 6 — kesin ortak sözleşme (2026-10-04 revizyonu)

Bu bölüm Hub, Flutter ve Router devirlerinde aynı sözleşmedir. Aşağıdaki kurallar
kesindir; uygulama henüz tamamlanmış sayılmaz. Mevcut kod anlatımı hedef sözleşmenin
yerine geçmez. Önce Hub–Flutter, sonra Router TLS ve Hub'ın tüm Router istemcileri uygulanır.

| Kanal | Keşif (DNS-SD / native NSD) | Transport ve sınır |
| --- | --- | --- |
| Hub web | `_orion._tcp.local.` / `_orion._tcp` (değişmez) | Mevcut HTTP portu ve web hesabı/oturumu korunur; TLS şifrelemesi ve sunucu kimliği doğrulaması yok. |
| Hub mobil | `_orion-mobile._tcp.local.` / `_orion-mobile._tcp` | Ayrı HTTPS listener (varsayılan 8443, ayarlanabilir); zorunlu TXT: `id`, `v=1`, `scheme=https`. |
| Router güvenli API | `_orion-router-tls._tcp.local.` / `_orion-router-tls._tcp` | Ayrı TLS kaydı ve HTTPS listener; port ayarlanabilir, SRV gerçek TLS portudur. |

- Keşif yalnızca adres ipucudur. UUID kalıcıdır; SRV gerçek portu taşır. Mobil
  TXT'deki `v=1` protokol sürümüdür; eski `version` alanıyla değiştirilmez.
  `name` ve `path` ek bilgi olabilir. Eski `_orionrouter._tcp` HTTP kaydı TLS
  kaydı değildir. Keşif pini güncelleyemez; kayıtlı hedef yoksa başka hedefe geçilmez.
- `device_token` cihazı yetkilendirir; hesap girişi ayrıdır ve token yönetici
  yetkisi vermez. Mobil hesap oturumu eşleşmiş cihaza bağlanır. Hesap gerektiren
  isteklerde geçerli cihaz ve kullanıcı oturumu birlikte gerekir. Kullanıcı çıkışı
  cihaz eşleştirmesini silmez. HTTP listener hem cihaz token'ını hem mobil kullanıcı
  oturumunu reddeder; mevcut web oturumu ayrı kalır. Mobil istemci HTTP'ye sır göndermez.
- HTTPS/HTTP ayrımı gerçek dinleyiciden (TLS soketi/listener kimliği) yapılır.
  `X-Forwarded-Proto`, `Forwarded`, Host veya başka istemci başlıkları TLS kanıtı
  değildir; başlıkla HTTP isteği HTTPS yetkisi kazanamaz. Pair claim yalnızca HTTPS'tedir.
- Kod kriptografik rastgele 40 bit (8 base32 karakter), TTL 120 saniye ve tek
  kullanımlıktır. Kod tüketimi ve cihaz token'ı oluşturma tüm süreçler arasında
  atomiktir; eşzamanlı claim'lerden yalnızca biri başarılı olur. Yanlış denemeler
  kodları iptal ETMEZ. Gerçek kaynak IP başına ve genel hız sınırı, backoff ve
  429 uygulanır; kaynak IP istemci başlığından alınmaz. Bilinmeyen, süresi dolmuş
  veya kullanılmış kod aynı `403 pair_code_invalid` cevabını verir.
- Claim: `POST /api/v1/pair/claim`, istek `{code,device_id,device_name,platform}`,
  başarı `{hub_id,hub_name,device_token}`. QR/metin `{v:1,id,url,fp,code,name}`
  güvenilir yerel ekrandan alınır. Aday pin yalnızca claim için kullanılır;
  başarılı claim ve QR UUID eşitliğinden sonra pin/token kalıcılaşır. Cihaz token'ı
  256 bit rastgele, yalnızca bir kez döner; sunucuda yalnızca hash'i saklanır.
- Cihaz silinince token ve bağlı mobil oturumlar iptal edilir; açık SSE ve
  WebSocket bağlantıları kapatılır. Yalnızca sonraki isteği reddetmek yeterli
  değildir. Kimlik sıfırlama tüm cihazlar ve açık bağlantılar için aynı sonucu verir.
- Mobil REST, SSE, WSS ve dosya indirme/yükleme dahil tüm bağlantılar tek pinli
  HttpClient fabrikasını kullanır. Ses WebSocket'i pinli HttpClient ile
  `WebSocket.connect(..., customClient: pinnedClient)` üzerinden açılır; kanala
  gerekiyorsa bu soket sarılır. Doğrudan pinsiz `WebSocketChannel.connect` kullanılmaz.
  `SecurityContext(withTrustedRoots:false)`, ek CA yok, yalnızca aday/kayıtlı yaprak
  DER SHA-256 eşitliği kabul edilir. Pin doğrulanmadan kod, parola, token, API
  anahtarı veya gövde gönderilmez. Redirect ve HTTP fallback kapalıdır.
- Router pini Hub→Router bağlantılarının tamamında uygulanır: keşif/bağlantı
  kontrolü, worker, sohbet, model/grup listesi, ses katalogları, STT/TTS, HTTP
  akışları ve ses WebSocket'i. Aiohttp pini ayrı WebSocket istemcisine kendiliğinden
  geçmez; onun transport'u da sır gönderilmeden pini doğrular. Pin kaydı API ve
  worker tarafından ortak kullanılır; hedef/pin değişiminde eski havuz ve akışlar kapanır.
- Hub ve Router kalıcı ECDSA P-256 özel anahtar ve yaklaşık 50 yıllık self-signed
  sertifika kullanır. Otomatik yenileme yoktur. Pin yaprak sertifikanın DER SHA-256
  parmak izidir; SPKI pini değildir. Pinli istemci sertifika süresine bakmaz,
  özel anahtara sahip olunduğu TLS el sıkışmasıyla doğrulanır. Sertifika/anahtar
  değişimi açık yeniden eşleştirme gerektirir; normal restart kimliği değiştirmez.

Kabul kontrolleri: servis adları/TXT/SRV ve web HTTP uyumluluğu; hem cihaz hem
mobil oturumunun HTTP'de reddi (sahte TLS başlıkları dahil); cihaz token'ıyla
yönetici erişiminin reddi; yanlış kodlarla mevcut kodun iptal olmaması, IP ve
genel backoff/429, TTL/tek kullanım/atomik yarış; cihaz silinince açık SSE/WS'nin
kapanması; REST/SSE/WSS/dosya ve tüm Hub→Router yollarında pin hatasında sıfır
sır gönderimi; doğru pinle süre kontrolü olmadan bağlantı; yanlış pin ve özel
anahtarsız sertifika kopyasında bağlantının reddi; IP/port değişiminde pinin korunması.


## Hedef ve kapsam

Mobil Flutter, güvenilen Hub PC ekranından alınan QR/metinle ilk eşleşmesini yapar.
Sertifika parmak izi bağlantıda doğrulanır; UUID ve IP kopyalayan başka bir cihaz,
Hub'ın özel anahtarına sahip olmadan kabul edilmez. Keşif yalnızca adres bulur.
HTTP fallback ve yönlendirme yoktur. IP değişince eşleşme korunur.

Mevcut web dashboard ve HTTP hesabı/oturumu çalışmaya devam eder. Mobil için
ayrı HTTPS listener önerisi: varsayılan 8443, ayarlanabilir. Yönetim paneli
loopback'te kalır. Flutter web build'ini sunmak bu işin dışında.
Router TLS çalışması kendi reposunda yapılır; Hub'ın Router istemcilerinin
pinning değişiklikleri bu Hub reposunda yapılmalıdır.

## Mevcut kod ve korunacak durum

- Workspace: `D:\krstlcm Workspace\DevProjects\orion-ai-assistant`.
- Native başlatıcı: `services/hub/run_local.py`.
- Şu an yerel listener `127.0.0.1:80`: `/` başlangıç, `/hub` sohbet,
  `/panel` yönetim, `/panel/router/` Router seçimi.
- `ORION_HTTP_PORT` ayarlanabilir, mevcut varsayılanı **80**. Önceki konuşmada
  8911 önerildi fakat kod değiştirilmedi ve bu karar kesinleşmedi. `/panel`
  yolunu kullanmak tek başına port-80 çakışmasını çözmez.
- Özel/eski owner listener 127.0.0.1:8909; public HTTP API/dashboard 0.0.0.0:8910.
- Loopback peer + localhost/127.0.0.1/::1 Host kontrolü ve Origin kontrolü mevcut.
  Sadece Host başlığına bakarak yetki vermeye dönüştürme; forwarded başlıklara güvenme.
- İnsanların yazdığı ortak `orion.local`, `orionpanel.local`, `orionrouter.local`
  alias'ları kaldırıldı. Protokolün teknik UUID hostname `.local.` kayıtları kalır.
- Hub kimliği: `services/hub/data/mdns-id`; yeniden başlatmada UUID değişmez.
- Hub keşfi `_orion._tcp.local.`; TXT id/name/path/version; SRV mevcut HTTP portu.
- Yerel web son başarılı Hub UUID'sini hatırlar; ilk kez kendi Hub'ını seçer.
  Kayıtlı Hub yoksa başkasına kendiliğinden geçmez. Uzak Hub API'si localhost
  peer proxy'den çağrılır; başka Hub'ın HTML'i sunulmaz, admin sırları aktarılmaz.
- Router kaydı `services/hub/data/router-connection.json`; API ve worker ortak
  okur. `_orionrouter._tcp.local.` keşfi UUID ile adresi yeniler. Kaydedilen Router
  yoksa başka Router/env adresine otomatik geçiş yapılmaz.
- Eski Router HTTP sürümünde `/v1/models` ve `/v1/discovery/self` zorunlu değil;
  mevcut kod bunların 404 olması durumunu destekler. Bu uyumluluk TLS güveninin
  alternatifi değildir; eşleştirilmiş Router'ı HTTP'ye geri düşürmek için kullanılamaz.
- Hesapları, sohbetleri, davetleri, JWT imza anahtarını ve mevcut UUID'leri koru.
- Çok sayıda değişiklik henüz commit edilmedi. Önce `git status` oku; TTS ve
  diğer önceki değişiklikleri geri alma. Yeni checkout/reset ile çalışmayı kaybetme.
- Gerçek telefonda ve Router/Flutter güncel kodunda son birlikte test ayrıca gerekli.

## Faz 6 revizyonu: uygulanacak kesin kurallar

### 1. Cihaz eşleştirmesi ile kullanıcı hesabı ayrıdır

QR claim cihazı eşleştirir ve `device_token` verir. Bu token
tek başına sohbet hesabına veya yönetici yetkisine erişim vermez. Sonra kullanıcı
mevcut davet/giriş akışıyla, pinli HTTPS üzerinden kendi hesabına giriş yapar.
Mobil giriş oturumu eşleşmiş cihaz kaydına bağlanır. Sohbet isteklerinde hem
geçerli cihaz hem aktif kullanıcı gerekir; kullanıcıların veri ayrımı korunur.

Mevcut `/auth/login` web JWT'sini mobilde aynen kullanmak yeterli değildir:
HTTPS'te üretilen mobil kullanıcı oturumu da yalnızca mobil TLS listener'da geçerli
olmalıdır. Web HTTP oturumu mevcut haliyle ayrı kalır. HTTP listener cihaz token'ını
ve mobil oturum token'ını reddeder. HTTPS listener'da owner/admin yolları açılmaz.

HTTP/HTTPS ayrımı TLS'i sonlandıran gerçek dinleyiciden (soket) yapılır.
Listener/app kimliği ve ASGI scheme yalnızca bu sunucu bilgisinden türetilir;
`Forwarded`, `X-Forwarded-Proto`, Host veya diğer istemci başlıklarına güvenilmez.
Proxy headers kapalı tutulur.
WebSocket de aynı cihaz/kullanıcı/transport doğrulamasından geçer.

### 2. Web keşfi ile mobil HTTPS keşfi ayrılmalıdır

Mevcut `_orion._tcp.local.` kaydını doğrudan 8443'e çevirmek web Hub seçimini
bozar: `hub_directory.py` şu an bu kayıttan HTTP URL üretir ve `local_peer.py`
HTTP public API'ye bağlanır. Mevcut web HTTP keşif kaydı korunmalıdır.

Üç repo için **kesin sözleşme**: web HTTP kaydı `_orion._tcp.local.` değişmez.
Mobil HTTPS kaydı `_orion-mobile._tcp.local.`, Flutter NSD biçimi `_orion-mobile._tcp`.
Zorunlu TXT: `id=<kalıcı Hub UUID>`, `v=1`, `scheme=https`; `name`,
`path=/api/v1`, uygulama `version` alanları eklenebilir. `v` protokol sürümüdür,
uygulama `version` alanının yerine kullanılmaz. SRV gerçek HTTPS portunu taşır.
Router güvenli bağlantısı ayrı `_orion-router-tls._tcp.local.` kaydını kullanır
(NSD: `_orion-router-tls._tcp`); zorunlu TXT `id=<kalıcı Router UUID>`, `v=1`,
`scheme=https`; SRV gerçek Router TLS portudur. Eski `_orionrouter._tcp.local.`
yalnızca legacy HTTP keşfidir; pinli bağlantı buna geri düşmez.
Bu yeni kayıtlar henüz uygulanmadı. TXT parmak izi olursa yalnızca ipucudur;
QR ile kaydedilmiş pini güncelleyemez. HTTP ve HTTPS kayıtlarının aynı UUID
taşıması normaldir; aynı servis türündeki çelişkili endpoint'ler ayrı ele alınır.

### 3. İptal gerçekten tüm mobil erişimi kesmelidir

Panelde cihaz silinince onun cihaz token'ı ve bağlı mobil oturumları iptal edilir;
mevcut SSE/WebSocket bağlantıları da cihaz silme işlemiyle kapatılır.
Tüm süreçlerde cihaz-bağlantı eşlemesi ve iptal bildirimi gerekir; yalnızca yeni
istekleri reddetmek veya token süresinin dolmasını beklemek yeterli değildir.
Kimlik yenilenince tüm eşleşmeler, mobil oturumlar ve eski TLS bağlantıları
geçersizleşir. Sertifika dosyasını değiştirmek çalışan TLS listener'ı ve açık
bağlantıları kendiliğinden yenilemez; kontrollü listener yeniden başlatma gerekir.
Anahtar/sertifika normal restart'ta yeniden üretilmez. Dosya kaybı/bozulması
sessiz kimlik değişimiyle örtülmez; kullanıcı açık yeniden eşleştirme yapar.

### 4. Eşleştirme kodu denemeleri hizmet engellemeye dönüşmemelidir

Kod 40 bit (8 base32 karakter), TTL 120 saniye, tek kullanımlıktır. Kriptografik rastgele
üretilir; tüketim tüm süreçler arasında atomik yapılır (DB transaction/Redis gibi).
Cihaz token'ı 256 bit rastgele ve sunucuda yalnızca hash olarak tutulur.
Kod/token/QR içeriği loglara yazılmaz. Cihaz adı ve platform girdisi sınırlanır.

Yanlış denemelerle hiçbir eşleştirme kodu iptal EDİLMEZ; tek kodu veya tüm aktif
kodları yanlış deneme sayısıyla iptal eden sayaç bulunmaz. Gerçek soket kaynak
IP'si başına ve ayrıca genel hız sınırı uygulanır; artan bekleme (backoff),
429 ve `Retry-After` kullanılır. Kaynak IP başlıklardan alınmaz. Hız sınırı kodun
TTL'sini veya geçerliliğini değiştirmez. Süresi dolmamış kod yalnızca başarılı
atomik claim veya yetkili açık iptal ile tüketilir/iptal edilir. Hız sınırına
takılmayan geçersiz/süresi dolmuş/kullanılmış kodlar aynı 403 cevabını verir.

### 5. Sertifika ve pin sözleşmesi açık tutulmalıdır

ECDSA P-256 + kalıcı özel anahtar + self-signed sertifika uygundur. Özel anahtar
POSIX 0600, Windows'ta servis hesabına göre sınırlı ACL ile saklanır. Normal
başlatmada UAC/hosts/firewall değişikliği yapılmaz; kurulum adımları ayrı tutulur.

Hub ve Router sertifikaları yaklaşık 50 yıllık üretilir; otomatik/periyodik
yenileme yapılmaz. Pin `fp = SHA-256(yaprak sertifika DER)` değeridir; SPKI veya
CA pini kullanılmaz. Pinli istemciler sertifika süresine (`notBefore`/`notAfter`)
bakmaz; eşleşen yaprak pini ve TLS özel anahtar sahipliği doğrulanır.
Normal restart aynı anahtar ve sertifikayı kullanır. Açık kimlik sıfırlamada
sertifika değişirse anahtar aynı olsa bile pin değişir ve yeniden eşleştirme
gerekir. Bu politika Flutter ve tüm Hub→Router istemcilerinde aynıdır.

Flutter'da taslağın roots kapalı, hiçbir CA eklenmeyen özel HttpClient'ı ve yalnızca
eşleşen parmak izini kabul eden callback yaklaşımı uygulanabilir. Callback sadece
normal doğrulamanın kabul etmediği sertifikalar için çağrılır; sonradan bu client'a
varsayılan CA köklerini eklemek pin kontrolünü atlama riski oluşturur. Her Hub için
ayrı sabit pin/token client; pin hatasında istek gövdesi/sırlar gönderilmez.
Redirect kapalı, yalnızca https/wss, SSE/dosya/ses/WebSocket dahil tek güvenli
transport fabrikası. Native mobil içindir; Flutter web kapsam dışı.
Ses WebSocket'i `WebSocket.connect(..., customClient: pinnedHttpClient)` ile
aynı pinli `HttpClient` üzerinden açılır. Varsayılan ayrı WS istemcisi kullanılmaz.
Pin kontrolü her yeni bağlantıda REST, SSE, WSS ve dosya yükleme/indirme dahil
uygulanır; ilk bağlantı testi sonraki bağlantılar için güven kanıtı değildir.

## Router pinning kapsamı: yalnızca bağlantı testini düzeltmek yetmez

`services/hub/src/orion/api/router_connection.py` keşif/bağlantı kontrolü yapar.
Gerçek Router çağrıları başka yerlerde de yapılır:

- `services/hub/src/orion/worker/services/router.py`: sohbet ve ses HTTP istemcileri.
- `services/hub/src/orion/kernel/router_models.py`: model/grup/ses katalogları;
  mevcut kod Router dashboard API'sine yönetici anahtarı da gönderir.
- `services/hub/src/orion/api/routes.py`: ayrı STT WebSocket istemcisi;
  mevcut kod kullanıcı JWT'sini doğrudan decode eder ve Router'a ayrı WS açar.
- `services/shared/environment.py` ve `services/shared/router_connection.py`:
  kalıcı Router seçimi ve API/worker ortak adres kullanımı.

Pin bu çağrıların **hepsinde**, sır/istek gönderilmeden uygulanmalıdır. Worker'a
kalıcı pin kaydı erişmelidir. Aiohttp fingerprint kontrolü, websockets istemcisine
kendiliğinden uygulanmaz. WS için ayrıca güvenli pin doğrulayan transport gerekir.
Pin/kimlik değişiminde eski connection pool ve katalog cache'leri yenilenmelidir.
Router ekranından alınmış parmak izi panelde onaylanır; ağdaki id/sha değerini
otomatik güvenilir sayıp API anahtarını göndermek yasaktır.

## Uygulama sırası ve hızlı, anlamlı kabul testleri

1. Üç repoya ortak sözleşmeyi ve ilgili devir dosyasını aktar; bu revizyonu uygula.
2. Hub anahtar saklama, ayrı TLS listener, panel QR/cihaz listesi ve atomik claim.
3. Mobil cihaz+kullanıcı oturumu ayrımı; HTTP reddi ve cihaz iptalinin tüm akışlara etkisi.
4. Flutter secure storage + pinli istemci + gerçek telefonla eşleştirme.
5. Router reposunda TLS ve güvenilir fingerprint gösterimi; Hub'da tüm Router istemcilerinde pin.

Focused tests: farklı sertifika/same UUID reddi; kopyalanmış sertifika fakat özel
anahtar olmadan TLS başarısızlığı; aynı sertifika yeni IP'de başarı; paralel iki
claim'den yalnızca biri; süresi dolan/yeniden kullanılan kod reddi; yanlış hub_id
ve başarısız claim'de pinin kaydedilmemesi; HTTP'de cihaz VE mobil kullanıcı
token'larının reddi; sahte forwarded-proto reddi; redirect/fallback reddi; SSE ve
WS dahil pin doğrulaması; cihaz iptali ve kimlik yenilenmesinde açık akışların
kapanması; Hub değişiminde sırların karışmaması; mevcut web login/peer seçiminin
bozulmaması; Router pin uyuşmazlığında bütün istemcilerde sıfır sır gönderimi;
yanlış deneme/backoff sırasında kodların geçerli kalması; kaynak IP ve genel
hız sınırının ayrı uygulanması; web/mobil/Router TLS keşif kayıtlarının ayrılığı;
pin eşleşirken sertifika tarihinin bağlantıyı reddettirmemesi; REST/SSE/WSS/dosya
ve worker/model listesi/ses bağlantılarının her birinde farklı yaprak pininin reddi.

Önceki oturumda focused Python/Node kontrolleri ve gerçek localhost Chrome giriş
ekranı doğrulandı; tam proje testi yapılmadı. TLS için bunların yerine gerçek
TLS/WS entegrasyon testleri gereklidir. Mevcut geçiş kodunu yeniden yazmak yerine
bu sınırların üstüne ekleme yap.

## Korumanın sınırı

| Bağlantı | Faz 6 sözleşmesindeki koruma |
| --- | --- |
| Web HTTP | TLS şifrelemesi ve sunucu kimliği doğrulaması yok |
| Flutter→Hub HTTPS/WSS | Her bağlantıda yaprak sertifika pini + TLS; cihaz yetkisi ve hesap oturumu ayrı |
| Hub→Router HTTPS/WSS | Worker, model listesi ve ses dahil her bağlantıda Router yaprak pini + TLS |

Mobil HTTPS+pinning, mobil bağlantının hedefini doğrular ve taşınan veriyi şifreler.
HTTP web aynı kalınca aynı hesabın HTTP'den yapılan girişinin riski devam eder;
mobilin güvenliği web üzerinden açığa çıkan şifreyi/hesabı geri korumaz. Hub–Router
HTTP kaldığı sürece o bağlantı henüz bu güvenlik kapsamına girmez. Pinning ağdaki
hizmet engellemeyi veya ele geçirilmiş Hub/telefonu çözmez.

## Kaynaklar

- https://api.dart.dev/dart-io/HttpClient/badCertificateCallback.html
- https://docs.aiohttp.org/en/stable/client_advanced.html
- https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html
- Mevcut protokol: `files/orion-yerel-giris-kesif-sozlesmesi.md`
- Mevcut keşif/yerel giriş dokümanı: `services/hub/docs/lan-discovery.md`

## Yeni oturum için başlangıç talimatı

`files/faz-6-guvenli-eslestirme-devir.md` ve mevcut keşif sözleşmesini oku.
Önce çalışma ağacındaki değişiklikleri incele ve koru. Bu dosyadaki düzeltmeleri
kullanıcının Faz 6 planına uygula. Web HTTP girişini koruyarak Hub–Flutter güvenli
eşleştirme işini yap; Router reposu değişiklikleri ayrı yürütülür, fakat Hub'ın
gerçek Router çağrılarının pinning değişiklikleri Hub'da yapılır. TLS henüz var
sanma; portsuz localhost varsayılanını 8911'e taşıma kararı verilmiş sanma.
