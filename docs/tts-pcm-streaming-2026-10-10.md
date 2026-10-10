# TTS PCM streaming — Hub, dashboard, Flutter

LLM metni tamamlandıktan sonra tek TTS isteği açılır. Cümle bazlı üretim yoktur.
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
önce yayınlanır. `arrival_ms` ilk ses verisinin istekten itibaren geliş süresidir.
Kullanıcının durdurduğu PCM akışının iletilmiş kısmı WAV olarak saklanır (`status: stopped`).
İlk örnek gelmeden durdurulursa kayıt veya yeni TTS isteği oluşmaz. Hatalı akışın
kısmi sesi tamamlanmış kayıt olarak saklanmaz.
WAV/MP3/OGG/AAC/FLAC yanıtları PCM gibi beslenmez; tamamlanmış dosya yolu kullanılır.

## Oynatma ve iptal

Yalnız `origin_session_id` eşleşen, seçili sohbet oturumu otomatik oynatır.
Diğer oturumlar da gelen PCM parçalarını toplar ve elle dinleyebilir.
Canlı akış ve kayıt aynı oynatıcıyı kullanır; slider’ın toplam süresi parçalar
geldikçe büyür. Duraklatma ve seek, kaydı silmeden yerel çıkışı yeniden kurar.
Akış sırasında oynatıcının durdur düğmesi mevcut turn üretimini de keser. Canlı sesi alan oturumda tamamlanmış
kayıt ikinci kez otomatik başlamaz. Geçmiş yükleme ve yeniden bağlantı canlı
PCM kuyruğunu tekrar oynatmaz; kayıt üzerinden elle dinleme kullanılabilir.

İlk tampon 8640 bayt / 180 ms'dir; kısa seslerde `audio_done` bekleyen tamponu açar.
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
