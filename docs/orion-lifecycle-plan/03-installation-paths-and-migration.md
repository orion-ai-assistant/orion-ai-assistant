# Aşama 03 — Installer konum seçimi ve veri ayrımı

Durum: DOĞRULAMA EKSİK. Konum doğrulaması, kaynak bootstrap’ı, model yolu entegrasyonu ve Models/Downloads taşıma çekirdeği uygulandı; tam ürün/DB/runtime taşıması ve kurulum kabulü tamamlanmadı.

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
