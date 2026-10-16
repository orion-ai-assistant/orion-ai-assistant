# Aşama 04 — Başlatıcı ve Windows başlangıcı

Durum: DOĞRULAMA EKSİK. 16 Ekim 2026: kaynak kurulum başlatıcısı, açık tercih akışı ve izole Windows Startup kısayolu uygulandı; gerçek Unicode Hub/DB/Flutter sunucusu testi geçti. Temiz VM/gerçek oturum açılışı ve pencere odaklama/kapatma OS kabulü açık. 01/03 kabul borcu korunur; 05–08 uygulanmadı.

[Ana plan](../orion-installation-lifecycle-implementation-plan.md) · [Ortak sözleşmeler](shared-contracts.md)

## Ön koşullar ve okunacaklar

Ön koşul aşamalar: [01](01-process-lifecycle.md), [02](02-installation-registry-and-router-selection.md), [03](03-installation-paths-and-migration.md). Kullanıcının 16 Ekim 2026 talimatı, 01/03 açık kabulleri korunarak yalnız 04 geliştirmesini yetkilendirdi. Bu istisna önceki aşamaların veya yayın kabulünün tamamlandığı anlamına gelmez.

Ortak kararlar; 2.3 süreç yönetimi ve 2.4 OS sınırları; 01–03 sonuç kayıtlarındaki kararlı arayüzler.

Ön koşulların yalnızca durum etiketini değil, sonuç kaydını ve mevcut koddaki karşılığını doğrula. İlgisiz gelecek aşamaların tamamını okumak veya uygulamak gerekmez. Repo talimatları ayrıca geçerlidir.

## Bu aşamadaki iş ve kabul koşulları

Küçük sabit giriş noktası kayıt üzerinden ortak yaşam döngüsü katmanını çağırır. Masaüstü ve kullanıcı Başlangıç klasörü aynı girişe gider. Servis otomatik başlatma ile pencere gösterme ayrılır. Tekrar tıklama mevcut pencereye yönlendirir veya yalnız pencereyi açar. Başlatıcı hata/onarma için Hub'a bağımlı değildir. Hub/Router/yardımcı kilitleriyle aynı anda start/stop/update çakışmaları önlenir.

Kabul/test: temiz Windows oturumunda başlatma, art arda tıklama, oturum açılışı ve elle açılışın çakışması, tercih aç/kapat, pencereyi kapatınca servis politikasına uyma, D diski yokken anlaşılır hata, Hub açılamazken günlük/onarma erişimi. Gerçek Windows VM oturumuyla doğrulanır.

## Sonraki aşamaya devredilecek çıktı

Tek başlatıcı giriş noktası; kullanıcı başlangıç kısayolu ve pencere tercihleri; yerel hata/onarma davranışı.

Uygulanan arayüzlerin gerçek dosya yolları, giriş noktaları, şema sürümleri ve hata sonuçları aşağıdaki sonuç kaydına yazılır. Sonraki model tasarım varsayımı yerine bu kaydı ve kodu esas alır.

## Kapsam sınırı

Yalnız bu aşamanın işini uygula. Ön koşulda eksik sözleşme varsa dar kapsamlı düzeltmeyi kaydet; başka aşamanın tüm özelliğini sessizce üstlenme. Gerçek kullanıcı verisinde hata enjeksiyonu yapma. Aşama testleri geçmeden sonraki aşamaya geçme; bu aşama bittiğinde sonuçları raporlayıp dur. Ana plan tablosundaki durum da güncellenir.

## Sonuç ve test kaydı

- Durum: DOĞRULAMA EKSİK; kaynak kurulum çekirdeği uygulandı.
- Tarih: 16 Ekim 2026. Yalnız Hub deposu değişti; Router/Flutter depoları korunur.
- Sonraki aşama: bu oturumda devam edilmez; ayrı kullanıcı talimatı gerekir. SDK'sız dağıtım 05, eski runtime/DB dönüşümü 05/08 bağımlılığıdır.

