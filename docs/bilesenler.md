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
  - **TTS:** motor kullanıcının seçimi (OmniVoice ya da VoxCPM2). OmniVoice'un iki dosyası başlıklarındaki
    mimariden (`omnivoice-lm`, `omnivoice-tokenizer`), VoxCPM2'ninkiler (`minicpm4` BaseLM, `voxcpm-acoustic`)
    ayırt edilir; tanınmayan dosya adıyla söylenir.

### VoxCPM2 (2026-11-03)

- **Ne:** OpenBMB'nin VoxCPM2'si (Apache-2.0; 30 dil, Türkçe dahil; 48 kHz; ses tasarımı ve klonlama), PyTorch'suz:
  GGUF'ları [DennisHuang648/VoxCPM2-GGUF](https://huggingface.co/DennisHuang648/VoxCPM2-GGUF) sabit commit
  `169f64d`'den (BaseLM Q8_0 1.73 GB + Acoustic F16 1.83 GB, SHA-256 katalogda), çalışma zamanı
  [tc-mb/llama.cpp-omni](https://github.com/tc-mb/llama.cpp-omni) (MIT; OpenBMB'nin kendi gösterdiği yol)
  `d30f00f`'deki `llama-tts-server` (OpenAI uyumlu `/v1/audio/speech`, `/stream`).
- **Parçada:** TTS'in ikinci modeli `voxcpm2-gguf`, kendi motoruyla (`engines.voxcpm2-gguf`, sürüm
  `voxcpm2-v1.0.0`, `exe: llama-tts-server`; CUDA (7.5+, CUDA çalışma zamanı llama.cpp'nin cudart zipinden), Vulkan,
  CPU). Bir parçanın modelleri farklı motorlarda çalışabilir: `local_parts.for_model(part, model)` modelin motorunu
  ve o motorun `env`'ini verir; komut, kurulum işi, hazır olma, bekçi, kaldırırken kullanılmayan motorlar
  (`part_engines`) bundan geçer. Sayfadaki model satırında kendi motorunun donanım başına durumu var (`engines`).
- **Servis:** `services/tts/engines.py` `VoxCPM2GGUFEngine`: `llama-tts-server`'ı alt süreç olarak açar
  (`VOXCPM2_BIN_DIR`, `MODEL_PATH` BaseLM, `ACOUSTIC_PATH`), sesler `*_voxcpm2-gguf.pt` (WAV örneği + metni; sunucu
  ses tutmaz, her istekte `reference_audio` gider; kullanıcı klon metni yazdıysa `prompt_text` olarak o da gider (isteğe bağlı, 2026-11-05). Metin kayıtta söylenenle birebir aynı olmalı: uyuşmazsa model fazlasını da konuşur, 4,6 sn örnek + 255 karakterlik metin → 2 sn yerine 13-15 sn çıktı; sebep guidance değil uyuşmazlıktı. 8810 sayfasında alanın yanında bu uyarı var). OmniVoice'un klonları VoxCPM2'de geçmez.
  Klon kuralı aynı (~5 sn önerilir, en çok 20 sn). Ses tasarımı: tarif metnin başına parantez içinde.
- **Derleme:** `services/tts/voxcpm2-gguf/build_release.bat` (kendi fork'umuz krstalacam/llama.cpp-omni, `orion`
  dalı: MSVC düzeltmesi ve sunucuda tek seferde tek üretim + kopunca durma), `package_release.py` zipleri (her biri bütün takım) ve SHA-256'ları verir;
  ayrıntı [tts-motoru-derleme.md](tts-motoru-derleme.md).
- **Ölçüm** (RTX 3060 Ti, kartta tek sunucu, aynı altı Türkçe cümle, üç tekrarın ortancası; gerçek zaman oranı):
  CUDA varsayılan ses 0.258, 4.5 sn klon 0.333; Vulkan 0.372 / 0.458 (OmniVoice: CUDA 0.25 / 0.27-0.29, Vulkan
  0.30-0.32 / 0.39-0.41). NVIDIA'da CUDA önde, TTS'in `cuda, vulkan, cpu` sırası VoxCPM2'ye de uyar. Model ~4 sn'de
  yüklenir.
- **CPU** (2026-11-03, aynı altı cümle ve 4.5 sn klon, 32 adım, bir tur; Ryzen 5 5600X, OmniVoice 6 iş parçacığıyla): VoxCPM2 varsayılan
  ses 4.74, klon 6.00; OmniVoice (paketteki yalnızca CPU `bin/`) 9.04 / 13.08. CPU'da VoxCPM2 iki kat hızlı ama
  ikisi de gerçek zamandan çok yavaş: kartsız bilgisayarda yalnızca kısa cümleler için.
- **Uçtan uca** (2026-11-03, geliştirici Hub'ının kendi `.local_parts`'ı, Bileşenler API'sinin çağırdığı
  `select` + `start_job`/`apply_choice`; Orion TTS API'si 8810'da, aynı Türkçe cümle):

  | Adım | Nasıl | Hazır | Arka uç | Ses / süre |
  |---|---|---|---|---|
  | OmniVoice + CUDA | Kur (motor indi) | 100 sn | CUDA0 | 2.71 sn / 1.1 sn |
  | OmniVoice + Vulkan | Uygula (motor indi) | 23 sn | Vulkan0 | 2.71 sn / 1.1 sn |
  | OmniVoice + CPU | Uygula (paketteki) | 20 sn | CPU | 2.71 sn / 21.0 sn |
  | VoxCPM2 + CPU | Uygula (model ve motor değişti) | 23 sn | CPU | 2.72 sn / 12.8 sn |
  | VoxCPM2 + Vulkan | Uygula | 26 sn | Vulkan0 | 2.56 sn / 1.3 sn |
  | VoxCPM2 + CUDA | Uygula (motor + cudart indi) | 91 sn | CUDA0 | 2.56 sn / 1.0 sn |

  Her geçişte eski sunucu kapandı (aynı anda tek TTS süreci); Kaldır (modeller kalsın) servisi ve
  `llama-tts-server`'ı durdurdu, motorlar yerinde kaldı.
- **Yayın** (2026-11-03): CUDA, Vulkan ve CPU zipleri `voxcpm2-v1.0.0`'da; katalogdaki adreslerden geri indirilip
  SHA-256 ve boyut tuttu. Bileşenler'in kurulum işiyle (geçici `ORION_PARTS_HOME`, PATH'te CUDA Toolkit yok) VoxCPM2
  CUDA'da ve Vulkan'da kuruldu, başladı, Orion'un TTS API'sinden ses üretti (`backend=CUDA0`, `Vulkan0`).
