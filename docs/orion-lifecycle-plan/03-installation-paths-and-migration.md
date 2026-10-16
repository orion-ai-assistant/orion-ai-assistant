# Aşama 03 — Installer konum seçimi ve veri ayrımı

Durum: DOĞRULAMA EKSİK (paket dönüşümü/Unicode PG ve tam uygulama taşıma kapsamı). Son takipte gerçek offline host gate’i, konum ekranı/API, özel kökte gerçek source setup/reinstall ve Hub/DB/TLS koruma testleri geçti. Tam DB/runtime taşıması 05/08 bağımlılığıyla kapalı; bu kullanıcıdan beklenen bir rutin test değildir.

[Ana plan](../orion-installation-lifecycle-implementation-plan.md) · [Ortak sözleşmeler](shared-contracts.md)

## Ön koşullar ve okunacaklar

Ön koşul aşamalar: [02](02-installation-registry-and-router-selection.md)

Ortak kararlar; bölüm 2 veri düzeni, 2.2 taşıma sözleşmesi ve 2.4 OS sınırları; 02 sonuç kaydı.

Ön koşulların yalnızca durum etiketini değil, sonuç kaydını ve mevcut koddaki karşılığını doğrula. İlgisiz gelecek aşamaların tamamını okumak veya uygulamak gerekmez. Repo talimatları ayrıca geçerlidir.

## Bu aşamadaki iş ve kabul koşulları

Konum seçimi indirmeden önce sunulur; mevcut `install.ps1` önce sabit konuma clone edip sonra seçim yaptırmaz. Küçük ilk kurucu, görsel kurulum ve sessiz kurulum parametreleri aynı doğrulamayı kullanır. Yeni kurulum seçilen köke oluşturulur; komutlar, kısayollar, günlükler ve yardımcılar kayıtlı yolları kullanır. Model konumu varsayılanı seçilen kökü izler.

Eski kurulum için iki ayrı işlem vardır: (a) mevcut konumda kullanmaya devam etmek; (b) kullanıcı seçerse kontrollü taşımak/dönüştürmek. Çalışan DB'nin dizini doğrudan kopyalanmaz. Kaynak durdurularak tutarlı kopya veya PostgreSQL destekli yedek yöntemi kullanılır. Aynı PostgreSQL ana sürümü ve runtime uyumu denetlenir. Kopya hedefte doğrulanır, kimlikler ve örnek veri kontrol edilir, kayıt ancak sonra değiştirilir. Eski kaynak hemen silinmez; iki kopyanın aynı anda yazması engellenir. Kesinti günlüğü sonraki açılışta toparlanır.

Kabul/test: varsayılan/özel kök, yeniden kurulum, eski bağımsız Router'ı koruma, dolu/geçersiz/yazılamayan hedef, disk dolması ve C–D kopyasında kesinti, açık DB, ayar/model/tarayıcı profili korunması, şifre ve sertifika eşleşmesi. Başarısız taşıma kaynağı değiştirmez. Özel hedefteki başlatıcı sabit AppData kod yolunu kullanmaz.

Ek test: yalnız model/indirme/runtime/Router taşıma; paylaşılan modeli iki bileşenin kullanması; aktif model yüklemesi; her taşıma adımında kesinti ve tekrar açılış; hedef hash hatası; kullanıcıya ait hedef dosyasıyla çakışma; junction/symlink; kopyadan sonra kaynak temizliğinin kesilmesi; Python runtime'ın yeni yolda çalışması. Güncelleme/taşıma aynı anda yapılamaz. Büyük dosya kopyasında kaynak değiştirilirse işlem farkı algılar ve eski doğrulama sonucunu kullanmaz.

## Sonraki aşamaya devredilecek çıktı

Gerçek konum çözümleme arayüzü; yeni kurulum ve yeniden kurulum akışı; taşıma günlüğü, kurtarma ve temizleme sözleşmesi.

Uygulanan arayüzlerin gerçek dosya yolları, giriş noktaları, şema sürümleri ve hata sonuçları aşağıdaki sonuç kaydına yazılır. Sonraki model tasarım varsayımı yerine bu kaydı ve kodu esas alır.

## Kapsam sınırı

