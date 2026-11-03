# Bileşenler (isteğe bağlı yerel yapay zekâ parçaları)

Orion kurulumu yalnızca çekirdeği kurar (Hub, pencere, Router). Hafıza (embedding), yerel dil modeli (LLM),
ses tanıma (STT) ve seslendirme (TTS) kullanıcı istediğinde, istediği yerden kurulur:

- **Ayarlar > Bileşenler**: her parça bir kutucuk. Kutucukta ne işe yaradığı, durumu, ortada model alanı,
  katlanmış "Gelişmiş ayarlar" ve altta tek sıra düğme var (ana düğme sağda). Seyrek işler (Kaldır, Yeniden
  başlat, değişiklik beklerken Durdur) kutucuğun ⋮ menüsünde: Kaldır hiçbir zaman Başlat'ın yanında durmaz.
- **Özelliğin kendisi**: parçası olmayan özellik kapalı kalır ve kullanıcının karşısına çıktığı yerde bunu
  söyler ("Bileşenleri aç" düğmesi o kutucuğu öne alıp işaretler). Parça kurulunca özellik kendiliğinden açılır.
- Kurulumda hiçbir şey sorulmaz.

## Nasıl çalışıyor

| Parça | Dosya |
|---|---|
| Katalog (parçalar, modeller, motor adresleri, işletim sistemine göre) | `services/shared/local_parts.json` |
| Donanım, durum, işler, servisin başlatılması | `services/shared/local_parts.py` |
| Modellerin yeri ve taşınması | `services/shared/parts_location.py` (+ `content_migration.py`) |
| Hub API (`/api/v1/parts`) | `services/hub/src/orion/api/parts_routes.py` |
| Hub açılınca kurulu parçaları başlatan bekçi | `services/hub/src/orion/api/main.py` (`keep_parts`) |
| Flutter sayfası, model paneli, `offerPart` | `orion-ai-assistant-flutter/.../features/settings/components_page.dart` |

- **Nerede durur:** kurulu Orion'da `%LOCALAPPDATA%\Orion\Parts` (modeller, kurulumun models konumu varsa
  orada: `<konum>/parts/<parça>`), kaynaktan çalışan Hub'da depo içindeki `.local_parts/` (modeller
  `.local_parts/models/<parça>`). Motorlar her zaman `Parts/engines` altında; yalnızca modeller taşınır.
- **Modellerin yeri** (sayfanın üstü: "Modeller: <yol> · X GB · Değiştir"; `services/shared/parts_location.py`,
  `/api/v1/parts/location/*`): yer `local_parts.location()`. Kurulu Orion'da `install.json`'un `locations.models`
  konumu; kaynak Hub'da `.local_parts/models`, taşınınca kendi küçük kaydı (`.local_parts/location/install.json`;
  kurulu Orion'un kaydına hiç yazmaz).
  - Değiştir: alan şimdiki yerle dolu açılır (üstünde kayan etiket yok); yol yazılır ya da "Gözat…" Hub bilgisayarında Windows'un klasör penceresini açar (Orion
    penceresinin önüne getirilir, `to_front`). Hub klasörü denetler (`check`: modeller tam olarak nereye gider,
    o diskte ne kadar boş, neden olmaz); sayfa sığıp sığmadığını kendisi hesaplar ("Hedefte yer yetmiyor",
    Taşı kapalı). İçinde başka dosya olan klasör seçilirse modeller onun `Orion Models` klasörüne gider. Ağ,
    çıkarılabilir ya da NTFS olmayan disk, dolu klasör, şimdiki yerin içi kabul edilmez.
  - Taşı: iş kendi sürecinde (`python -m services.shared.parts_location move <yol>`, durumu `jobs/move.json`).
    Çalışan parçalar durur; `content_migration` her dosyayı kopyalar, kopyayı ve kaynağı SHA-256 ile denetler,
    kayıttaki yeri değiştirir; duran parçalar yeni yerden yeniden başlar. Sayfa aşamayı (okunuyor, kopyalanıyor,
    denetleniyor), payı, boyutu ve hızı gösterir. Taşınırken hiçbir parça başlamaz, inmez, silinmez
    (`local_parts.moving`; bekçi de bekler). Yer yetmeyecekse iş hiç başlamaz.
  - Eski klasör: kopya denetlenip yer değişince Hub onu kendisi siler (kullanıcının isteği, 2026-11-03: "başarıyla
    taşıdıktan sonra sil orayı"). İçinde Orion dışından açık tutulan bir dosya yüzünden silinemezse sayfada "Eski
    klasör silinemedi: <yol> · X GB [Kalsın] [Sil]" çıkar (Sil önce sorar). Yarıda kalan ya da başarısız taşıma eski
    klasöre hiç dokunmaz, eski yeri kullanmaya devam eder; yalnızca kendi kopyaladığı (kendi işaretli) klasör
    silinir ve günlüğü `migrations/undone/` altına alınır, bir sonraki taşımayı engellemez. Yer değişmişse
    (kayıt yeni yeri gösteriyorsa) taşıma bitmiş sayılır.
  - Kurulumun kaydında models konumu yoksa yer değiştirilemez (Değiştir görünmez).
- **İşler:** indirme/kurulum Hub'ın açtığı ayrı bir süreçte çalışır (`python -m services.shared.local_parts job`).
  İnternetten inen her şeyin (motor, model, GGUF başlığı) sertifikasını Windows doğrular (`public_opener`,
  `windows_trust.py`, güncellemelerdeki gibi). Python'un kendi denetimi GitHub'ın yeni Sectigo zincirinde
  "unable to get issuer certificate" verip GitHub'dan gelen motorları (llama.cpp, TTS, STT) hiç indirmiyordu
  (2026-11-03, bu bilgisayarda). Paketli Python'da `truststore` yoksa eski denetim kullanılır.
  Pencereyi kapatmak işi durdurmaz; tam inen dosyalar (motor parçaları dahil) kalır, yarım kalan dosya baştan iner (kaldığı yerden eklemek bozuk model verebiliyordu). Durum
  `jobs/<parça>.json`'da: aşama, bayt, toplam, hız, hata, indirilen dosyaların rolü (`fetch`: model, ek).
  İş yalnızca eksik olanı adlandırır ve sayar: yalnızca mmproj iniyorsa "Ek dosya indiriliyor: Görsel ve ses
  anlama · x / 941 MB" (eskiden "Model indiriliyor" deyip var olan modeli de sayıyordu). Kutucuğun durumu her
  zaman servisin durumudur: Uygula için indirme sürerken "Çalışıyor" kalır, iş ayrıca gösterilir; "Kuruluyor"
  yalnızca yeni kurulan parçada.
- **Motor:** llama.cpp tek sefer indirilir, LLM ve hafıza paylaşır (`engines/llama.cpp-<sürüm>/<cuda|vulkan|cpu>`).
  Kurulumla gelmez (CUDA ~616 MB, Vulkan ~30 MB, CPU ~18 MB; hangisi gerekeceği seçilen donanıma bağlı).
  "Kaldır + indirilen dosyaları da sil" motoru, onu kullanan başka kurulu parça yoksa siler (`unused_engines`).
- **Model bilgileri modelin kendi dosyasından:** katalog yalnızca modelin adını, adresini ve boyutunu tutar.
  Bağlam sınırı, vektör boyutu (embedding) ve ek dosyanın (mmproj) neyi anladığı (görsel/ses) GGUF başlığından
  okunur: indirilmişse dosyadan, indirilmemişse internetten dosyanın ilk birkaç KB'ı (`headers.json` önbelleği).
- **Sayfa neden anlık (yeni özellikte de böyle kalmalı):** Hub yalnızca gerçekleri gönderir: diskte ne var
  (her modelin ana ve ek dosyası ayrı), her donanımın motoru hazır mı ve kaç MB, her modelin kendi sınırları,
  ne çalışıyor ve neyle, kayıtlı seçim. Seçimden hesaplanan hiçbir şeyi göndermez (toplam indirme, boyut,
  bağlam sınırı, "uygulanmadı"). Ekranda görünen her şey sayfada tek yerde, `_PartView.of` içinde, bu
  gerçeklerden ve kullanıcının son seçiminden hesaplanır; kutucuklar Hub'ın verisini doğrudan okumaz. Bu kuralı
  iki test korur: Hub'da `test_the_status_holds_facts_only_so_no_choice_waits_for_it` (seçim değişince
  gerçekler değişmez), sayfada "the Hub's answer changes nothing on screen" (Hub'ın yanıtı geldiğinde ekranda
  tek harf değişmez).
