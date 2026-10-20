# Aşama 01 — Mevcut yaşam döngüsünü düzelt

Durum: DOĞRULAMA EKSİK — 16 Ekim 2026. Windows Job Object ile hızlı ebeveyn ölümü/torun açığı giderildi; son genişletilmiş yaşam döngüsü seti **40/40** geçti. Kalan tam aktif kullanım/UI/OS doğrulamaları nedeniyle 02'ye geçilmez. Güncel sonuç en sondaki Windows Job kaydındadır; önceki başarısız tablolar tarihsel kanıttır.

[Ana plan](../orion-installation-lifecycle-implementation-plan.md) · [Ortak sözleşmeler](shared-contracts.md)

## Ön koşullar ve okunacaklar

Ön koşul aşamalar: [00](00-inventory-and-test-environment.md)

Ortak kararlar; ortak sözleşmeler 2.3 (PID/port, sahiplik ve kilitler), 2.4 (OS sınırları); 00 aşamasının sonuç kaydı.

Ön koşulların yalnızca durum etiketini değil, sonuç kaydını ve mevcut koddaki karşılığını doğrula. İlgisiz gelecek aşamaların tamamını okumak veya uygulamak gerekmez. Repo talimatları ayrıca geçerlidir.

## Bu aşamadaki iş ve kabul koşulları

Mevcut `manager.py` ve installer girişleri korunur; ortak yaşam döngüsü katmanına yönlendirilir. Çakışan uygulamalar kopyalanmaz. Her bileşende işletim sisteminin süreç kapanınca bırakacağı tek başlatma kilidi kullanılır; denetle/başlat/kaydet aralığı kilit altındadır. PID, süreç başlangıç zamanı, tam yürütülebilir yol ve bileşen kimliği eşleştirilir. Port çakışması ayrı hata, hazır olma ayrı kontroldür. Sahipliği kanıtlanamayan süreç kapatılmaz.

Genel isim/port/dizin parçası ile toplu kapatma kaldırılır. Önce kontrollü kapanma, ardından yalnızca doğrulanmış süreç ağacına sınırlı zorlama uygulanır. DB kullanan işler durmadan DB durdurulmaz. Durma doğrulanmadan başarı veya PID temizliği yapılmaz. DB hazır değilse bağımlı servis başlamaz. Canlı kilit dosyası silinmez; eski kayıt ancak gerçekten eski olduğu doğrulanınca temizlenir. PostgreSQL dayanıklılık ayarları düzeltilir.

Kabul/test: aynı anda 2–10 başlatma tek örnek oluşturur; kısmen çalışan kurulumda yalnızca eksikler başlar; yabancı port/PID ve ikinci Orion etkilenmez; PID tekrar kullanımı ve eski kilit sınanır; durdurma zaman aşımı doğru hata verir; DB hazır değilken Hub iş kabul etmez; Router tek başına çalışmaya devam eder. CLI ve installer aynı sonucu verir.

Ek geçiş koşulu: ortak sözleşmeler bölüm 2.3'teki ömür boyu tek örnek kilidi ile işlem kilidi ayrı sınanır; supervisor çökmesi, yaşayan çocuklar, erişim reddi ve sinyal öncesi PID değişimi kapsanır. Ortak sözleşmeler bölüm 2.4'teki OS sınırları korunur. Yeni kaynak kurulumunu otomatik tanımak için Aşama 2'nin tüm arayüzü beklenmez; mevcut kayıtlarla uyumlu en küçük kimlik katmanı eklenir.

## Sonraki aşamaya devredilecek çıktı

CLI ve installer tarafından kullanılan yaşam döngüsü arayüzü; kimlik/süreç kaydı ve kilit sözleşmesi; hata ve hazır olma sonuçları.

Uygulanan arayüzlerin gerçek dosya yolları, giriş noktaları, şema sürümleri ve hata sonuçları aşağıdaki sonuç kaydına yazılır. Sonraki model tasarım varsayımı yerine bu kaydı ve kodu esas alır.

## Kapsam sınırı

Yalnız bu aşamanın işini uygula. Ön koşulda eksik sözleşme varsa dar kapsamlı düzeltmeyi kaydet; başka aşamanın tüm özelliğini sessizce üstlenme. Gerçek kullanıcı verisinde hata enjeksiyonu yapma. Aşama testleri geçmeden sonraki aşamaya geçme; bu aşama bittiğinde sonuçları raporlayıp dur. Ana plan tablosundaki durum da güncellenir.

## Sonuç ve test kaydı

- Durum: DOĞRULAMA EKSİK. Torun süreç açığı son Windows Job uygulamasında kapatıldı; 40 yaşam döngüsü testi geçti. Önceki tablolar tarihsel sonuçtur; kalan geniş ürün/OS kabulü tamamlandı sayılmaz.
- Başlangıç / sonuç kaydı tarihi: 16 Ekim 2026 / 16 Ekim 2026 (Europe/Istanbul).
- Çalışılan depolar: Hub ve kardeş Router. Flutter kaynakları değiştirilmedi; başlangıçtan kalan `chat_presentation_test.dart` değişikliği korundu. Hub'daki önceki tasarım/plan değişiklikleri geri alınmadı. Commit/push yapılmadı.
- Plan veya bağımlılık değişikliği: aşama bağımlılıkları değişmedi. Paketleme, birleşik kurulum kaydı, taşıma, otomatik güncelleme ve Windows autostart eklenmedi. Mevcut Python ortamları korundu; yeni dependency kurulmadı.
- Sonraki aşamaya uygunluk: **hayır**. Sıradaki iş bu aşamanın aşağıdaki kalan kabul kontrolleridir; 02 başlamadı.

### Değişen dosyalar ve uygulanan davranış

Hub:

