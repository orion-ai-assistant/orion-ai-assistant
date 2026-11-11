# TTS PCM streaming — Hub, dashboard, Flutter

Seslendirilen her yanıt (asistan da düz sohbet de) tek yoldan gider; 2026-11-12'den beri `speech_mode` ve
asistandaki "Yanıtı erken seslendir" anahtarı yok (eski uygulamanın gönderdiği alan yok sayılır). Metin geldikçe
parçalara bölünür; **ilk 3 istek birer parça** (tek cümle), sonrakiler hazır olanlardan en çok 3 cümle / 600 karakter
olarak istenir (`adaptive_tts.py` `SINGLE_REQUESTS`, `sentence_segmenter.py`). Kısa yanıt bitmiş olsa da cümle cümle
gider. İlk parça yanıtın bitmesini beklemez (2026-10-31'de 250 ms bekleme denendi: kısa yanıtların çoğu tek istek oldu
ama ilk ses gecikti; istek sayısı yerine hız seçildi). Emoji (resim karakterleri, bayraklar, ten rengi, birleştiriciler)
hiç gönderilmez; `°` gibi metin işaretleri ve `1️⃣`'in rakamı kalır. Kesim yerleri, önem sırasıyla:
1. Satır sonu: modelin ayrı satıra yazdığı her şey (liste maddesi, `Tavşan: “...”`, başlık) ayrı parçadır.
   Sonraki satır küçük harfle başlıyorsa satır yalnız kaydırılmıştır, cümle sürer.
2. Cümle sonu: `. ! ? …` (arkasında kapanış tırnağı olabilir). Açık bir tırnağın içinde kesilmez
   (`“Bu yumurta çok akıllı! İçinden ...” demiş.` tek parça); kapanış tırnağından sonra küçük harfle süren
   konuşma etiketi (`“Muuuu!” diye`) cümleye dahildir. Kısaltma, tek harf ve ondalık sayıda kesilmez.
3. İki nokta cümle sonu değildir: `:` ile biten bir satır (`Hoca şaşkınlıkla sormuş:`) sonraki satırla birlikte
   okunur. Yalnız yanıtın ilk satırı (`Tabii, işte bir tane daha:`) tek başına gider ki ilk ses çabuk gelsin.
4. 3 kelimeden kısa parça (`Selam.` → `Selam. Bugün nasılsınız?` tek parça) ve harf/rakam içermeyen parça (`---`)
   bir sonrakine eklenir; 600 karakteri aşan cümle kelime sınırından bölünür.
5. Yanıtın sonunda kalan kısa parça, son parça henüz istenmemişse ona eklenir. İstenmişse kelime içeriyorsa tek
   başına gider (kısa yanıt da seslendirilir). Yalnız emojiden oluşan satır boş kalır, hiç gönderilmez (2026-10-31:
   sondaki `🙌` tek başına bir istek olup 1,2 sn ses üretmişti). Yanıtta seslendirilecek hiçbir şey yoksa ne istek ne
   uyarı olur.

Bir istekteki cümleler satır sonuyla birleştirilir (sesin yapıyı görmesi için); sohbete kaydedilen metin düzdür.
Hub, mevcut güvenilir Router session/TLS ve kullanıcı sağlayıcı ayarlarını kullanır.
Yeni bir WebSocket açılmaz; sohbet ses eventleri mevcut kullanıcı kapsamlı SSE/Redis
kanalından yayınlanır. Stop mevcut turn kapsamlı HTTP endpoint'ini kullanır.

## Sözleşme

`JobCreateRequest.origin_session_id`: isteği gönderen uygulama oturumunun rastgele
kimliği; izin veya cihaz eşleştirme kimliği değildir. Her tarayıcı belgesi ve Flutter
controller örneği ayrı kimlik üretir. Kimlik yoksa otomatik oynatma yapılmaz.

Tüm ses eventleri `chat_id`, `turn_id` ve `generation_id` taşır:

| Event | data |
| --- | --- |
| `audio_start` | `stream_id` (= turn), `origin_session_id`, `format: pcm`, `encoding: pcm_s16le`, `sample_rate: 24000`, `channels: 1`, `bits_per_sample: 16`, `arrival_ms` |
| `audio_chunk` | `stream_id`, `origin_session_id`, sıfırdan başlayan `sequence`, base64 `chunk` |
| `audio_done` | `stream_id`, `origin_session_id`, `status: completed / stopped / failed`, `total_bytes`, `chunk_count` |
| `audio` | Mevcut tamamlanmış ses payload'ı; ayrıca `origin_session_id`, `stream_id`, `status` ve `streamed` |

