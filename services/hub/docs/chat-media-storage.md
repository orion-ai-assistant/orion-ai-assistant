# Hub sohbet medyası

Kalıcı medya Hub'ın PostgreSQL veritabanındaki `orion_chat_media` tablosundadır.
Hub web, Flutter web ve telefon aynı kullanıcıya ait Hub sohbet referanslarını
kullanır. Router'ın admin log arşivi bu akışa dahil değildir.

## Yeni kayıtlar

- Görsel/video baytları yeniden kodlanmadan `BYTEA` olarak saklanır.
- Kullanıcı sesi mevcut 16 kHz mono PCM16 WAV normalizasyonundan sonra binary
  saklanır. Bu normalizasyon ve sağlayıcıya gönderim sözleşmesi değişmez.
- TTS'nin geçmiş kopyası WAV ise PyAV/libopus ile 32 kbps hedefli mono Ogg/Opus'a
  dönüştürülür. Kaynak ve çıktı süreleri tam decode ile karşılaştırılır.
  Diğer TTS biçimleri binary korunur. Canlı ses ve PCM olayları değişmez.
- SHA-256 aynı baytların **aynı sohbet içinde** tekrar yazılmasını önler.
  Farklı sohbetlerin kayıtları bağımsızdır; sohbetler arası global deduplikasyon yoktur.
- Mesaj, Redis geçmişi, kuyruk isteği ve ek yankısında `media_id`, `media_path`,
  MIME, boyut ve dosya adı bulunur. `content` içinde ikinci medya kopyası tutulmaz.
- Worker yalnızca model bağlamına seçilen eski mesajların medyasını yükler;
  Router'a mevcut data URL / input_audio / input_video sözleşmesini gönderir.
- Metin ekleri inline kalır. Harici görsel URL'leri indirilip arşivlenmez.

Eklerin tamamı transaction ile kaydedilmeden iş Redis'e kabul edilmez. DB hatasında
503 döner; istemci taslağı/eklerini koruyup tekrar gönderebilir. Birden fazla ekten
biri bozuksa yeni binary INSERT'leri geri alınır. Başarılı medya yazımından sonra
Redis kabulü başarısız olursa kayıtsız medya sohbet silinene kadar kalabilir.

TTS Opus kodlaması başarısızsa orijinal ses binary saklanır. TTS'nin DB yazımı
başarısızsa geçmiş için eski inline Base64 fallback korunur ve hata loglanır.
Bu fallback kalıcı mesaj yazımının başarı garantisi değildir; mevcut Hub
write-through davranışı DB hata durumunda Redis'e yazılan mesajlarla devam eder.

## Erişim ve istemciler

`GET|HEAD /api/v1/chats/{chat_id}/media/{sha256}` normal kullanıcı/cihaz doğrulaması
ve sohbet sahipliği kontrolünden sonra binary döner. Başka sohbetin hash'i erişim
vermez. Tek byte Range, 206/416, `nosniff`, sandbox CSP ve `private, no-store`
başlıkları vardır. Admin Router anahtarı kullanılmaz.

Eski `/turns/{turn_id}/audio` JSON endpoint'i korunur. Yeni TTS referansları da
bu endpoint'ten gerekirse Base64 okunabilir; güncel geçmiş arayüzleri binary yolu
kullanır. Hub web `include_audio=false` ister, eski sesleri de oynatınca yükler.
Önizleme kapandığında blob URL iptal edilir; sohbet değişince ses istekleri ve
blob oynatıcıları temizlenir.

Flutter SQLite'ta yeni ekler referans olarak `metadata_json` içinde kalır.
Telefon/masaüstü indirilen binary medyayı kullanıcı+Hub kapsamında hashlenmiş
anahtarlarla uygulamanın geçici dizininde tutar: 128 MiB sınırı, yazım sırasında
30 gün kullanılmamış dosyaların temizlenmesi. Bellekte en fazla 32 MiB / 16 medya
tutulur. Web yalnızca bu sınırlı bellek önbelleğini kullanır. Bu önbellekler kalıcı
arşiv değildir; işletim sistemi silebilir. Çevrimdışı yalnızca daha önce önbelleğe
alınmış medya açılabilir. Gönderilmemiş ekler mevcut taslak davranışıyla korunur;
uygulama kapanışından sonra taslak kurtarma bu değişikliğin kapsamı değildir.

