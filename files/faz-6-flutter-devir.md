# Faz 6 — Flutter güvenli eşleştirme devir notu

Tarih: 2026-10-04. Bu belge uygulama değişikliği değildir; TLS/QR/cihaz token'ları
henüz uygulanmadı. Hub, Flutter ve Router için kesin kaynak
[ortak sözleşmedir](orion-yerel-giris-kesif-sozlesmesi.md).
[Hub deviri](faz-6-guvenli-eslestirme-devir.md) ve
[Router deviri](orion-router-yerel-adres-copilot.md) aynı revizyonu kullanır.

## Uygulanacak kurallar

- Native mobil keşif `_orion-mobile._tcp.local.` / NSD `_orion-mobile._tcp`.
  Zorunlu TXT `id=<kalıcı Hub UUID>`, `v=1`, `scheme=https`; SRV gerçek HTTPS
  portudur. `_orion._tcp` web HTTP kaydı değişmez. Router güvenli keşfi
  `_orion-router-tls._tcp` kullanır; Flutter doğrudan Router'a bağlanmaz.
- Keşif adres bulur; UUID/TXT fingerprint güven kanıtı değildir. İlk pin
  güvenilen Hub PC ekranındaki QR/metinden gelir. Başarısız claim'de eşleşme
  kalıcı kaydedilmez. IP değişince aynı UUID/pin korunur; pin keşiften yenilenmez.
  Kayıtlı Hub yoksa başka Hub'a sessiz geçiş, HTTP fallback veya redirect yoktur.
- `device_token` yalnızca cihaz yetkisidir; hesap girişi ayrı pinli HTTPS
  akışıdır. Mobil kullanıcı oturumu cihaza bağlanır; sohbet için ikisi de gerekir.
  Cihaz token'ı yönetici yetkisi vermez. HTTP portu cihaz token'ını ve mobil
  kullanıcı oturumunu reddeder; HTTPS owner/admin yollarını açmaz. Hub bu
  ayrımı gerçek dinleyiciden (soket) yapar; başlıklar TLS kanıtı olmaz.
- Eşleştirme kodu 40 bit / 8 base32 karakter, TTL 120 saniye, tek kullanımlık,
  sunucuda tüm süreçler arasında atomik tüketilir. Yanlış denemelerle kodlar
  iptal EDİLMEZ. Gerçek kaynak IP başına ve genel hız sınırı/backoff uygulanır;
  429/`Retry-After` beklemesi gösterilir. Hız sınırı kodun TTL'sini değiştirmez.
- Her Hub'ın UUID/pin/cihaz token'ı/mobil oturumu secure storage'da ayrı tutulur;
  başka Hub'a parola/davet/token aktarılmaz. Cihaz silinince sunucu token'ı ve
  bağlı mobil oturumları iptal eder, açık SSE/WebSocket bağlantılarını kapatır.
  İptal edilmiş cihazla otomatik yeniden bağlanılmaz; yeniden eşleştirme gerekir.
- Hub ve Router sertifikası yaklaşık 50 yıllıktır, otomatik/periyodik yenilenmez.
  Pin `fp = SHA-256(yaprak sertifika DER)`; SPKI/CA pini değildir. Pinli istemci
  sertifika süresine (`notBefore`/`notAfter`) bakmaz; yaprak pin eşleşmesini ve
  TLS özel anahtar sahipliğini doğrular. Sertifika değişirse yeniden eşleştirme
  gerekir; normal restart pin değiştirmez.
- REST, SSE, WSS ve dosya yükleme/indirme aynı pinli `HttpClient` fabrikasını
  kullanır. CA kökleri eklenerek pin kontrolü atlanamaz. Ses WebSocket'i
  `WebSocket.connect(..., customClient: pinnedHttpClient)` ile açılır.
  Her yeni bağlantıda pin kontrol edilir; ilk REST kontrolü WS için yeterli
  değildir. Pin uyuşmazlığında sır/istek gövdesi gönderilmez.
- Hub tarafında Router pini worker, model listesi ve ses dahil tüm Hub→Router
  bağlantılarında zorunludur; bu Flutter→Hub pininden ayrı bir güven kaydıdır.

| Bağlantı | Koruma |
| --- | --- |
| Web HTTP | TLS şifrelemesi ve sunucu kimliği doğrulaması yok |
| Flutter→Hub HTTPS/WSS (Faz 6 hedefi) | Her bağlantıda yaprak sertifika pini + TLS; cihaz ve kullanıcı yetkisi ayrı |

## Birlikte kabul kontrolü

Gerçek telefonda aynı pin/yeni IP başarısı, aynı UUID/farklı pin reddi,
REST/SSE/ses WSS/dosya bağlantılarının her birinde pin reddi, HTTP'de cihaz ve
mobil oturum reddi, cihaz silinince açık SSE/WS kapanması doğrulanır. Paralel
claim'de tek başarı, 120 saniyelik TTL, yanlış denemelerin kodu iptal etmemesi
ve IP/genel backoff Hub ile test edilir. Eşleşen pinle sertifika tarihi tek
başına ret üretmez. Web HTTP girişinin ve ayrı hesap veri sınırının korunduğu
doğrulanır. Flutter web bu native mobil TLS işinin kapsamı dışındadır.
