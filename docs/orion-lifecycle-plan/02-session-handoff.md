# Aşama 02 için oturum devri — 16 Ekim 2026

[Ana rehber](../orion-installation-lifecycle-implementation-plan.md) · [Aşama 02 kapsamı](02-installation-registry-and-router-selection.md) · [Ortak sözleşmeler](shared-contracts.md) · [01 sonuçları](01-process-lifecycle.md)

## Kullanıcının son kararı ve sıradaki iş

Yalnız **Aşama 02** uygulanacak; sonuçlar kendi aşama dosyasına ve ana tabloya yazılıp durulacak. 03'e geçilmeyecek. Bu devir oturumunda yalnız belgeler güncellendi; 02 kodu, AppData kurulumu veya yeni ürün testi yapılmadı.

01'in çekirdek süreç yönetimi uygulandı, son 40 yaşam döngüsü testi ve izole gerçek Hub/Router aç-kapat koşuları geçti. Durumu yine **DOĞRULAMA EKSİK**: bütün ürün/UI/OS kabulü tamamlanmadı. Kullanıcı 02'ye devam etmek istiyor ve kendi manuel kabulünü tek tuşla başlatma akışı hazır olduğunda yapacak. Bu kayıt, eski “01 tamamen bitmeden 02'ye geçme” talimatına yalnız 02 geliştirmesi için kullanıcı tarafından verilen istisnadır; eksik testleri geçmiş veya yayını onaylanmış göstermez. İlgili başlatıcı aşamasında manuel kabul borcunu yeniden görünür kıl.

Testleri agent kendisi hazırlayıp çalıştıracak; kullanıcıya rutin test işi devredilmeyecek. Gerçekten çalıştırılamayan kontrolün nedeni ve kalan kapsam kaydedilecek. Kullanıcı paralel agent çalışmasına izin verdi: bağımsız okuma, inceleme ve izole birim testleri ayrılabilir. Aynı canonical motoru eşzamanlı değiştirme. **Ağır gerçek ürün/PostgreSQL/Next.js koşularını sırayla çalıştır:** önceki paralel koşular kaynak yükü nedeniyle mutex/readiness zaman aşımı verdi; sıralı tekrarlar geçti.

## AppData yerleşimi ve izin

Ortak `%LOCALAPPDATA%\Orion` üst klasörü mevcut tasarımın da kararıdır. Varsayılan altında `Hub`, `Router`, `Client` (Flutter), `Models`, `Downloads` vb. bulunur; kesin hedef ağaç ortak sözleşmeler bölüm 2'dedir. Bu henüz uygulanmış paket kurulumu değildir. Ortak klasör, Router'ın bağımsız kurulup çalışmasını engellemez. Eski Router bulunduğu yerde kaydedilir; sırf bu düzen için taşınmaz.

Kullanıcı agent'ın AppData'da ayrı yerel kurulum hazırlayıp test etmesine açıkça izin verdi; kendi geliştirme çalıştırmaları D sürücüsünde kalacak. Önce mevcut AppData kaydını/dizinlerini incele. Gerçek kurulum varsa üzerine yazma; benzersiz test alt kökü ve ayrı kayıt fixture'ı kullan. Test örneğinin kod/veri/state yollarını, portlarını, DB/Redis'ini, kimliklerini, TLS güvenini, tarayıcı profilini ve loglarını geliştirme/gerçek kurulumdan ayır. Canlı iki kopyaya aynı Router UUID/TLS kimliğini verme. Mevcut kurulumun kayda alınması sırasında ise UUID/SPKI ve veriyi aynen koru.

Bu izin D deposunu taşımak/resetlemek, mevcut kullanıcı verisini kopyalayıp hata fixture'ı yapmak, kullanıcı süreçlerini kapatmak veya Windows başlangıcı/otomatik güncellemeyi etkinleştirmek için izin değildir. 02'de kayıt/uyarlayıcı ve bunların gerçek konum testleri yapılır; tam paketli yükleyici 05'in işidir. `.venv` taşınabilir varsayılmaz. AppData workspace dışındadır; yazma için araç sandbox yükseltmesi gerekirse bu teknik sınırı açıkça belirt, kurulmuş gibi raporlama. Kullanıcıdan aynı iş iznini yeniden isteme.

Hyper-V normal testlerin şartı değildir. VM yalnız izole yıkıcı OS/güç kesintisi deneyleri için seçeneklerden biridir; ana Windows oturumunda güç kesme/yeniden başlatma testi yapma. Önceki Hyper-V sorgusunda yönetici erişimi yoktu; VM kurulmadı.

## Uygulanmış arayüzler: yeniden tasarlama yerine kodu doğrula

