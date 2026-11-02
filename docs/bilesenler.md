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
| Hub API (`/api/v1/parts`) | `services/hub/src/orion/api/parts_routes.py` |
| Hub açılınca kurulu parçaları başlatan bekçi | `services/hub/src/orion/api/main.py` (`keep_parts`) |
| Flutter sayfası, model paneli, `offerPart` | `orion-ai-assistant-flutter/.../features/settings/components_page.dart` |

- **Nerede durur:** kurulu Orion'da `%LOCALAPPDATA%\Orion\Parts` (modeller, kurulumun models konumu varsa
  orada), kaynaktan çalışan Hub'da depo içindeki `.local_parts/`.
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
  - **STT (faster-whisper):** bizim modellerdeki dosyaların hepsi zorunlu: `model.bin` (başlığı CTranslate2
    Whisper olmalı), `config.json`, `tokenizer.json`, `vocabulary.json` ya da `.txt`, `preprocessor_config.json`
    (sesin kaç mel bandına bölüneceği; `model.bin`'in istediğiyle aynı olmalı, large-v3 ve turbo 128, eskiler
    80). faster-whisper bu adlarla açar. CTranslate2 4.8.4 ve faster-whisper 1.2.1 ile denendi (2026-11-02): iki
    sözcük listesi de aynı sonucu verdi, hiçbiri yoksa model açılmadı; turbo 80 bantlık sese hata verdi. Ses
    servisleri internete çıkmaz (`HF_HUB_OFFLINE`): eksik bir dosyanın yerine hiçbir şey indirilmez.
  - **TTS:** motor kullanıcının seçimi (OmniVoice; VoxCPM2 yakında). OmniVoice'un iki dosyası başlıklarındaki
    mimariden (`omnivoice-lm`, `omnivoice-tokenizer`) ayırt edilir; tanınmayan dosya adıyla söylenir.

  "Kaldır + indirilen dosyaları da sil" kullanıcının modellerini ayrıca sormadan silmez.
- **Çalıştırma:** her parça Orion'un süreç yöneticisiyle (`process_lifecycle`) çalışır, yalnızca 127.0.0.1'i
  dinler, Hub durunca durur. Hub'ın sahip süreci dakikada bir kurulu parçaların açık olduğunu denetler (çöken
  geri gelir; kaldırılan açılmaz).
- **Yetki:** durumu oturum açmış herkes görür (telefon salt okunur). Kurma, kaldırma, ayar yalnızca Hub
  bilgisayarındaki Orion penceresinden (`update_routes.local_control`, güncellemelerle aynı kural).
- **Donanım:** `nvidia-smi` ile NVIDIA kartları ve bellekleri; Windows'ta diğer kartların adları. Varsayılan:
  NVIDIA varsa CUDA, başka kart varsa Vulkan, yoksa CPU; model kart belleğine sığıyorsa tüm katmanlar kartta.
  Gelişmiş ayarlar: nerede çalışsın, hangi kart (birden çok varsa), bağlam, eşzamanlı istek (kartta tüm katmanlar kartta).

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
   - **TTS:** omnivoice-gguf (`tts-server.exe`); CPU dosyaları Orion paketinde, CUDA zipi `tts-v1.0.0` yayınından.
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
8. **Eski geliştirici kurulum paneli** (`installer/app`, 127.0.0.1:7171): yerel servis kurma/indirme işleri artık
   buraya taşınabilir; panelin hangi kısımlarının kaldırılacağı kullanıcıya sorularak karar verilecek.
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