İlk geçerli parça geldiğinde `audio_start` yayınlanır. TCP parçaları sample sınırına
göre yeniden birleştirilir; PCM chunk'ları daima çift bayt uzunluğundadır.
HTTP 429 ve PCM başlık hataları ses yayınından önce yakalanır. Akış başladıktan
sonra başka Router adresiyle yeniden deneme yapılmaz. Okuma timeout'u toplam
konuşma süresine değil veri gelmeyen süreye uygulanır.

Hub akışın tamamını (en fazla 24 MB) toplar ve PCM'yi WAV olarak mevcut mesaj ses
alanına kaydeder. Başarılı `audio` kaydı `audio_done` ve sohbet `done` eventlerinden
önce yayınlanır. `arrival_ms` (arayüzde ♫) mesajın oluşturulmasından ilk ses verisinin Hub'a gelişine kadar geçen
süredir: Hub'ın Router'a iletmesi, modelin ilk cümleyi yazması ve TTS'in ilk sesi dahildir, cihazdaki 180 ms tampon
dahil değildir. 2026-10-31'de tipik 2,8 sn'nin ~2,5 sn'si sağlayıcılardandı (model 1,4 sn, TTS 1,0 sn).
Kullanıcının durdurduğu PCM akışının iletilmiş kısmı WAV olarak saklanır (`status: stopped`).
İlk örnek gelmeden durdurulursa kayıt veya yeni TTS isteği oluşmaz. Cümle bazlı (adaptive)
üretimde her parçanın (1-3 cümlelik istek) sesi geldiği anda çalınır. Parça hiç ses gelmeden hata verirse aynı
parça 1 saniye sonra bir kez daha istenir. Çalmaya başlamış bir parça koparsa yeniden istenmez (başı iki kez
duyulurdu), sonraki cümlelere geçilir; yarım örnek kalmaz. (2026-10-31'de parçayı tamamlanana kadar tutmayı
denedik: Gemini 3.1 TTS sesi parça parça verdiği için düz sohbette ses geç başladı, geri alındı.) Parça iki
denemede de olmazsa atlanır ve sonraki cümleler yine seslendirilir; kayıt `status: failed` ile, duyulan parçaların
metniyle sohbete kaydedilir ve dinlenebilir. Şu durumlarda yeniden denenmez ve
kalan metin seslendirilmez: HTTP 401/403/429 (kota, erişim) ve art arda iki parçanın (ikişer denemeyle)
başarısız olması. Router, `response_format: pcm` isteyene her zaman `audio/pcm` döner; bayt tahmini yalnız
biçim belirtilmediğinde çalışır (2026-10-31: ham PCM'in ilk örneği MP3 başlığına benzeyince yanıt `audio/mpeg`
diye etiketlenip o parça düşüyordu). Her başarısız deneme Hub loguna `Speech part N failed (attempt a of b): <hata>`
olarak yazılır; başarılı her parça için `Speech part N: ... first audio after X ms, P pieces over Y ms` satırı
sağlayıcının akıtıp akıtmadığını gösterir. 2026-10-31 ölçümleri: `gemini-3.1-flash-tts-preview`,
`gemini-3.8-flash-tts` ve `gemini-3.8-flash-lite-tts` ilk sesi 0,5-1,1 sn'de verip sesi çalınma hızının 3-6 katı hızla
akıttı (yükteyken bir kez ancak 1,2 katı); `gemini-2.5-flash-preview-tts` akıtmadı, parçanın tüm sesini 2,6-15 sn sonra
tek seferde verdi. İlk ses için bekleme `max(tts_timeout_seconds, 30 + karakter/10)`
saniyedir: Router yanıtı ancak ilk ses gelince başlatır ve Gemini yükteyken kısa bir cümle için 20 sn bekletti.
Hub, Router'a TTS isteğinde `Connection: keep-alive` gönderir: `Connection: close` ile Python 3.11'deki Router büyük
ses gövdesinin sonunu TLS kapanışında arada bir düşürüyordu (deneyde 150'de 6, hep aynı baytta). Tek istekli (whole) akışta hatalı akışın kısmi sesi saklanmaz.
WAV/MP3/OGG/AAC/FLAC yanıtları PCM gibi beslenmez; tamamlanmış dosya yolu kullanılır.

## Oynatma ve iptal

Yalnız `origin_session_id` eşleşen, seçili sohbet oturumu otomatik oynatır.
Diğer oturumlar da gelen PCM parçalarını toplar ve elle dinleyebilir.
Akış `audio_done` ile `status: failed` biterse (bir sonraki cümle yapılamadı) çalan ses kesilmez:
gelen örnekler sonuna kadar çalınır; yalnız kullanıcı durdurması (`stopped`) veya eksik/sırası bozuk
chunk sesi keser.
Canlı akış ve kayıt aynı oynatıcıyı kullanır; slider’ın toplam süresi parçalar
geldikçe büyür. Duraklatma ve seek, kaydı silmeden yerel çıkışı yeniden kurar.
Akış sırasında oynatıcının durdur düğmesi mevcut turn üretimini de keser. Canlı sesi alan oturumda tamamlanmış
kayıt ikinci kez otomatik başlamaz. Geçmiş yükleme ve yeniden bağlantı canlı
PCM kuyruğunu tekrar oynatmaz; kayıt üzerinden elle dinleme kullanılabilir.

İlk tampon 8640 bayt / 180 ms'dir; kısa seslerde `audio_done` bekleyen tamponu açar. Çalan ses biterse (sonraki
parça geç geldi) devam etmeden önce 300 ms birikir; parça (`audio_batch_done`) veya akış sonu bekleyen tamponu hemen
açar. 2026-10-31'de Gemini yükteyken sesi çalındığından ancak biraz hızlı verdi; 180 ms'lik tamponla her parçanın önünde
kısa bir kesik duyuluyordu.
Eksik/sırası bozuk chunk, durdurma, sayfa/sohbet değişimi ve bağlantı kesilmesi
yerel çıkış kuyruğunu temizler. Pencere odağı veya görünürlüğü değişince ses kesilmez.
Durdurma kaydedilmiş örnekleri silmez; sonradan tekrar oynatılabilir. Mikrofon düğmesi yerel sesi kesip mevcut işi
durdurur. Otomatik VAD algılayıcısı eklenmemiştir.