- **Ayar değişince:** seçimler (model, görsel anlama, gelişmiş ayarlar) hemen kaydedilir ama hiçbir şeyi
  yeniden başlatmaz. Çalışan parçada kutucuk "Uygulanmamış değişiklikler" kutusunda her farkı satır satır
  gösterir ("Bağlam boyutu: 8192 → 4096", "Görsel ve ses anlama: kapalı → açık"); altında "Geri al" (çalışana
  döner) ve "Uygula ve yeniden başlat" (gerekirse önce indirir: "İndir ve uygula · ~X MB") var (`apply_choice`,
  `/api/v1/parts/<id>/apply`). Durmuş parça yeni ayarları Başlat'ta (gerekirse "İndir ve başlat"), kurulmamış
  olan Kur'da alır. Neyle çalıştığı `run/<parça>/applied.json`'da. Başlatılamayan parça durur ve öyle kalır;
  servis günlüğü her başlatmada sıfırlanır, bellek yetmezliği gibi bilinen hatalar Türkçe söylenir.
- **Modeller paneli:** her model bir kutucuk: adı ve etiketleri (Önerilen, Seçili, Çalışıyor), altta her
  dosyaları bizim modellerde de kendi modellerinde de aynı düzende: bir "Model" satırı (yanında küçük ve gri token
  sınırı / vektör boyutu, birden çok dosyası varsa kaç tanesinin hazır olduğu), altında dosyaları virgülle (eksik olan
  soluk renkte taşıması gereken adıyla ya da ne için olduğuyla), ek dosya (mmproj) kendi satırında; boyutu ve satırın sonunda o dosyanın kendi düğmesi (yoksa İndir, varsa
  Sil, inerken dönen simge). Durum yazısı yok: düğme dosyanın durumunu söyler. Katalog modelinin adının yanında kaynak
  düğmesi: modelin geldiği sayfayı açar (`source`: kataloğun `source`'u, yoksa Hugging Face deposunun sayfası;
  kullanıcının kendi modelinde yok). Ek dosyanın (mmproj) satırının
  başında onu kullanma kutusu var. Kendi modellerinde adın yanında klasör simgesi. Kutucuğa dokunmak modeli
  seçer.
- **Silme ve indirme dosya dosya:** modelin dosyasının Sil'i modeli (ek dosyasıyla), ek dosyanın Sil'i yalnızca
  onu siler (seçili modelinse görsel anlama kapanır); İndir yalnızca o satırın dosyasını indirir
  (`?file=model|extra`). Kendi modelinde her dosyanın Sil'i yalnızca o dosyayı siler, son dosyayla klasör de
  gider. Seçili olan dahil her model silinebilir; ne silineceği önce söylenir. Çalışan servisin açık tuttuğu
  dosya için servis önce durur, dosya sonra silinir (`drop`, `apply` içinde); parça Başlat'a kadar durur.