Yalnız bu aşamanın işini uygula. Ön koşulda eksik sözleşme varsa dar kapsamlı düzeltmeyi kaydet; başka aşamanın tüm özelliğini sessizce üstlenme. Gerçek kullanıcı verisinde hata enjeksiyonu yapma. Aşama testleri geçmeden sonraki aşamaya geçme; bu aşama bittiğinde sonuçları raporlayıp dur. Ana plan tablosundaki durum da güncellenir.

## Sonuç ve test kaydı

- Durum: DOĞRULAMA EKSİK.
- Başlangıç / oturum bitişi: 16 Ekim 2026.
- Depo: Hub. Router ve Flutter kaynaklarında bu aşamaya ait değişiklik yok.
- Uygulanan arayüzler / testler / korunma kanıtı / kalanlar: aşağıda.
- Sonraki aşamaya uygunluk: tam kabul verilmedi; 04’e başlanmadı. Kullanıcının son talimatı 03’ü bu oturumda çalışıp commit’ten sonra durmaktır.

## Uygulanan çekirdek ve giriş noktaları

### İndirme öncesi bootstrap

`install.ps1` ve `install.sh`, `scripts/bootstrap_installation.py` planlayıcısını ve canonical kayıt/yol kodunu sıkıştırılmış olarak içerir. `scripts/generate_bootstrap_wrappers.py` bu dosyaları üretir; `--check` drift’i denetler. Standalone betik herhangi bir yardımcı indirmeden önce konumu sorar/doğrular. Windows `-Root`, `-ModelsPath`, `-RegistryPath`, `-NonInteractive`, `-PlanOnly` ve `-Browse` (FolderBrowserDialog) vardır; Bash aynı planlayıcının `--root`, `--models-path`, `--registry-path`, `--non-interactive`, `--plan-only` parametrelerini kullanır. Görsel seçim ile sessiz kurulum aynı doğrulamaya gider. Browse gerçek kullanıcı UI kabulü yapılmadı.

Yeni kaynak kurulumunda seçilen kök altında `Hub` ve `Models` ayrılır. Bu, 05’in değişmez `releases/<id>/data/state` paket yerleşiminin tamamlandığı anlamına gelmez. Varsayılan Windows ana kökü `%LOCALAPPDATA%/Orion`; kayıtlı Hub’ın gerçek code_path’i önceliklidir. Geçersiz/bozuk/kayıp/çoklu kayıt, farklı kayıtlı köke geçiş, dolu kayıtsız hedef ve legacy locator varken sessiz yeni kurulum reddedilir. Legacy manifest önce açıkça 02 arayüzünden içe alınır. Mevcut kaynakta git fetch/init/reset/remote değiştirme yok; yalnız taze hedef clone edilir. Clone/setup hatasından sonra installer açılmaz, yarım hedef otomatik silinmez. Uzak Git kaynağından gerçek yeni clone/dependency kurulumu bu oturumda yapılmadı; komut sınırları fixture’larla test edildi. Yeni commit’ler yereldir, uzak main’in bu kodları içerdiği varsayılmaz.

`services/shared/installation_paths.py`: `canonical_path`, `validate_destination`, `validate_private_directory`, `resolve_location`, `registered_component_paths`. Mutlak yerel sabit disk, NTFS/ReFS, Windows özel adları/yol kaçışı, junction/symlink ataları, dolu hedef, iç içe kökler, başka bileşen yolu, boş alan ve yazma denemesi doğrulanır. Kod/runtime için owner/DACL/current SID OS API’leriyle okunur; own user/SYSTEM/Administrators dışı yazma yetkisi reddedilir. Ürün ACL mirasını değiştirmez. D geliştirme kökünün izinleri genişse kullanıcı kurulumu hedefi olarak reddedilebilir; geliştirme deposunun ACL’si düzeltilmedi.

`installation_registry.py` şema **v1** isteğe bağlı `locations:{models,downloads,runtime,backups}` alanıyla genişletildi. `ORION_INSTALLATION_REGISTRY` açık, mutlak kayıt override’ıdır; varsayılan değişmez. `operation_lock(registry_path)` kurulum işlemleri için ortak kilittir. Eski alanlar/UUID/tercihler korunur; yeni alanı tanımayan eski okuyucu güvenli biçimde hata verebilir.