Hub HTTP akışını ilk parça öncesinde ve aktarım sırasında turn stop sinyaliyle
iptal eder. Router/upstream tarafındaki gerçek disconnect temizliği canlı sağlayıcı ile
ayrıca ölçülmelidir.

Flutter çıkışları: Android AudioTrack, Windows waveOut, iOS/macOS AVAudioEngine,
web AudioContext. Yeni paket bağımlılığı yoktur. Linux'ta canlı PCM çıkışı yoktur;
tamamlanmış kayıt mevcut oynatıcı yoluyla kullanılabilir.

## Doğrulama

- Hub: `test_tts_stream.py`, `test_audio_history.py`; HTTP başlık/hata kontrolü,
  ilk chunk öncesi ve akış içi iptal, PCM sample hizalama, WAV kaydı, dosya fallback.
- Dashboard: `pcm_stream.test.cjs`, audio timing/history ve stop/navigation testleri.
- Flutter: `pcm_speech_test.dart`, chat flow/voice/status recovery ve TTS playback
  testleri; çoklu oturum, kısa ses, eksik chunk, iptal ve bozuk çıkış temizliği.
- Android debug APK, Windows debug uygulaması ve standart Flutter web build.
- iOS/macOS native derlemesi, fiziksel cihaz ve canlı Gemini gecikme/stop testi
  bu Windows ortamında yapılmadı.
- Worker resilience seti ayrıca geçti. Daha geniş `test_pending_recovery.py`
  çalışması ilerlemeyince durduruldu; başarılı test sayısına dahil edilmedi.

## Model ses kataloğu ve sohbet arayüzü

Router `model_voice_groups` alanını Hub/account kataloglarından geçirir; Hub ve
Flutter bunu sağlayıcı listesiyle birlikte saklar. Ses seçimi önce tam model adına,
sonra model grubu desenine, sonra varsayılan gruba göre yapılır. Katalog erişilebilirken
başka gruptan kalmış seçim temizlenir; erişim yoksa kullanıcı tercihi korunur.
Gemini sağlayıcısı hem dosya hem stream isteğinde modelin ses kümesini ve
varsayılanını kullanır, geçersiz sesi upstream çağrısından önce reddeder.

Flutter boş araç dizilerini panel olarak göstermez. Yanıt içeriği başlamadan önce
üç hareketli nokta gösterir. Hub ve Flutter’da mesajı yeniden hazırlama düğmesi yoktur.

## Geçmiş yükleme ve ses süresi

Flutter geçmişi `history?include_audio=false` ile ister. Yanıt sesin base64 verisi
yerine `duration_ms` ve `audio_path` taşır; eski WAV kayıtlarının süresi yalnızca
başlık okunarak hesaplanır. Varsayılan tam geçmiş yanıtı Hub dashboard için korunur.
İlk oynatma veya slider üzerinde konum seçme, sohbet sahipliği kontrol edilen
`GET /api/v1/chats/{chat_id}/turns/{turn_id}/audio` yolundan kaydı yükler.
Flutter bu isteği oturum içinde paylaşır; metin önbelleğine ses payload'ını tekrar
yazmaz. İlk oynatma bağlantı gerektirir; eski gömülü kayıtlar desteklenmeye devam eder.

Sohbet başlıkları hesap kapsamındaki yerel önbellekten sunucu yanıtı beklenirken
gösterilir. Eşzamanlı liste yenilemeleri birleşir; geçmiş isteği disk okumasıyla
birlikte başlar ve bekleyen eski önbellek yazıları atlanır.