- **Kendi modellerin:** her parçanın klasörü `models/<parça>/custom/<ad>/`; birden çok motoru olan parçada
  motor başına bir klasör (`models/tts/custom/OmniVoice/<ad>/`). Panelde "Kendi modellerin > Ekle": motor
  seçilir (birden çoksa; Orion'da olmayan motor "yakında" diye görünür, seçilemez), ad verilir, o adla klasör
  açılır ve Explorer'da öne getirilir (`_to_front`: Hub arka planda olduğundan Windows pencereyi bazen geride
  açıyordu). Dosyalar kopyalandıkça satırlar işaretlenir, kopyalanan dosyada dönen simge. Elle doldurulan
  klasör de aynı biçimde görünür. Hangi dosyanın ne olduğu dosyaların içinden okunur, adından değil
  (`USER_KINDS`):
  - **LLM ve hafıza:** GGUF (+ isteğe bağlı mmproj), başlıktaki mimariden ayırt edilir. Bağlam sınırı, vektör
    boyutu, havuzlama dosyadan okunur.
  - **STT (whisper.cpp):** tek dosya, adı ne olursa olsun (`ggml-small.bin`, `ggml-large-v3-turbo-q5_0.bin`...).
    Whisper modeli olduğu başlığından okunur (`whisper_meta`): `ggml` imzası, Whisper'ın sabit ses (1500) ve metin
    (448) pencereleri, 80 ya da 128 mel bandı. Silero VAD dosyası ve eski llama.cpp ggml dosyaları böylece
    ayrılır. Klasörde birden çok Whisper dosyası varsa hangisi olduğu tahmin edilmez ("many"). Yanlış biçimde bir
    model dosyası adı ve biçimiyle söylenir (`_other_format`): "model.bin: faster-whisper (CTranslate2) biçiminde;
    whisper.cpp'nin ggml .bin dosyası gerekir" (Systran'ın dosyaları), GGUF, başka bir ggml, safetensors, ONNX,
    PyTorch. Yanlarındaki `config.json` gibi dosyalar model sayılmaz, söylenmez. Ses servisleri internete çıkmaz (`HF_HUB_OFFLINE`): eksik bir dosyanın yerine
    hiçbir şey indirilmez.
  - **TTS:** motor kullanıcının seçimi (OmniVoice; VoxCPM2 yakında). OmniVoice'un iki dosyası başlıklarındaki
    mimariden (`omnivoice-lm`, `omnivoice-tokenizer`) ayırt edilir; tanınmayan dosya adıyla söylenir.

  "Kaldır + indirilen dosyaları da sil" kullanıcının modellerini ayrıca sormadan silmez.
- **Çalıştırma:** her parça Orion'un süreç yöneticisiyle (`process_lifecycle`) çalışır, yalnızca 127.0.0.1'i
  dinler, Hub durunca durur. Hub'ın sahip süreci dakikada bir kurulu parçaların açık olduğunu denetler (çöken
  geri gelir; kaldırılan açılmaz).
- **Yetki:** durumu oturum açmış herkes görür (telefon salt okunur). Kurma, kaldırma, ayar yalnızca Hub
  bilgisayarındaki Orion penceresinden (`update_routes.local_control`, güncellemelerle aynı kural).
- **Donanım:** `nvidia-smi` ile NVIDIA kartları ve bellekleri; Windows'ta diğer kartların adları. Varsayılan:
  ekran kartı varsa Vulkan (NVIDIA'da CUDA da seçilebilir; TTS ve STT NVIDIA'da önce CUDA,
  AMD/Intel kartta Vulkan: parçanın `hardware` sırası, `choices()`), yoksa CPU; model kart belleğine sığıyorsa tüm katmanlar kartta.
  Gelişmiş ayarlar: nerede çalışsın, hangi kart (birden çok varsa), bağlam, eşzamanlı istek (kartta tüm katmanlar kartta).
