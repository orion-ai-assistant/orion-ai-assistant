# Bağlantı kurulumu ve yeni kurulum sihirbazı: plan

Durum: kararlaştırıldı, kodlanmadı (2026-11-01).

## Amaç

Bir laptop, başka bir PC'deki ana Hub'ı telefon gibi kullanabilsin. Laptop'a Router, veritabanı ve yapay
zekâ servisleri kurulmaz; yalnızca Orion penceresi ve hafif bir Hub ("aracı") kurulur.

## Kararlar

- QR ve yeni cihaz bağlama yalnızca ana Hub'ın kurulu olduğu PC'de olur. Aracı Hub 8443'ü açmaz, mDNS ile
  kendini duyurmaz, eşleşme isteği kabul etmez; telefonlar onu "Cihazları ara"da görmez.
- Aracı Hub, ana Hub'a telefon gibi bağlanır: kısa bağlantı/QR, SPKI sabitleme, cihaz anahtarı. Ana Hub'ın
  cihaz listesinde tek bir cihaz olarak görünür; yeni cihaz QR'ı ana Hub'ın PC'sinde açılır.
- Laptop'ta QR: kamera varsa pencere içinden okutulur (127.0.0.1 güvenli bağlam), yoksa bağlantı yapıştırılır.
- Hesaplar ve sohbetler ana Hub'da durur. Aracı yalnızca eşleştiği Hub'ları (kimlik, parmak izi, cihaz
  anahtarı) küçük bir yerel dosyada tutar.
- Tam Hub kurulu PC'de de aynı aracı kodu çalışır: giriş ekranında Hub seçici, varsayılan "Bu bilgisayar",
  son seçilen hatırlanır; "Hub değiştir" oturumu kapatıp seçiciye döner (her Hub'ın hesabı ayrı).
- Uzak Hub seçiliyken bilgisayarı yöneten bölümler (yönetim paneli, güncellemeler, gelen cihaz QR'ları,
  tarayıcı aracı uyarısı) yereldir/gizlenir; sohbet, modeller ve ses iletilir.
- Bağlantı sayfası (orion-ai-assistant.github.io/p) bilgisayarda "Bu bilgisayardaki Orion'da aç"
  (`http://127.0.0.1:8910/hub#pair=…`) ve "Bağlantıyı kopyala" düğmelerini gösterir; pencere eklemeden önce
  Hub adı ve adresiyle onay ister.

## Kurulum akışı (2026-11-02 kararı)

- Tek satırlık komut (`irm …/get-orion.ps1 | iex`, `get-orion.cmd`) artık kendisi kurmaz: installer'ı indirip
  açar. Sessiz kurulum yalnızca parametreyle (`-Mode merkez|baglan -Yes`).
- Installer: 1) kurulum yeri, 2) tür (merkez / bağlan), 3) seçilen yerde mevcut kurulumu algılayıp özet
  ("Router zaten kurulu: kullanılacak", "+ Hub kurulacak", isteğe bağlı kutular, başlangıçta aç, kısayol),
  4) Kur → tek ilerleme → Orion açılır.
- Bakım: installer bakım modunda açılır (özellik ekle/kaldır, model indir, taşı, kaldır); Başlat menüsünde
  "Orion Kurulumu" kısayolu. Penceredeki Ayarlar → "Sistem" kartı (yalnızca Hub'ın PC'si) servis durumunu
  gösterir, aç/kapat/yeniden başlat ve "Kurulumu aç".
- Hafif kurulum diğer bileşenler gibi otomatik güncellenir.

## Kurulum sihirbazı (installer yenilenecek)

Mevcut kurulum (get-orion → Install-Orion → install_new.py, konum seçme) eski kaldı. Yeni akış:

1. Konum seçimi (mevcut).
2. Soru: **"Sıfırdan Orion kur"** mu, **"Var olan bir Hub'a bağlanmak için yalnızca arayüzü kur"** mu?
3. Seçime göre bileşenler otomatik kurulur; kullanıcıya tek tek sorulmaz:

| Bileşen | Sıfırdan kurulum | Yalnızca arayüz |
|---|---|---|
| Hub | zorunlu | hafif (aracı) mod |
| Orion penceresi | zorunlu | zorunlu |
| Router (+ PostgreSQL) | zorunlu | yok |
| Hub PostgreSQL, Redis, worker | zorunlu | yok |
| TTS / STT | isteğe bağlı | yok |
| Embedding modeli | vektör veritabanı eklenince zorunlu | yok |
| Vektör veritabanı | ileride, zorunlu | yok |
| Güvenlik duvarı kuralları (8443, 9443, mDNS) | evet | hayır |

Bileşen listesi tek yerde tanımlanmalı (zorunlu / isteğe bağlı / kurulum türü); yeni bileşen (vektör DB,
embedding) eklemek yalnızca o listeye satır eklemek olmalı.

## İş sırası

1. Aracı modülü (Hub): uzak Hub ile eşleşme, eşleşme isteme, istek iletme (HTTP, SSE, WebSocket), testler.
2. Hafif Hub modu: veritabanı, Redis, worker ve 8443 olmadan yalnızca 8910 + aracı.
3. Flutter penceresi: hafif modda "Hub'a bağlan" ekranı (yapıştır, kamera QR, Cihazları ara); tam Hub'da seçici.
4. Kurulum sihirbazı: kurulum türü sorusu, bileşen tablosu, başlatıcının hafif modu.
5. Bağlantı sayfasının bilgisayar düğmeleri.