### Kararlı girişler ve davranış

`services/shared/launcher.py`: `launch(registry, startup=False, background=False, stop=False)`, `install_entry(registry, desktop=True)`, `preferences(registry, start_with_windows=..., show_client=..., startup_directory=None)`. `startup_directory` test enjeksiyonudur; ürün API'si bunu kabul etmez. CLI: `python -m services.shared.launcher --registry <mutlak install.json> [launch|stop|install|preferences]`. Tercih komutu iki açık bayrak ister: `--start-with-windows on|off --show-client on|off`. `--background` pencere açmaz. Elle normal açılış pencereyi ister; `show_client` yalnız oturum açılışında pencere gösterme tercihidir. Pencere kapanışı servisleri kapatmaz; açık `stop` Hub'ı sonra yönetimi yetkili Router'ı durdurur.

Sabit giriş `<registry-parent>/bootstrap/Orion.ps1`; kaynak `services/shared/launcher_entry.ps1`. Kayıt dosyası `install.json` olmalıdır. Masaüstü ve Startup `Orion.lnk` aynı girişe gider; Startup yalnız `-Startup` ekler. PowerShell, seçilmiş Hub'ın installer venv'ini, yoksa Hub venv'ini kullanır; PATH'ten Python seçmez. Kayıtlı disk/runtime yoksa yeni kurulum oluşturmaz. Python/Hub çalışmasa bile yerel WinForms onarma açıklaması, launcher log klasörü ve kayıt klasörü erişilebilir. `-Repair` bu erişimi doğrudan açar; `-NonInteractive` otomatik test/teşhis için aynı hatayı çıkış kodu 1 ile döndürür. Otomatik veri/DB/runtime onarımı yapılmaz.

`windows_autostart.py`: Known Folder API ile kullanıcı Desktop/Startup konumu; WScript COM ile `.lnk`. Shell metnine yol gömülmez; JSON ortam girdisi ve argv kullanılır. Var olan linkin açıklama/target/argümanları aynı kurulumla eşleşmezse değiştirilmez. Etkinleştirme linki hazırlar, sonra tercihi yazar. Kayıt yazımı başarısızsa eski tercih kalır. Kapatma önce tercihi kapatır, sonra linki kaldırır; kalan link `-Startup` korumasıyla etkisizdir. Güç kesintisinde iki dosyanın tek atomik işlem olduğu iddia edilmez.

Kayıt şeması v1 değişmedi: `start_with_windows` varsayılan false, `show_client` varsayılan true. Okuma/ekran açılışı tercihi etkinleştirmez. `GET/POST /api/installation/launcher` installer'ın mevcut yerel erişim sınırında çalışır; POST yalnız iki strict boolean kabul eder. TR/EN panel ayrı iki seçenek gösterir. Bootstrap başarılı açık kaynak kaydından sonra masaüstü girişini kurar; Startup linkini kendiliğinden kurmaz. `ORION_SKIP_GLOBAL_REGISTRATION=1` testlerde masaüstü/PATH kaydını da engeller.

Başlatıcı registry operation kilidini en çok 180 saniye bekler; aynı kullanıcı/kanonik kayıt adı ve bileşen operation/instance kilitleri kullanılır. `operation_lock(path, timeout=15)` geriye uyumlu bekleme parametresi eklendi; gömülü `install.ps1`/`install.sh` yeniden üretildi. Hub/Router yolları ve mevcut lifecycle kimlikleri start öncesi denetlenir; connect-only Router çalıştırılmaz/durdurulmaz. Harici data/state yerleşimi ve packaged dağıtım henüz açık değildir. Çalışan serviste pencere hatası servisleri kapatmaz.

