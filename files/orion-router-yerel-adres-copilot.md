# Orion Router için güncel görev

Eski `orionrouter.local` alias/gateway/port-80 tasarımı iptal edildi.
Router kendi API/UI portunda Hub'dan bağımsız çalışacak. Ortak kısa adres
duyurusu, port-80 gateway ve Hub proxy/başlatma/durdurma bağımlılıklarını kaldır.

Tam sözleşme aynı klasördeki **orion-yerel-giris-kesif-sozlesmesi.md** dosyasında.
**Faz 6 revizyonu (2026-10-04) kesindir; TLS henüz uygulanmadı.**
Hub, Flutter ve Router [aynı sözleşmeyi](orion-yerel-giris-kesif-sozlesmesi.md)
kullanır. Aşağıdaki HTTP uyumluluk notları pinli TLS bağlantısını HTTP'ye düşürmez.
Özellikle:

- `_orionrouter._tcp.local.` legacy HTTP için korunacak; güvenli bağlantı ayrı
  `_orion-router-tls._tcp.local.` kaydıyla duyurulacak (NSD `_orion-router-tls._tcp`).
  TLS TXT zorunlu alanları `id=<kalıcı Router UUID>`, `v=1`, `scheme=https`;
  SRV gerçek TLS portunu taşıyacak. `name`, `path=/v1`, `version` eklenebilir.
- Kalıcı UUID, PC adı içeren name, path=/v1, version TXT alanları olacak.
- Teknik UUID hostname'i kalabilir, ortak orionrouter.local duyurulmayacak.
- Yeni sürümlerde isteğe bağlı `GET /v1/discovery/self` id/name/version döndürebilir.
  Mevcut sürümün çalışması için yeni endpoint zorunlu değildir.
- Mevcut Bearer API anahtarı kuralları korunacak. Hub eksik models endpoint'i
  yerine mevcut chat endpoint'inin model çalıştırmayan hata yanıtını kullanabilir.
- IP değişimi, arayüz filtreleri ve goodbye kapanışı korunacak.
- Ağdan gelen UUID/TXT fingerprint güven kanıtı sayılmayacak. Router'ın güvenilen
  ekranındaki yaprak parmak izi Hub panelinde açıkça onaylanacak; onay/pin
  doğrulamasından önce API anahtarı gönderilmeyecek. Redirect/HTTP fallback yok.
- Kalıcı ECDSA P-256 anahtar + yaklaşık 50 yıllık self-signed sertifika kullanılacak.
  Sertifika otomatik/periyodik yenilenmeyecek; normal restart kimliği değiştirmeyecek.
  Pin `fp = SHA-256(yaprak sertifika DER)`; SPKI/CA pini değil. Pinli istemciler
  sertifika süresine (`notBefore`/`notAfter`) bakmayacak; yaprak pin ve TLS özel
  anahtar sahipliği doğrulanacak. Açık kimlik sıfırlama yeniden eşleştirme gerektirir.
- Hub→Router pin kontrolü her bağlantıda uygulanacak: bağlantı testi, worker
  sohbet/ses, model/grup/ses listeleri, dashboard API ve STT WebSocket dahil.
  API ve worker aynı kalıcı UUID/pin kaydını kullanacak; pin değişiminde eski
  bağlantılar/havuzlar ve cache'ler geçersizleştirilecek.

| Bağlantı | Koruma |
| --- | --- |
| Mevcut web HTTP | TLS şifrelemesi ve sunucu kimliği doğrulaması yok |
| Hub→Router HTTPS/WSS (Faz 6 hedefi) | Tüm bağlantılarda yaprak sertifika pini + TLS |

HTTP/HTTPS ayrımı gerçek dinleyiciden (soket) yapılır; istemci veya forwarded
başlıkları TLS kanıtı olmaz. Hub web `_orion._tcp` kaydı değişmez; mobil Hub
keşfi `_orion-mobile._tcp` (`id`, `v=1`, `scheme=https`) kullanır. Cihaz token'ı
Hub cihaz yetkisidir; Router API anahtarı veya Hub yönetici yetkisi değildir.

Hub `/panel/router/` üzerinden seçim yapar; kayıtlı UUID için güncel IP'yi
yeniden bulur. Kayıtlı cihaz kapalıysa başka Router'a sessizce geçmez. Model/API
istekleri Router'ın gerçek portuna gider; ortak hostname veya gateway'e gitmez.