- **CUDA'nın alt sınırı:** kartın compute capability'si ayrı bir `nvidia-smi --query-gpu=index,compute_cap`
  çağrısıyla okunur (bu alanı bilmeyen eski sürücüde kart listesi kaybolmasın diye ayrı; okunamazsa kart uygun
  sayılır, `hardware.json`'daki eski önbellek de öyle). Her CUDA tarifinde `min_compute` var; hiçbir NVIDIA kart
  o sınıra yetmiyorsa o parçada CUDA seçeneği çıkmaz, parça Vulkan'a (varsa) ya da CPU'ya geçer; kaydedilmiş bir
  CUDA seçimi de (`effective()`). Birden çok kartta "Hangi ekran kartı" yalnızca o parçanın CUDA derlemesinin
  çalıştığı kartları listeler. Sınırlar, indirilen dosyaların içindeki GPU kodundan (fatbin: ELF = hazır kod,
  PTX = sürücünün o karta derlediği ara kod; en düşük mimari sınırdır) 2026-11-03'te okundu:

  | Motor | `min_compute` | Kaynak |
  |---|---|---|
  | llama.cpp b11026 (hafıza, LLM) | 5.0 | resmi `llama-b11026-bin-win-cuda-12.4-x64.zip` `ggml-cuda.dll`: ELF sm_86, sm_89; PTX sm_50, 61, 70, 75, 80, 90 |
  | OmniVoice `tts-v1.0.0` (TTS) | 7.5 | `CMakeLists.txt` `CMAKE_CUDA_ARCHITECTURES=75-virtual;80-virtual;86-real;89-real` (CUDA 12.1 ile); `bin/cuda/ggml-cuda.dll`: ELF sm_86, 89; PTX sm_75, 80 |
  | whisper.cpp v1.9.5 (STT) | 5.0 | resmi `b5454/whisper-bin-win-cuda-12.4.0-x64.zip` `ggml-cuda.dll`: ELF sm_86, sm_89; PTX sm_50, 61, 70, 75, 80, 90 (llama.cpp'ninkiyle aynı); yanındaki `cublas64_12.dll` (CUDA 12.4) sm_50'den başlıyor. 2026-11-03'e kadar STT faster-whisper/CTranslate2 idi ve sınırı cuDNN yüzünden 7.5'ti |

  Örnek: GTX 1060 (6.1) hafıza, LLM ve STT'de CUDA'yı görür, TTS'te Vulkan ya da CPU; GTX 16xx/RTX 20xx (7.5) ve
  sonrası her parçada CUDA'yı görür. Motor ya da cuDNN sürümü değişince sınır yeniden okunur:
  `cuobjdump --list-elf --list-ptx <dll>` (CUDA 12.1'inki yeni cuDNN/cuBLAS DLL'lerinde sm_100+ girdilerinde
  takılıyor; o zaman fatbin başlıkları doğrudan okunur: `0xBA55ED50` imzası, her girdide tür ve `sm` alanı).

Doğrulandı (2026-11-02, bu bilgisayarda): hafıza parçası CPU motoru + Jina v5 Omni Nano ile gerçekten indirildi,
kuruldu, `/health` "ok" ve `/v1/embeddings` vektör döndü; Kaldır servisi durdurdu; bekçi kurulu parçayı açtı;
ayar değişince yeniden başladı.

## Neyi kim yayınlar

Bizim GitHub yayınlarımızda yalnızca **bizim derlediğimiz** dosyalar durur (STT'nin Vulkan zipi, TTS'in zipleri).
Başkasının yayınladığı her şey (resmi whisper.cpp/llama.cpp zipleri, modeller, VAD) **kaynağından** iner: sabit
sürümden (Hugging Face'te `/resolve/<commit>/`, GitHub'da sürüm etiketli adres) ve katalogdaki SHA-256 ile
denetlenerek. Kullanıcının kararı (2026-11-03): "ya hepsini alcaz ya da hiçbirini"; yarısını aynalamak tutarsız.

## Ses tanıma (STT): whisper.cpp

2026-11-03'te faster-whisper/CTranslate2'den whisper.cpp'ye geçildi (kullanıcının kararı): CTranslate2'nin ekran
kartı desteği yalnızca CUDA, yani AMD/Intel kartta ses tanıma işlemcide kalıyordu. whisper.cpp ggml tabanlı,
CUDA, Vulkan ve CPU'da çalışıyor.

- **Servis:** `services/stt/whisper/api.py` dış katman olarak kaldı (REST, canlı WebSocket, ipucu cümlesi,
  halüsinasyon süzgeçleri, dil kataloğu). Model çağrıları `whisper_server.py`'ye gider. O, motor klasöründeki
  `whisper-server.exe`'yi kendi loopback portunda alt süreç olarak açar (TTS'in `tts-server`'ı gibi) ve
  faster-whisper'ın `transcribe`/`detect_language` arayüzünü taklit eder; bu yüzden canlı akışın kodu ve
  testleri değişmedi. Karşılıklar:
  - `beam_size`, `language`, `initial_prompt` → `prompt`, `temperature` (tek değer ya da faster-whisper'ın
    0, 0.2... dizisi → `temperature_inc`), `no_speech_threshold` → `no_speech_thold`, `log_prob_threshold` →
    `logprob_thold`, `without_timestamps` → `no_timestamps`.
  - Yanıt `verbose_json` (her parçanın `no_speech_prob`'u canlı süzgeç için). `no_language_probabilities=true`
    gönderilir; yoksa sunucu dil olasılıkları için sesi bir kez daha kodlar.
  - `vad_filter` + `vad_parameters` → whisper.cpp'nin kendi Silero VAD'ı (`--vad -vm ggml-silero-v6.2.0.bin`,
    istek başına `vad_threshold`, `vad_min_speech_duration_ms`, `vad_min_silence_duration_ms`,
    `vad_speech_pad_ms`; parametre verilmezse faster-whisper'ın varsayılanları).
  - `detect_language` → `detect_language=true`; sunucu dilin tam adını döndürür ("turkish"), koda
    whisper.cpp'nin tablosuyla çevrilir (`LANGUAGES`, `stt_languages.json`'daki 100 dilin aynısı; testi var).
  - Karşılığı olmayanlar: `compression_ratio_threshold` (whisper.cpp yeniden denemeye entropiyle karar verir) ve
    `condition_on_previous_text` (sunucu istekler arasında metin taşımaz).
  - whisper-server ffmpeg olmadan yalnızca WAV/MP3/FLAC okur. REST'e gelen her biçim (Router'ın oyun alanından
    webm/ogg/m4a) servis içinde PyAV ile çözülür (`av` zaten Hub'ın paketli Python'unda) ve 16 kHz WAV gider.
  - CPU'da `-t <çekirdek sayısı>` (Ryzen 5600X'te 12 iş parçacığı 6'dan %20 hızlı), kartta sunucunun
    varsayılanı (4).
- **Motor tarifi** (`engines.whisper.cpp`, `exe: whisper-server`: indirilen motorda bu program yoksa kurulum
  reddedilir). Her tarif yalnızca o donanım seçilince iner:

  | Donanım | Parça | Boyut |
  |---|---|---|
  | vulkan | `stt-whispercpp-v1.9.5/whisper-cpp-v1.9.5-windows-vulkan.zip` (bizim derlememiz) | 15.5 MB |
  | cuda (`min_compute` 5.0) | whisper.cpp'nin `b5454/whisper-bin-win-cuda-12.4.0-x64.zip`'i (cuBLAS ve cudart içinde) | 685 MB |
  | cpu | whisper.cpp'nin `b5454/whisper-bin-x64.zip`'i (işlemciye göre seçilen `ggml-cpu-*.dll` varyantları) | 8.9 MB |
  | hepsi | Silero VAD, whisper.cpp biçiminde: Hugging Face `ggml-org/whisper-vad`, revizyon `9ffd54a1`, `ggml-silero-v6.2.0.bin` (zip değil, olduğu gibi motor klasörüne konur) | 0.9 MB |

  `b5454`, whisper.cpp v1.9.5 ile aynı commit'in (d1be6fde) gece derlemesi; v1.9.5 etiketinde dosya yok. Her
  parçanın SHA-256'sı katalogda (resmi zip'lerinki GitHub'ın kendi özetiyle, VAD'ınki HF'nin LFS özetiyle aynı).
  Motor parçası zip ise açılır, değilse olduğu gibi konur (`_install_engine`). CUDA'da eski STT GPU
  paketlerinin (cuBLAS + cuDNN + NVRTC, ~1.37 GB) yarısı iner, cuDNN gerekmez.
- **Vulkan zipi:** whisper.cpp'nin yayınında Windows Vulkan derlemesi yok. `services/stt/whisper/engine/build_vulkan.bat`
  v1.9.5'i `%TEMP%\wcpp`'ye indirir, `%TEMP%\wcpp-vk`'de derler (Vulkan SDK + VS 2022, kısa yol: 260 karakter
  sınırı; `GGML_NATIVE=OFF`). `python services/stt/whisper/engine/package.py` zipi (whisper-server + 5 DLL +
  whisper.cpp lisansı) yapar, SHA-256'yı ve boyutu yazar. Yayın `gh release create ... --latest=false` ile (güncelleme akışı `feed` etiketinden okunur, ama "Latest"
  rozetini başka yayınlara kaptırmamak için). MSVC çalışma zamanı (MSVCP140, VCRUNTIME140, VCOMP140) resmi
  zip'lerde de, llama.cpp ve TTS'te de System32'den gelir.
