# Yerel giriş ve LAN keşfi

Native Hub başlatıcısı yerel arayüz ile ağ bağlantısını ayırır:

| Listener | Amaç | TLS koruması |
| --- | --- | --- |
| `127.0.0.1:80` | `localhost/` başlangıç, `/hub` sohbet, `/panel` yönetim | TLS şifrelemesi ve sunucu kimliği doğrulaması yok |
| `127.0.0.1:8910` | `/manage` gerçek yönetim HTML’i, yerel sohbet ve directory | Yalnızca gerçek loopback HTTP; native token kabul etmez |
| `0.0.0.0:8911` | Diğer PC/telefon tarayıcısı için public web API ve HTML dashboard | HTTPS; normal tarayıcı sertifika doğrulaması |
| `127.0.0.1:8909` | Eski doğrudan yönetim girişi | HTTPS |
| `0.0.0.0:8443` | Native mobil pairing, REST/SSE/WS | HTTPS ve SPKI pinning |

Portlar env ile değiştirilebilir. Yerel giriş her zaman loopback'e bağlanır;
eski `ORION_HTTP_BIND_HOST`, `ORION_ADDRESS_GATEWAY`, `ORION_HTTP_EXTERNAL_PORT`
ayarları kullanılmaz. Hub Router için ortak adres veya proxy sunmaz.
Port 80 başka uygulamadaysa onu durdurma; yerel portu değiştir veya eski adres
servisinin sahibi olan uygulamada bu servisi kapat.

```dotenv
ORION_LOCAL_SHORTCUTS=1
ORION_LOCAL_SHORTCUT_PORT=80
ORION_LOCAL_PORT=8910
ORION_WEB_PORT=8911
ORION_ROUTER_LOCAL_PORT=20128
ORION_MDNS=1
# ORION_MDNS_NAME=Hiro
# ORION_MDNS_INTERFACES=Wi-Fi,Ethernet
# ORION_MDNS_ID_FILE=<kalıcı data>/mdns-id
# ORION_ROUTER_CONNECTION_FILE=<kalıcı data>/router-connection.json
```

`ORION_LOCAL_SHORTCUTS=0` port 80 başlangıç listener'ını kapatır; 8910 yönetim kalır.
`http://localhost/orionrouter` Router’ın `http://127.0.0.1:20128/dashboard`
arayüzüne yönlendirir. HTTP yönetim `/manage` ve `/panel` sayfalarında gerçek
HTML sunar. Peer loopback değilse, Host yerel değilse veya herhangi bir forwarded
header varsa HTTP reddedilir; Docker private-peer override bunu aşamaz.
Native device token ve cihaza bağlı JWT, yerel HTTP’de de reddedilir.
`ORION_MDNS=0` ağda duyuru ve taramayı kapatır; manuel bağlantı kullanılabilir.

## Ortak adresler kaldırıldı, DNS-SD kaldı

`orion.local`, `orionpanel.local`, `orionadmin.local` duyurulmaz. `_orion-entry`
alias kaydı/election ve gateway adapter kaldırıldı. Hosts/tarayıcı ayarı gerekmez.
Servis keşfinin teknik `.local.` eki korunur; bu tarayıcı kısayolu değildir.

