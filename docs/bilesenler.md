# Bileşenler (isteğe bağlı yerel yapay zekâ parçaları)

Orion kurulumu yalnızca çekirdeği kurar (Hub, pencere, Router). Hafıza (embedding), yerel dil modeli (LLM),
ses tanıma (STT) ve seslendirme (TTS) kullanıcı istediğinde, istediği yerden kurulur:

- **Ayarlar > Bileşenler**: her parça bir kutucuk. Kutucukta ne işe yaradığı, durumu, ortada model alanı,
  katlanmış "Gelişmiş ayarlar" ve altta tek düğme var.
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
  Pencereyi kapatmak işi durdurmaz; yarım kalan indirme `.part` dosyasından kaldığı yerden sürer. Durum
  `jobs/<parça>.json`'da: aşama, bayt, toplam, hız, hata.
- **Motor:** llama.cpp tek sefer indirilir, LLM ve hafıza paylaşır (`engines/llama.cpp-<sürüm>/<cuda|vulkan|cpu>`).
- **Çalıştırma:** her parça Orion'un süreç yöneticisiyle (`process_lifecycle`) çalışır, yalnızca 127.0.0.1'i
  dinler, Hub durunca durur. Hub'ın sahip süreci dakikada bir kurulu parçaların açık olduğunu denetler (çöken
  geri gelir; kaldırılan açılmaz).
- **Yetki:** durumu oturum açmış herkes görür (telefon salt okunur). Kurma, kaldırma, ayar yalnızca Hub
  bilgisayarındaki Orion penceresinden (`update_routes.local_control`, güncellemelerle aynı kural).
- **Donanım:** `nvidia-smi` ile NVIDIA kartları ve bellekleri; Windows'ta diğer kartların adları. Varsayılan:
  NVIDIA varsa CUDA, başka kart varsa Vulkan, yoksa CPU; model kart belleğine sığıyorsa tüm katmanlar kartta.
  Gelişmiş ayarlar: nerede çalışsın, hangi kart (birden çok varsa), kart katmanı, bağlam, eşzamanlı istek.

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