- **Modeller:** Hugging Face `ggerganov/whisper.cpp`, revizyon `5359861c739e955e79d9a303bcbc70fb988958b1`
  (adres sabit commit'e bağlı; her dosyanın SHA-256'sı katalogda, HF'nin LFS özetiyle aynı). Katalogda her
  boyuttan yalnızca bir nicemleme var, adında yazıyor. Başka nicemleme ya da boyut isteyen kendi modelini ekler.
  - **Whisper Small (q8_0):** `ggml-small-q8_0.bin`, 252 MB (önerilen; işlemcide de rahat).
  - **Whisper Large-v3 Turbo (q5_0):** `ggml-large-v3-turbo-q5_0.bin`, 547 MB. q8_0 ve f16 ile aynı doğruluk,
    kartta ~0.9 GB.

  Eski CTranslate2 modelleri (`stt-whisper-ct2-v1` yayını) kullanılmıyor; yayın 2026-11-03'te silindi.
- **Ölçüm** (2026-11-03, RTX 3060 Ti, Ryzen 5 5600X; FLEURS tr_tr dev setinden 60 farklı cümle, 12.2 dk; Orion'un
  son cümle ayarları: beam 4, Türkçe ipucu, temperature 0; whisper.cpp HTTP üzerinden, WAV yükleme dahil;
  "canlı 3 sn" = 3 saniyelik sesin beam 2 ile deşifresi, canlı yazmadaki tek çağrı):

  | | WER % | RTFx | canlı 3 sn (s) | VRAM (MB) |
  |---|---|---|---|---|
  | faster-whisper CUDA turbo (eski) | 8.55 | 41.7 | 0.215 | 1155 |
  | whisper.cpp CUDA turbo q5_0 | 8.15 | 42.7 | 0.174 | 1022 |
  | whisper.cpp **Vulkan** turbo q5_0 | 8.25 | 38.6 | 0.196 | 902 |
  | whisper.cpp Vulkan turbo q8_0 | 8.15 | 42.4 | 0.168 | 1192 |
  | faster-whisper CPU small (eski) | 16.0 | 5.4 | 1.61 | – |
  | whisper.cpp CPU small q8_0 (12 iş parçacığı) | 15.5 | 5.9 | 1.46 | – |
  | whisper.cpp CPU turbo q5_0 (6 iş parçacığı) | 8.65 | 1.1 | 10.5 | – |

  Türkçe doğruluk iki motorda aynı (60 cümlede ±0.4 WER gürültü). Turbo, small'a göre WER'i yarıya indiriyor.
- **Sıra `vulkan, cuda, cpu`** (Vulkan varsayılan; CUDA destekleyen NVIDIA kartta seçilebilir). Yalnız çalışınca
  Vulkan bu kartta CUDA kadar hızlı, ~120 MB daha az kart belleği kullanıyor (887'ye karşı 1007 MB) ve motoru
  15 MB (CUDA'nınki 685 MB). STT ve TTS aynı kartta aynı anda çalışabilir (sesli sohbette yanıt okunurken mikrofon
  dinler). Temiz ölçüm (2026-11-03, RTX 3060 Ti; kartta yalnızca bu iki sunucu, kart belleği tepe 2.4-2.7/8 GB,
  paylaşılan bellek hiç büyümedi; turbo q5_0 + OmniVoice Q8_0):

  | STT / TTS | Canlı 3 sn, boşta | Canlı 3 sn, TTS yanıt okurken | Sürekli yük: STT RTFx / TTS cümle/sn |
  |---|---|---|---|
  | CUDA / CUDA | 0.147 s | 0.26 s | 20.5 / 0.45 |
  | Vulkan / CUDA | 0.172 s | 0.26-0.28 s | 24.3 / 0.44 |
  | Vulkan / Vulkan | 0.155 s | 0.27 s | 26.1 / 0.39 |
  | CUDA / Vulkan | 0.142 s | 0.30 s | 21.5 / 0.46 |

  İkisi aynı anda çalışınca kart sırayla paylaşılıyor: STT'nin canlı sonucu ~0.15'ten ~0.27 s'ye, bir TTS yanıtı
  ~1.7 kat uzuyor. Dört bileşim birbirinden yalnızca yüzde birkaç farklı: CUDA ile Vulkan'ı karıştırmanın ek bir
  bedeli yok. Önceki "karışınca 18-30 kat yavaş" ölçümü kirliydi (başka bir oturumun tts-server'ı da açıktı,
  kart belleği 7.9/8 GB doluydu: Vulkan sistem belleğine taşıyordu). Ölçüm betikleri ve tablonun tamamı oturum
  notlarında (`stt-vulkan-whisper-cpp.md`).
- **Güncelleme yolu (eski STT kurulu bilgisayar):** Hub her açılışta bir kez `tidy()` çalıştırır (parçalar
  başlamadan): katalogda adı geçmeyen motor klasörlerini (`engines/faster-whisper-1.2.1-py311`, cuDNN'li
  ~1.4 GB; ileride bir llama.cpp sürüm değişikliğinin eski klasörü de) ve parçanın `retired_models` listesindeki
  eski model klasörlerini (`models/stt/whisper-small-official`, `whisper-large-v3-turbo-ct2`) siler. Açık tutulan
  bir dosya kalırsa sonraki açılışta gider. Kayıtlı seçim kendiliğinden yenisine geçer (`retired_models`:
  small → `whisper-small`, turbo → `whisper-large-v3-turbo`). Eski `compute_type` ayarı atılır. Kayıtlı donanım
  (cuda/cpu) geçerli kalır. Yeni motor ve model inmeden parça başlamaz: kutucuk "İndir ve başlat" gösterir.
  Kullanıcının `custom/` klasöründeki CTranslate2 modelleri silinmez, ama "whisper.cpp modeli değil" der.
- **Kalanlar:**
  - Eski geliştirici kurulum paneli (`installer/app`) ve Docker dosyaları
    (`services/stt/whisper/manifest.json`, `docker-compose.*.yml`) hâlâ faster-whisper'ı anlatıyor. Panelin
    kendisi kullanılmıyor; kaldırılınca bunlar da gider (Yapılacaklar 8).
  - Flutter'daki gelişmiş ayar özetinde `compute_type` satırı var ama Hub artık bu ayarı göndermiyor, ekranda
    görünmüyor.
  - Gerçek bir AMD/Intel kartta denenmedi.

## Yapılacaklar

Sıra önerisi yukarıdan aşağıya.

1. **Hafıza özelliğinin kendisi.** Embedding parçası kuruluyor ama onu kullanan bir hafıza özelliği yok
   (`worker/services/router.py` `generate_embeddings` çağrılmıyor ve embedding modeli yerine sohbet model
   grubunu gönderiyor). Hafıza kapalıyken sohbet ekranında "Hafıza kapalı. Açmak için hafıza servisini kur
   [Bileşenleri aç]" kutucuğu (`offerPart(part: 'embedding')`). Tasarım notları: `embedtaktik.md`,
   `assets/yz hafıza sistemi/`.
2. ~~Yerel STT ve TTS'yi buradan kurmak~~ (2026-11-02 yapıldı, PyTorch'suz):
   - **STT:** önce faster-whisper/CTranslate2 idi; 2026-11-03'ten beri whisper.cpp (aşağıda "Ses tanıma (STT)").
   - **TTS:** omnivoice-gguf (`tts-server.exe`); CPU takımı Orion paketinde (`services/tts/omnivoice-gguf/bin`),
     CUDA zipi `tts-v1.1.0` yayınından. Her biri bütün takım (`tts-server.exe`, `omnivoice-codec.exe`, `ggml*.dll`;
     CUDA'da ek olarak `ggml-cuda.dll`), birbirine karışmaz: `ggml.dll` derlendiği arka ucun DLL'ine doğrudan
     bağlı (`GGML_BACKEND_DL=OFF`), CUDA tarifi paketteki dosyaları hiç kullanmaz. İkisi de `GGML_NATIVE=OFF`
     (AVX2 tabanı), yani yalnızca derlendiği işlemcide değil, AVX2'li her işlemcide çalışır. Derleme
     `build_release.bat` (`cpu` ya da `cuda` yalnızca birini derler; CPU takımı `bin/`'e, CUDA takımı
     `build/cuda/`'ya), CUDA zipi `package_release.py`. Vulkan zipi de bütün takım (`build_release_vulkan.bat`,
     `package_release.py vulkan`), `tts-v1.1.0` yayınından.
     - 2026-11-03'e kadar `bin/` CUDA derlemesindendi: `ggml.dll` `ggml-cuda.dll`'yi istiyordu, o pakete girmediği
       için kurulu Orion'da "İşlemci (CPU)" seçeneği hiç açılmıyordu (0xC0000135). Geliştirici bilgisayarında
       `bin/`'de izlenmeyen bir `ggml-cuda.dll` durduğu için görünmüyordu. `test_an_engine_brings_every_dll_it_needs`
       artık tariften kurulan her motorun içe aktardığı DLL'lerin yanında (ya da Windows'ta, ekran kartı
       sürücüsünde) olduğunu denetler; arşivli tarifleri, zipleri bu bilgisayarda varsa (hiçbir şey indirmez).
     - Doğrulandı (2026-11-03): yalnızca `bin/`'deki beş dosyanın olduğu bir klasörde, PATH'te yalnızca System32
       varken `tts-server.exe` CPU'da ses üretti; çıktı eski (yerel işlemciye göre derlenmiş) takımla bayt bayt
       aynı, hız da aynı (2,7 sn ses ~21 sn, 6 iş parçacığı). CUDA zipi tek başına açılıp CUDA0'da çalıştı.
     - **CUDA çalışma zamanı** (2026-11-03 düzeldi): `ggml-cuda.dll` `cudart64_12.dll` ve `cublas64_12.dll` (o da
       `cublasLt64_12.dll`) istiyor; eski zip de yeni zip de bunları getirmiyordu, CUDA Toolkit'siz bilgisayarda TTS'in
       CUDA seçeneği de 0xC0000135 ile açılmazdı. CUDA tarifinin ikinci arşivi artık llama.cpp'nin
       `cudart-llama-bin-win-cuda-12.4-x64.zip`'i (yalnızca bu üç DLL, 391 MB, SHA-256 katalogda; CUDA 12 içinde
       küçük sürüm uyumu). PATH'te Toolkit yokken iki zip birlikte CUDA0'da ses üretti.
   - Servisler `services/shared/part_python.py` ile başlar; `ORION_PART` varken servisler depodaki geliştirici
     `.env`'sini okumaz. Kaynaktan çalışan Hub başka bir Python sürümündeyse (3.14) STT kutucuğu bunu söyler.
   - Doğrulandı: kurulu Orion'un Python 3.11'iyle STT ve TTS gerçekten kuruldu; TTS'in ürettiği Türkçe cümle STT ile
     yazıya döndü. Bu sırada TTS'in akışlı WAV'ın yalnızca ilk parçasını döndürdüğü hata düzeltildi.
   - Kalan: STT/TTS paketlerinin sürümünü yenilerken `uv pip compile -c
     hub-requirements.txt --python-version 3.11 --python-platform x86_64-pc-windows-msvc --generate-hashes`
     ile yeniden çöz, adres ve SHA-256'ları PyPI'den alıp kataloğa yaz (bunu yapan bir betik henüz depoda yok).
