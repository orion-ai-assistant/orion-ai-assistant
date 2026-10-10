# Yerel Hub: güvenli pending recovery

Bu koruma tek yerel Worker içindir. Redis `agent_jobs` stream'i, `orion-workers`
consumer group'u ve mevcut SSE olayları korunur. Redis 5.0.14.1 üzerinde
`XPENDING` ve `XCLAIM` kullanılır; `XAUTOCLAIM` kullanılmaz.

## İşlem kuralları

- İşletim sistemi dosya kilidi aynı kurulumda ikinci Worker'ı engeller. Redis'te
  süresiz sahiplik kaydı farklı kurulumların aynı kuyruğu eşzamanlı işlemesini
  engeller. PID ile süreç oluşturulma zamanı birlikte kontrol edilir.
- Sahip yalnızca yerel süreç kesin olarak ölmüşse değiştirilir. Başka bilgisayar,
  doğrulanamayan sahip veya canlı eski Worker varsa devralma reddedilir. Kilit
  süre aşımı, lease veya heartbeat yoktur.
- API mesajı kabul ederken `orion:job:<turn_id>` kaydını `queued` olarak yazar.
  Yeni Worker yalnızca başlangıçta doğrulanmış yerel pending kayıtları devralır.
- Router/tool işleminden önce kayıt atomik olarak `started` olur. Bu yazının
  başarılı olduğu doğrulanamazsa dış işlem başlatılmaz. İşaret Worker belleğinde
  değildir; Redis'te tutulur ve Worker yeniden başlamasından etkilenmez.
- Recovery sırasında `queued` yeniden işlenebilir. `started` veya eski/belirsiz
  kayıtlar tekrar çalıştırılmadan `failed` olur. Terminal kayıt yeniden işlenmez.
- Aynı tur için çift stream teslimi yerel olarak sıraya alınır. ACK, yalnızca
  terminal Redis kaydı mevcutsa ve Worker hâlâ sahibi ise gönderilir.
- Bir tool hata verirse aynı çalışan tur içinde o tool'un yeniden çalıştırılması
  da engellenir. Bu yerel takip, çökmeler arasında Redis `started` korumasıyla
  tamamlanır; kapsamlı bir dış sistem idempotency protokolü değildir.
- Normal sonuç kaydı, terminal journal ve aktif turun kaldırılması aynı Redis
  transaction'ında yapılır. Recovery/timeout hatası, sohbet durumu ve hata geçmişi
  aynı Lua işlemiyle kaydedilir. Pub/Sub başarısı ACK'in ön şartı değildir.
- `started`/`terminal` işareti, kayıp Redis yanıtı nedeniyle admission transaction'ı
  tekrar edilse bile `queued` olarak sıfırlanmaz.

## Bekleme ve arayüz

Her API girişinin lifespan'ında bağımsız denetim çalışır. Kuyruk süresi 60
saniyedir; tarama aralığı 5 saniyedir. Sürenin dolması yalnızca henüz başlamamış
işleri başarısız yapar. Aktif işler süreye bakılarak terk edilmiş sayılmaz.
`started` işin yerel sahibi kesin olarak ölmüşse API de işi başarısız yapabilir;
bunun için Worker recovery'sini beklemek gerekmez.

Arayüz aktif tur durumunu 3 saniyede bir sorgular; HTTP isteği en fazla 5 saniye
bekler. Kaçırılan `error`/`done` bilgisi kayıtlı durumdan öğrenilir. Sunucuya 30
saniye boyunca ulaşılamazsa bekleme görüntüsü kaldırılır; bunun sunucudaki işi
iptal ettiği iddia edilmez. Sağlıklı, uzun AI işlemleri bu eşikle durdurulmaz.
Yeniden hazırlama düğmesi metni giriş kutusuna koyar; otomatik göndermez. Ekler
gerekirse kullanıcı tarafından yeniden eklenir.