- `services/shared/process_lifecycle.py`: ortak yaşam döngüsü çekirdeği; `InstanceLock`, `ProcessReference`, `Runtime`, `ManagedChild`, `ensure_started`, `stop_component`, `owns_listener`, `wait_health`.
- `services/shared/process_child.py` ve `process_child.cjs`: Python/Node uygulamasının kendi graceful SIGINT yolunu tetikleyen yerel kontrol adaptörleri. Python adaptörü yalnız standart kütüphaneyi kullanır; her model ortamına psutil kurulmasını gerektirmez.
- `services/shared/native_postgres.py`: hedef postmaster kaydı, tam exe/komut/veri yolu/başlangıç zamanı, `pg_ctl status`, tek yetkili SQL sorgusunda `data_directory` (okunamıyorsa sunucu başlangıç zamanı) ve dayanıklılık kontrolleri. Başlatmada `fsync`, `full_page_writes`, `synchronous_commit` açık; `-F` yok. DB zorla sonlandırılmaz; kontrollü kapanma başarısızsa kayıt/veri korunur ve hata döner.
- `services/shared/native_services.py`, `service_supervisor.py`: Hub CLI ve installer için ortak yerel başlat/durdur köprüsü. Eski Router'ın global Python ortamı veya checkout venv'i korunur. Model servisinin uygulaması kendi interpreter'ında; supervisor installer ortamında çalışır. Base Python'da psutil yoksa mevcut kurulu ortama yönlendirilir, otomatik pip kurulmaz.
- `manager.py`: yerel start/stop/status ortak köprüye bağlandı; PostgreSQL isim/port kill, Python/Redis dizin-parçası taraması ve kör postmaster temizliği kaldırıldı. Docker toplu seçimi tam Compose proje etiketi + bu checkout'a ait gerçek servis diziniyle sınırlı; envanter API'sinin döndürdüğü manifest **dosyasının dizini** esas alınır. Installer Ctrl+C fallback'i PID metni yerine elde tutulan Popen referansıyla bekler/sonlandırır.
- `installer/app/services.py`: aynı köprü, sahipliksiz PID/dizin taraması ve Router kilidi silme kaldırıldı; durdurma doğrulanmazsa kaldırma/sıfırlama devam etmez. Yerel durum artık port/dizin parçası taraması yerine ortak `component_status` sonucunu kullanır; `lifecycle_status` alanı eklenir. Yabancı açık port hazır servis sayılmaz; hazır kaydın çocuğu eksikse `starting` gösterilir.
- `services/hub/run_local.py`: supervisor lifetime kilidi, DB hazır olma kapısı, sahipli Redis PING, süreç+TLS/UUID sağlık kontrolleri, doğrulanmış çocukların kullanılması/eksik listener'ların onarımı, DB'den önce uygulama çocuklarının kapanması. Worker'ın mevcut sınırlı restart bütçesi korundu.
- `services/shared/flutter_launcher.py`: mevcut UI web çocuğu aynı runtime'a kaydedilir; UI tercihleri değiştirilmedi.
- `local_db_setup.py`: ilk DB hazırlama sırasında da dayanıklılık açık ve hedef cluster doğrulanmış; SQL parolası argv yerine stdin üzerinden gider.
- Test/yardımcılar: `scripts/lifecycle/{probe_supervisor.py,test_process_lifecycle.py,run_baseline.py,sync_router_engine.py,test_environment.py}`, `services/hub/tests/{test_installer_browser_lifecycle.py,test_worker_supervisor.py}`. Eski doğrudan-force beklentileri yeni supervisor sözleşmesine uyarlandı; diğer testler korundu.
- Hub ve Router `.gitignore`: yeni yaşam döngüsü kayıt/kontrol dizinleri kullanıcı çalışma durumu olarak dışarıda tutulur.

Router:

- `core/{process_lifecycle.py,process_child.py,process_child.cjs,native_postgres.py}` ortak motorun bağımsız dağıtım kopyasıdır. **Tek kaynak Hub `services/shared` altıdır**; PostgreSQL dosyasında yalnız import namespace'i değişir. İkinci bir algoritma geliştirilmedi. `scripts/lifecycle/sync_router_engine.py` varsayılan olarak dört dosyanın eşitliğini denetler; `--write` yalnız kardeş Router checkout'una senkronize eder. Kabul testi de aynı eşitliği doğrular.
- `bin/{common.py,prod.py,dev.py,stop.py}` aynı motoru kullanır. Port temizleme API'leri artık teşhis/çakışma hatası verir, yabancı listener/container kapatmaz. Eski PID tek başına durdurma yetkisi değildir. Prod/dev supervisor'ları ayrı bileşen kilitleri taşır; DB çocuklar çıkmadan durmaz. Next.js, shell komut metni yerine argv ve Node kontrol adaptörüyle başlar.
- `core/lifespan.py`: API içinden port tahminiyle DB stop/PID silme/`-F` ile restart kaldırıldı; DB başarısızlığı startup hatası olarak kalır.
- `core/lifecycle_cli.py`, `install.ps1`, `install.sh`: yeni üretilecek `orionrouter start/stop` wrapper'ları ortak doğrulanmış hazır olma/hata sözleşmesini kullanır; yalnız PID var diye başarı veya stale dosya silme yok. Windows installer ve üretilen yerel PowerShell wrapper sözdizimi parser ile doğrulandı. **Mevcut AppData launcher'ları bu görevde çalıştırılmadı veya yeniden yazılmadı.**
- `dev/test-orion-router/test_native_lifecycle.py`: bağımsız Router port/PID/lock/launcher regresyonları.

### Kararlı arayüz ve kayıt

`<bileşen kaynak kökü>/.orion-lifecycle/<component>/identity.json` ve `processes.json`, şema **v1**. Bileşenler: `hub`, `router-prod`, `router-dev`, genel installer servisi için kendi servis kökünde `service`. Bu bir Aşama 02 birleşik `install.json` kaydı değildir. Hub/Router TLS UUID ve mevcut locator şemaları değiştirilmedi.

Identity kaydı: bileşen UUID, kanonik kök, bileşen adı. Süreç kaydı: bileşen/kurulum UUID, supervisor çalışma UUID, legacy-source dağıtım işareti, supervisor/çocuk PID, OS başlangıç zamanı (Windows FILETIME dahil), gerçek exe, komut hash'i, kullanıcı, beklenen entrypoint, çocuk çalışma UUID ve gözlenen alt süreç referansları. Ham argv/parola yazılmaz. Paket sürüm kimliği Aşama 05'in sürüm metaverisiyle genişletilecek; `legacy-source` imzalı sürüm kanıtı değildir.

Bu dar v1'de `installation_id` alanı bileşenin yerel identity UUID'sine eşittir; Hub ve Router'ı birleştiren global kurulum UUID'si henüz uygulanmadı. Aşama 02 bu ilişkiyi mevcut kimlikleri yenilemeden kurmalıdır. Kamuya açık DB yolları/port/hesap adı (parola olmadan) süreç kaydında tutulur; supervisor yeniden bağlanamadan çökerse kurtarma aynı DB'nin kapanma bilgisini kaybetmez.