3. **TTS ses klonları:** Bileşenler'in kurduğu TTS klonları `Parts/run/tts/voices` altında tutar; Orion'la gelen
   sesler kod klasöründe. Klon yönetimi ekranı yok.
   - **Örneğin uzunluğu** (2026-11-03): örneğin kodları bir kez çıkarılıp saklanır, ama örnek her konuşmada yeni
     cümleyle birlikte modelden geçer; uzun örnek her konuşmayı yavaşlatır. Önerilen ~5 sn, üst sınır 20 sn
     (`services/tts/engines.py` `CLONE_RECOMMENDED_SECONDS`, `CLONE_MAX_SECONDS`). Süre kodlardan hesaplanır
     (`rvq_seconds`: 8 kod kitabı x 11 bit, saniyede 25 kare). 20 sn üstü Türkçe nedeniyle reddedilir; 6 sn üstü
     kaydedilir ama uyarılır (`/v1/voices/clone` yanıtında `seconds`, `warning`). TTS paneli (`services/tts/dashboard`,
     tek ses ekleme yeri) seçilen dosyanın süresini hemen gösterir; sınırlar `/v1/model_info` `clone` alanından.
   - **Ölçüm** (RTX 3060 Ti, CUDA, zephyr2'nin 17 sn'lik kaydı cümle sonlarından kesilip ve art arda eklenip, altı
     Türkçe cümle, üç tekrarın ortancası; gerçek zaman oranı, klonsuz ses 0.24):

     | Örnek | 4.5 sn | 9 sn | 13.4 sn | 17 sn | 34 sn | 52 sn |
     |---|---|---|---|---|---|---|
     | 32 adım | 0.27 | 0.33 | 0.41 | 0.46 | 0.76 | 1.16 |
     | 22 adım | 0.20 | 0.24 | 0.28 | 0.31 | 0.53 | 0.83 |

   - **Neden 20 sn:** OmniVoice'un kendisi 20 sn'den uzun örneği kırpar (`omnivoice/utils/audio.py`
     `trim_long_audio`, demosu 3-10 sn önerir). Modelin bağlamı (40960 token, ~27 dk) sınır değil; kart belleği çok
     önce biter: 8 GB'ta 69 sn'lik örnek +4.9 GB alıp gerçek zamandan 2.6 kat yavaş konuştu, 121 sn'lik sistem
     belleğine taştı ve tek cümle 15 dakikayı geçti. Daha küçük kartta sınır daha aşağıda olur.
   - Eski uzun klonlar (zephyr2 17 sn) çalışmaya devam eder; denetim yalnızca yeni eklenen sese.