`bootstrap_registration.py:register_bootstrap` başarılı **local** setup sonrası Hub kaydını oluşturur/mevcut kaydı korur; bağımsız Router-only kaydına Hub ekleyebilir. Yeni bileşen kurulum UUID’si TLS kimliği oluşturmaz veya yenilemez. `.env.global.local` sırları ve diğer satırlar korunarak yollar yazılır; config yazımı başarısızsa kayıt değişmez, kayıt commit’i başarısızsa yalnız kendi config yazımı geri alınır. İki dosyanın tek atomik işlem olduğu veya gerçek güç kesintisinin doğrulandığı iddia edilmez. Manager hook’u installer venv’inin doğrulanmış interpreter’ını kullanır; Docker’a yerel yönetim yetkisi yazmaz. `ORION_SKIP_GLOBAL_REGISTRATION=1` izole setup testinde PATH/profile yazımını engeller; gerçek setup bu oturumda çalıştırılmadı.

### Model yolları ve veri koruması

`services/shared/model_locations.py:models_directory`, yalnız çalışan Hub’ın kayıtlı code_path’ine ait native servisleri `locations.models/<service-id>` altında çözer. Legacy kayıt tek başına eski service/models klasörünü taşımaz; development ağacı kurulu Hub’ın model havuzuna yönlenmez. Docker mevcut manifest yolunu kullanır. Installer liste/indir/sil/hazırla ve native başlatma aynı çözümleyiciyi kullanır. Native model servislerine `ORION_NATIVE_MODELS_DIR` iletilir; llama-cpp, embedding, Whisper API/streaming ve VoxCPM bu kökü kullanır. Gerçek model/GPU/ses yükleme testi yapılmadı; llama/embedding komutları sentetik dosyalar ve sahte process ile doğrulandı.

İndirme/listeleme/silme ve yeni model-servis başlatma, taşıma ile aynı `installation_operation` kilidini alır. Eski `startswith` silme kontrolü gerçek `Path.resolve/is_relative_to` sınırıyla değiştirildi. Eski Hub içi Router kurulumunun `.git/HEAD` yok diye dizin silmesi, WMIC isim eşleşmesiyle süreç öldürmesi ve uzaktan mutating installer çağrısı kaldırıldı. Tanınmış Router yerinde kullanılır; yoksa açık eksik özellik hatası verilir. Bu, yeni Router paket kurulumunun tamamlandığı anlamına gelmez; bağımsız Router deposunun eski standalone installer’ı ayrıca 05/08 kapsamındadır.

### Kontrollü Models/Downloads taşıma çekirdeği

`services/shared/content_migration.py`: `registry_revision`, `begin_content_migration`, `resume_content_migration`. Yalnız **Models/Downloads**; source/runtime/DB/Router/TLS/tarayıcı profili taşımayı üstlenmez. Kaynak başka bileşen/code/data/state ile iç içeyse reddedilir. İlgili bütün tüketiciler/yazıcılar işlem boyunca duraklatılmış tutulmalıdır; güvenilir host’un `quiescence_gate` callback’i tam `True` vermedikçe işlem başlamaz/devam etmez. Bu gate’i üründeki aktif işlerle bağlayan drain/orchestration henüz yoktur; kullanıcıya taşıma butonu açılmadı.

Kalıcı `migrations/<UUID>.json`: `planned -> copying -> verified -> switching -> completed`, hata `repair-required`. Registry revision CAS, UUID’ye bağlı hedef marker’ı, envanter/boyut/SHA256, fsync ve sınır hata enjeksiyonu vardır. Kaynak hiçbir zaman silinmez. Hedefte kullanıcı dosyası üzerine yazılmaz; değişen kaynak/yanlış hash/yarım kopya güvenli onarma durumunda kalır. Registry switch sonrası eski veriye otomatik dönülmez. Tamamlanan hedef tekrar başka yere taşınabilir, eski günlük/marker korunur. Yarım dosya sessiz tamamlanmış sayılmaz; otomatik kaynak temizliği uygulanmadı.

## Test ve kanıt