Durumlar: `starting`, `ready`, `stopping`, `children-stopped`, `stopped`. Atomik fsync+replace yazımı ve Windows'un kısa paylaşım çakışmaları için **iki saniyeyle sınırlı** okuma/yazma tekrarı vardır. Kalıcı erişim reddi, bozuk JSON veya süreç bilgisine erişim sorunu başarı/çalışmıyor diye yorumlanmaz.

CLI/panel `component_status` ayrıca `unverified`, `ownership-unavailable`, `ownership-error`, `recovery-required` sonuçları üretebilir. Bunlar kalıcı Runtime durum makinesine yeni geçiş değildir; yönetim/okuma sonucudur.

Windows: aynı kullanıcı SID'sine sınırlı DACL'li isimli mutex; ayrı `operation` ve supervisor ömrü boyunca `instance` kilidi. İsim SID+kanonik bileşen state dizini+kilit türünden türetilir. İşlem kilidi çağıran komutu, lifetime kilidi supervisor'ı korur; biri diğerinin yerine geçmez. Abandoned mutex testi gerçek Windows nesnesi üzerinde yapıldı. Unix flock adaptörü var, macOS/Linux desteği bu koşuyla ilan edilmez.

Sinyal öncesinde açılan Windows process handle, kayıtla yeniden karşılaştırılır; force/wait bu handle'a gider. Kontrol, çalışma UUID'sine özgü `stop-<run-id>` ve çocuk kontrol dosyalarıyla yapılır. `LifecycleError` çakışma, sahiplik uyuşmazlığı, erişim reddi, sağlıksız DB, hazır olma/kapanma timeout'u için görünür hata üretir. Supervisor çökmüşse kayıtlı yaşayan çocuklar doğrulanır; aynı hedef çocuk tekrar açılmaz. Ana süreç ölmüş ama kayıtlı torunları yaşıyorsa yeni kopya açmak yerine uzlaştırma hatası verilir.

### Ortam ve test komutları

Windows / PowerShell; Python 3.11.2, psutil **7.2.2**, PostgreSQL **16.3**. Mevcut `.tmp/policy-test-runtime/Scripts/python.exe` kullanıldı. Ürün DB'sine bağlantı kurulmadı. Gerçek PostgreSQL testleri `initdb` ile yeni geçici cluster'lar ve boş loopback portları oluşturur; SQL yalnız bu portlardaki `fixture/postgres` hesabına gider. Redis ve Node testleri de yalnız yeni geçici uygulama/veri kökleri kullanır.

```powershell
& .tmp/policy-test-runtime/Scripts/python.exe scripts/lifecycle/run_baseline.py lifecycle
& .tmp/policy-test-runtime/Scripts/python.exe scripts/lifecycle/run_baseline.py hub
& .tmp/policy-test-runtime/Scripts/python.exe scripts/lifecycle/run_baseline.py router
& .tmp/policy-test-runtime/Scripts/python.exe scripts/lifecycle/run_baseline.py fixtures
& .tmp/policy-test-runtime/Scripts/python.exe scripts/lifecycle/sync_router_engine.py
git diff --check
git -C ../orion-router diff --check
```

Runner sınırı artık **240 saniye**; başarısız/timeout kökü otomatik silinmez. Seçili testler geçici APPDATA/LOCALAPPDATA/XDG/security/seçim/UUID/TEMP/TMP ile çalışır; `asyncpg.connect/create_pool` beklenmeyen erişimi yasaklar. Bu guard, gerçek geçici PostgreSQL testindeki açık `psql` kullanımını yasaklamaz; canlı asyncpg girişimleriyle karıştırılmamalıdır.

| Kontrol | Son sonuç | Kanıt |
| --- | --- | --- |
| Aşama 01 kabul seti | **27 passed**, 50.38 s; 0 fail/skip | 10 eşzamanlı start, kısmi kurulum, supervisor çökmesi/yaşayan çocuk, iki kurulum/yabancı listener, ayrı operation/lifetime kilitleri, gerçek abandoned mutex, PID/komut/exe uyuşmazlığı, erişim reddi, sinyal öncesi kimlik değişimi, shutdown timeout, stale lock, bozuk kayıt, DB başarısızlığında iş kabul etmeme, CLI/installer ortak sonuç, DB'den önce çocuk kapanması, Compose sahipliği, TLS UUID/sertifika/yerel süreç, Redis SAVE + yeniden açılış, Node graceful handler, iki gerçek PostgreSQL cluster, DB kurtarma metaverisi, eski global Python/servis interpreter'ı, hazır durumun yanlış raporlanmaması |
| Hub/installer regresyonu | **48 passed, 2 subtests passed**, 37.36 s; 0 fail/skip | Başlangıç seti + WorkerSupervisor testleri; aiohttp kalıtımı için mevcut deprecation |
| Router | **9 passed**, 1.74 s | Mevcut 4 locator/TLS testi + 5 port/PID/lock/launcher testi; locale.getdefaultlocale için mevcut deprecation |
| Aşama 00 fixture | **7 passed**, 7.054 s | Kök, ortam, kimlik, salt okunur örnek DB/model/sentinel hash'leri. TLS örneğine loopback SAN eklendi |
| Kaynak eşitliği / sözdizimi | Başarılı | 4 vendored dosya aynı; Python AST; PowerShell kurucu + oluşturulacak yerel wrapper parse |
| Whitespace | Başarılı | Her iki depoda `git diff --check`; LF/CRLF bilgilendirmeleri hata değil |

Son başarılı setlerin toplamı **91 test + 2 alt test**, sıfır fail/skip. Python AST kontrolü **28 dosyada** geçti. Test sayısı VM ürün kabulünün tamamlandığını göstermez.

### Başarısız denemeler ve düzeltmeleri