4. **Yerel mi bulut mu seçimi.** Her kutucukta "Bulut (Router üzerinden)" seçeneği: STT için Gemini
   transcribe var ama canlı sesli yazma hep yerel servise gidiyor (`orion-router/api/transcriptions.py`);
   embedding için `GeminiEmbedProvider` var ama `gemini/models.json`'da embedding modeli listelenmemiş. Seçim
   Router ayarlarıyla (stt_model, embed modeli) tek yerde tutulmalı.
5. **macOS ve Linux.** Katalogda `engines.llama.cpp.mac` (Metal, arm64) ve `linux` (cuda/vulkan/cpu) derlemeleri
   eklenince kutucuklar o sistemlerde kurulabilir olur; donanım algılama Metal'i zaten seçenek olarak veriyor.
6. ~~**İndirme doğrulaması.**~~ (2026-11-03 yapıldı) Hugging Face'ten inen her model dosyası (mmproj dahil) sabit bir
   commit'ten iner (`/resolve/<commit>/`, `main` değil: depo sahibi dosyayı değiştirse bile bizimki değişmez) ve
   inince kataloğun `sha256`'sıyla denetlenir; tutmazsa silinir, iş "bozuk indi" der. Değerler HF API'sinin LFS
   özetinden (`/api/models/<depo>/revision/main?blobs=true`); bir model eklerken ya da güncellerken commit ve
   özetler oradan alınır (indirilmiş Jina dosyasıyla tuttuğu denendi). Motor zipleri zaten SHA-256'lı.
   `test_every_model_from_hugging_face_is_one_fixed_file_checked_by_its_sha256` eksik olanı yakalar. Modeller
   kendi yayınımıza taşınmadı (kullanıcı kararı): GitHub bir yayın dosyasını en çok 2 GB alıyor, Gemma ve LCO
   büyük.
7. **Disk alanı denetimi** indirmeden önce, yetmezse kutucukta açıkça.
8. **Eski geliştirici kurulum paneli** (`installer/app`, 127.0.0.1:7171): kullanılmıyor (2026-11-03 kararı). Kod
   şimdilik duruyor ama başlamıyor: `python -m installer.app` ve `manager.py installer` bir uyarıyla çıkar. Kurulu
   Orion onu zaten hiç başlatmıyordu; dosyaları hâlâ Hub paketine giriyor (`build_package.py` `SOURCE_PREFIXES`).
   Tamamen kaldırmak sonra konuşulacak.
9. **LLM'in sohbette seçilmesi:** LLM parçası çalışınca Router'da `local-chat` modeli kullanılabilir; Ayarlar'daki
   model seçicisinde "Yerel (bu bilgisayar)" diye görünmesi ve parça kapalıyken uyarması.
10. **Misafir hesaplar** geldiğinde: kurma/kaldırma yalnızca ana kullanıcı (şimdilik Hub bilgisayarındaki pencere).
11. **Kendi modellerin, kalanlar** (temeli 2026-11-02 yapıldı, yukarıda):
    - **VoxCPM2 (GGUF):** ekleme ekranında "yakında" diye duruyor (`USER_KINDS['tts']`, motoru `voxcpm2`).
      Motoru eklenince: türün dosyaları (mimarileriyle), kataloğa motor tarifi ve parçanın motorunun modele göre
      seçilmesi (şimdi `part['engine']` parça başına tek).
    - **Hafızada embedding olmayan model:** şimdilik reddedilmiyor (GGUF'ta bunu güvenle söyleyen bir alan
      yok); havuzlama söylenmemişse `--pooling last` verilir.
    - **Hafıza modeli değişirse** eski vektörler yeni modelle karşılaştırılamaz (boyut ve uzay farklı): hafıza
      dizini modelin kimliği ve boyutuyla tutulmalı, model değişince arkada yeniden hesaplanmalı.