## Silme, yedek ve dağıtım

Sohbet silindiğinde FK `ON DELETE CASCADE` o sohbetin tüm binary medyasını siler.
Medyanın ayrıca süresiz tutulması veya Router arşivinde olduğu gibi bağımsız
saklanması amaçlanmaz. Flutter önbelleği kendi kapasite/yaş politikasıyla temizlenir.
Tam PostgreSQL yedeği tabloyu içerir; seçici yedekte `orion_chat_media` eklenmelidir.

Hub ve iki arayüz birlikte güncellenmelidir: eski ek arayüzleri yeni referansları
çözemeyebilir. Tablo ilk medya kullanımında otomatik oluşturulur. PyAV zaten Hub
bağımlılığıdır. Router güncellemesi gerekmez.

Eski PostgreSQL Base64 mesajlarına, eski Flutter kayıtlarına ve zaten birikmiş
Redis stream kayıtlarına toplu göç/temizlik uygulanmaz. Eski mesajlar okunabilir.
Yeni tamamlanmış işler sahiplik/terminal kontrolünden sonra atomik XACK+XDEL ile
kuyruktan kaldırılır. Yeni saklama mevcut DB dosyalarını kendiliğinden küçültmez.

## Doğrulama

`tests/test_chat_media_storage.py` ayrı geçici PostgreSQL cluster'ı kullanır;
canlı DB bağlantısı kurmaz. Yerel `.local_db/postgres/bin` araçları yoksa atlanır.
Binary bütünlüğü, eşzamanlı deduplikasyon, transaction rollback, sohbet silme,
referans sahipliği, model payload'ı, Opus süresi, canlı WAV korunması ve GET/HEAD/Range
kontrol edilir. `tests/chat_media_loading.cjs` Hub web'in ihtiyaç anında yüklemesini
ve blob temizliğini kontrol eder. Flutter'da `test/chat_media_storage_test.dart`
SQLite referansları, binary yükleme/önbellek ve indirilmeyen kartları doğrular.

## Elle kontrol

1. Hub API ve worker'ı güncel kodla yeniden başlatın. Hub web'de sayfayı tam
   yenileyin; Flutter web ve telefon uygulamasını aynı sürüme güncelleyin.
2. Yeni bir test sohbetinde PNG/JPEG görsel gönderin. AI'nin görseli anlayabildiğini
   kontrol edin. Sohbetten çıkıp geri dönün; karta tıklayınca orijinal görsel açılmalı.
3. Aynı dosyayı aynı sohbete tekrar gönderin. Görsel için tek binary satır kalmalı.
   Farklı sohbete göndermek ayrı kayıt oluşturur.
4. Kullanıcı ses eki gönderin. Ses analizi ve geçmişten oynatma çalışmalı; binary
   kayıt mevcut normalizasyonun çıktısı olan `audio/wav` olmalı.
5. TTS açıkken yeni yanıt üretin. Canlı sesi dinleyin; sohbetten çıkıp geri dönün
   ve geçmiş sesini tekrar oynatıp ileri/geri sarın. Başarılı WAV dönüşümünde kayıt
   `audio/ogg`, geçmiş metadata'sı `format=ogg` olmalı. Durdurulan yanıtı da kontrol edin.
6. Tarayıcı Network panelinde sohbet açılırken `/media/` indirmesi yapılmadığını,
   ek açma/oynatma sırasında yetkili binary GET geldiğini kontrol edin. Eski Base64
   eklerde dosya zaten geçmiş JSON'unda bulunabilir; bu kontrol yeni kayıtlar içindir.
7. Telefonda bir medyayı açın, sohbetten çıkıp dönerek tekrar açın. Dosya önbelleği
   oluşmuşsa ikinci açılış indirme gerektirmemeli. Bağlantı kesildiğinde daha önce
   önbelleğe alınmış medya açılabilir; hiç indirilmemiş medya yükleme hatası göstermeli.
   Flutter web'de sayfa yenilenmesi bellek önbelleğini sıfırlar.
8. İkinci kullanıcıyla ilk kullanıcının medya adresini istemeyi deneyin: erişim
   reddedilmeli. SHA-256 kimliğini bilmek tek başına erişim vermez.