1. İlk eşzamanlı koşuda Windows okuyucuları sırasında `os.replace` erişim reddi görüldü; sınırlı yazma retry eklendi. Daha sonraki 10-start koşusu da `read_text` sırasında kısa erişim reddini yakaladı; aynı sınırlı retry okumaya da eklendi. Kalıcı erişim sorunu hâlâ hata verir.
2. İlk gerçek PG koşusunda Windows `pg_ctl` daemon'a stdout pipe devretti; 240 saniye runner timeout'u oluştu. **İlk runner'ın eski TemporaryDirectory temizliği yalnız sentetik cluster'ın dosyalarını kısmen sildi; cluster bu nedenle abnormal shutdown yaşadı.** Bu koşu veri koruma başarısı sayılmadı. Canlı kullanıcı cluster'ı değildi. Runner başarısız kökü artık koruyor; pg_ctl start log+DEVNULL kullanıyor. Düzeltme sonrası iki yeni cluster'ın temiz shutdown kayıtları ve diğer cluster'ın aynı sahiplikle sağlıklı kalması doğrulandı. Hasarlı sentetik cluster için kontrollü stop denendi; PG_VERSION silindiğinden hata verdi. Sonraki yalnız-test-PID kontrolünde süreç zaten çıkmıştı; kullanıcı PostgreSQL'ine sinyal gönderilmedi.
3. Kısmi kurulum testinde çocuk hazır olma kontrolüyle aynı anda çıktı. Runtime artık başarı ilan etmeyip `starting` durumunu saklıyor ve supervisor eksik çocuğu tekrar denetliyor. İlgili kabul seti yeniden geçti.
4. Eski WorkerSupervisor main testi, yeni Runtime/DB kapısı ve Redis referansı için fixture uyarlaması gerektirdi. Test kaldırılmadı; worker restart/PID değişimi ve shutdown delegasyonu korunarak regresyon seti geçti.
5. `uv lock --offline --check --project services/hub` denemesi yerel cache'de aiohttp bulunmadığından resolver hatası verdi. Dependency yükseltmesi yapılmadı; psutil 5.9 uyumluluğu için eski connections API fallback'i korundu ve pyproject/lock kısıtları başlangıçtakiyle aynı bırakıldı. Bu deneme başarılı lock yeniden üretimi sayılmaz.
6. PowerShell üretilmiş betik kontrolünün ilk yardımcı çıkarımı string içindeki `else` satırında bölündüğü için eksik blok hatası verdi; gerçek script bloğu sınırıyla çıkarım düzeltildi ve iki parse kontrolü geçti. Kurucu çalıştırılmadı.

### Korunma, kalan doğrulama ve geri alma

Gerçek Orion servisi/DB'si başlatılmadı, durdurulmadı veya taşınmadı. Gerçek kurulum kimliği, model, tarayıcı profili, ayar ve ACL mirası değiştirilmedi. Fixture snapshot'ları hedef dışındaki yapay veri/kimliği, gerçek ikinci geçici PostgreSQL'in aynı owner ve sağlığını ve kayıtlı olmayan listener'ın erişilebilir kalmasını doğruladı. Redis testinde sentetik değer SAVE sonrası yeniden açılarak okundu. Asyncpg guard son başarılı koşularda **0 connect / 0 pool** girişimi bildirdi.

**İlk koşu sonunda kalan zorunlu doğrulama (güncel durum aşağıdaki ek kayıtta):** tam Hub ve bağımsız Router uygulamalarının izole Windows VM üzerinde gerçek API/DB şemasıyla start/start, start/stop, panel/CLI eşzamanlılığı; aktif sohbet/araç/ses işlerinin ve tarayıcıların kapanma sınırı; tam prod/dev/Next.js ve eski/yeni launcher entegrasyonu; gerçek OS erişim reddi ve süreç ağacında kapanmayla eşzamanlı yeni torun oluşturma yarışı. İlk gerçek süreç/TLS/PostgreSQL/Redis/Node testleri bunların yerine geçmiş sayılmadı. Hyper-V modülü bulundu; `Get-VM` normal koşuda CIM erişim hatası, sandbox dışı salt okunur denemede de **işletim sistemi yetkilendirme hatası** verdi. Bu otomatik onay incelemesi reddi değil, Hyper-V yönetim yetkisi eksikliğidir. VM oluşturulmadı/başlatılmadı/değiştirilmedi; üretim kurulumuyla bu açığı kapatmak için test yapılmadı. Bu nedenle aşama tamamlandı olarak işaretlenmedi.

Gözlenen çocuk/torunlar için handle doğrulaması var; OS job/container ile süreç ağacına doğum anında atomik sahiplik bu uygulamada yok. Hızla doğup ebeveyni kapanan, henüz gözlenmemiş torunların tam ürün senaryosunda kontrolü ek kabul kanıtı gerektirir. Legacy raw PID kayıtlarına otomatik yönetim yetkisi verilmez; yükseltmeden önce yaşayan legacy servisler mevcut yetkili yöntemleriyle kapatılmalıdır. Kurucu şablonunun güncellenmesi, kullanıcıdaki eski wrapper'ın güncellendiği anlamına gelmez. Docker daemon/volume mutasyonları ve macOS/Linux gerçek entegrasyonu çalıştırılmadı.

Geri alma: bu görevden önce açık olan kullanıcı değişikliklerini koruyarak yalnız yukarıdaki uygulama/test dosyalarının görev diff'ini geri al; iki depodaki canonical/vendored motor birlikte geri alınmalıdır. Tüm çalışma ağacını reset etme. Bu motorla gerçekten çalışan kurulum varsa önce yeni kontrollü stop yoluyla kapanmayı doğrula; `.orion-lifecycle` kayıtlarını yaşayan süreç varken elle silme. Gerçek veri/kimlik geri yüklemesi bu oturum için gerekmez; başarısız koşuların yalnız sentetik scratch/logları `.tmp/lifecycle` altında korunmuştur.

## Ek test kaydı — 16 Ekim 2026, gerçek ürün kopyaları

Kullanıcının kalan testleri de agent'ın çalıştırması isteğiyle Aşama 01 testleri genişletildi; Aşama 02 başlatılmadı. **Hyper-V ürün bağımlılığı veya normal testlerin zorunlu ön koşulu değildir.** Bu koşular mevcut Windows üzerinde yeni kaynak/veri kökleri, boş loopback portları ve sentetik kimliklerle yapılmıştır. VM, ana bilgisayarı etkilemeden OS kapatma/güç kesme/yeniden açma gibi yıkıcı senaryolar için gereklidir; bunlar burada çalıştırılmadı.

### Test ortamı ve tekrar çalıştırma

`scripts/lifecycle/run_product_acceptance.py` tam Hub, bağımsız Router prod ve Router dev/Next.js'i `.tmp/lifecycle/product-*` altında çalıştırır. Kaynaklar kopyalanır; mevcut `.env`, kullanıcı DB'si, persistent kimlik, model veya tarayıcı profili taşınmaz. Var olan runtime/dependency dosyaları tekrar kurulum yapılmadan kullanılır; PostgreSQL/FFmpeg ve Node dependency dosyaları için yalnız bağımsız dosya girişleri/hardlink kullanılır, veri dizinleri bağlanmaz. Başarılı/başarısız ürün kökleri silinmez. İlk iki dev fixture'ındaki Node junction denemesi Turbopack tarafından reddedildi; son fixture bağımsız `node_modules` dizini kullanır. `.next`, DB ve diğer yazılan veriler fixture içindedir.