Flutter sunucusu Hub supervisor'ının çocuğudur; pencere açma supervisor'dan ayrılır. Kayıtlı client veya açık `ORION_FLUTTER_APP_DIR` kullanılır; yoksa seçili kurulumun `Client` klasörü beklenir, kardeş development deposu seçilmez. Pencere açmadan önce kaydedilmiş Flutter sürecinin listener sahipliği ve sağlık yanıtı denetlenir. Browser profile kayıt köküne özeldir. Tekrar tıklamada yalnız aynı kullanıcı ve dedicated profile ait Windows penceresi öne getirilir; yeni HWND oluşma aralığı için kısa bekleme vardır. OS foreground kısıtlamaları ve gerçek pencere davranışı aşağıdaki açık kabul kapsamındadır.

### Gerçek ürün kanıtı ve ilk hata geçmişi

Komut (Hub kökünde):

```powershell
& .tmp/policy-test-runtime/Scripts/python.exe scripts/lifecycle/run_product_acceptance.py hub --registered-paths --launcher --unicode-root --postgres-archive .tmp/postgresql-16.15-1-windows-x64-binaries.zip
```

Son koşu `.tmp/lifecycle/product-aswv20ee/result.json`: **success true**. Yeni `Türkçe İıŞşĞğ 中文/Hub`, yeni DB/TLS/UUID/portlar ve kopyalanmış mevcut Flutter web build kullanıldı; SDK/build/indirme yok. Gerçek PowerShell girişinden 1 Startup + 2 elle açılış eşzamanlı çağrısı tek supervisor'da birleşti; repeat-start aynı run kimliğini korudu. Flutter owned listener/health, kapalı başlangıç tercihinin etkisizliği, gerçek COM linkinin yalnız fixture Startup klasöründe açılıp kaldırılması, aktif Hub'da taşıma reddi, kontrollü stop/restart, örnek DB satırı ve TLS/UUID hash korunması geçti. PostgreSQL native `pg_trgm` ve Unicode SQL roundtrip de geçti. Gerçek kullanıcı Startup klasörü kullanılmadı.

İlk sandbox koşusu `product-rtrchde8`, Redis DLL hardlink için WinError 5 nedeniyle ürün başlamadan durdu; başarı sayılmadı. Normal kullanıcı koşusu `product-4v_n_0b4`, Flutter test yolunun launcher varsayılanıyla örtülmesini yakaladı (`KeyError: flutter`); servis/DB cleanup tamamlandı. Açık client yolu korunarak düzeltildi, son başarılı koşu bu değişikliği içerir. Başarısız sonuçlar silinmedi.

### Regresyon ve ekran kanıtı

