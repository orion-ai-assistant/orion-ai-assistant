# Orion yerel giriş ve keşif sözleşmesi

Bu dosya eski ortak `.local` adres/port-80 gateway talimatlarının yerine geçer.
Hub, Flutter ve Router ajanları aynı sözleşmeyi kullanmalı.
Faz 6 revizyon tarihi: **2026-10-04**. Aşağıdaki Faz 6 kuralları kesindir;
TLS/QR/cihaz token'ları mevcut kodda henüz uygulanmadı. Mevcut HTTP davranışı
ile uygulanacak güvenli bağlantı sözleşmesi ayrı belirtilmiştir.

## Faz 6: üç repo için ortak güvenli bağlantı sözleşmesi

| Kullanım | DNS-SD / native NSD | TXT | SRV ve koruma |
| --- | --- | --- | --- |
| Hub web HTTP | `_orion._tcp.local.` / `_orion._tcp` | Mevcut `id`, `name`, `path=/api/v1`, `version` değişmez | Mevcut HTTP portu; TLS şifrelemesi ve sunucu kimliği doğrulaması yok |
| Hub mobil HTTPS | `_orion-mobile._tcp.local.` / `_orion-mobile._tcp` | Zorunlu `id=<Hub UUID>`, `v=1`, `scheme=https` | Gerçek HTTPS portu; yaprak sertifika pini + TLS |
| Router güvenli bağlantı | `_orion-router-tls._tcp.local.` / `_orion-router-tls._tcp` | Zorunlu `id=<Router UUID>`, `v=1`, `scheme=https` | Gerçek Router TLS portu; yaprak sertifika pini + TLS |
| Router legacy HTTP | `_orionrouter._tcp.local.` / `_orionrouter._tcp` | Mevcut `id`, `name`, `path=/v1`, `version` | Eski HTTP bağlantısı; TLS şifrelemesi ve sunucu kimliği doğrulaması yok |

Yeni TLS kayıtlarında `name`, `path` ve uygulama `version` bilgileri eklenebilir;
`v=1` protokol sürümüdür. Aynı cihazın HTTP/TLS kayıtları aynı kalıcı UUID'yi
taşır. Keşif yalnızca adres bulur; TXT fingerprint veya UUID güven kanıtı olmaz,
kaydedilmiş pin keşiften güncellenmez. Aynı servis türünde çelişkili endpoint
otomatik seçilmez. Pinli bağlantıda HTTP fallback veya redirect yoktur.

- `device_token` cihazı yetkilendirir; kullanıcı hesabına giriş ayrı yapılır.
  Cihaz token'ı hesap/sohbet veya yönetici yetkisi vermez. Mobil hesap oturumu
  eşleşmiş cihaza bağlanır; sohbet için geçerli cihaz ve aktif kullanıcı gerekir.
  HTTP listener hem `device_token` hem mobil kullanıcı oturumunu reddeder.
  Web HTTP oturumu ayrı kalır; mobil HTTPS listener owner/admin yollarını açmaz.
- HTTPS/HTTP ayrımı gerçek dinleyiciden (soket) yapılır. Listener/app kimliği
  ve ASGI scheme sunucunun soket bilgisinden türetilir. `Forwarded`,
  `X-Forwarded-Proto`, Host veya başka istemci başlıklarına güvenilmez;
  proxy headers kapalıdır. Aynı sınır WebSocket'te de geçerlidir.
- Eşleştirme kodu kriptografik rastgele **40 bit / 8 base32 karakter**,
  **TTL 120 saniye**, tek kullanımlık ve tüm süreçler arasında atomik tüketilir.
  Yanlış denemelerle hiçbir kod iptal EDİLMEZ. Gerçek soket kaynak IP'si başına
  ve ayrıca genel hız sınırı, backoff ve 429/`Retry-After` uygulanır; bunlar
  kodun TTL'sini/geçerliliğini değiştirmez. Geçersiz/süresi dolmuş/kullanılmış
  kod, hız sınırına takılmadığında aynı 403 yanıtını verir. Cihaz token'ı 256 bit
  rastgeledir, sunucuda yalnızca hash saklanır; kod/token/QR loglanmaz.
- Cihaz silinince token ve bağlı mobil oturumlar iptal edilir, açık SSE ve
  WebSocket bağlantıları tüm süreçlerde kapatılır. Yalnızca yeni istekleri
  reddetmek veya token süresini beklemek yeterli değildir.
- Flutter REST, SSE, WSS ve dosya yükleme/indirmede her yeni bağlantıyı aynı
  pinli transport fabrikasıyla açar. Ses WebSocket'i
  `WebSocket.connect(..., customClient: pinnedHttpClient)` kullanır. Her Hub'ın
  pin/token istemcisi ayrıdır; pin uyuşmazlığında sır/istek gövdesi gönderilmez.
  CA kökleri eklenerek pin doğrulaması atlanamaz.