Hub tipi `_orion._tcp.local.` (NSD'de `_orion._tcp`), TXT `id`, `name`,
`path=/api/v1`, `version`, `scheme=https`. SRV hostname UUID'den türetilen
`orion-<UUID hex>.local.`, port gerçek public API portudur. UUID kalıcı
TLS security dizinindeki `hub-id` dosyasındadır; eski mDNS kimliği ilk geçişte
korunur. Silmek kimliği değiştirir. Bozuk dosya değiştirilmez.

Yalnızca aktif fiziksel RFC1918 IPv4'ler duyurulur. Docker/WSL/vEthernet/VPN
arayüzleri filtrelenir. IPv6-only ve Docker bridge için otomatik keşif yoktur.
Listener hazır olduğunda duyuru başlar; arayüz/IP değişimi 30 saniyede kontrol
edilir. Kapanışta goodbye gönderilir. mDNS hatası Hub'ı durdurmaz.

## Yerel web istemcisi

İlk `/hub` açılışında bu cihaz seçilir ve ağ taraması beklenmez. Son başarılı
girişin UUID'si `orion_last_hub` anahtarıyla saklanır. Token anahtarı
`orion_token:<UUID>`; farklı Hub'lara parola/token/davet taşınmaz. İlk yeni
localhost origin'inde yeniden giriş gerekebilir, mevcut hesaplar korunur.

Değiştir/Yenile Python mDNS taramasını çağırır. Kayıtlı cihaz kapalıysa giriş
kapalı kalır, başka cihaza sessizce geçilmez. Davet ait olduğu Hub'da kalır.

- `GET /api/v1/discovery/self`: id/name/desktop/local_entry.
- `GET /api/v1/discovery/hubs`: current_id, hubs [{id,name,url,conflict?}].
- `GET /api/v1/discovery/connection`: yalnızca loopback; güncel LAN URL listesi.

`http://127.0.0.1:8910/api/v1/discovery/hubs` yerel browser bootstrap için
açıktır. Self kaydı HTTP loopback `/hub` adresidir; keşfedilen LAN kayıtları
gerçek HTTPS web portunu (varsayılan 8911) içerir. Mobil trust için bu directory
kullanılmaz: `_orion-mobile._tcp` ile 8443 ve QR/SPKI pairing kullanılır.

Tarama en fazla 64 servis çözer, 5 saniye cache kullanır. Aynı UUID farklı
endpoint'lerden duyurulursa conflict olur. Yerel istemci başka Hub seçtiğinde
adres çubuğu localhost'ta kalır. `/api/v1/peers/<UUID>/...` yalnızca keşifteki
RFC1918 endpoint'e, UUID eşitliği kontrolünden sonra public API isteklerini
aktarır. Cookie/admin anahtarı/HTML, discovery scan ve browser kontrolü aktarılmaz.
Yönlendirmeler izlenmez, env proxy kullanılmaz. Gövde limiti 32 MiB.
SSE ve sesli yazma WebSocket'i aktarılır; bu adapter LAN'dan kullanılamaz.

Panel `/panel` altında kalır; ayrı `/panel` HttpOnly/SameSite cookie ve mevcut
yetki kontrolleri korunur. Panel linkleri `/hub`, `/panel/manage`, `/panel/admin`,
`/panel/router` kullanır. Desktop browser seçimi yalnızca bu PC'de ve yetkili
kullanıcıyla kullanılabilir; başka Hub için proxy açılmaz.

## Router bağlantısı

`/panel/router/` yönetici şifresiyle açılır. Router tipi `_orionrouter._tcp.local.`,
TXT `id`, `name`, `path=/v1`, `version`, SRV gerçek API portudur.
Router isteğe bağlı `GET /v1/discovery/self` sunuyorsa id karşılaştırılır. Bu
endpoint mevcut değilse UUID yalnızca keşif kaydından alınır. Global Router API
anahtarı models endpoint'i mevcutsa onunla, yoksa mevcut chat endpoint'ine boş JSON
gönderilip model eksikliği için bilinen 400 yanıtı kontrol edilerek denenir.
Bu kontrol model çalıştırmaz. Tek sonuç ilk bağlantıda otomatik kaydedilir;
çoklu sonuç yöneticiye seçtirilir. Elle yazılmış `ROUTER_BASE_URL` ayarı otomatik değiştirilmez; UUID tabanlı eski
Router hostname'i yalnızca aynı UUID'nin IP/port kaydına taşınabilir.

Kaydedilmiş UUID başka cihazla değiştirilmez. Public Hub arka plan görevi her
30 saniyede güncel IP/portu çözüp native API/worker ortak bağlantı kaydını atomik
günceller. Bulunamazsa endpoint kapatılır; 75 saniyeden eski endpoint kullanılmaz.
API/owner süreçlerinin seçimleri Redis lock ile koordine edilir. API anahtarı bu
dosyaya yazılmaz. Manuel adres seçimi keşiften bağımsızdır; legacy Router için kalır.
Docker kullanımı için ortak kalıcı data yolu ayrıca bağlanmalıdır; bridge ağda
otomatik mDNS keşfi desteklenmez.

## Güvenlik ve gerçek cihaz kontrolü

Faz 6 revizyonu (2026-10-04) için kesin kaynak
[üç repo ortak sözleşmesidir](../../../files/orion-yerel-giris-kesif-sozlesmesi.md).
Mevcut `_orion._tcp` web HTTP kaydı değişmez. Uygulanacak mobil HTTPS kaydı
`_orion-mobile._tcp`, Router güvenli kaydı `_orion-router-tls._tcp`;
ikisinde de zorunlu TXT `id`, `v=1`, `scheme=https`, SRV gerçek TLS portudur.
TLS/cihaz yetkisi gerçek soket listener'ından belirlenir; başlıklara güvenilmez.
HTTP hem cihaz token'ını hem mobil kullanıcı oturumunu reddeder. Bu gelecek
uygulama kuralları mevcut HTTP listener'larında uygulanmış kabul edilmemelidir.

UUID/id eşitliği kimlik kanıtı değildir. Bu sürüm TLS/pinning/QR eşleştirmesi
eklemez. UI “Kayıtlı” der. Yerel HTML ortak ağ adına dayanmaz; telefon veya uzak
API HTTP trafiği hâlâ şifrelenmez. Başka PC için ayarlarda güncel IP:8910 URL'si
gösterilir; IP değişebilir. Flutter web'i sunmak/başlatmak sonraki iştir.

Port 80 için LAN firewall izni veya hosts kaydı gerekmez. Telefon için private
ağ profilinde gerçek Python sürecinin TCP 8910 ve UDP 5353 erişimi gerekir.
AP/client isolation, misafir ağı veya firewall multicast'i engelleyebilir.
`setup_windows_shortcuts.ps1` artık eski TCP-80 firewall kuralını kapatmak için
isteğe bağlı `-DisableLegacyFirewallRule` seçeneği sunar; varsayılanı yalnızca
yerel adresleri gösterir, hosts dosyasına dokunmaz.

Flutter/Router ajanlarına repo kökündeki
`files/orion-yerel-giris-kesif-sozlesmesi.md` verilmelidir. Gerçek telefonda keşif,
iki bağımsız Hub/Router, IP değişmesi ve offline senaryoları ayrıca denenmelidir.