- `.tmp/policy-test-runtime/Scripts/python.exe -m pytest -q scripts/lifecycle/test_launcher.py scripts/lifecycle/test_installation_registry.py scripts/lifecycle/test_installation_integration.py scripts/lifecycle/test_bootstrap_installation.py scripts/lifecycle/test_bootstrap_registration.py scripts/lifecycle/test_migration_host.py --junitxml=.tmp/lifecycle/results/stage04-regression.xml -p no:cacheprovider`: **75 passed + 10 subtests**, 30.26 s, skip/fail yok. Normal Windows kullanıcı bağlamında `ORION_BOOTSTRAP_PRIVATE_FIXTURE=1`; dar DACL yalnız yeni private bootstrap fixture'ında. İlk sandbox koşusu bir hata sonrası alt süreç/mutex erişiminde ilerlemediği için kesildi, başarı sayılmadı; aynı set normal bağlamda geçti.
- `python scripts/lifecycle/run_baseline.py hub`: **51 passed + 2 subtests**, 32.40 s; `baseline-hace3ukk-hub.{json,xml}`. Beklenmedik gerçek DB connect/pool girişimi **0/0**. Kaynak focus helper regresyonu ve yeni strict preference API testleri dahil.
- `python scripts/lifecycle/run_baseline.py storage-ui`: **1 passed**, 8.49 s; `baseline-m_p30gdd-storage-ui.{json,xml}`. Ayrı headless Chrome profili ve mock HTTP API; gerçek Startup'a istek yok. İlk GET'te POST yok, iki tercihin ayrılığı, hata metninin HTML olarak çalışmaması, hata sonrası canonical tercihe dönme ve başarılı kayıt/yeniden okuma doğrulandı. `.tmp/browser-tests/tmprfya90bk/storage-ui.png` görsel olarak incelendi; Türkçe panel okunaklı, kontroller/sonuç görünür.
- Son masaüstü/Startup aynı giriş ve yerel hata/repair ek kontrolleri: `python -m pytest -q scripts/lifecycle/test_launcher.py --junitxml=.tmp/lifecycle/results/stage04-launcher-final.xml -p no:cacheprovider`: **17 passed**, 13.69 s. Gerçek COM linkleri yalnız fixture Desktop/Startup klasörlerinde; gerçek hesap Startup'ı kullanılmadı. Eksik runtime/disk, bozuk kayıt, yetkisiz Router, lifecycle kimliği uyuşmazlığı ve registry yazma hatası reddi dahil. Son ek test gerçek sentetik HTTP alt sürecinin listener/health kanıtını `show_window` içinde doğrular; sahipli sunucu yokken browser çağrılmaz, sahipli ve sağlıklıyken doğru 127.0.0.1 URL/profile ile çağrılır. Browser açma işlemi mock'tur; gerçek HWND kabulü değildir.
- `python scripts/generate_bootstrap_wrappers.py --check`, 11 değişen Python dosyasında AST ve `git diff --check` geçti. `.tmp/lifecycle/results/stage04-final-audit.json`: başarısız/başarılı iki ürün fixture'ı `stopped`, yaşayan supervisor/çocuk `[]`, DB owner `null`; ilk hardlink hatalı fixture'da ürün süreç kaydı yok. Gerçek kullanıcı Startup klasöründe `Orion.lnk` **yok**; diğer uygulamaların dosyaları salt okunur denetlendi.

Bu setler örtüşür; test sayıları toplanmaz. Mevcut aiohttp/httpx deprecation uyarıları dependency/lock güncellenmeden korundu. Başlatıcı odaklama birim testi gerçek HWND kabulünün yerine geçmez.

### Açık kabul, sınırlar ve geri alma

Temiz Windows VM oturumu ve gerçek logon/logoff bu ortamda çalıştırılmadı; ana kullanıcı oturumu kapatılmadı. İzole Startup çağrısı gerçek Windows logon testi değildir. Gerçek browser HWND odaklama/tekrar pencere açma, pencere kapatıldıktan sonra servis politikasının tam OS kabulü, WinForms'ın görsel/etkileşimli onarma kabulü ayrıca doğrulanmalıdır. Script'in headless hata/repair yolu, UI mock testleri veya focus birim testi bunları geçmiş yapmaz. SDK'sız temiz bilgisayar/paket kurulumu 05/08 işidir; bu oturum yeni kaynak çalıştırma yolunu sağlar.

01'in aktif sohbet/araç/ses/OS kabulü, 03'ün tam DB/runtime taşıması ve eski kurulum dönüşümü açık kalır. Windows başlangıcı kullanıcının hesabında etkinleştirilmedi; yalnız yeni test dizinleri kullanıldı. Kullanıcının Hub `docs/orion-update-strategy-proposal.md`, `services/hub/uv.lock` ve Flutter `chat_presentation_test.dart` değişiklikleri görev dışında tutulur.

Geri alma: önce kendi kayıtlı kurulumunu açık `stop` ile kontrollü kapat. Etkinleştirdiysen tercihi açıkça kapatıp yalnız bu kurulumun sahipli Startup/masaüstü linklerini kaldır; başka linkleri silme. Ardından görev commit'ini geri al; install wrapper'ları ve canonical registry helper'ı birlikte tut. DB/state/kimlikleri veya D'deki depoları silme/taşıma. Push yapılmaz.
