# TTS motorunu derlemek (OmniVoice: CUDA, Vulkan, CPU)

OmniVoice'un C++ motoru (`services/tts/omnivoice-gguf`: `tts-server.exe`, `omnivoice-codec.exe` ve ggml
DLL'leri) kendi ggml kopyasıyla CMake'le derlenir. Kullanıcılar derlemez: Bileşenler sayfası motoru
`services/shared/local_parts.json`'daki adresten indirir (SHA-256 denetimiyle) ve
`Parts/engines/omnivoice-gguf-<sürüm>/<cuda|vulkan|cpu>` altına açar. Derleme yalnızca motor değiştiğinde,
geliştirici bilgisayarında yapılır. Ölçümler ve parça davranışı için: [bilesenler.md](bilesenler.md) (Yapılacaklar 13).

## 1. Bir kerelik kurulum (geliştirici bilgisayarı)

| Araç | Ne için | Nereden |
|---|---|---|
| Visual Studio 2022 (C++ masaüstü), CMake, Ninja | her derleme | VS kurulumu (CMake ve Ninja onunla gelir) |
| CUDA Toolkit 12.1 | yalnızca CUDA derlemesi | NVIDIA |
| LunarG Vulkan SDK | yalnızca Vulkan derlemesi | https://vulkan.lunarg.com/sdk/home (Windows) |

Vulkan SDK bu bilgisayara 2026-11-03'te kuruldu (1.4.363.0, `C:\VulkanSDK\1.4.363.0`):

```bash
curl -s https://vulkan.lunarg.com/sdk/latest/windows.txt
```

```bash
curl -L -o vulkansdk-windows-X64-1.4.363.0.exe https://sdk.lunarg.com/sdk/download/1.4.363.0/windows/vulkansdk-windows-X64-1.4.363.0.exe
```

- Dosyanın SHA-256'sı LunarG'nin yayımladığıyla karşılaştırıldı
  (`https://vulkan.lunarg.com/sdk/sha/<sürüm>/windows/vulkansdk-windows-X64-<sürüm>.exe.json`) ve imzası
  denetlendi (Authenticode: `LunarG, Inc.`).
- Sessiz kurulum (yönetici onayı ister):
  `vulkansdk-windows-X64-1.4.363.0.exe --root C:\VulkanSDK\1.4.363.0 --accept-licenses --default-answer --confirm-command install`.
  Kurulum `VULKAN_SDK` ortam değişkenini sistem geneline yazar (yeni açılan terminaller görür).
- SDK'nın getirdikleri: Vulkan başlıkları (`vulkan.hpp` dahil), `vulkan-1.lib`, SPIRV-Headers ve güncel `glslc`
  (gölgelendiricileri derler). Android NDK'daki eski `glslc` (2022) bu ggml için yetmez.
- Kullanıcıların bilgisayarında SDK **gerekmez**: Vulkan'ı ekran kartı sürücüsü sağlar (`vulkan-1.dll`).
- Kaldırmak için: `C:\VulkanSDK\1.4.363.0\maintenancetool.exe` (yeniden derlemek gerekince tekrar kurulur).

## 2. Derleme

İki betik de `services/tts/omnivoice-gguf` içinde durur; çift tıklanarak ya da tam yoluyla çağrılır.
Visual Studio'nun `vcvars64.bat`'ı çalışma klasörünü değiştirebildiği için betik adını göreli yazıp başka bir
klasörden çağırınca cmd betiğin kendisini bulamaz ("is not recognized").