- Aşama 03 ilk birleşik seti: **69 passed, 53 subtests**, 23.08 s; `.tmp/lifecycle/results/stage03-unit.xml`. Windows standalone PowerShell ve Git Bash PlanOnly, gerçek junction reddi, ACL okuma, disk/yazma hataları, bootstrap kayıt/config hataları, model liste/indir/sil yolu ve taşıma sınırları dahildir. Gerçek wrapper testleri yeni sentetik fixture’a özel DACL verir; kullanıcı/ürün dizininin ACL’si değişmez. Platform testleri normal sandbox’da skip edebildiğinden son koşu normal kullanıcı bağlamında `ORION_BOOTSTRAP_PRIVATE_FIXTURE=1` ile yapıldı; bu koşuda skip yok.
- Hub/installer regresyonu: **48 passed, 2 subtests**, 36.34 s; `results/baseline-ynnoj0ff-hub.{json,xml}`. Beklenmedik DB bağlantı/pool girişimi 0/0.
- Native llama/embedding komut yolları ve Router kaynak koruması: ilk **2 passed, 2 subtests**, 0.12 s; yeni test gerçek model/sağlayıcı veya kullanıcı servisi açmaz.
- Gerçek AppData **C→D** kopya/kurtarma: `python scripts/lifecycle/run_path_acceptance.py --appdata C:/Users/krsta/AppData/Local`; `C:/Users/krsta/AppData/Local/Orion/lifecycle-tests/stage03-6sfck09_/result.json` başarılı. Hedef `.tmp/stage03-cross-volume-qmj4tpcd`; doğrulama sınırındaki sentetik crash sonrası kayıt eski yerde kaldı, resume sonrası yeni hedefe geçti; kaynak/config hash’leri ve bileşen UUID’leri aynı. Gerçek public Hub/Router/install manifest hash’leri değişmedi. Bu, çalışan DB veya runtime taşıma kanıtı değildir; güç kesme değil süreç içi hata enjeksiyonudur.
- **Birleşik 02–03 regresyonu: 133 passed, 70 subtests**, 62.59 s; `results/stage02-03-final.xml`. Son legacy locator düzeltmesiyle güncel bootstrap seti ayrıca **14 passed**, 1.51 s; `results/stage03-bootstrap-final.xml`. Örtüşen koşuların sayıları toplanmaz. Son 23 Python AST ve kaynak/belge denetimleri geçti; kendi test kapsamında kalan süreç yok (`results/stage03-final-audit.json`). Ham loglar ignored `.tmp/lifecycle/results` ve yukarıdaki AppData fixture’ında korunur.

## Tamamlanmamış kabul ve sonraki iş

Aşama **DOĞRULAMA EKSİK**. Tam uygulama/code/data/state yerleşimi, PostgreSQL tutarlı taşıma/yedek/şema/kimlik doğrulaması, Python runtime’ın yeni konumda gerçek çalışması, Router’ın kendi konum değiştirme akışı, ortak modeli birden fazla aktif tüketicinin güvenli bırakması, model/DB işlerini duraklatan gerçek host gate’i, tam yeni kurulum/reinstall/özel kökten başlatıcı denemesi ve gerçek UI Browse/ayar akışı bitmedi. Eski kaynak/venv güvenli taşınamıyorsa dönüşüm 05/08’e açık bağımlılıktır; kopyalayıp çalışır denmedi. Ana Windows oturumu kapatılmadı; gerçek OS güç kesintisi testi yoktur. 01’in açık ürün/UI/OS kabul borcu ve eski kapsam dışı audio/browser test hataları sürer.

Geri alma: yalnız bu görev commit’i; gerçek kullanıcı kayıt/verisi değiştirilmedi. Fixture’lar kanıt olarak kalır. Gerçek bir içerik switch’i olmuşsa kaynağa otomatik dönme/silme yapma; journal ve aktif hedefin yeni yazımlarını incele. Yeni `locations` içeren kaydı eski okuyucuya uyarlamadan code-only rollback’in sorunsuz olacağı varsayılmaz. Eski kullanıcı diff’leri ve Flutter testi korunur. Kullanıcının son talimatıyla commit’ten sonra durulur, 04’e geçilmez; sonraki oturum [devir kaydıyla](03-session-handoff.md) başlar.