Python ürün süreçlerine loopback dışı socket connect/bind reddeden test `sitecustomize.py` verilir; yalnız ürünün harici bağımlılıkları izole edilir, API/DB/Redis'in kendileri mock değildir. mDNS, Flutter otomatik açılışı ve yerel port-80 shortcut'ı testte kapalıdır. Native Hub Redis de loopback'e bağlanır. Gerçek Chrome testi yeni, headless profile bağlanır; masaüstü otomasyon yardımcıları güncel baseline runner'da mock'lanır. Bu guard nedeniyle gerçek kişinin izin penceresi/varsayılan profili üzerinde kabul testi yapılmış sayılmaz.

Ortam: Windows; ürün Hub Python **3.14.3**, Router/baseline Python **3.11.2**, Node **24.21.0**, Next.js **16.2.6**, PostgreSQL **16.3**. Dev fixture'ında mevcut FFmpeg/ffprobe da kullanılır; ilk prod fixture'ında FFmpeg kopyalanmadığı için TTS archive runtime hatası görülmüştür. İlk prod koşusu bu özelliğin çalıştığını kanıtlamaz.

```powershell
& .tmp/policy-test-runtime/Scripts/python.exe scripts/lifecycle/run_product_acceptance.py hub
& .tmp/policy-test-runtime/Scripts/python.exe scripts/lifecycle/run_product_acceptance.py router
& .tmp/policy-test-runtime/Scripts/python.exe scripts/lifecycle/run_product_acceptance.py router-dev
& .tmp/policy-test-runtime/Scripts/python.exe scripts/lifecycle/run_product_acceptance.py router-dev --reuse .tmp/lifecycle/product-mx0wgwng
& .tmp/policy-test-runtime/Scripts/python.exe scripts/lifecycle/run_baseline.py lifecycle
& .tmp/policy-test-runtime/Scripts/python.exe scripts/lifecycle/run_baseline.py boundaries
& services/hub/.venv/Scripts/python.exe scripts/lifecycle/run_baseline.py jobs
& services/hub/.venv/Scripts/python.exe scripts/lifecycle/run_baseline.py audio
& services/hub/.venv/Scripts/python.exe scripts/lifecycle/run_baseline.py browser
& services/hub/.venv/Scripts/python.exe scripts/lifecycle/run_baseline.py browser-api
& services/hub/.venv/Scripts/python.exe scripts/lifecycle/run_baseline.py browser-bridge
& services/hub/.venv/Scripts/python.exe scripts/lifecycle/run_baseline.py browser-live
```

`--reuse` yalnız daha önce bu runner tarafından oluşturulmuş, supervisor/çocuk/DB kapanışı doğrulanmış `router-dev` test kökünü kabul eder; eski sonuç/logları `previous-<timestamp>` altında saklar. Ürün komutlarının logları, `ports.json` ve `result.json` test kökündedir. Yeni baseline koşuları `.tmp/lifecycle/results` altında JSON ve pytest XML sonuçlarını da saklar; başarısız/timeout scratch korunur. Test setleri örtüştüğü için aşağıdaki sayılar birbirine eklenerek benzersiz test toplamı oluşturulmaz.

### Tamamlanan koşular ve bulunan hatalar

| Kontrol | Sonuç / kanıt |
| --- | --- |
| Tam Hub | **Başarılı**; `product-9tn5f_tt/result.json`. Üç eşzamanlı gerçek start tek supervisor'a bağlandı; repeat-start çalışma UUID'sini korudu; gerçek API/Worker/Redis/PG kapandı; yeniden aç/kapat ve bileşen identity koruması geçti |
| Bağımsız Router prod | **Başarılı**; `product-9fzid4iv/result.json`. Aynı dört kontrol geçti. TTS runtime sınırlaması yukarıda açıklandı |
| Tam Router dev/Next.js | **Başarılı son tekrar**; `product-mx0wgwng/result.json`. Gerçek reload backend + HTTPS Next.js sayfası hazır; üç eşzamanlı start/repeat-start, iki kapanış, yeniden açılış ve identity koruması geçti. Önceki 40 s shutdown timeout sonucu `previous-*` altında korunur; son koşu 120 s toplam sınırla geçti |
| Son Hub/installer regresyonu | **48 passed, 2 subtests passed**, 35.09 s; `results/baseline-3eh0caqw-hub.{json,xml}` |
| Yaşam döngüsü seti | **31 passed, 1 failed**, 77.16 s; `results/baseline-q0gu3k9e-lifecycle.{json,xml}`. PostgreSQL iki-cluster testi dahil geçti; başarısız olan gözlenmemiş torun yarışı |
| Yeni sınır alt seti | **4 passed, 1 failed**, 2.79 s; `results/baseline-uiep_t09-boundaries.{json,xml}`. Gerçek Windows System PID 4 için yalnız OpenProcess denemesinde erişim reddi, Node fork graceful kontrolü, ayrı kurulum Worker'ı ve journal/RESP2 kontrolü geçti; aynı torun yarışı başarısız |
| Worker/job/stream | **34 passed, 9 subtests passed**, 22.98 s; gerçek geçici Redis crash/ACK/pending kurtarma ve stream stop. Sağlayıcı yan etkileri sentetik/mocked; ücretli sohbet/gerçek mikrofon kaydı testi değildir |
| Ses | **19 passed, 1 failed**, 2.36 s; `baseline-lqaz2or9`. `test_transport_chunk_boundaries_do_not_change_resampling`: 216 beklenirken 218 byte. PCM ürünü/test beklentisi bu aşamada değiştirilmedi |
| Tarayıcı manager ağırlıklı ilk bölüm | **7 failed, 11 passed**, ardından **240 s timeout/KeyboardInterrupt**. Altı test artık olmayan `open_inspect_window` API'sine bağlı; endpoint discovery testi `None` dönüyor. Bu koşu tam tarayıcı seti başarısı değildir |
| Tarayıcı API/araç/CDP | **45 passed, 1 failed**, 6.69 s; `results/baseline-8oa58_gq-browser-api.{json,xml}`. `test_browser_control_uses_selected_existing_tab`, mevcut `inactive=True` argümanını beklemiyor. Ürün davranışı/test bu aşamada değiştirilmedi |
| Tarayıcı süreç köprüsü | **5 passed**, 3.59 s; `results/baseline-r72kd3rk-browser-bridge.{json,xml}`. Ayrı süreçte paylaşım ve harici Hub kapanış köprüsü dahil |
| Gerçek headless Chrome/CDP | **1 passed**, 9.40 s. Ayrı geçici profil/cookie, görünür/gizli hedefler, bağlantı kapatma; mevcut browser fixture'ının eski helper patch'i güncel API'ye uyarlandı |
| Router regresyonu | **9 passed**, 3.77 s; `results/baseline-xvayhjop-router.{json,xml}` |
| Son izolasyon fixture seti | **7 passed**, 10.493 s; `results/baseline-5liym_ki-fixtures.json` |
| Son kaynak/sözdizimi/whitespace | **33 Python AST**, 4 canonical/vendored eşitliği, iki depoda `git diff --check` başarılı |