- Canonical motor `services/shared/process_lifecycle.py`: `Runtime`, `InstanceLock`, `ProcessReference`, `ManagedChild`, `ensure_started`, `stop_component`, `living`, `child_running`, `child_tree_alive`, `owns_listener`, `wait_health`. Ortak CLI/installer sözleşmesi `services/shared/native_services.py`; PG sahipliği `native_postgres.py`; genel servis `service_supervisor.py`.
- `windows_job.py` ve `process_host.py`: stdlib host gerçek uygulamayı açmadan Windows Job'a katılır; çocuk/torunlar aynı Job'da kalır. Host `-I -S` ile açılır. Host ve supervisor handle tutar; ikisi kaybolursa `KILL_ON_JOB_CLOSE` yalnız o grubu kapatır. PG ayrı kontrollü adapter'dadır; zorla DB öldürme yoktur.
- Kalıcı başlatma niyeti PID'den önce yazılır. `process: null` geçerli ara durumdur, hazır demek değildir. Gecikmiş host için `cancel-<instance-id>` vardır. Job kimliği kullanıcı SID + canonical root + component + kimliklerle doğrulanır. Ham PID/port/isim, yönetim veya öldürme yetkisi değildir; tarihsel PID listesi Job üyeliğinin yerine geçmez.
- Süreç state'i `<gerçek bileşen kaynak kökü>/.orion-lifecycle/<component>/{identity.json,processes.json}`, şema v1. Bileşenler `hub`, `router-prod`, `router-dev`, genel `service`. Buradaki `installation_id` mevcut **bileşen** kimliğidir; 02'nin global kurulum UUID'si henüz yoktur. Kayda bağlarken bu kimlikleri/TLS kimliğini yenileme. State'de DB parolası tutulmaz.
- Router `core/` altında altı dosya vendored: `process_lifecycle.py`, `process_child.py`, `process_child.cjs`, `native_postgres.py`, `windows_job.py`, `process_host.py`. Hub `scripts/lifecycle/sync_router_engine.py` denetler; `--write` eşitler (PG import uyarlaması dahil). İki ayrı algoritma geliştirme.
- Hub worker readiness Redis journal'daki gerçek worker/owned descendant kanıtına bağlıdır; Redis RESP2 kullanır. `kernel/job_safety.py` taraması kurulum kapsamındadır. DB şeması kurulumu `kernel/registry.py` advisory lock ile eşzamanlı açılışa dayanır.
- Router dev hazır olması backend TLS/UUID ve Next HTTPS `/dashboard` 200 ister. Python/Node graceful kontrolü, Redis `SHUTDOWN SAVE`, sonra doğrulanmış Job üzerinden sınırlı fallback uygulanır. Yaşayan grup varken state/iptal dosyalarını silme.
- Eski AppData launcher dosyaları değiştirilmedi. Installer şablonları güncellendi; paketli yükleyici henüz üretilmedi. macOS/Linux/Docker gerçek entegrasyonu doğrulanmış değildir.

02'nin önemli entegrasyonu: `%LOCALAPPDATA%\Orion\router-installation.json` eski kaydı uyarlayıcıyla koru; development/installed hedeflerini açık ayır. Bozuk kayıt sessiz yeni kurulumla örtülmez. Seçilmiş klasörde sırf doğrulama için keyfi betik çalıştırılmaz. Kapalı/tanınmış kurulum ile çalışan/sağlıklı kurulum ayrı sonuçlardır. Ayrıntılı şema ve test matrisi için 02 ve ortak sözleşmeler 2/2.1 esas alınır.

## Test ortamı ve tekrar komutları

Komutlar Hub repo kökünden PowerShell'de çalışır. Hazır test Python'u `.tmp/policy-test-runtime/Scripts/python.exe` (3.11.2, psutil 7.2.2); gerçek Hub Python'u `services/hub/.venv/Scripts/python.exe` (3.14.3), Router `.venv` 3.11.2'dir. Portable PG16.3 ve Redis `.local_db` altında; Node24/Next16 ve Router FFmpeg mevcut. Gerekmeden bağımlılık indirme veya lock dosyası yenileme.

```powershell
& .tmp/policy-test-runtime/Scripts/python.exe scripts/lifecycle/run_baseline.py lifecycle
& .tmp/policy-test-runtime/Scripts/python.exe scripts/lifecycle/run_baseline.py hub
& .tmp/policy-test-runtime/Scripts/python.exe scripts/lifecycle/run_baseline.py router
& .tmp/policy-test-runtime/Scripts/python.exe scripts/lifecycle/sync_router_engine.py
# Ağır ürün komutlarını ayrı ayrı, sırayla çalıştır:
& .tmp/policy-test-runtime/Scripts/python.exe scripts/lifecycle/run_product_acceptance.py hub
& .tmp/policy-test-runtime/Scripts/python.exe scripts/lifecycle/run_product_acceptance.py router
& .tmp/policy-test-runtime/Scripts/python.exe scripts/lifecycle/run_product_acceptance.py router-dev
```