## 16 Ekim 2026 takip çalışması — 04 öncesi açıkların denetlenmesi

Kullanıcı 03’ün açıklarını bu oturumda ele almayı istedi; 04’e başlanmadı. Önceki sonuçlar tarihsel kanıttır. Bu takipte aşağıdaki açıklar kapatıldı:

- **Gerçek host gate:** `services/shared/migration_host.py` içindeki `StoppedInstallation`, `move_content`, `recover_content`; ortak registry operation lock ile kayıtlı Hub/Router ve manifestli model servislerinin canonical `operation` ve `instance` kilitlerini tutar. Çalışan/onarım bekleyen state, yaşayan çocuk/DB, bilinmeyen yerel sahiplik, desteklenmeyen external state/client koordinasyonu ve ilgili unmanaged interpreter/native executable reddedilir. Hiçbir kullanıcı servisini kapatmaz. Taşıma boyunca normal native başlatma girişleri aynı kilitlerde bekler/reddedilir. Rastgele harici uygulamaların model dosyasını açmasını OS seviyesinde engelleme garantisi verilmez; desteklenen lifecycle girişleri koordine edilir. İlgisiz korumalı `ctfmon.exe` için cwd erişim hatası, testte yakalanıp executable-path/interpreter ayrımıyla düzeltildi. Süreç adı kapatma yetkisi değildir; burada yalnız belirsiz interpreter kapsamını daraltır.
- Düşük seviye migration çekirdeği host’un zaten tuttuğu operation lock’u ikinci kez almaz; yeni `_begin_content_migration_locked` ve `_resume_content_migration_locked` girişleri yalnız kilitli host içinde kullanılır. Böylece Windows mutex reentrancy’sine bağlı ortak kod yazılmadı. Gerçek lock/crash/recovery testi eklendi.
- **Yerel ayar akışı:** installer `/api/installation/locations` GET, `/locations/move` ve `/locations/recover` POST; revision CAS, kaynak konumları, busy/ownership/onarma hataları. CLI `move-content`/`recover-content` gerçek host’u çağırır. Installer kayıt dosyasını `.env.global.local` içindeki açık kayıt yoluyla tutarlı seçer. Mevcut aynı-origin/loopback middleware sürer.
- Installer’da TR/EN **Model ve indirme konumları** paneli eklendi: gerçek kaynak konumu, tür/hedef seçimi, taşıma ve yenileme, hata/başarı bildirimi. Kullanıcıya DB/uygulama taşımasının desteklenmediği açıkça gösterilir. UI hiçbir servisi durdurmaz. Native Windows Browse dialog’unun manuel kabulü yapılmadı; bu yeni panel ayrı headless Chrome profiliyle otomatik doğrulandı.
- **Gerçek özel-kök source setup/reinstall:** `run_product_acceptance.py hub --registered-paths --setup-offline --scratch-base C:/Users/krsta/AppData/Local/Orion/lifecycle-tests`. Fixture `product-7pgzrs78`: manager setup ve tekrar setup, yeni installer venv’inde mevcut uv cache’inden çevrimdışı geçti; PATH/profile değiştirme kapalı. Gerçek Hub/Worker/Redis/PostgreSQL üç start/repeat-start/iki stop ile çalıştı. Hub açıkken host taşıma reddetti; kapalıyken Models taşıması tamamlandı; ardından Hub yeniden açıldı. Gerçek PG’de sentetik `orion_lifecycle_acceptance` satırı ve Hub TLS/UUID dosya hash’leri aynı kaldı. Source kodu fixture’a kopyalandı; upstream’den yeni clone ve SDK’sız paket kurulumu kanıtı değildir. Hub test venv’i önceki runner gibi mevcut dependency kitaplıklarını kullanır; ürün runtime’ının geliştirme D sürücüsünden bağımsız paketlendiği iddia edilmez.
- **Gerçek C→D gate/kurtarma:** `run_path_acceptance.py` artık `lambda: True` yerine gerçek host kullanır. `stage03-lkh1dc40/result.json`, D hedefi `.tmp/stage03-cross-volume-x12a2m90`, doğrulama sınırında kesinti/yeniden açma başarılı; kaynak/config/UUID ve gerçek public manifestler korundu.