- **CPU ve CUDA:** `build_release.bat` (argümansız ikisini, `cpu` ya da `cuda` ile yalnızca birini derler).
  CPU takımını `%TEMP%\ov-cpu`'da derleyip `bin\`'e (git'te izlenir, Orion paketine girer), CUDA takımını
  `%TEMP%\ov-cuda`'da derleyip `build\cuda\`'ya kopyalar. Bayraklar: CPU `-DGGML_CUDA=OFF -DGGML_NATIVE=OFF`,
  CUDA `-DGGML_CUDA=ON -DGGML_NATIVE=OFF`. Süre: CPU ~2 dakika, CUDA ~20 dakika (190 adım, çoğu CUDA çekirdeği).
- **Vulkan:** `build_release_vulkan.bat`, `%TEMP%\ov-vk` klasörüne derler ve takımı `build\vulkan\`'a kopyalar.
  - Bu klasör kısa tutulmak zorunda: ggml-vulkan'ın gölgelendirici aracı (`vulkan-shaders-gen`) depo içindeki
    derin bir yolda Windows'un 260 karakter sınırına takılıp "Configuring incomplete" ile durur.
  - Bayraklar: `-DGGML_VULKAN=ON -DGGML_CUDA=OFF -DGGML_NATIVE=OFF`. `GGML_NATIVE=OFF` işlemcide AVX2 tabanı
    demektir; böylece zip başka işlemcilerde de çalışır.
  - Süre yaklaşık 5 dakika (360 adım, çoğu gölgelendirici).

`GGML_BACKEND_DL=OFF` olduğu için `ggml.dll` derlendiği arka ucun DLL'ine doğrudan bağlıdır: CUDA derlemesinin
`ggml.dll`'si `ggml-cuda.dll`'yi, Vulkan'ınki `ggml-vulkan.dll`'yi ister. Bu yüzden her takım bütündür ve
birbirine karışmaz: `bin/` yalnızca CPU, CUDA ve Vulkan zipleri kendi `tts-server.exe`, `omnivoice-codec.exe` ve
`ggml*.dll`'lerini taşır. 2026-11-03'e kadar `bin/` CUDA derlemesindendi ve kurulu Orion'da CPU seçeneği
açılmıyordu (0xC0000135); `test_an_engine_brings_every_dll_it_needs` bunu artık yakalar.

**CUDA çalışma zamanı:** `ggml-cuda.dll` ayrıca `cudart64_12.dll`, `cublas64_12.dll` ve (onun üzerinden)
`cublasLt64_12.dll` ister. Bunlar yalnızca CUDA Toolkit kurulu bilgisayarda vardır, sürücüyle gelmez. Bizim zipimize
konmaz: CUDA tarifinin ikinci arşivi llama.cpp'nin `cudart-llama-bin-win-cuda-12.4-x64.zip`'idir (yalnızca bu üç
DLL, 391 MB; SHA-256 katalogda). Derleme 12.1 ile yapılıyor, 12.4 çalışma zamanı aynı ana sürüm olduğu için onu
çalıştırır (2026-11-03'te PATH'te Toolkit yokken CUDA0'da ses üretti; yalnızca bizim zip 0xC0000135 verdi).
Test, ziplerin hepsi bu bilgisayardaysa (`services/tts/omnivoice-gguf/` ya da `ORION_ENGINE_ARCHIVES` klasörü,
katalogdaki SHA-256'yla) tarifin kurduğu klasörü açıp her `.exe`/`.dll`'nin istediği DLL'in yanında, System32'de
ya da ekran kartı sürücüsünde (`nvcuda.dll`, `vulkan-1.dll`) olduğunu denetler. Test hiçbir şey indirmez.
`GGML_BACKEND_DL=ON` (tek takım, arka uçlar sonradan yüklenen DLL'ler) seçilmedi: arka uç DLL'i tam o
`ggml-base.dll` derlemesine bağlanır, yani her yeniden derlemede bütün zipler yine yenilenir.

## 3. Denemek

Derlenen sunucu çalışırken arka ucu ortam değişkeniyle seçilir:
`GGML_BACKEND=CUDA0`, `Vulkan0` ya da `CPU`. `GGML_SCHED_DEBUG=2`, scheduler'dan geçen her işlemin hangi arka
uçta çalıştığını günlüğe yazar. CPU'ya düşen bir işlem olursa orada `[CPU]` diye görünür.

```bash
GGML_BACKEND=Vulkan0 services/tts/omnivoice-gguf/build/vulkan/tts-server.exe --model services/tts/models/omnivoice-gguf/omnivoice-base-Q8_0.gguf --codec services/tts/models/omnivoice-gguf/omnivoice-tokenizer-Q8_0.gguf --host 127.0.0.1 --port 18809
```

İstek: `POST /v1/audio/speech` `{"input": "...", "response_format": "wav", "seed": 42, "steps": 22}`. Klon ses için
önce `POST /v1/audio/voices` `{"name", "ref_text", "rvq_b64" | "wav_b64"}`. Tarifin kendi kendine yettiğini
görmek için: tarifin bütün arşivlerini boş bir klasöre açıp PATH'te yalnızca `C:\Windows\System32` varken
çalıştır. Vulkan için tek zip yeter. CUDA için kendi zipimizle birlikte `cudart` zipi de gerekir; yalnızca bizim
zip Toolkit'siz bilgisayarda açılmaz.

## 4. Paketlemek ve yayına koymak (herkesin indirebilmesi için)

1. `python package_release.py vulkan` (CUDA için argümansız, `build\cuda\`'dan) zipi oluşturur ve SHA-256 ile
   boyutu yazar. CPU takımı zip değildir: `bin/` commit'lenir, Hub paketiyle gider.
   Zip `services/tts/omnivoice-gguf/` altında durur; `.gitignore` onu dışarıda tutar.
2. Zip, bu deponun GitHub yayınına yüklenir. Etiket `tts-v<sürüm>`; CUDA ve Vulkan zipleri `tts-v1.1.0`'da
   (katalogdaki motor sürümü de `tts-v1.1.0`). CUDA'nın ikinci arşivi (`cudart`) llama.cpp'nin yayınından
   gelir, bizim yayına yüklenmez:
   ```bash
   gh release create tts-v1.1.0 services/tts/omnivoice-gguf/omnivoice-gguf-windows-cuda.zip services/tts/omnivoice-gguf/omnivoice-gguf-windows-vulkan.zip --title "TTS — Ses Sentezi Paketleri (CUDA, Vulkan)" --notes "OmniVoice tts-server: CUDA (NVIDIA) ve Vulkan (AMD, Intel, NVIDIA), her biri bütün takım."
   ```
3. Adres, SHA-256 ve boyut `local_parts.json`'a yazılır: `engines` → `omnivoice-gguf` → `windows` → `<donanım>`.
   Motorun `version` alanı değişirse her bilgisayar o motoru yeniden indirir; yalnızca yeni bir donanım tarifi
   eklenince değiştirmek gerekmez.
4. Katalog Hub paketinin içinde gider: kullanıcılar yeni tarifi bir sonraki Hub yayınıyla (`release.cmd`) alır.
   Zip yayında yokken katalog onu gösterirse o seçenek indirmede hata verir. Bu yüzden sıra: önce zip, sonra
   Hub yayını.

## 5. Hangi kartta hangisi

- **CUDA:** yalnızca NVIDIA. Bizim derlememiz `CMAKE_CUDA_ARCHITECTURES=75-virtual;80-virtual;86-real;89-real`
  ile yapılıyor, yani compute capability 7.5 ve üstü: GTX 16xx, RTX 20xx ve sonrası. GTX 10xx (6.1) ve daha
  eskileri bu derlemeyle çalışmaz. Bu sınır `local_parts.json`'da tarifin `min_compute` alanıdır (`7.5`):
  kartın compute capability'si (`nvidia-smi --query-gpu=index,compute_cap`) altındaysa TTS'te CUDA seçeneği hiç
  çıkmaz, parça Vulkan'a (yoksa CPU'ya) geçer. Okunamazsa kart uygun sayılır. Derlenen `ggml-cuda.dll`'de
  gerçekten ne olduğu `cuobjdump --list-elf --list-ptx build/cuda/ggml-cuda.dll` ile görülür (2026-11-03: ELF
  sm_86, sm_89; PTX sm_75, sm_80). Mimari listesi değişirse `min_compute` de değişir; öteki motorların sınırları
  [bilesenler.md](bilesenler.md)'de ("CUDA'nın alt sınırı").
  CUDA tarifi CUDA 12.4 çalışma zamanını getirir; sürücünün CUDA 12 desteklemesi gerekir.
- **Vulkan:** AMD, Intel ve NVIDIA (sürücü Vulkan 1.2+ desteklediği sürece). NVIDIA'da CUDA'dan yavaş
  (RTX 3060 Ti: varsayılan seste 1.2-1.35, klon seste ~1.7 kat), ekran kartı belleğinde ~150 MB daha az.
- **Sıra** (`local_parts.choices()`): TTS parçası `cuda, vulkan, cpu` listeler. NVIDIA'lı bilgisayarda varsayılan
  CUDA, yalnızca AMD/Intel kartı olanda ya da NVIDIA kartı 7.5'in altında olanda Vulkan, kartsızda CPU. CUDA
  seçeneği yalnızca `nvidia-smi` 7.5 ya da üstü (veya capability'si bilinmeyen) bir kart bulursa çıkar.

## 6. Yeni bir TTS modeli ya da motoru

- **Aynı mimaride yeni GGUF** (ör. OmniVoice'un başka bir nicemlemesi ya da ince ayarı): yeniden derleme yok.
  Dosyalar Bileşenler'den "Kendi modellerin" ile eklenir, aynı `tts-server.exe` çalıştırır.
- **Başka bir mimari** (ör. VoxCPM2): `tts-server.exe` yalnızca OmniVoice'u tanır; başka mimari kendi ggml
  motoruyla gelir, bir kez CUDA, Vulkan ve CPU için derlenir, zipleri yayına konur, katalogda motor olarak tarif
  edilir ve modelin `engine` alanı onu gösterir. Ondan sonra o mimarinin GGUF'ları yeniden derleme olmadan çalışır.
  Depodaki `services/tts/voxcpm2` PyTorch'lu Python sürümüdür, Bileşenler'de kullanılmaz.

### Yeni motorda model ayarları (Router): her motor kendi ayarını kullanır

Router'da `local-tts` modelinin ayarları (ses, seed, adım, dil, ses tarifi, cinsiyet/yaş...) her motor için ayrı
saklanır (`default_config.per_engine.<motor>`). Router, isteğin boş bıraktığı alanları **o an çalışan motorun**
kaydından doldurur (`orion-router/core/router/runners/tts.py`, `_engine_defaults`); motoru TTS'in
`/v1/model_info` adresinden öğrenir (`providers/local/tts.py`, `active_engine`). Yeni bir motor eklenince:

- `active_engine` motorun adını bir aileye çevirir (`voxcpm2-gguf` → `voxcpm2`). Yeni motor bu eşlemeye eklenmezse
  Router onu tanımaz ve ayarları kaydedildiği gibi, yani başka motorun değerleriyle gönderebilir.
- Motorun almadığı alanlar Router'da düşürülür (`_NOT_TAKEN`; VoxCPM2: OmniVoice'un cinsiyet/yaş tasarımı, hız ve dil;
  yalnız yazılı ses tarifini alır). Router panelindeki form da aynı alanları o motor için göstermez ve kaydetmez
  (`TTSDefaultConfig.NOT_TAKEN`); ikisi birlikte güncellenir. Yeni motorun almadığı alanlar da buraya eklenmeli.
  Aksi halde bir motorun ayarı diğerine geçer ya da görünmeyen bir değer gönderilir. 2026-11-11'de böyle oldu: OmniVoice'un "female, young adult" ayarı VoxCPM2'ye ses tarifi
  olarak gitti ve VoxCPM2 hazırlık sesi yerine yeni bir ses tasarladı, Hub'daki ses TTS sayfasındakinden farklı çıktı.
- Router panelinde Modeller → `local-tts` formunda (`dashboard/app/models/page.tsx`) "Ayarlar Görünümü"ne yeni
  motor eklenir; form her motorun alanlarını o motorun kendi kaydından gösterir.
- Router panelindeki istek logu, TTS servisine (ya da OpenAI'ya, Gemini'ye) giden gövdenin kendisidir, gönderildiği
  gibi (`orion-router/core/outgoing.py`: ortak HTTP istemcisi ve Gemini istemcisi her JSON gövdesini kaydeder; API
  anahtarı başlıkta gider, logda olmaz). Ses beklenenden farklıysa önce buna bak: modelin ayarlarından gelen bir
  değer de orada görünür. Router, modelde kayıtlı olmayan hiçbir değeri kendiliğinden eklemez.

## 7. VoxCPM2 motoru (llama.cpp-omni)

- Kaynak: kendi fork'umuz [krstalacam/llama.cpp-omni](https://github.com/krstalacam/llama.cpp-omni), `orion` dalı
  `9b49812768a984852496d1ef07ece328ca35fb74` (MIT; motor `voxcpm2-v1.0.2`), yalnızca `llama-tts-server` hedefi. Üst depo
  [tc-mb/llama.cpp-omni](https://github.com/tc-mb/llama.cpp-omni) `d30f00f` üstüne kendi commit'lerimiz: MSVC'de derlenir
  (`src/llama-moe-slot.cpp` POSIX `ssize_t` / `struct iovec` kullanıyordu) ve TTS sunucusu üretimi tek tek yapar
  (`server-voxcpm2.cpp` `generate_mutex`; önce httplib iki isteği aynı KV önbellekte çalıştırıp çöküyordu), akışsız
  `/v1/audio/speech` istemci kopunca durur (`VoxCPM2GenerateParams::cancelled`, `decode_loop` her adımda sorar).
  v1.0.2: `encode_reference_audio` aynı ses örneğinin AudioVAE kodlamasını örneklerin hash'iyle saklar (en çok 8);
  Orion her cümlede aynı hazırlık sesini/klon örneğini gönderdiği için kodlama sesi başına bir kez yapılır
  (RTX 3060 Ti'de 100-280 ms). Aynı commit `server-voxcpm2.cpp`'deki bir string içindeki gerçek satır sonunu düzeltti:
  `build_release.bat`'taki eski `COMMIT` (`0f0b094`) fork'ta yoktu ve dalın ucu (`89456ef`) bu yüzden derlenmiyordu.
  Yeni bir düzeltme o dala commit'lenir, `build_release.bat`'taki `COMMIT` güncellenir.
  `services/tts/voxcpm2-gguf/build_release.bat [cuda|vulkan|cpu]` kaynağı `%TEMP%\ovx\src`'ye indirir, o commit'e
  geçer, `%TEMP%\ovx\b-<donanım>`'da derler
  ve takımı `build\<donanım>\`'a kopyalar. Depo içindeki yol yine 260 karakter sınırına takılıyordu (git checkout
  bile yarıda kalır; `core.longpaths=true` ile indirilir).
- Bayraklar: hepsinde `-DGGML_NATIVE=OFF -DLLAMA_BUILD_UI=OFF -DLLAMA_OPENSSL=OFF -DLLAMA_BUILD_TESTS=OFF
  -DLLAMA_BUILD_EXAMPLES=OFF`; Vulkan `-DGGML_VULKAN=ON`; CUDA `-DGGML_CUDA=ON`,
  `CMAKE_CUDA_ARCHITECTURES=75-virtual;80-virtual;86-real;89-real` ve `CUDAFLAGS=-allow-unsupported-compiler`
  (CUDA 12.1 bu Visual Studio'yu ancak böyle kabul eder; CMake derleyiciyi tanırken bunu ortam değişkeninden okur).
  Batch argümanında `=` ayırıcıdır: bayrakları argüman olarak değil ortam değişkeniyle geçir.
- `python package_release.py` her takımı `voxcpm2-gguf-windows-<donanım>.zip` yapar, adres, SHA-256 ve boyutu
  yazar; ziplerin yeri `voxcpm2-v<sürüm>` yayını (şu an `voxcpm2-v1.0.2`; `RELEASE_TAG`). CUDA zipine CUDA çalışma zamanı konmaz (OmniVoice'taki gibi
  llama.cpp'nin `cudart` zipi ikinci arşivdir).
- KV önbelleği (commit `43cf1f9`, yalnız Vulkan denendi): BaseLM (llama bağlamı) ile ResidualLM'in önbelleği koda gömülü 32768
  konumla baştan ayrılıyordu (~1,1 GB). Artık tek ayardan: `--voxcpm2-ctx N` (env `LLAMA_ARG_VOXCPM2_CTX`, varsayılan 2048).
  Sınır aşılacaksa `prefill`/`decode_step` `fail("request too long ...")` ile hata döner, çökmez; her istek logda
  `VoxCPM2: request uses N of M positions` yazar. Ölçüm (Vulkan, `--voxcpm2-n-gpu-layers 0`, RTX 3060 Ti): 30 karakter 38,
  300 karakter 313-356, 600 karakter 490-510 konum; Orion'un en kötü isteği (<=287 karakter + 20 sn referans + <=200 adım)
  bunun altında, yani 2048'in yarısını geçmez. 2048 ile 32768 aynı metin+seed için bayt bayt aynı uzunlukta wav üretir.
  Boşta commit 3,61 -> 2,32 GB, WorkingSet 1,09 -> 0,23 GB, VRAM 2,50 -> 2,06 GB; 7 katman GPU'da: commit 3,95 -> 2,63,
  VRAM 2,91 -> 2,32 GB. Kart doluyken (başkaları ~4,8 GB) 32768 ile uzun/klonlu istekler 500 verdi, 2048 ile hepsi geçti.
- Denemek: `llama-tts-server.exe --voxcpm2-base-lm VoxCPM2-BaseLM-Q8_0.gguf --voxcpm2-acoustic
  VoxCPM2-Acoustic-F16.gguf --host 127.0.0.1 --port 18807`; `POST /v1/audio/speech`
  `{"model": "voxcpm", "input": "...", "reference_audio": "<base64 WAV>", "prompt_text": "<örneğin metni>"}`.