Yeni baseline regresyonlarında beklenmeyen asyncpg bağlantı/pool girişimi **0/0**. Tam ürün fixture'ları ise yalnız kendi oluşturdukları DB'lere gerçekten bağlandı. İlk tarihsel 91 test sayısı yukarıdaki başarısız kabulü geçersiz kılmaz.

Son süreç denetimi `.tmp/lifecycle/final-process-audit.json`: sonucu kaydedilmiş ürün kökleri ve baseline TEMP alanlarına ait **kalan test süreci yok**. Bu denetim kullanıcı servislerini kapatmadı veya değiştirmedi.

### Bu testlerde yapılan dar Aşama 01 düzeltmeleri

- `run_local.worker_ready`: yaşayan venv launcher'ı tek başına başarı sayılmaz; kendi Redis journal owner'ının gerçek Worker/alt sürece ve başlangıç zamanına ait olması gerekir. Redis 5 için açık **RESP2** kullanılır; başlangıçta bounded bekleme, restart sırasında `starting` durumuna dönüş vardır.
- `kernel/job_safety.py`: eski Worker taraması başka kurulum kökündeki Worker'ı engellemez; aynı kurulumun legacy Worker kontrolü ve Redis owner kilidi korunur.
- `kernel/registry.py`: yeni DB'ye eşzamanlı API/Worker bağlanınca PostgreSQL catalog oluşturma yarışını önlemek için şema işlemleri session advisory lock **51730101** altında sıraya alınır; şema hatası yutulmaz.
- Hub public listener için `ORION_PUBLIC_BIND_HOST` (mevcut varsayılan `0.0.0.0`), native Redis için açık loopback bind; mobil advertise `ORION_MDNS=0` ayarına uyar. Bootstrap/trusted Router refresh görevi mDNS kapalıyken de korunur.
- Node preload kontrol yolu fork'lara miras kalır; önceki env silme Next.js server child'ını başlatamıyordu. Gerçek Node fork regression testi eklendi.
- `wait_health` artık HTML readiness için yerel `path`/`json_response` seçeneklerine de sahiptir; dev supervisor backend TLS/UUID yanında gerçek Next.js `/dashboard` HTTPS 200 yanıtını da bekler.
- Startup hata yolunda steady-state loop'a girilmemiş olsa bile `Runtime.stop_children` önce mevcut alt süreçleri yakalar. **Bu, ebeveyni zaten ölmüş/gözlenmemiş torun yarışını çözmez.**
- PG fast shutdown **30 s** (önce 10 s), toplam `stop_component` beklemesi **120 s**, Hub'ın Router stop subprocess sınırı **150 s**. Çocuk kapanmadan DB stop veya DB'ye force eklenmedi. Native Router text çıktısında OEM decode hataları `errors=replace` ile loglanır.
- Windows native `initdb` testi çıktı pipe'ı yerine log dosyası kullanır, bound **90 s**. Pipe kullanan ara koşular 30/90 s timeout verdi; son dosya loglu koşuda iki PG cluster testi geçti. İlk aşamadaki başarısız timeout kökleri başarı sayılmadı.

### Önceki ek koşunun kabul engeli ve kalan kapsam

**Birincil engel:** `test_fast_parent_exit_does_not_leave_unobserved_grandchild`, ebeveyn hemen kapanırken yeni torunun kontrollü stop sonrası yaşadığını gerçek Windows süreçleriyle doğruluyor. Test başarısız kalmıştır; xfail/skip yapılmadı. Testin tuttuğu OS handle ile sentetik torun `finally` içinde temizlenir. Çözüm yeni polling/isim taraması değil, Windows'ta süreç ağacını doğum anında kurulum sahipliğine bağlayan bir mekanizma gerektirir; diğer platform sınırları ayrı tutulmalıdır.

İlk başarısız dev fixture'ları üç reload server'ını geride bıraktı (`product-hom0ywwf`, `product-bpqhrb15`, `product-7sq1b5dd`). Her biri test kökü, tam multiprocessing komutu, PID başlangıç zamanı ve held OS handle ile doğrulandıktan sonra yalnız o test süreçleri temizlendi; kayıt `.tmp/lifecycle/dev-orphan-cleanup.json`. Genel Python/port kill yapılmadı. Bu başarısız koşular temiz kapanma kanıtı değildir. Eski kayıtlı kısa ömürlü descendant PID'si tekrar kullanıldığında bazı runner cleanup'larında sahiplik uyuşmazlığı hatası da görüldü; yabancı PID'ye sinyal gönderilmedi, kayıt korundu. Uzun ömürlü tarihsel descendant listesinin bu durumu ele alması da yeniden değerlendirilmelidir.

Henüz tam kanıtı olmayanlar: tek tam ürün senaryosunda aktif sohbet/araç/ses işi sürerken tüm uygulamayı kapatma; gerçek manuel tarayıcı izin penceresi ve kullanıcı profili; panel/CLI'nin tam UI üzerinden eşzamanlılığı; eski kurulu launcher'ın gerçek yükseltmesi; VM/OS ani güç kesme/yeniden açma ve macOS/Linux/Docker daemon entegrasyonu. Mevcut unit/gerçek Redis/headless Chrome testleri bu eksikleri tek başına kapatmaz. Gerçek ana Windows kapatılmadı, mevcut kullanıcı servisleri/DB'si test amacıyla başlatılıp durdurulmadı. **Aşama 01 DEVAM EDİYOR; Aşama 02'ye geçilmez.**

Geri alma sınırı önceki bölümle aynıdır; ek değişikliklerde `kernel/{job_safety,registry}.py`, `api/mobile_server.py`, browser test fixture'ı ve yeni test runner'ları da görev diff'ine dahildir. Başlangıçtaki kullanıcı değişiklikleri korunur; commit/push yapılmadı.

