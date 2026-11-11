# Yerel giriş ve LAN keşfi

Native Hub başlatıcısı yerel arayüz ile ağ bağlantısını ayırır. Sohbet yalnızca
Orion uygulamasındadır (bu PC'de Flutter penceresi, telefonda Flutter uygulaması);
Hub hiçbir porttan tarayıcıya sohbet sayfası sunmaz.

| Listener | Amaç | TLS koruması |
| --- | --- | --- |
| `127.0.0.1:8910` | Flutter penceresinin API'si, `/manage` ve `/panel` yönetim; `/`, `/hub`, `/dashboard` Flutter'a yönlenir | Yalnızca gerçek loopback HTTP; native token kabul etmez |
| `127.0.0.1:8909` | Eski doğrudan yönetim girişi; Router bağlantı döngüsü burada çalışır | HTTPS |
| `0.0.0.0:8443` | Orion uygulaması: pairing, REST/SSE/WS; `/connect` kopyalanan bağlantıyı uygulamaya verir | HTTPS ve SPKI pinning |

Eskiden diğer cihazların tarayıcısı için açılan `0.0.0.0:8911` (eski HTML sohbet)
2026-11-01'de kaldırıldı; ayrıntı: [Flutter ana arayüz geçişi](../../../docs/flutter-primary-ui-migration.md).
`127.0.0.1:80` kısa yolu (`http://localhost/`, `/orionrouter`) 2026-11-11'de
kaldırıldı: Orion penceresi ve yönetim paneli kısayollardan açılır, 80 başka
uygulamalara bırakılır.

Portlar env ile değiştirilebilir. Yerel giriş her zaman loopback'e bağlanır;
eski `ORION_HTTP_BIND_HOST`, `ORION_ADDRESS_GATEWAY`, `ORION_HTTP_EXTERNAL_PORT`,
`ORION_LOCAL_SHORTCUTS`, `ORION_LOCAL_SHORTCUT_PORT` ayarları kullanılmaz.
Hub Router için ortak adres veya proxy sunmaz.

```dotenv
ORION_LOCAL_PORT=8910
ORION_ROUTER_LOCAL_PORT=20128
ORION_MDNS=1
# ORION_MDNS_NAME=Hiro
# ORION_MDNS_INTERFACES=Wi-Fi,Ethernet
# ORION_MDNS_ID_FILE=<kalıcı data>/mdns-id
# ORION_ROUTER_CONNECTION_FILE=<kalıcı data>/router-connection.json
```

Yönetim paneli `http://127.0.0.1:8910/panel` adresindedir; Orion penceresindeki
panel düğmesi (`/api/v1/browser/panel/open`) bunu açar. HTTP yönetim `/manage` ve `/panel` sayfalarında gerçek
HTML sunar. Peer loopback değilse, Host yerel değilse veya herhangi bir forwarded
header varsa HTTP reddedilir; Docker private-peer override bunu aşamaz.
Native device token ve cihaza bağlı JWT, yerel HTTP’de de reddedilir.
`ORION_MDNS=0` ağda duyuru ve taramayı kapatır; QR ile eşleştirme kullanılabilir.

## Ortak adresler kaldırıldı, DNS-SD kaldı

`orion.local`, `orionpanel.local`, `orionadmin.local` duyurulmaz. `_orion-entry`
alias kaydı/election ve gateway adapter kaldırıldı. Hosts/tarayıcı ayarı gerekmez.
Servis keşfinin teknik `.local.` eki korunur; bu tarayıcı kısayolu değildir.

Hub ağda yalnızca Orion uygulaması dinleyicisini duyurur: `_orion-mobile._tcp`
(8443, TXT `id`, `name`, `v=1`, `scheme=https`, `fp`). Eski tarayıcı kaydı
`_orion._tcp` artık duyurulmaz ve taranmaz. Yalnızca aktif fiziksel RFC1918
IPv4'ler kullanılır; Docker/WSL/vEthernet/VPN arayüzleri filtrelenir.

## Flutter penceresi ve Hub kimliği

- `GET /api/v1/discovery/self`: id/name/desktop/local_entry.
- `GET /api/v1/discovery/hubs`: current_id ve yalnızca bu Hub'ın kaydı
  (`http://127.0.0.1:8910/hub`). Flutter penceresi açılırken Hub'ın hazır olduğunu
  bununla bekler. Başka Hub listelenmez; başka Hub'a Orion uygulaması QR ile bağlanır.

Eski `/api/v1/discovery/connection` (LAN tarayıcı adresleri) ve
`/api/v1/peers/<UUID>/...` (yerel tarayıcıdan başka Hub'a aktarım) kaldırıldı.

Panel `/panel` altında kalır; ayrı `/panel` HttpOnly/SameSite cookie ve mevcut
yetki kontrolleri korunur. Desktop browser seçimi yalnızca bu PC'de ve yetkili
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

Kaydedilmiş UUID başka cihazla değiştirilmez. Yönetim API'si (8909) arka plan görevi her
30 saniyede güncel IP/portu çözüp native API/worker ortak bağlantı kaydını atomik
günceller. Bulunamazsa endpoint kapatılır; 75 saniyeden eski endpoint kullanılmaz.
API/owner süreçlerinin seçimleri Redis lock ile koordine edilir. API anahtarı bu
dosyaya yazılmaz. Manuel adres seçimi keşiften bağımsızdır; legacy Router için kalır.
Docker kullanımı için ortak kalıcı data yolu ayrıca bağlanmalıdır; bridge ağda
otomatik mDNS keşfi desteklenmez.

## Güvenlik ve gerçek cihaz kontrolü

> Not (2026-11-01): Bu bölüm Faz 6 dönemine aittir. `_orion._tcp` web kaydı ve
> 8911 tarayıcı girişi o tarihten beri yoktur.

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

Hosts kaydı gerekmez. Telefon için private ağ profilinde gerçek Python sürecinin
TCP 8443 ve UDP 5353 erişimi gerekir. AP/client isolation, misafir ağı veya
firewall multicast'i engelleyebilir.

Flutter/Router ajanlarına repo kökündeki
`files/orion-yerel-giris-kesif-sozlesmesi.md` verilmelidir. Gerçek telefonda keşif,
iki bağımsız Hub/Router, IP değişmesi ve offline senaryoları ayrıca denenmelidir.