02'ye özgü testleri ayrıca ekle; yukarıdakiler regresyon araçlarıdır, 02 kabulünün yerine geçmez. Baseline runner sentetik fixture ve sınırlı ömürlü sahipli süreç kullanır; JSON/XML `.tmp/lifecycle/results` altındadır. Ürün runner gerçek kaynak/runtime ile `.tmp/lifecycle/product-*` altında yeni boş DB, sentetik home/AppData/TLS, ayrı portlar kullanır; ücretli sağlayıcı, mDNS ve Flutter otomatik açılışı kapalıdır. Bu testin sentetik AppData kullanması gerçek `%LOCALAPPDATA%` kurulumu yapıldığı anlamına gelmez.

`--reuse` yalnız runner'ın oluşturduğu, tüm süreç ağacı ve PG'si kapalı doğrulanmış Router prod/dev fixture'ları içindir; gerçek kuruluma uygulama. Önceki logları `previous-*` altında saklar. Başarısız kanıtları silme. Başarılı test temizliği yalnız doğrulanmış kendi fixture kökünde ve süreçler kapandıktan sonra yapılır; genel isim/port üzerinden kill yoktur.

Son mevcut kanıtlar (`.tmp/lifecycle/` altında; 01'de ayrıntılı tarihçe var):

| Koşu | Sonuç / kanıt |
| --- | --- |
| Yaşam döngüsü | 40 passed, 69.08 s; `results/baseline-xzg01kyx-lifecycle.{json,xml}` |
| Hub/installer | 48 passed + 2 subtests, 164.98 s; `results/baseline-fy4t30v2-hub.{json,xml}` |
| Router | 9 passed, 49.31 s; `results/baseline-bw_t9atl-router.{json,xml}` |
| Gerçek Hub | `product-ka_45t5c/result.json`: eşzamanlı start, aynı kimlikle repeat-start, iki aç/kapat çevrimi başarılı |
| Gerçek Router dev | `product-mx0wgwng/result.json`: son sıralı tekrar başarılı; eski başarısız denemeler arşivde |
| Gerçek Router prod | `product-aogd4vfe/result.json`: son sıralı tekrar başarılı; eski denemeler arşivde |
| Son süreç denetimi | `job-process-audit.json`: `remaining_scoped_test_processes: []` |

Bu sonuçlar önceki oturumda ölçüldü; yeni oturumda tekrar çalıştırılmadan güncel koşu diye sunulmaz. Son kaynak denetiminde altı vendored dosya eşit, 37 Python AST ve iki repo `git diff --check` başarılıydı.

## Açık kabul borcu ve bilinen diğer hatalar

01'de açık: tek tam ürün senaryosunda aktif sohbet/araç/ses işi sırasında tüm uygulamayı kapatma; gerçek manuel tarayıcı izin/profil akışı; tam panel/CLI UI eşzamanlılığı; kurulu legacy launcher yükseltmesi; izole VM/OS güç kesme/yeniden başlatma. Bunlar 40 süreç testiyle veya headless tarayıcı testiyle tamamlanmış sayılmaz.

Önceki geniş regresyonda lifecycle dışı hatalar da var: audio resampling testinde 216/218 örnek farkı (19 pass/1 fail); browser-manager eski `open_inspect_window` API testleri ve discovery sorunu (11 pass/7 fail, sonra timeout); browser-api `inactive=True` beklentisi (45 pass/1 fail). Browser-bridge 5, izole gerçek headless Chrome 1, jobs 34 + 9 subtest, fixture 7 test geçti. Ayrı koşuları örtüşmeli toplama veya bütün testler yeşil iddiasına dönüştürme; 02 kapsamı dışında kalanları gizleme.

## Çalışma ağacını koruma

Kullanıcı devirden sonra bu çalışmanın Hub ve Router depolarında yerel commit olarak korunmasını istedi. İlgili commit'ler için iki deponun `git log -1` çıktısını kontrol et; uzak depoya push istenmedi. Başlangıç kullanıcı değişiklikleri Hub `docs/orion-update-strategy-proposal.md` ve Flutter `main_app/app/test/chat_presentation_test.dart` bu commit kapsamının dışındadır; bunları resetleme. Hub `services/hub/uv.lock` modified görünüyor ancak önceki içerik karşılaştırması HEAD ile aynıydı; bağımlılık değişikliği varsayma. Testlerin özetleri bu belgelerde korunur; ignored `.tmp/lifecycle` ham logları yereldir ve commit yedeğine dahil değildir. İşe başlamadan üç repoda güncel status/diff ve geçerli AGENTS talimatlarını kontrol et; toplu clean/reset yapma. Sonraki agent mevcut kaynak ve sonuç kayıtlarını doğrular, geçmiş konuşmayı yeniden kurmak zorunda değildir.