### Gerçek testte bulunan platform sorunu ve düzeltme

İlk gerçek Unicode code-root denemesi **başarısız**: `C:/Users/krsta/AppData/Local/Orion/lifecycle-tests/product-hzi_e15s/result.json`; bundled PostgreSQL 16.3 `initdb` non-ASCII Windows yolunda `invalid byte sequence for encoding UTF8` verdi. Fixture korunur. İlk boşluklu ASCII-kök ürün denemesi `product-_vcif_ft` ve son offline setup koşusu `product-7pgzrs78` geçti.

Bu runtime için native bootstrap, **indirme/clone öncesi non-ASCII Windows code-root’u açık hata ile reddeder** (`plan_installation(...,native=True)`); code-root değişikliği/boş kurulumla örtme veya ACL değiştirme yoktur. Ayrı Unicode Models konumu desteklenmeye devam eder. Bu bir Unicode PostgreSQL desteği çözümü veya bütün Türkçe kurulum yolları kabulü değildir: söz konusu platform/paket açığı 05’te desteklenen runtime hazırlanırken ele alınmalıdır. Test PlanOnly/görsel seçimin aynı native politikasını kullanmasını ve komut çalışmamasını doğrular. Önceki standalone Unicode plan testi Docker/genel yol planını sınar; native PG kurulumu başarısı diye yorumlanmaz.

### Takip test kanıtları

- Host + migration + kayıt/HTTP entegrasyonu + native bootstrap birleşik ara koşu: **53 passed, 21 subtests**, 33.44 s; `results/stage03-followup.xml`.
- Son host seti: **7 test**, gerçek OS kilidi, yaşayan supervisor, unmanaged sentetik process, yeni start kilidi, HTTP stale/active/success ve crash/recovery senaryolarını içerir; son geniş koşuda ayrıca ölçülür.
- Native bootstrap seti **15 passed**, 1.60 s; `results/stage03-native-bootstrap.xml`.
- Headless UI son kaynak: **1 passed**, 8.80 s, `results/baseline-anw2f1sm-storage-ui.{json,xml}`; DB/pool denemesi 0/0. UI screenshot `.tmp/browser-tests/tmp4k_b_n0q/storage-ui.png` görsel incelendi. İlk UI koşusunda sabit İngilizce beklentisi Türkçe tarayıcı dilinde başarısızdı (`baseline-g3k7hnzz`); çeviriye göre beklenti düzeltildi. CSS class değişikliğine bağlı ara test-seçici hatası (`baseline-58ka90by`) da saklandı.
- Hub/installer son regresyonu **48 passed, 2 subtests**, 37.38 s; `results/baseline-ic5u_wpv-hub.{json,xml}`, beklenmedik DB/pool girişimi 0/0.

**Güncel sınır:** canlı kullanım sırasında otomatik online drain/taşıma açılmadı; gerçek offline gate aktif kullanımda güvenli biçimde busy döner. Tam Hub/Router/DB/runtime taşıması, runtime’ı code/data/state yerleşiminden ayıran doğrulanmış paket dönüşümü, non-ASCII Windows PostgreSQL kökü ve SDK’sız kurulum 05/08 bağımlılığıyla kapalıdır. Bunlar kullanıcının yapmasını beklediğimiz rutin testler değildir. 03’ün bütün ilk kabul maddeleri tamamlandı denmez; durum DOĞRULAMA EKSİK kalır. 04 için kayıt/yol/offline içerik taşıma arayüzleri testlidir; 04 geliştirmesi bu kısıtlarla ilerletilecekse kabul borcu açık taşınmalıdır. Kullanıcının 01/manuel kabul ertelemesi yayın onayı değildir.

Son geniş takip regresyonu **142 passed, 70 subtests**, 68.43 s; `.tmp/lifecycle/results/stage03-readiness-final.xml`. Önceki örtüşen koşularla toplanmaz. Generated wrapper `--check`, JS syntax, Python AST ve belge bağlantı kontrolleri son kaynakta denetlenir. Bu takip ayrı yerel commit ile korunur; push/04 uygulaması yoktur.