- **Kalan:** klon yönetimi her motor için ayrı (OmniVoice'un klonları VoxCPM2'de geçmez).
- **Akışlı / akışsız, cümle cümle** (2026-11-03; Orion TTS API'si `services/tts/api.py` üzerinden, Bileşenler'in
  kurduğu motorlarla, Router'ın kendi isteğiyle: `steps` 15, `guidance_scale` 2.0, varsayılan ses; tek sunucu,
  ısınma sayılmaz, 3 tekrarın ortancası). Akışsız: istekten tam WAV'a. Akışlı: ilk parça (dinleme başlar), son
  parça; kesinti: ilk parçada çalmaya başlayan dinleyicinin beklediği toplam süre.

  | Motor, donanım | Cümle (karakter) | Ses | Akışsız tam WAV | Akışlı ilk / son parça | Kesinti |
  |---|---|---|---|---|---|
  | OmniVoice CUDA | 1 (47) | 2.62 sn | 0.34 | (akış yok) | — |
  | OmniVoice CUDA | 2 (67) | 3.47 | 0.51 | | |
  | OmniVoice CUDA | 3 (79) | 4.32 | 0.61 | | |
  | OmniVoice CUDA | 4 (77) | 4.43 | 0.57 | | |
  | OmniVoice CUDA | 5 (238) | 13.53 | 1.63 | | |
  | OmniVoice CUDA | 6 (187) | 10.43 | 1.22 | | |
  | VoxCPM2 CUDA | 1 (47) | 2.56 | 0.73 | 0.32 / 0.90 | yok |
  | VoxCPM2 CUDA | 2 (67) | 2.72 | 0.75 | 0.32 / 0.94 | yok |
  | VoxCPM2 CUDA | 3 (79) | 5.12 | 1.42 | 0.34 / 1.81 | yok |
  | VoxCPM2 CUDA | 4 (77) | 4.64 | 1.26 | 0.32 / 1.61 | yok |
  | VoxCPM2 CUDA | 5 (238) | 12.48 | 3.25 | 0.34 / 4.23 | yok |
  | VoxCPM2 CUDA | 6 (187) | 12.48 | 3.27 | 0.36 / 4.28 | yok |
  | OmniVoice Vulkan | 1 / 5 | 2.63 / 13.65 | 0.42 / 1.98 | (akış yok) | — |
  | VoxCPM2 Vulkan | 1 / 5 | 2.56 / 12.48 | 1.00 / 4.70 | 0.51 / 1.45, 0.54 / 6.86 | yok |
  | OmniVoice CPU | 1 / 5 | 2.60 / 13.54 | 11.15 / 58.84 | (akış yok) | — |
  | VoxCPM2 CPU | 1 / 5 | 2.72 / 12.48 | 15.27 / 64.14 | 6.14 / 20.18, 8.28 / 93.25 | 11.2 / 73.4 sn |

  - **OmniVoice'un akışı yok:** MaskGIT bütün cümleyi birlikte çözer (30 sn'ye kadar tek parça), sunucusu PCM'i
    ancak bitince gönderir; `api.py` OmniVoice'ta `stream`'i yok sayar (tam WAV döner). 15 adımla CUDA'da
    tam WAV, sesin ~%12'si kadar sürede gelir; beklemek yine de cümle başına 0.3-1.6 sn.
  - **VoxCPM2'nin akışı gerçek:** 160 ms'lik yamalar üretildikçe gelir; kartta ilk ses cümle boyundan bağımsız
    ~0.33 sn (Vulkan 0.5 sn), kesinti yok. Akışlı toplam akışsızdan ~%30 uzun. CPU'da üretim oynatmadan yavaş:
    akış dinleyiciyi bekletir (kullanılmamalı).
  - **Orion'da akış (2026-11-03'ten beri, cümle grubunun içinde de):** Hub yanıtı cümlelere böler (ilk cümle
    hemen, sonra en çok 3 cümle / 600 karakterlik gruplar; `adaptive_tts.py`) ve her grubu Router'dan
    `stream: true` ister; yerel TTS için PCM (`router.py` `stream_tts`). Router'ın yerel sağlayıcısı
    (`orion-router/providers/local/tts.py` `generate_speech_stream`) TTS'e `stream: true` + `pcm` gönderip
    parçaları geldikçe iletir. Akışı olan motor (VoxCPM2) ham 24 kHz PCM gönderir, Hub ilk parçada çalmaya
    başlar; akışı olmayan (OmniVoice) tek tam WAV döndürür, Router olduğu gibi geçirir (`audio/wav`), Hub onu
    eskisi gibi tamamlanınca çalar. Önceden Router yerel TTS'e hep `stream: False` gönderiyordu: VoxCPM2'de
    5. cümlenin ilk sesi Hub'a ancak ~3.3 sn sonra (tam WAV) geliyordu.
  - **48 → 24 kHz:** Orion'un ses yolu baştan sona 24 kHz mono 16 bit. VoxCPM2'nin 48 kHz'i TTS servisinde,
    kaynağında yarıya indirilir (`engines.py` `HalfRate`: 63 dokunuşlu Blackman pencereli sinc alçak geçiren,
    kesim 11 kHz, sonra her ikinci örnek; filtrenin geçmişi parçalar arasında taşınır, eklerde kesinti yok).
    Ölçülen: 9 kHz'e kadar düz, 11 kHz -6 dB, 12 kHz -22 dB, 13 kHz'den yukarısı -65 dB'nin altında. Akışlı PCM
    (`X-Sample-Rate: 24000`) ve akışsız WAV ikisi de 24 kHz. Hub'ın `PcmNormalizer`'ı ve `_batch_wav_pcm`'i
    başka hızları hâlâ filtresiz doğrusal ara değerlemeyle çevirir (aşağı örneklemede katlanma); yerel TTS artık
    oraya 24 kHz dışında ses göndermediği için bu genel düzeltme ayrı bir iş.
  - **Uçtan uca, geliştirici Router + Hub** (2026-11-03; Hub'ın kaynak `stream_tts`'i gerçek Router üzerinden,
    VoxCPM2 CUDA, varsayılan ses, 3 tekrarın ortancası; aynı anda TTS'e doğrudan istekte ilk ses 0.60 sn idi,
    yani Router + Hub ~0.05 sn ekliyor; o 0.60'ın nedeni aşağıda):

    | Cümle (karakter) | Hub'a gelen | İlk ses | Son parça | Ses |
    |---|---|---|---|---|
    | 1 (47) | PCM akışı | 0.64 sn | 1.52 | 3.68 |
    | 2 (67) | PCM akışı | 0.65 | 1.40 | 3.36 |
    | 3 (79) | PCM akışı | 0.65 | 1.82 | 4.48 |
    | 4 (77) | PCM akışı | 0.64 | 2.19 | 5.44 |
    | 5 (238) | PCM akışı | 0.75 | 6.91 | 17.60 |
    | 6 (187) | PCM akışı | 0.66 | 5.31 | 13.60 |
    | OmniVoice CUDA 1 / 5 | tam WAV | 0.76 / 3.45 | aynı | 2.97 / 15.52 |

    (Hub isteğinde tohum yok; ses süreleri bu yüzden yukarıdaki tohum 42'li tablodan farklı.)

  - **İlk sesteki 0.5 sn kartın paylaşılmasından değil, TTS API'sinin tamponundan** (2026-11-03, ölçüm; Orion
    TTS API'si, yukarıdaki yöntem, 1. / 5. cümle, akışlı ilk parça / son parça, sn):

    | Durum | İlk parça | Son parça |
    |---|---|---|
    | `llama-tts-server`'ın kendisi (ham HTTP) | 0.09 | — |
    | Kart boş, düzeltme öncesi (48→24 kHz süzgeçli) | 0.54 / 0.58 | 0.93 / 4.40 |
    | Kart boş, 52afffb öncesi (48 kHz, süzgeçsiz) | 0.31 / 0.36 | 0.92 / 4.35 |
    | Kart boş, motor sınıfı süreç içinde (api.py'siz), eski ve yeni | 0.09-0.10 | — |
    | LLM de kartta (gemma-4-E2B Q3_K_S, CUDA), boşta; düzeltme öncesi | 0.54 / 0.55 | 0.91 / 4.24 |
    | LLM aynı anda yanıt üretirken; düzeltme öncesi | 1.34 / 1.51 | 2.32 / 11.76 |
    | Kart boş; api.py düzeltilmiş (geçici kopya) | 0.10 / 0.13 | 0.94 / 4.37 |
    | Kart boş; düzeltme depoda | 0.10 / 0.13 | 0.94 / 4.29 |
    | LLM boşta; düzeltilmiş | 0.10 / 0.13 | 0.91 / 4.23 |
    | LLM üretirken; düzeltilmiş | 0.28 / 0.29 | 2.52 / 16.29 (5. cümlede 2.6 sn kesinti) |

    Neden: `api.py` motorun senkron üretecini olay döngüsünün içinde döndürüyor, uvicorn da yazılanları ancak
    ~64 KB birikince gönderiyor; ses 24 kHz'e inince 64 KB'ın dolması iki kat sürüyor (0.33 → 0.54 sn). Süzgeç
    kendisi parça başına 0.1 ms. Düzeltme: `api.py` üreteci iş parçacığında döndürüyor (`run_in_threadpool` ile
    ilk `next`, `iterate_in_threadpool` ile parçalar); ilk ses 0.10 sn. İstemci akışın ortasında (0.05 / 0.5 /
    1.5 / 3 sn) kapatınca motor durur (GPU hemen boşa düşer), hemen gelen sonraki istek kilitte beklemez (ilk
    parça 0.11-0.21 sn, normali 0.10); logda hata yok. Akışsız WAV (0.74 / 3.29 sn) ve OmniVoice'un tam WAV'ı
    (0.35 / 1.63 sn) öncekiyle aynı. Akışsız üretim ve klon da iş parçacığında (klon üretim kilidini bekler):
    üretim sürerken `/health` en çok 0.014 sn'de yanıtlar (önce 3.4 sn), akış sırasında gelen klon akış bitince
    işlenir, 20 sn'den uzun / WAV olmayan örnek 400; WAV baytları aynı. Akışsız istekte izleyici görev artık
    beklenmez, iptal edilir (beklemek her yanıta 0.5 sn'ye kadar ekliyordu). Açık: akışsız istekte istemci
    kapatınca motor durmaz (iki motorun `generate`'i tek parça istek, `abort_event`'e bakmaz; önceden de böyleydi),
    sonraki istek onun bitmesini bekler. Python tarafında bağlantıyı kesmek denendi ve geri alındı: OmniVoice'un
    sunucusu işi yine bitirir (kazanç yok); VoxCPM2'nin `llama-tts-server`'ı ise istekleri sıraya koymaz
    (`server-voxcpm2.cpp`: `state.mutex` yalnızca çalışma zamanı işaretçisini korur, üretim kilitsiz; httplib iş
    parçacığı havuzu iki isteği aynı `VoxCPM2Runtime` ve KV önbellek üzerinde çalıştırır). Kesilen akışsız istek
    sürerken gelen yeni istek "inconsistent sequence positions" ile bozulur, sunucu çöker. Aynı yarış akışta da
    var (önceden de): akış kesilince sunucu sonraki parçada durur, ama hemen gelen istekle çakışırsa bozulur
    (süreç içinde kesip beklemeden yeni istek: 2 denemede çöktü; API üzerinden gerçek aralıklarla 4/4 sağlam).
    **Düzeltildi (voxcpm2-v1.0.1, 2026-11-04):** fork'umuz krstalacam/llama.cpp-omni `orion` dalında sunucu
    üretimi kilitle tek tek yapar, akışsız uç nokta istemci kopunca durur. Ölçüm (CUDA): akışı kesip beklemeden
    yeni istek 36/36 sağlam (önce 0/18, sunucu çöküyordu); aynı anda iki istek ikisi de doğru, tek başına
    üretilenle aynı bayt (önce biri 500); akışsız istek kesilince sunucu durur, sonraki kısa istek 0.74 sn (normali);
    ses v1.0.0 ile bayt bayt aynı, hız aynı (akışsız 5. cümle 3.33 / 3.29 sn, akış ilk parça 0.11 sn). Bileşenler'den
    kurulup CUDA ve Vulkan'da konuştu. OmniVoice'un sunucusu istekleri zaten sırayla işler; kesilen işi bitirir.
    LLM yalnızca yüklüyken etkisiz (VRAM 7.8 / 8 GB, Windows
    paylaşılan GPU belleği 160 → en çok 380 MB; taşma yok denecek kadar); LLM üretirken TTS ~3 kat yavaşlıyor
    (LLM de 100 → 32-94 token/sn), uzun cümlede üretim gerçek zamanın gerisine düşebiliyor. STT ölçülmedi:
    diskte STT modeli yok. Uçtan uca koşudaki 6.3 GB'ı LLM + VoxCPM2 tutuyordu.

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
  **Bellek kullanımı** (`vram_mode`: tam / dengeli / düşük): dengeli ve düşükte modelin bir kısmı işlemcide kalır
  (llama.cpp: katmanların 1/2'si ve 1/4'ü, `-ngl`; VoxCPM2: BaseLM'in 28 katmanından 14 ve 7'si,
  `--voxcpm2-n-gpu-layers`). Ölçüm (RTX 3060 Ti, Ryzen 5 5600X, VoxCPM2): tam +4,9 GB, 14 katman +3,7 GB, 7 katman
  +3,2 GB; hız oranı 0,35 → 0,56 → 0,66 (1'in altı = gerçek zamandan hızlı), akış kesilmedi. Katmanlar sırayla
  çalıştığı için işlemcideki her katman süreyi uzatır; yavaş işlemcide sonuç farklı olur. OmniVoice (katman
  ayarı yok) ve ses tanıma (whisper.cpp'de yalnız `--no-gpu` var) için böyle bir mod yok.
  **Bellek tahmini** (kartın "Gelişmiş ayarlar" satırı): llama.cpp modelleri = dosya + bağlamın önbelleği (GGUF
  başlığından) + ~0,3 GB tampon, moda göre payı; VoxCPM2 ölçülen değerler; OmniVoice ~1-1,5 GB (gözlem).
  Ses tanıma (STT): model dosyası boyutu kadar varsayıldı, **henüz ölçülmedi** (yapılacak). VoxCPM2'de klon sesle
  üretim yaklaşık 1 GB daha fazla bellek ister (ölçüm: tam hızda zirve +6,0 GB, klonsuz +5,2 GB).
  VoxCPM2'de istekte ne konuşma tarifi ne klon ses varsa motor "A clear voice sweet girl." ile konuşur
  (`DEFAULT_VOICE_DESIGN`, engines.py). 48 → 24 kHz indirme (`HalfRate`) parça sınırlarında kesintisiz ve filtresi
  doğru (FFT'ye göre ~%1,4 hata) olarak doğrulandı.
  **Hazırlık sesi (2026-11-05):** VoxCPM2 kısa cümleleri ("Selam naber.") tek başına Türkçe bilmeyen biri gibi
  okuyordu. Artık her cümle bir ses örneğinin devamı olarak istenir (`reference_audio`); VoxCPM2 isteğinde `prompt_text`
  kullanılmaz. Klon ekranındaki referans metin VoxCPM2'de gizlidir ve API'ye gönderilse bile yok sayılır:
  - **Çıkış başı kırpma:** Yalnızca VoxCPM2 WAV ve PCM akışında uygulanır; arayüzdeki 0-300 ms kaydırıcısı çalışma anında
    ayarlanır ve varsayılanı 50 ms'dir. Akışta süre tüm parçalar üzerinden toplam olarak atlanır; her parçadan ayrı ayrı
    kırpılmaz. OmniVoice etkilenmez. Router'ın yerel TTS'si aynı servisi kullandığında aynı değeri alır.
  - Klon kaydında `tidy_reference` sessizliği kırpar, stereo örneği mono yapar ve seviyeyi ayarlar; ardından yalnızca
    referans kayda klon kaydedilirken 5 kHz yumuşak low-pass (`soften_reference`) bir kez uygulanır. Klon ve tasarım
    üretimlerinin son WAV/stream çıktısına filtre uygulanmaz. Yalnızca ayrıca kaydedilen hazırlık WAV'ı 5 kHz yumuşatma
    alır. Ekstra seviye normalizasyonu yapılmaz. Noise Gate kullanılmaz.
    Var olan klon cache'leri geriye dönük değişmez; filtre için klonu kaynak kayıttan yeniden kaydetmek gerekir.
  - Klonsuz hazırlık: Metin `"Merhaba bugün nasılsınız?"`. 8810 arayüzündeki buton, VoxCPM2 ses tarifi ve o anki
    seed/yaratıcılık/kalite adımı/rastgelelik değerleriyle normal tam üretimin PCM işleme yolunu kullanır; ek ses seviyesi
    veya sessizlik normalizasyonu yapmadan normal üretimle aynı başlangıç kırpmasını uygular ve
    `voices/_prime_voxcpm2-gguf_custom.wav` olarak saklar. Seed `-1` ise bir seed seçip arayüz alanına yazar; sonraki
    tam üretim aynı seed'i kullanabilir. Ses tarifi boş üretimler ve `GET /v1/prime` bu WAV'ı kullanır.
  - Klon: Kaydedilen örneğe yalnızca `tidy_reference` uygulanır ve VoxCPM2'ye her istekte doğrudan bu `ref_wav`
    gönderilir; klon için ayrıca modelden hazırlık sesi üretilmez. Eski klon cache'lerinde `prime_wav` bulunsa bile
    yok sayılır; arayüzdeki klon oynatıcısı da doğrudan kayıtlı referansı çalar. Referans metin VoxCPM2'de kullanılmaz.
  - Ses tarifi (`instructions`) verilince hazırlık kullanılmaz. 8810'da kullanılan hazırlık sesi dinlenebilir (`GET /v1/prime`).
  Motor (llama.cpp-omni yaması, `encode_reference_audio`): aynı ses örneğinin AudioVAE kodlaması saklanır (en çok 8);
  ilk istekte ~100-280 ms, sonra önbellekten. Akışta ilk ses ("Selam naber.", CUDA, 7 katman, sıcak): hazırlıksız
  0,28 sn; hazırlıklı önbelleksiz 0,55; önbellekli 0,43-0,49 (klonsuz ~0,46, 4,4 sn örnekli klon ~0,54). Kalan fark, dil
  modelinin hazırlığı her istekte yeniden okuması: yeni cümlenin yazısı sesten önce geldiği için saklanamaz. Denendi ve
  bozuk çıktı: sırayı değiştirmek (hazırlık önde, yeni yazı arkada; üç varyant) ve yazısız hazırlık (Türkçe kötü).
  İstekler arasında beklenince ilk ses ~0,25 sn daha geç (soğuk; sebebi bilinmiyor, ekran kartı saati düşmüyor);
  2 sn'de bir küçük istekle sıcak tutmak yalnızca ~0,1 sn kazandırdı, yapılmadı. Sunucu her açıldığında arkada bir kez
  "Merhaba." üretilir (`_warm_up`, ~2,3 sn): açılıştan sonraki ilk klonsuz cümle ~1,0 → 0,51 sn. Klonun ilk cümlesi
  yine ~0,2 sn geç (o klonun örneği o an kodlanır).
  VoxCPM2 ayarları (varsayılanların hepsi tek yerde: engines.py, `VOXCPM2_DEFAULTS`; 8810 sayfası da oradan okur): `seed` (-1 ya da verilmezse varsayılan ve klon seste 32, ses tarifi varsa her istekte rastgele; göndermezsek
  sunucu hep aynı tohumu kullanıyordu); `guidance_scale` → `cfg_value` (2-4, verilmezse 3: sayfalarda "yaratıcılık 1";
  2026-11-05'e kadar 2,6); `steps` → `inference_timesteps` (verilmezse 15; Router artık
  adım göndermiyor, OmniVoice'a TTS servisi yine 15 verir); kaydedilmiş özel hazırlık sesi yoksa klonsuz hazırlık 2,6 / 0,75 / 15 ile üretilir; özel hazırlık arayüzden geçerli ayarlarla kaydedilebilir. `temperature` (arayüzde "Rastgelelik", 0,5-0,9; 8810 sayfası varsayılandan başlar (0,05'lik adım; varsayılanlar "Varsayılan" yazar),
  istek vermezse motor 0,85 kullanır, PyTorch'ta 1,0'dı). Hız (`speed`) VoxCPM2'de yok: model süre/hız ayarı almıyor, eski PyTorch motoru da `speed`'i hiç kullanmıyordu (OmniVoice'un
  sunucusu alır); 8810 ve Router Oyun Alanı'nda VoxCPM2 açıkken gizli. Sonradan hızlandırma kullanıcı kararıyla
  eklenmedi; "speaking fast" gibi stil etiketleri tutarsız çıktı. Router'ın Oyun Alanı "Temperature" alanı
  "yaratıcılık" (= guidance) olarak kullanılıyor; rastgelelik (`temperature`) Router'dan gitmez, motorun 0,9'u kullanılır; Oyun Alanı'nda bu alan yerel TTS'te 2-4 arası, 0,1'lik adımla.
  Guidance yükselince ses seviyesi de artar (klonsuz rms: cfg 2 ≈ 1,5 bin, 3 ≈ 3 bin, 5 ≈ 9,7 bin, 8 ≈ 19 bin;
  klonda cfg 2 ≈ 5 bin, 5 ≈ 13 bin, tepe tam ölçekte): yüksek değerler bağırır gibi duyulur. `Leveler` (engines.py)
  sesi yalnızca kısar: 20 ms'lik çerçevelerde konuşmanın seviyesini izler (hızlı yükselir, yavaş iner, sessizlik
  sayılmaz), kazancı çerçeveden çerçeveye kaydırır; hedef rms 3000, sessiz konuşma olduğu gibi kalır, akışta da
  çalışır. Eski PyTorch motoru bütün cevabı tepe değerine göre ölçekliyordu (akışta yapılamaz). Çok yüksek cfg'de
  sunucunun kendi 16 bit çıktısı kırpıldığı için o bozulma kalır; seviye yine de iner.
  Bu yüzden VoxCPM2'de guidance en az 2, en fazla 4 (`MIN_GUIDANCE`, `MAX_GUIDANCE`), verilmezse 2,6; Router ve 8810
  servisi verilmeyen guidance'ı artık 2.0 diye doldurmuyor, OmniVoice'un varsayılanı 2.0 kaldı. 8810 sayfasında
  VoxCPM2'de guidance 2-4 (0,1'lik adım, başlangıç 2,6), kalite adımı 15'ten, rastgelelik 0,5-0,9 (0,05'lik adım, 0,9'dan) başlar.
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
     hub-requirements.txt --python-version 3.14 --python-platform x86_64-pc-windows-msvc --generate-hashes`
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
    - ~~**VoxCPM2 (GGUF)**~~ (2026-11-03 yapıldı): aşağıda "VoxCPM2".
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

- **Router Oyun Alanı'nda akış:** ses 0,5 sn birikmeden çalmaya başlamaz (8810 sayfasının kendi oynatıcısı gibi ön
  yükleme yapar). Parçalar 40-120 ms arayla gelir, ilki ~40 ms'lik sestir; hemen çalınca ikincisi gelmeden ilki
  biter ve ilk sesten sonra "tık" duyulurdu.
- **Router → TTS "yaratıcılık":** Router'ın 0-2 aralıklı "temperature" değeri TTS servisine `creativity` olarak gider;
  servis motora göre hizalar: OmniVoice'ta guidance'ın kendisi, VoxCPM2'de 2-4 (0 → 2, 1 → 3, 2 → 4). 8810 sayfası
  doğrudan `guidance_scale` yollar.
- **Motorlardan bağımsız ayarlar:** `/v1/voices?engine=omnivoice|voxcpm2` o motorun klon seslerini verir (çalışan motor
  ne olursa olsun), `/v1/languages` her zaman OmniVoice listesini; Router `local-tts-info` `voices_by_engine` döner ve
  Modeller sayfası seçili motorun (Engine View) kendi seslerini/dilini gösterir. Hız alanı VoxCPM2 görünümünde yok.
- **8810 sayfası ve Router aynı ölçekte:** VoxCPM2'de "Yaratıcılık" 0-2 (0,1 adım, 0,6'dan başlar = guidance 2,6) ve
  `creativity` olarak gider; OmniVoice'ta guidance 1-10 kalır. Her motorun değerleri (hız, yaratıcılık, adım,
  rastgelelik, seed) tarayıcıda motor adıyla ayrı saklanır (`orionTts:<motor>:<alan>`).
- **Router Modeller sayfası:** `default_config.per_engine = {omnivoice: {...}, voxcpm2: {...}}`; Engine View
  değişince ekrandakiler o motora kaydedilir, öbürününki gelir; Kaydet ikisini birden yazar. Oyun Alanı çalışan
  motorun `per_engine` değerlerini kullanır (yoksa eski düz alanlar).
- **Dil listesi:** OmniVoice'un 647 dili `services/tts/omnivoice_languages.json` dosyasından gelir (lang_map.py'den
  üretildi; PyTorch'lu paket release'te olmadığı için liste yalnız Auto/Turkish/English'e düşüyordu). Dosya Hub
  paketine girer.
- **Klon sibilansını yumuşatma (2026-11-09):** Kayıttaki keskin s/ş/t sesleri klonla büyüyebildiğinden, klon örneğine
  kaydetme anında bir kez 5 kHz yumuşak low-pass uygulanır (`soften_reference`, 8. derece eğri). Klonsuz karakter
  tasarımına ve üretilen klon sesine uygulanmaz; Noise Gate kullanılmaz. Var olan cache'ler yeniden klonlanmadan
  değişmez. Bellek/RAM sıkışınca sunucu "LocEnc sequence forward failed" ya da ggml
  `mem_buffer` hatasıyla düşebilir (sistemin boş taahhüt belleği ~4 GB iken görüldü).