- Router pini tüm Hub→Router bağlantılarında zorunludur: bağlantı testi,
  worker sohbet/ses, model/grup/ses listeleri, dashboard API ve STT WebSocket.
  API ve worker aynı kalıcı UUID/pin kaydını okur; API anahtarı pin doğrulaması
  sonrası gönderilir. İlk testin geçmesi diğer bağlantılarda pini atlatmaz.
  Pin/kimlik değişiminde eski havuzlar, açık bağlantılar ve katalog cache'leri
  geçersizleştirilir. Router ekranından alınan pin panelde açıkça onaylanır;
  keşiften veya ilk ağ yanıtından otomatik güven oluşturulmaz.
- Hub ve Router: kalıcı ECDSA P-256 özel anahtar ve yaklaşık **50 yıllık**
  self-signed sertifika. Sertifika otomatik/periyodik **yenilenmez**;
  pin **`fp = SHA-256(yaprak sertifika DER)`** değeridir, SPKI/CA pini değildir.
  Pinli istemciler sertifika süresine (`notBefore`/`notAfter`) **bakmaz**;
  yaprak pin eşleşmesi ve TLS özel anahtar sahipliği doğrulanır. Normal restart
  kimliği değiştirmez. Dosya kaybı/bozulması sessiz yeni kimlik üretmez;
  açık kimlik sıfırlama, eski erişimleri kapatma ve yeniden eşleştirme gerekir.

Repo devirleri: [Hub/Faz 6](faz-6-guvenli-eslestirme-devir.md),
[Flutter](faz-6-flutter-devir.md), [Router](orion-router-yerel-adres-copilot.md).

## Hub (bu repoda)

- Native yerel listener `127.0.0.1:80`: `/` başlangıç, `/hub` sohbet,
  `/panel` yönetim, `/panel/router/` Router bağlantısı. Port yapılandırılabilir.
- Yerel girişte gerçek loopback peer + localhost/loopback Host gerekir;
  forwarded başlıklarla bu kontrol aşılamaz. LAN veya bağımsız gateway girişi yok.
- Public web API/dashboard `0.0.0.0:8910` olarak kalır. Mevcut telefon/başka PC
  web erişimi bu adrestedir; Faz 6 native mobil erişimi ayrı HTTPS listener'dadır
  (varsayılan 8443, ayarlanabilir). Panel LAN'dan açılamaz.
- `orion.local`, `orionpanel.local`, `orionadmin.local` ortak duyuruları yok.
  `_orion-entry._tcp.local.` duyurusu ve alias sahipliği/election kaldırıldı.
- İlk yerel giriş bu Hub'ı seçer. Başarılı girişte son Hub UUID'si saklanır;
  kayıtlı Hub kapalıysa başka cihaz otomatik seçilmez. Değiştir/Yenile listesi
  Python mDNS taramasından gelir. Uzak Hub seçilince HTML localhost'tan kalır;
  yalnızca izin verilen public API/SSE/STT istekleri seçili Hub'a aktarılır.
  Yönetici çerezleri ve anahtarları diğer Hub'a aktarılmaz.
- Hesaplar/sohbetler korunur. Localhost'un yeni origin'inde bir kez yeniden
  giriş gerekebilir. Flutter web build'ini sunmak ve Python ile başlatmak sonraki iştir.

## Flutter (kendi reposunda)

- Varsayılan/otomatik yedek `orion.local` bağlantısını kaldır.
- Faz 6 native mobil için doğrudan NSD/mDNS keşfi kullan:
  `_orion-mobile._tcp.local.`; platform NSD tipi `_orion-mobile._tcp`.
  `_orion._tcp` yalnızca web HTTP keşfidir. Listeyi Hub HTML/API'sinden alma.
- Zorunlu TXT `id=<kalıcı UUID>`, `v=1`, `scheme=https`; isteğe bağlı `name`,
  `path=/api/v1`, `version`. Gerçek IP ve HTTPS portu SRV/A kaydından alınır.
- İlk kullanım tek sonuçsa eşleştirme hedefi olarak seç, birden fazla sonuçsa
  kullanıcı seçsin. Hiçbir keşif sonucu QR/metin pini olmadan güvenilir olmaz.
  Son başarılı girişin UUID'sini kaydet; yeniden keşifle IP/portu güncelle.
  Kayıtlı cihaz yoksa sessizce diğerine geçme. Aynı UUID farklı endpoint'lerden
  duyuruluyorsa belirsizlik göster, otomatik seçim yapma.
- `GET /api/v1/discovery/self` yanıtında `id`, `name`, `desktop`, `local_entry`
  bulunur. Bağlanmadan önce id eşitliğini kontrol et; bunu güven kanıtı sayma.
