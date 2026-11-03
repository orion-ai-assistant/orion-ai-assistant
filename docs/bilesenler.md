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
  Pencereyi kapatmak işi durdurmaz; tam inen dosyalar (motor parçaları dahil) kalır, yarım kalan dosya baştan iner (kaldığı yerden eklemek bozuk model verebiliyordu; çoğu modelin SHA-256'sı yok). Durum
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
  dosyası kendi satırında: ne olduğu, boyutu ve satırın sonunda o dosyanın kendi düğmesi (yoksa İndir, varsa
  Sil, inerken dönen simge). Durum yazısı yok: düğme dosyanın durumunu söyler. Ek dosyanın (mmproj) satırının
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
  - **STT (faster-whisper):** `model.bin` (başlığı CTranslate2 Whisper olmalı), `config.json`, `tokenizer.json`,
    `vocabulary.json` ya da `vocabulary.txt` (Systran'ın small/medium modelleri ve bizim small `.txt` getirir).
    `preprocessor_config.json` sesin kaç mel bandına bölüneceğini söyler: model 80'den çok bant istiyorsa
    (large-v3, turbo: 128; `model.bin`'den okunur) zorunlu, değilse isteğe bağlı; varsa sayısı modelinkiyle aynı
    olmalı. CTranslate2 4.8.4 ve faster-whisper 1.2.1 ile denendi (2026-11-02): iki sözcük listesi de aynı sonucu
    verdi, hiçbiri yoksa model açılmadı; turbo 80 bantlık sese hata verdi. Ses servisleri internete çıkmaz
    (`HF_HUB_OFFLINE`): eksik bir dosyanın yerine hiçbir şey indirilmez.
  - **TTS:** motor kullanıcının seçimi (OmniVoice; VoxCPM2 yakında). OmniVoice'un iki dosyası başlıklarındaki
    mimariden (`omnivoice-lm`, `omnivoice-tokenizer`) ayırt edilir; tanınmayan dosya adıyla söylenir.

  "Kaldır + indirilen dosyaları da sil" kullanıcının modellerini ayrıca sormadan silmez.
- **Çalıştırma:** her parça Orion'un süreç yöneticisiyle (`process_lifecycle`) çalışır, yalnızca 127.0.0.1'i
  dinler, Hub durunca durur. Hub'ın sahip süreci dakikada bir kurulu parçaların açık olduğunu denetler (çöken
  geri gelir; kaldırılan açılmaz).
- **Yetki:** durumu oturum açmış herkes görür (telefon salt okunur). Kurma, kaldırma, ayar yalnızca Hub
  bilgisayarındaki Orion penceresinden (`update_routes.local_control`, güncellemelerle aynı kural).
- **Donanım:** `nvidia-smi` ile NVIDIA kartları ve bellekleri; Windows'ta diğer kartların adları. Varsayılan:
  ekran kartı varsa Vulkan (NVIDIA'da CUDA da seçilebilir; STT'de yalnızca CUDA; TTS NVIDIA'da önce CUDA,
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
  | faster-whisper / CTranslate2 4.8.4 (STT) | 7.5 | `ctranslate2.dll`: ELF sm_53-86, PTX sm_86 (kendisi 5.3); ama `nvidia-cudnn-cu12 9.27.0.42` `cudnn_ops64_9.dll` ve `cudnn_cnn64_9.dll`: ELF sm_75, 80, (86,) 90, 100, 120, 121, PTX yalnızca sm_121, yani cuDNN 7.5'ten başlıyor. Whisper'ın kodlayıcısındaki evrişimler cuDNN'den geçer. `nvidia-cublas-cu12 12.9.2.10` `cublas64_12.dll`: sm_50'den başlıyor, sınırı belirlemiyor |

  Örnek: GTX 1060 (6.1) hafıza ve LLM'de CUDA'yı görür, TTS'te Vulkan ya da CPU, STT'de yalnızca CPU; GTX 16xx/RTX
  20xx (7.5) ve sonrası her parçada CUDA'yı görür. Motor ya da cuDNN sürümü değişince sınır yeniden okunur:
  `cuobjdump --list-elf --list-ptx <dll>` (CUDA 12.1'inki yeni cuDNN/cuBLAS DLL'lerinde sm_100+ girdilerinde
  takılıyor; o zaman fatbin başlıkları doğrudan okunur: `0xBA55ED50` imzası, her girdide tür ve `sm` alanı).

Doğrulandı (2026-11-02, bu bilgisayarda): hafıza parçası CPU motoru + Jina v5 Omni Nano ile gerçekten indirildi,
kuruldu, `/health` "ok" ve `/v1/embeddings` vektör döndü; Kaldır servisi durdurdu; bekçi kurulu parçayı açtı;
ayar değişince yeniden başladı.

## Yapılacaklar

Sıra önerisi yukarıdan aşağıya.

1. **Hafıza özelliğinin kendisi.** Embedding parçası kuruluyor ama onu kullanan bir hafıza özelliği yok
   (`worker/services/router.py` `generate_embeddings` çağrılmıyor ve embedding modeli yerine sohbet model
   grubunu gönderiyor). Hafıza kapalıyken sohbet ekranında "Hafıza kapalı. Açmak için hafıza servisini kur
   [Bileşenleri aç]" kutucuğu (`offerPart(part: 'embedding')`). Tasarım notları: `embedtaktik.md`,
   `assets/yz hafıza sistemi/`.
2. ~~Yerel STT ve TTS'yi buradan kurmak~~ (2026-11-02 yapıldı, PyTorch'suz):
   - **STT:** Orion'un paketli Python'u (3.11) + faster-whisper/CTranslate2 paketleri (43 MB, sabit sürüm ve
     SHA-256, `uv pip compile` ile Hub paketleriyle uyumlu çözüldü). GPU için ek olarak NVIDIA cuBLAS/cuDNN/NVRTC
     (~1.3 GB, yalnızca "Ekran kartı" seçilince). Modeller bizim `stt-whisper-ct2-v1` yayınımızdaki SHA'lı ziplerden.
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
       `bin/`'de izlenmeyen bir `ggml-cuda.dll` durduğu için görünmüyordu. `test_a_bundled_engine_brings_every_dll_it_needs`
       artık yalnızca paketten kurulan her motorun içe aktardığı DLL'lerin yanında (ya da Windows'ta) olduğunu denetler.
     - Doğrulandı (2026-11-03): yalnızca `bin/`'deki beş dosyanın olduğu bir klasörde, PATH'te yalnızca System32
       varken `tts-server.exe` CPU'da ses üretti; çıktı eski (yerel işlemciye göre derlenmiş) takımla bayt bayt
       aynı, hız da aynı (2,7 sn ses ~21 sn, 6 iş parçacığı). CUDA zipi tek başına açılıp CUDA0'da çalıştı.
     - **Kalan (CUDA):** `ggml-cuda.dll` `cudart64_12.dll` ve `cublas64_12.dll` (o da `cublasLt64_12.dll`) istiyor;
       eski zip de yeni zip de bunları getirmiyor. CUDA Toolkit'i PATH'te olmayan bir bilgisayarda TTS'in CUDA
       seçeneği de 0xC0000135 ile açılmaz (bu bilgisayarda Toolkit kurulu olduğu için görünmüyor). Çözüm adayı:
       CUDA tarifine llama.cpp'nin `cudart-llama-bin-win-cuda-12.4-x64.zip`'i (CUDA 12 içinde küçük sürüm uyumu)
       ya da STT'nin NVIDIA paketleri.
   - Servisler `services/shared/part_python.py` ile başlar; `ORION_PART` varken servisler depodaki geliştirici
     `.env`'sini okumaz. Kaynaktan çalışan Hub başka bir Python sürümündeyse (3.14) STT kutucuğu bunu söyler.
   - Doğrulandı: kurulu Orion'un Python 3.11'iyle STT ve TTS gerçekten kuruldu; TTS'in ürettiği Türkçe cümle STT ile
     yazıya döndü. Bu sırada TTS'in akışlı WAV'ın yalnızca ilk parçasını döndürdüğü hata düzeltildi.
   - Kalan: STT/TTS paketlerinin sürümünü yenilerken `uv pip compile -c
     hub-requirements.txt --python-version 3.11 --python-platform x86_64-pc-windows-msvc --generate-hashes`
     ile yeniden çöz, adres ve SHA-256'ları PyPI'den alıp kataloğa yaz (bunu yapan bir betik henüz depoda yok).
3. **TTS ses klonları:** Bileşenler'in kurduğu TTS klonları `Parts/run/tts/voices` altında tutar; Orion'la gelen
   sesler kod klasöründe. Klon yönetimi ekranı yok.
4. **Yerel mi bulut mu seçimi.** Her kutucukta "Bulut (Router üzerinden)" seçeneği: STT için Gemini
   transcribe var ama canlı sesli yazma hep yerel servise gidiyor (`orion-router/api/transcriptions.py`);
   embedding için `GeminiEmbedProvider` var ama `gemini/models.json`'da embedding modeli listelenmemiş. Seçim
   Router ayarlarıyla (stt_model, embed modeli) tek yerde tutulmalı.
5. **macOS ve Linux.** Katalogda `engines.llama.cpp.mac` (Metal, arm64) ve `linux` (cuda/vulkan/cpu) derlemeleri
   eklenince kutucuklar o sistemlerde kurulabilir olur; donanım algılama Metal'i zaten seçenek olarak veriyor.
6. **İndirme doğrulaması.** Modeller için SHA256 (Hugging Face `x-linked-etag`/LFS oid) ve motor zipleri için
   sabit özet; şimdilik yalnızca boyut (Content-Length) denetleniyor.
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
    paketteki `bin/ggml.dll` `ggml-cuda.dll`'ye bağlı, onsuz açılmaz.
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

      Sunucunun açılmasında arka uç fark etmiyor: önce 28-79 sn'ye 1.5 sn göründü, ama CUDA model dosyalarını
      D: (HDD) üzerinden soğuk okuyordu, Vulkan ise önbellekten. Soğukken üçü de 13-54 sn, ısınmışken 1-2 sn.
      NVIDIA'da CUDA hızlı (Vulkan varsayılan seste 1.2-1.35, klon seste ~1.7 kat yavaş), bu yüzden TTS'in sırası
      `cuda, vulkan, cpu`. AMD/Intel kartta Vulkan CPU'dan 30-40 kat hızlı. Vulkan her yeni şekilde bir kez
      pipeline derler (sunucu açıldıktan sonraki ilk istekler birkaç saniye uzun).
    - **Kalan:** zipi `tts-v1.1.0` yayınına yüklemek (kullanıcı; adres, SHA-256 ve boyut katalogda). Gerçek bir
      AMD/Intel kartta denenmedi.