## Son kayıt — 16 Ekim 2026, Windows Job sahipliği düzeltmesi

**Güncel sonuç:** önceki `test_fast_parent_exit_does_not_leave_unobserved_grandchild` hatası kapatıldı. Yeni genişletilmiş Aşama 01 seti **40 passed**, 69.08 s, fail/skip yok; `.tmp/lifecycle/results/baseline-xzg01kyx-lifecycle.{json,xml}`. Aşama durumu **DOĞRULAMA EKSİK**: aşağıda belirtilen kalan tam ürün/UI/OS kontrolleri tamamlanmadı; 02'ye geçilmedi.

### Uygulama ve kararlı arayüz

- Yeni `services/shared/windows_job.py` Windows uyarlayıcısıdır. `Job`, SID'ye sınırlı DACL'li kernel grubunu oluşturur/açar; ad kullanıcı SID + kanonik kök + bileşen/kurulum UUID + çocuk instance UUID'den türetilir. Job kaydının kapsamı `Runtime.read` içinde o kurulum kimliğiyle yeniden karşılaştırılır; başka kurulumun grubu kabul edilmez.
- Yeni `services/shared/process_host.py`, mevcut base CPython ile `-I -S` çalışır ve yalnız standart kütüphaneyi kullanır. Site/user hook'ları çalışmadan ve uygulamayı `CreateProcess` ile oluşturmadan önce kendisini Job'a atar. Uygulama, venv redirector'ı, reload worker'ları, Node fork'ları ve native çocuklar üyeliği doğumda miras alır. Breakaway açılmaz. Bu garanti WMI/uzak servis üzerinden başlatılmış bağımsız süreçleri kapsamaz.
- Supervisor ve uygulamayı bekleyen host ayrı Job handle'ları tutar. Supervisor tek başına çökerse host kurtarma için grubu açık tutar. `KILL_ON_JOB_CLOSE`, iki denetleyici de kaybolursa yalnız o grupta yetim kalmayı önler. İlk denemede son handle kapanınca named Job'ın yeniden açılabilirliği yetersiz görüldü; ayrı guardian denendi, son çözüm ek guardian gerektirmeyen bu iki-handle düzenidir.
- `Runtime.launch`, host'tan **önce** kapsamı ve çocuk UUID'sini içeren kalıcı niyeti yazar; kısa süre çocuk `process: null` olabilir. PID kaydı yalnız Job üyeliği doğrulandıktan sonra yayımlanır; null kayıt hazır sayılmaz. Sonraki kayıt yazımı başarısızsa exact Popen handle ve oluşturulmuş Job kapatılarak uygulama geride bırakılmaz. Supervisor PID yazamadan çökerse kurtarma Job kaydını kullanır.
- `cancel-<instance-id>` dosyası, Job handle'ını açmış ama henüz gruba katılmamış host'un sonradan uygulamayı başlatmasını önler. Host iptali **gruba girdikten sonra, uygulamayı oluşturmadan önce** denetler. Durdurma bu iptali grup boş görünse bile yazar. Gecikmiş host testinde bu aralık gerçek Windows handle'larıyla sınandı.
- `ManagedChild.stop`, önce aynı kontrol dosyası/Redis `SHUTDOWN SAVE` yolunu kullanır; sınırlı süreden sonra gerekiyorsa yalnız doğrulanmış Job handle'ına `TerminateJobObject` uygular. Güncel üyelik boşalmadan başarı/DB stop yoktur. PG, üretim supervisor'ının ayrı `Postgres` adaptörüyle yönetilir; uygulama Job'ına eklenmez ve DB'ye zorla sonlandırma eklenmedi.
- `descendants`, Job'lı çocuklarda yalnız güncel üyelikten üretilen teşhis snapshot'ıdır. Geçmiş torun PID'sinin tekrar kullanılması, başka süreci kapatma yetkisi vermez ve yeni Job'lı ağaçların kapanmasını engellemez. `child_running` root/üyeliği, `child_tree_alive` tüm Job'ı denetler. Installer durum yolu ve ürün kabul runner'ı bu ayrımı kullanır.
- Üst seviye `identity.json`/`processes.json` **v1**, bileşen UUID ve mevcut TLS kimlikleri korunur. Yeni çocuklarda `job: {v, scope, name}` ve başlangıçta nullable `process` alanı vardır. Job'sız tarihsel kayıtlar eski doğrulanmış PID/handle yolunu kullanır; yeni atomik ağaç garantisi onlara geriye dönük verilmez. Eski ham PID kayıtlarından otomatik sahiplenme hâlâ yoktur. Aşama 02 bu alanları ve null başlangıç niyetini dikkate almalıdır.
- Ortak sözleşme 2.3 bu Windows sınırını kaydeder. Router'a aynı `windows_job.py`/`process_host.py` de vendored edildi; eşitlik artık **6 dosyada** denetlenir. Model uygulaması kendi interpreter'ında kalır; host için yeni dependency kurulmaz. Paketleme aşaması host/modül dosyalarını ve uygun CPython runtime'ını paketlemeyi ayrıca ele almalıdır.

### Koşular ve hata geçmişi

| Koşu | Sonuç / kanıt |
| --- | --- |
| İlk genişletilmiş Job kabulü | **38 passed**, 100.84 s; `results/baseline-3rxgzyei-lifecycle.{json,xml}` |
| Son genişletilmiş kabul | **40 passed**, 69.08 s; `results/baseline-xzg01kyx-lifecycle.{json,xml}`. Eski 32 test + supervisor crash, eksik PID kaydı, başka Job scope'u, tarihsel PID tekrar kullanımı, eksik Job'da uygulamayı başlatmama, iki denetleyici kaybında OS kapanışı, PID kayıt yazma hatası ve gecikmiş host iptali |
| Hub/installer regresyonu | **48 passed, 2 subtests passed**, 164.98 s; `results/baseline-fy4t30v2-hub.{json,xml}`. Ağır paralel ürün yükü varken çalıştı |
| Router regresyonu | **9 passed**, 49.31 s; `results/baseline-bw_t9atl-router.{json,xml}` |
| Yeni Job motoruyla gerçek Hub | **Başarılı**, `product-ka_45t5c/result.json`: üç eşzamanlı start, repeat-start, iki gerçek aç/kapat çevrimi, kimlik koruması ve kontrollü PG kapanışı |
| Son kaynakla Router dev/Next.js | **Başarılı**, `product-mx0wgwng/result.json`: son sıralı tekrarın üç eşzamanlı start/repeat-start ve iki aç/kapat çevrimi geçti. Son kapatma denetimi yalnız root PID'lerini değil `child_tree_alive` ile tüm Job'ı denetler |
| Son kaynakla bağımsız Router prod | **Başarılı**, `product-aogd4vfe/result.json`: son sıralı tekrar aynı çevrimleri gerçek PG/TLS/FFmpeg ile tamamladı; eski başarısız koşu `previous-*` altında |
| Kaynak eşitliği / AST | **6 dosya** canonical/vendored aynı; **37 Python AST** başarılı |