## Garanti sınırları

- Redis kaydı Worker çökmesine dayanır; Redis'in veri kaybına karşı disk garantisi
  vermez. AOF/RDB, eviction ve Redis kesintileri ayrıca değerlendirilmelidir.
  Terminal journal 7 gün saklanır; tamamlanmamış journal'a TTL konmaz.
- Dış araç tamamlandıktan sonra kayıt yapılmadan çökülürse sonuç belirsizdir.
  Otomatik tekrar yapılmaz, fakat kullanıcının yeniden göndermesi dış işlemi
  tekrar yapabilir. Dış sistem idempotency desteği olmadan exactly-once garantisi
  verilmez.
- Watchdog API'nin çalışmasına ihtiyaç duyar. API/Redis tamamen erişilemezse
  arayüz yalnızca bağlantı uyarısı verebilir; kalıcı hata kaydedemez.
- İşletim sisteminde canlı görünen ama takılan Worker, yalnızca süreç bilgisiyle
  uzun süren sağlıklı bir AI isteğinden ayrılamaz. Mevcut Router/tool timeout'ları
  korunur; yeni işlem heartbeat'i veya işlem süresi sınırı eklenmez.
- Dağıtık, çok bilgisayarlı Worker failover desteklenmez. Uzaktan sahibin öldüğü
  varsayılmaz. Stream verisi silinmişse pending recovery içeriği geri getiremez.
- Hata geçmişi Redis'te saklanır; mevcut PostgreSQL geçmiş yazma davranışı ve
  veritabanı şeması değiştirilmemiştir.

## Doğrulama

İzole Redis 5 test sunucusu kullanılır; canlı Hub verileri değişmez. Gerçek alt
süreçler mesajı okumadan önce, okuduktan sonra, `started` sonrası, simüle Router/
tool işlemi sırasında ve terminal sonuç kaydedildikten sonra sonlandırılır.
Gerçek üretim `job_processor` akışı da sahte Router yanıtıyla doğrulanır.
Dosya kilidi, sahiplik yarışı, çift teslim, gerçek Redis bağlantı kopması,
belirsiz marker yazısı, terminal kayıt hatası, bağımsız API denetimi ve kaçırılan
Pub/Sub sonuçları test edilir. Node testleri bekleme göstergesinin kapanmasını,
eski durumun yeni turu etkilememesini ve manuel yeniden hazırlamayı doğrular.

## Flutter istemcisi

Flutter sohbet controller'ı bekleyen yanıtların durumunu 3 saniyede bir aynı Hub
endpoint'inden okur. Kaçırılan SSE hata/bitirme olayı kayıtlı sonuçla tamamlanır;
geçmişten yüklenen kısmi yanıtlar da izlenir. Durum isteği 5 saniye, geçmiş
isteği 10 saniye ile sınırlıdır. 30 saniye boyunca ilgili turun durumu alınamazsa
bekleme belirsiz sonuç uyarısıyla sonlandırılır; sunucu işlemi iptal edilmez.
Sağlıklı `processing` yanıtına süre sınırı uygulanmaz.

İstek cevapları mesaj nesnesi, turn ve kullanıcı/Hub kapsamıyla doğrulanır.
Durdurulmuş veya değiştirilmiş mesajın eski durum cevabı yeni yanıtı kapatamaz;
controller kapanınca zamanlayıcılar iptal edilir. `failed_turn` geçmiş kaydı
manuel yeniden hazırlama düğmesini etkinleştirir. Düğme metni forma taşır,
otomatik göndermez; önceki araç sonucunun kontrol edilmesini ve gerekli
eklerin yeniden seçilmesini hatırlatır. Flutter testleri kaçırılan SSE,
geçmişten recovery, bağlantı kesintisi, yanıt vermeyen HTTP, uzun AI işlemi,
durdurma yarışı, kullanıcı değişimi ve manuel yeniden hazırlamayı kapsar.