12. **Tanıtım ekranı** (2026-11-03, sonra): Flutter uygulaması ilk açılınca uygulamanın nasıl kullanıldığını
    anlatan kısa bir tanıtım; içinde Bileşenler ekranına (hafıza, yerel model, ses) yönlendiren adım.
13. ~~**TTS Vulkan derlemesi**~~ (2026-11-03 yapıldı, yayını kalan): OmniVoice `GGML_VULKAN=ON` ile derleniyor
    (`build_release_vulkan.bat`, LunarG Vulkan SDK gerekir; `python package_release.py vulkan` zipi ve SHA-256'yı
    verir; nasıl kurulup derlendiği: [tts-motoru-derleme.md](tts-motoru-derleme.md)). Zip bütün takımı taşır (`tts-server.exe`, `omnivoice-codec.exe`, `ggml*.dll`, `ggml-vulkan.dll`):
    her `ggml.dll` derlendiği arka uca bağlı, takımlar karışmaz.
    - **İşlemler:** OmniVoice'un kullandığı her ggml işlemi Vulkan'da; CPU'ya düşen yok (`GGML_SCHED_DEBUG=2`:
      ses çözücü ve WAV'dan ses kaydı (HuBERT + DAC) tamamen `Vulkan0`'da, dil modeli grafiği "doğrudan" yolda,
      o yol yalnızca her işlem destekleniyorsa seçilir).
    - **Ölçüm** (RTX 3060 Ti, aynı GGUF Q8_0, aynı tohum, altı Türkçe cümle, istekler sunucular arasında sırayla,
      üç tekrarın ortancası; gerçek zaman oranı = sentez süresi / ses süresi):

      | | CUDA | Vulkan | Vulkan, coopmat2 kapalı | CPU |
      |---|---|---|---|---|
      | Varsayılan ses, 32 adım | 0.24 | 0.31 | 0.29 | ~10 |
      | Varsayılan ses, 22 adım | 0.17 | 0.21 | 0.20 | |
      | Klon ses, 32 adım | 0.45 | 0.77 | 0.67 | |
      | Klon ses, 22 adım | 0.32 | 0.53 | 0.46 | |
      | Ekran kartı belleği: yüklenince | 1049 MB | 908 MB | | 0 |
      | Ekran kartı belleği: konuşurken en çok (varsayılan / klon) | 1453 / 1523-1554 MB | 1301-1305 / 1364-1417 MB | | 0 |

      Bellek: `nvidia-smi` kartın tamamında kullanılan bellek, sunucu açılmadan önceye göre fark; aynı model, aynı
      uzun cümle, ikişer ölçüm. Vulkan ~150 MB az kullanıyor.

      Sunucunun açılmasında arka uç fark etmiyor: önce 28-79 sn'ye 1.5 sn göründü, ama CUDA model dosyalarını
      D: (HDD) üzerinden soğuk okuyordu, Vulkan ise önbellekten. Soğukken üçü de 13-54 sn, ısınmışken 1-2 sn.
      Bu tablodaki klon 17 sn'lik zephyr2 ve üç sunucu aynı anda açıktı; temiz ölçüm aşağıda.
    - **Temiz ölçüm** (2026-11-03): kartta her seferinde tek `tts-server` (öncesinde ve boyunca başka TTS, whisper,
      llama sunucusu yok; parçalar kapalı), turlar sırayla CUDA, Vulkan, CUDA, Vulkan; her turda ısınma sayılmaz,
      altı cümlenin beş tekrarı; Windows GPU sayaçlarında paylaşılan bellek CUDA 78 MB, Vulkan 175 MB ve sabit
      (taşma yok). Klon: zephyr2'nin ilk cümlesi (4.5 sn). Gerçek zaman oranı, turun ortancası (en az-en çok),
      iki tur:

      | | CUDA 1. tur | CUDA 2. tur | Vulkan 1. tur | Vulkan 2. tur |
      |---|---|---|---|---|
      | Varsayılan ses, 32 adım | 0.259 (0.247-0.303) | 0.247 (0.234-0.255) | 0.322 (0.295-0.326) | 0.303 (0.297-0.316) |
      | Klon 4.5 sn, 32 adım | 0.291 (0.269-0.321) | 0.269 (0.258-0.275) | 0.405 (0.381-0.420) | 0.387 (0.385-0.414) |
      | Varsayılan ses, 22 adım | 0.187 (0.170-0.208) | 0.170 (0.163-0.175) | 0.212 (0.203-0.227) | 0.207 (0.204-0.217) |
      | Klon 4.5 sn, 22 adım | 0.208 (0.199-0.238) | 0.198 (0.188-0.201) | 0.270 (0.258-0.277) | 0.262 (0.258-0.278) |
      | Kart belleği (sunucu, en çok) | 1693 MB | 1687 MB | 1448 MB | 1448 MB |

      NVIDIA'da CUDA her durumda hızlı (Vulkan varsayılan seste 1.15-1.25, klon seste 1.3-1.45 kat yavaş), bu yüzden
      TTS'in sırası `cuda, vulkan, cpu`. AMD/Intel kartta Vulkan CPU'dan 30-40 kat hızlı. Vulkan her yeni şekilde bir kez
      pipeline derler (sunucu açıldıktan sonraki ilk istekler birkaç saniye uzun).
    - **Yayın** (2026-11-03): CUDA ve Vulkan zipleri `tts-v1.1.0`'da. Katalogdaki adreslerden geri indirilip SHA-256
      ve boyut tuttu. Bileşenler'in kendi kurulum işiyle (`local_parts job tts install`, geçici `ORION_PARTS_HOME`,
      PATH'te CUDA Toolkit yok) TTS hem CUDA'da hem Vulkan'da kuruldu, başladı ve Orion'un TTS API'sinden ses üretti
      (günlükte `CUDA0`, `Vulkan0`). Kullanıcılar bunu bir sonraki Hub yayınıyla alır (katalog Hub paketinde).
    - **Kalan:** gerçek bir AMD/Intel kartta denenmedi.