40 kabul testinin önceki ara koşusunda bir test `process: null` başlangıç aralığını beklemediği için `NoneType` hatası verdi (`baseline-269r_hsr`: 38 pass, 1 fail). Kısmi kurulum testi artık ikinci çocuk için niyetin yanı sıra doğrulanmış yaşayan root'u bekler; başarısız test silinmedi. Son 40'lı koşu bu düzeltmeyi ve gecikmiş host iptali testini içerir.

Ağır gerçek ürün/DB koşularıyla aynı anda çalıştırılan Router prod/dev denemelerinde başlangıç/mutex veya Next.js readiness zaman aşımı oluştu. Bunlar başarı sayılmaz; `product-aogd4vfe` ve `product-mx0wgwng/previous-*` içinde korunur. Genel zaman sınırları bu yükü gizlemek için kaldırılmadı; son ürün tekrarları sırayla çalıştırılır. Baseline testlerinde beklenmeyen asyncpg bağlantı/pool girişimi **0/0**; ürün fixture'larında yalnız sentetik DB'lere gerçek bağlantı var.

Son ürün tekrar komutları:

```powershell
& .tmp/policy-test-runtime/Scripts/python.exe scripts/lifecycle/run_product_acceptance.py router-dev --reuse .tmp/lifecycle/product-mx0wgwng
& .tmp/policy-test-runtime/Scripts/python.exe scripts/lifecycle/run_product_acceptance.py router --reuse .tmp/lifecycle/product-aogd4vfe
```

Runner'ın `--reuse` seçeneği artık yalnız kendisinin oluşturduğu ve kapalı olduğu doğrulanan **Router prod veya dev** fixture'ını kabul eder. Önceki log/sonuçlar arşivlenir; kaynak motor güncellenir, sentetik veri ve kimlik korunur. Gerçek kurulum üzerinde yeniden kullanma/taşıma özelliği değildir.

Son salt okunur süreç denetimi: `.tmp/lifecycle/job-process-audit.json`, `remaining_scoped_test_processes: []`. Bu koşulardan kalan ürün/baseline test süreci yoktur. Altı dosya eşitliği ve iki depoda `git diff --check` son durumda da geçti; kullanıcı servislerine sinyal gönderilmedi.

### Devam edecek iş ve geri alma

Önceki ses örnek sayısı hatası, eski tarayıcı API'sine bağlı testler ve browser-manager timeout'u bu süreç sahipliği düzeltmesinin kapsamına alınmadı; önceki sonuçlar geçerlidir. Tek tam ürün senaryosunda aktif sohbet/araç/ses işi sırasında kapanma, gerçek manuel tarayıcı izin akışı, tam panel/CLI UI eşzamanlılığı, kurulu legacy launcher'ın yükseltmesi ve uygun izole ortamda yıkıcı OS/güç kesme senaryoları hâlâ ayrı kabul kanıtı gerektirir. macOS/Linux/Docker daemon gerçek entegrasyonu ilan edilmez.

Torun yarışı artık başarısız kabul edilip yeniden tasarlanmamalıdır. 16 Ekim 2026 tarihli sonraki kullanıcı kararıyla sıradaki oturumda Aşama 02 geliştirmesine devam edilebilir; ayrıntı ve test komutları [02 oturum devir kaydındadır](02-session-handoff.md). Kullanıcı kendi manuel denemelerini tek tuşla başlatma hazır olduğunda yapacak. Yukarıdaki açık kabul kontrolleri ertelendi, geçmedi; bu aşamanın durumu **DOĞRULAMA EKSİK** kalır. Agent çalıştırabildiği testleri kendisi yürütür; tam ürün/OS kabulü ve yayın hazırlığı ayrıca kanıtlanır.

## 04 sonrası kod doğrulaması — kuyruk gözcüsü iptal yarışı

16 Ekim 2026 kullanıcı ek kod testi istedi; Computer Use/manuel pencere kontrollerini 05 sonrasına erteledi. Ayrı C AppData worker regresyonunda API lifespan kapanışının kuyruk gözcüsünde bekleyebildiği bulundu. Tanıda gözcü `cancelling()==1` iken Redis await'i sonrası yeniden uyuyordu. `kernel/job_safety.py:queue_watchdog` bu pending iptali Redis işlemleri sonrasında açıkça `CancelledError` ile devam ettirir. Yeni deterministik regresyon bağımlılığın iptali yutmasını sınar; mevcut gerçek Redis API-lifespan testi artık kısa sürede biter.

Son sonuç **35 jobs testi + 9 alt test**, 20.24 s (`stage04-code-d4yqmp8c`); ardından **51 Hub/installer testi + 2 alt test**, 7.81 s (`stage04-code-4jqpyo3p`). Tüm scratch/Redis veri/TEMP/kimlik alanları `C:/Users/krsta/AppData/Local/Orion/lifecycle-tests` altındadır; beklenmedik DB connect/pool 0/0. Önceki timeout'lar başarı sayılmadı; tam log/komut ve hata geçmişi [04 son takip kaydındadır](04-launcher-and-autostart.md). D development depoları kurulum/taşıma/silme hedefi yapılmadı; yalnız gerekli kaynak düzeltmesi ve testi eklendi. Diğer aktif sohbet/ses/OS/legacy kabul borcu açık kalır; 01 tamamlandı denmez.

Geri alma: önce bu motorla çalışan grupları aynı kontrollü stop yoluyla kapat ve boşaldıklarını doğrula. Ardından yalnız görev diff'ini geri al; yeni iki modül, ortak motor, `native_services`, testler ve Router'ın altı vendored dosyası birlikte ele alınmalı. Başlangıçtaki kullanıcı değişikliklerini veya gerçek veri/kimliği reset etme. Yaşayan grup varken state/iptal dosyalarını elle silme. Commit/push yapılmadı.