- Tokenlar Hub UUID'sine göre ayrı saklanır. Parola/davet/token başka Hub'a taşınmaz.
- Flutter web'i Hub içinde başlatma/servis etme bu değişikliğin kapsamında değil.

## Router (kendi reposunda)

- `orionrouter.local` ortak alias'ını ve ortak port-80 adres/gateway servisini
  kaldır. Hub'ı proxy'leme veya başlatıp durdurma. Kendi API/UI portunda bağımsız çalış.
- DNS-SD: `_orionrouter._tcp.local.`; native NSD: `_orionrouter._tcp`.
  Bu mevcut legacy HTTP kaydıdır. Faz 6 güvenli bağlantı ayrı
  `_orion-router-tls._tcp.local.` / `_orion-router-tls._tcp` kullanır;
  zorunlu TXT `id`, `v=1`, `scheme=https`, SRV gerçek TLS portudur.
- TXT: `id=<kalıcı UUID>`, `name=Orion Router — <PC adı>`, `path=/v1`,
  `version=<gerçek sürüm>`. SRV gerçek API portunu taşır (varsayılan 20128).
- Teknik SRV hostname `orionrouter-<UUID hex>.local.` olabilir. Protokolün bu
  `.local.` eki kalır; tarayıcıya yazılan ortak kısayol değildir.
- Yeni sürümlerde isteğe bağlı `GET /v1/discovery/self` sunulabilir: `{ "id": "UUID", "name": "Orion Router — Hiro",
  "version": "..." }`. Kimlik bilgisi dışında parola/token/anahtar dönmesin.
- Hub bu endpoint varsa id'yi karşılaştırır; mevcut sürümde 404 ise UUID
  keşif kaydından alınır, HTTP kimlik doğrulaması yapılmış sayılmaz. Global Router
  API anahtarı kontrolünde varsa `GET /v1/models` kullanılır; yoksa mevcut
  `POST /v1/chat/completions` boş JSON ile çağrılır. Anahtar geçerli olduğunda
  model eksikliği için dönen bilinen 400 kontrol edilir; model/mesaj çalıştırılmaz.
  Yeni endpoint eklemek mevcut sürümün kullanılması için zorunlu değildir. Bir yönlendirme kimlik/API kontrolü
  için kabul edilmez.
- Mevcut HTTP sürümünde ilk açılışta tek Router bulunup bu iki kontrol geçerse Hub UUID'yi kaydeder.
  Faz 6 TLS bağlantısında bu otomatik kayıt güven/pin oluşturmaz; Router'ın
  güvenilen ekranındaki yaprak pin panelde onaylanmadan API anahtarı gönderilmez.
  Çoklu sonuçta yönetici `/panel/router/` üzerinden seçer. Kaydedilen UUID dışında
  bir Router'a otomatik geçilmez. Elle adres seçimi mevcut/legacy Router için kalır.
- Hub native API/worker ortak `data/router-connection.json` kaydını kullanır.
  İsim/UUID saklanır; güncel endpoint 30 saniyede yenilenir. Kayıtlı Router
  bulunamazsa endpoint kullanılmaz, başka env adresine otomatik düşülmez.
  Bu kayıt parolaları veya API anahtarını içermez.
- Listener hazırken duyur, IP değişince arayüzleri/kayıtları yenile,
  kapanırken goodbye gönder. Docker bridge/WSL/VPN IP'lerini otomatik duyurma.

## Güvenlik sınırı ve uygulama durumu

UUID cihazı yeniden bulmak içindir, kriptografik kimlik kanıtı değildir.
TXT'deki SHA/fingerprint de tek başına kanıt sayılmaz. Şimdilik “Kayıtlı” ifadesi
kullanılır. Yerel localhost giriş LAN kaynaklı sahte HTML'e yönlendirilmez;
telefon/uzak PC ve uzak API bağlantıları HTTP olduğu sürece şifrelenmez.

Faz 6'nın yukarıdaki kesin sözleşmesi henüz uygulanmadı. Güvenilen PC ekranındaki
QR/metinle ilk eşleştirme ve yaprak sertifika pin doğrulaması yapılacak.
Kimlik değişince sessiz kabul ve HTTP fallback olmayacak.

## Birlikte doğrulama

1. Hub tek başına localhost girişlerini sunmalı; Router tek başına API/UI sunmalı.
2. Flutter gerçek telefonda Hub'ı bulmalı, aynı UUID IP değişince korunmalı.
3. Router tek sonuçsa kontrol edilip kaydedilmeli; çoklu sonuçta yönetici seçmeli.
4. Kayıtlı Hub/Router kapandığında başka cihaza otomatik geçilmemeli.
5. Aynı UUID ile iki farklı endpoint bulunursa otomatik bağlantı durmalı.
6. Yönetim paneli, çerezi ve yönetici anahtarı LAN veya peer proxy'ye açılmamalı.
