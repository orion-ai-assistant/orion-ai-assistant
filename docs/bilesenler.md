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
  `jobs/<parça>.json`'da: aşama, bayt, toplam, hız, hata.
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
- **Model silme (Modeller paneli):** seçili olan dahil her model silinebilir; ne silineceği önce söylenir. Ek
  dosya (mmproj) kendi satırında: açılıp kapanır ve tek başına silinebilir (seçili modelinse görsel anlama
  kapanır). Çalışan servisin açık tuttuğu dosya için servis önce durur, dosya sonra silinir (`drop`, `apply`
  içinde); parça Başlat'a kadar durur.
- **Kendi modellerin:** her parçanın kendi klasörü var (`models/<parça>/kendi/<ad>/`). Panelde "Kendi modelini
  ekle": ad verilir, o adla klasör açılır (Explorer'da da açılır), dosyalar kopyalandıkça listede tek tek
  işaretlenir (kopyalanan dosya "kopyalanıyor" görünür). Hangi dosyanın ne olduğu dosyalardan okunur
  (`USER_KINDS`): LLM ve hafıza GGUF (+ isteğe bağlı mmproj, GGUF başlığındaki mimariden ayırt edilir), STT
  faster-whisper klasörü (`model.bin`, `config.json`, `tokenizer.json`, `vocabulary.*`; large-v3 için
  `preprocessor_config.json`), TTS OmniVoice'un iki GGUF'u (`omnivoice-lm` ve `omnivoice-tokenizer`). Elle
  bırakılan klasör de aynı biçimde görünür. Bağlam sınırı, vektör boyutu, havuzlama dosyadan okunur. "Kaldır +
  indirilen dosyaları da sil" kullanıcının modellerini ayrıca sormadan silmez.
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
    - **Başka TTS motoru** (Kokoro vb.): `USER_KINDS['tts']`'e ikinci tür eklenince ekleme ekranı önce motoru
      sordurmalı (şimdi tek tür olduğu için yalnızca "Motor: OmniVoice (GGUF)" yazıyor) ve parçanın motoru
      modele göre seçilmeli.
    - **Hafızada embedding olmayan model:** şimdilik reddedilmiyor (GGUF'ta bunu güvenle söyleyen bir alan
      yok); havuzlama söylenmemişse `--pooling last` verilir.
    - **Hafıza modeli değişirse** eski vektörler yeni modelle karşılaştırılamaz (boyut ve uzay farklı): hafıza
      dizini modelin kimliği ve boyutuyla tutulmalı, model değişince arkada yeniden hesaplanmalı.
