# Architecture

Project architecture documentation for Orion AI Assistant.

## Veri ve bellek (2026-11-13)

- **PostgreSQL tek kayıttır.** Sohbetler, mesajlar, medya (`orion_chat_media`) orada. Redis yalnız canlı durum
  tutar (akan yanıt, kilitler, bildirimler); sohbet geçmişinin Redis kopyası yoktur.
- Listeler ve geçmiş sayfa sayfa okunur (anahtar sırasıyla sayfalama, geçmiş pencereleri); istemci medyayı
  önbelleğe alır (medya yanıtları değişmez, uzun süre önbelleklenir).
- Hub'ın PostgreSQL'i (port 5445) ve Router'ınki (port 5433) **küçük bellekle** başlar:
  `shared_buffers=32MB`, `max_connections=50`.
  - Neden: Windows `shared_buffers`'ı açılışta tümüyle ayırır (varsayılan 128 MB, sunucu başına) ve bu
    Windows'un bellek sınırına sayılır. 3-10 kullanıcı için 32 MB yeter; verinin geri kalanını Windows'un dosya
    önbelleği tutar. İkisi birlikte ~190 MB daha az ayırır.
  - Bağlantılar: Hub havuzu en fazla 8 (`kernel/registry.py`, boşta 5 dk sonra kapanır), Router havuzu en fazla
    20 (`database/manager.py`). 50 bunlara rahat yeter.
  - Ayar komut satırında verilir (`postgresql.conf`'a yazılmaz):
    `services/shared/native_postgres.py` ve Router'da `core/native_postgres.py` içindeki `MEMORY_SETTINGS`.
    İki dosya birebir aynı kalmalı.
  - Vektör veritabanı (pgvector) gelince de bu ayar uygundur. Dizin büyürse (yüz binlerce vektör) önce
    `shared_buffers`'ı 64-128 MB'a çıkarmak düşünülür; HNSW dizini kurarken geçici `maintenance_work_mem`
    yükseltmek daha çok fark eder.

## Hub'ın sunucuları (2026-11-13)

- Hub **iki** API sunucusu çalıştırır: yerel (127.0.0.1:8910, düz HTTP: PC penceresi ve `/panel` altında sahip
  paneli) ve telefon (0.0.0.0:8443, TLS, sabitlenmiş sertifika). Ayrı olmaları bilinçli bir güvenlik ayrımı:
  ağa yalnız TLS'li, telefon kimliği isteyen uygulama açık; pencere yalnız bilgisayarın içinden erişir.
- Üçüncüsü (127.0.0.1:8909, `HUB_PORT`) kaldırıldı: panelin uygulamasını ikinci kez sunuyordu ("yalnızca yönetim
  içindir") ve hiçbir istemcisi kalmamıştı. ~110 MB RAM. Güncelleme sonrası sağlık kontrolü artık yerel sunucuda
  (`/health`, `/panel/ready`). `HUB_PORT` yalnız eski Docker dosyalarında duruyor.

## Pencere gizliyken bellek (2026-11-13)

`OrionUi.exe` pencere gizlenince/küçülünce WebView2'ye `MemoryUsageTargetLevel.Low` verir, gösterilince Normal.
Sayfa (zamanlayıcılar, ses) çalışmaya devam eder. Ölçüm (taze profil, tek koşu): fiziksel RAM (working set)
gösterilirken ~505 MB, gizliyken ~160 MB; taahhüt edilen bellek (private bytes) neredeyse değişmez (~345 → 320 MB).

## Dinlerken pencerenin belleği (2026-11-13)

PC'de dinleme açıkken sayfada "Orion" algılayıcısı üç kez bulunur (ham mikrofonda algılama, Orion konuşurken
temizlenmiş seste algılama, Hub'a gitmeden önce doğrulama) ve Silero VAD iki kez. İşleri ayrı; birleştirmek
istek sırası gerektirir. Algılama modelinin wasm'ı 128 MB başlangıç belleğiyle derlenmişti, gerçekte ~6 MB
kullanıyor: başlangıç 16 MB'a indirildi (`packages/vad_audio/tool/shrink_wake_word_memory.py`, yeni model
dışa aktarılınca tekrar çalıştırılmalı). Sayfa süreci dinlerken ~760 MB → ~490 MB taahhüt. Kalan büyük parçalar:
Flutter'ın çizim motoru (CanvasKit, 64 MB), iki ONNX çalışma ortamı (Silero).

## Bellek ölçümü (2026-11-13)

- Denetçi her 10 dakikada `hub.log`'a `[MEMORY] local … MB, mobile … MB, worker … MB, redis … MB` yazar (her sürecin
  başlattıklarıyla birlikte ayırdığı bellek). Saatlerce büyüyen bir süreç burada görünür.
- Hub `PYTHONTRACEMALLOC=1` ile başlatılırsa API sunucuları da her 10 dakikada en çok büyüyen kod satırlarını yazar
  (`Memory local (pid …): … grew since start: …`, `orion/kernel/memory_trace.py`). Yalnız teşhis için: CPU ve
  bellek harcar. Yerel kütüphanelerin (numpy dışındaki C kodu, LiteRT) belleğini göstermez.
- numpy'nin OpenBLAS'ı Hub süreçlerinde tek iş parçacığıyla yüklenir (`run_local.py`): çekirdek başına tampon
  12 çekirdekte süreç başına +363 MB ayırıyordu.
- Bulunan sızıntı (izleme raporundan): yankı önleyicinin çıkış aygıtı denetimi (`audio/echo_cancel/loopback.py`) her 0,5 sn'de Windows ses
  numaralandırıcısını yaratıp bırakıyordu; her çağrı ~1,5 KB yerel bellek sızdırıyordu (python izlemesi görmez; ölçüm: 2000 çağrıda
  +3 MB) ve her seferinde yeni bir ctypes sınıfı (`GUID`, DLL) da yaratıyordu. Artık numaralandırıcı döngü iş parçacığında bir kez
  açılır (`RenderDeviceId`), sınıflar bir kez yaratılır (`_com()`, `pc_audio.py`'de de). Ölçüm: 6000 çağrıda 0 MB büyüme.
- Bileşen süreçleri (TTS/STT Python sarmalayıcıları, `services/shared/local_parts.py`) da `OPENBLAS_NUM_THREADS=1` ile açılır. Ölçüm (12
  çekirdek): yalnızca `import numpy` +363 MB ayırılmış bellek; `numpy` + `torch` 540 MB, tek iş parçacığıyla 187 MB.
- TTS motoru (llama-tts-server, CUDA, "düşük" mod = BaseLM'nin 7/28 katmanı GPU'da) ölçümü: ayrılan bellek (commit) 5,7 GB, fiziksel
  RAM 3,2 GB, GPU 3,5 GB. Ayrılanın ~3,5 GB'ı GPU belleğinin Windows'taki yansımasıdır (RAM'de yer tutmaz: özel bölgelerin 4,3 GB'ı ayrılmış
  ama yalnızca 0,8 GB'ı RAM'de). 0 katmanla bile 3,96 GB / GPU 2,65 GB (akustik model F16 1,8 GB, BaseLM katman sayısından bağımsız,
  KV önbelleği 32768 sabit; `-c` etkisiz). Katman başına ~120 MB GPU.