9. Yalnızca oluşturduğunuz test sohbetini silin. O sohbetin medya satırları da
   silinmeli. Diğer sohbetlerin kayıtları kalmalı. Eski sohbetlerden bir görsel ve
   TTS sesi açarak geriye uyumluluğu ayrıca kontrol edin.

PostgreSQL'de gerçek test sohbeti kimliğini kullanarak salt okunur kontrol:

```sql
SELECT id, mime_type, octet_length(data) AS size_bytes
FROM orion_chat_media
WHERE chat_id = 'TEST_CHAT_ID'
ORDER BY created_at;

SELECT id, role, content_json::jsonb -> 'attachments' AS attachments,
       content_json::jsonb -> 'audio' AS audio
FROM orion_chat_messages
WHERE chat_id = 'TEST_CHAT_ID'
ORDER BY id;
```

Yeni binary ek metadata'sında `data`/Base64 yerine `media_id` ve `media_path`
görülmeli. Başarılı yeni TTS saklamasında `audio.audio` Base64 alanı bulunmamalı.
Opus/DB fallback hatası varsa logla birlikte değerlendirin; fallback kaydı normal
başarılı sıkıştırılmış kayıt değildir.

Hub repo kökünde otomatik kontroller (PowerShell):

```powershell
$env:PYTHONPATH = 'services/hub/src;.'
& ./services/hub/.venv/Scripts/python.exe -m pytest services/hub/tests/test_chat_media_storage.py services/hub/tests/test_audio_history.py services/hub/tests/test_audio_metadata.py services/hub/tests/test_attachment_history.py services/hub/tests/test_attachment_labels.py services/hub/tests/test_direct_tool_history.py services/hub/tests/test_pending_recovery.py -q
node services/hub/tests/chat_media_loading.cjs
node services/hub/tests/audio_history_render.cjs
```

Flutter repo'sunun `main_app/app` dizininde:

```powershell
flutter analyze --no-pub
flutter test --no-pub --timeout 60s test/chat_media_storage_test.dart test/chat_flow_test.dart test/audio_retention_test.dart test/attachment_playback_test.dart
flutter build web --no-pub --no-wasm-dry-run
```

Bu otomatik kontroller gerçek telefon hoparlöründeki kaliteyi veya her tarayıcının
Opus oynatmasını doğrulamaz. Android/iOS ve hedef tarayıcılarda 4–5. adımların
uygulanması gerekir. Testler/değişiklikler canlı servisleri otomatik yeniden başlatmaz.

## TODO: iPhone TTS geçmişi uyumluluğu

Durum: Gerçek iPhone bulunmadığından iOS'ta Ogg/Opus geçmiş sesi oynatması
doğrulanmadı. Kullanıcının kararıyla iOS'a özel uyumluluk değişikliği ertelendi;
mevcut Opus saklama ve Flutter oynatma kodu bu notla değiştirilmedi.

- [ ] iPhone üzerinde yeni TTS geçmiş kaydının oynatılması, duraklatılması,
  ileri/geri sarılması ve uygulama yeniden açıldıktan sonra tekrar oynatılması
  kontrol edilmeli. Canlı PCM oynatması ile geçmiş Ogg/Opus oynatması ayrı testlerdir.
- [ ] Ogg/Opus yerel oynatıcıda desteklenmiyorsa uyumluluk yolu uygulanmalı.
  Öneri: Hub'daki tek Opus kaydını koruyup iOS isteğinde WAV'a çözülmüş binary
  sunmak ve Flutter iOS'ta geçici `.wav` dosyasından oynatmak. Bu yol henüz yoktur;
  ikinci kalıcı ses kopyası veya ikinci kayıplı kodlama gerektirmez, fakat indirme
  boyutu büyür. AAC/M4A alternatifi seçilirse dönüşüm ve saklama politikası ayrıca
  kararlaştırılmalı; yalnızca dosya uzantısını değiştirmek çözüm değildir.
- [ ] Düzeltme yapılırsa kullanıcı/sohbet yetkisi, önbellek kapsamı ve geçici dosya
  temizliği korunmalı; gerçek cihaz sonucu iOS sürümü ve oynatıcı sürümüyle yazılmalı.

Bu kontroller tamamlanmadan iOS TTS geçmişi desteği doğrulanmış sayılmamalı.
