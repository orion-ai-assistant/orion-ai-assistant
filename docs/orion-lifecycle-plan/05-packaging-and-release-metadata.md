# Aşama 05 — Hazır paketler ve sürüm uyumluluğu

Durum: DOĞRULAMA EKSİK. 17 Ekim 2026 paket/güven/başlatıcı geliştirmesi uygulandı. Temiz Windows VM, üretim güven kökü ve yayın kabulü açık; aşağıdaki gerçek test sonuçları bu sınırı kaldırmaz.

[Ana plan](../orion-installation-lifecycle-implementation-plan.md) · [Ortak sözleşmeler](shared-contracts.md)

## Ön koşullar ve okunacaklar

Ön koşul aşamalar: [02](02-installation-registry-and-router-selection.md), [03](03-installation-paths-and-migration.md)

16 Ekim 2026 son kullanıcı kararı: yeni oturumda yalnız 05 geliştirmesine geçilecek. 01/03/04 açık kabul borçları korunur; Computer Use ve manuel pencere/logon kontrolleri 05 sonrasındaki kullanıcı denemesine ertelendi. 03'ün eski runtime/DB dönüşümünde 05/08'e bağımlı eksikleri ve 04'ün bu manuel kontrolleri, 05 kod/paket geliştirmesini döngüsel biçimde bloke etmez. Temiz bilgisayar/SDK'sız paket kabulü ise kanıt olmadan geçmiş sayılmaz. [Hazır oturum promptu](05-session-prompt.md).

Ortak kararlar; bölüm 2 veri/runtime düzeni; 02–03 sonuç kayıtlarındaki kayıt ve yol sözleşmeleri.

Ön koşulların yalnızca durum etiketini değil, sonuç kaydını ve mevcut koddaki karşılığını doğrula. İlgisiz gelecek aşamaların tamamını okumak veya uygulamak gerekmez. Repo talimatları ayrıca geçerlidir.

## Bu aşamadaki iş ve kabul koşulları

CI her desteklenen Windows mimarisi/profili için hazır Hub runtime, Flutter ve bağımsız Router paketlerini üretir. Yeni kurulum SDK'sız temiz VM'de denenir. Sürüm kaydı paket kimliği, tam sürüm, API aralığı, veri şeması, platform, boyut, hash, imza, hedef adres ve geri dönüş politikasını içerir. İmza güven kökü, anahtar yenileme, eskimiş metaveri ve sürüm düşürme koruması belirlenir; doğaçlama güvenlik protokolü yazılmaz. Aynı hedef sürümün değişmez paketleri indirilir. Model ve motor sürümleri ayrıdır.

Kabul/test: yalnız Hub/yalnız Flutter/yalnız Router değişimi, uyumsuz Hub–Flutter çifti, eski mobil istemci, CPU/CUDA profil seçimi, imza/hash hatası, arşivden hedef dışına çıkma, yanlış mimari, internet yokluğu, eski metaveri. Kontrol edilemedi ile güncel ayrıdır; son başarılı kontrol zamanı korunur. Paket değişiminde modeller yeniden indirilmez.

## Sonraki aşamaya devredilecek çıktı

CI paket çıktıları; güvenilen sürüm metaverisi ve uyumluluk şeması; SDK içermeyen temiz Windows VM kurulum kanıtı.

Uygulanan arayüzlerin gerçek dosya yolları, giriş noktaları, şema sürümleri ve hata sonuçları aşağıdaki sonuç kaydına yazılır. Sonraki model tasarım varsayımı yerine bu kaydı ve kodu esas alır.

## Kapsam sınırı

Yalnız bu aşamanın işini uygula. Ön koşulda eksik sözleşme varsa dar kapsamlı düzeltmeyi kaydet; başka aşamanın tüm özelliğini sessizce üstlenme. Gerçek kullanıcı verisinde hata enjeksiyonu yapma. Aşama testleri geçmeden sonraki aşamaya geçme; bu aşama bittiğinde sonuçları raporlayıp dur. Ana plan tablosundaki durum da güncellenir.

## Sonuç ve test kaydı

- Durum: DOĞRULAMA EKSİK. Geliştirme ve yerel otomatik kabul vardır; üretim/temiz VM kabulü yoktur.
- Tarih: 17 Ekim 2026. Yalnız 05; 06–08'e geçilmedi.
- Üç depo: Hub'da `scripts/release/*`, `.github/workflows/*`, ortak paket/runtime/kayıt/başlatıcı arayüzleri ve dar Hub veri yolu uyarlamaları; Router'da bağımsız vendored motor, data/state/config/prod/stop ve eski updater engeli; Flutter'da yalnız `release/package.json` paket tanımı. Kullanıcının Hub tasarım önerisi/uv.lock ve Flutter sohbet testi diff'leri korunur, commit'e alınmaz.
- Kararlı girişler: `build_package.py`, `build_bootstrap.py`, `publish_metadata.py`, `install_new.py`, `accept_packages.py`; ayrıntılı kullanım ve güven kökü işletimi [araç rehberinde](../../scripts/release/README.md). Mevcut `bootstrap/Orion.ps1`, schema-1 registry ve native lifecycle/operation lock kullanılır. Router tek başına `Run-Router.ps1 -Registry <mutlak-install.json>` ile başlar; SDK gerekmez.
- Yeni paketler: Hub `0.1.1` / API 1, Router `0.1.3` / API 2, Flutter web Client `1.0.0` / API 1. Paket sürümleri birbirinden bağımsızdır. Hub'ın Router aralığı `[2,2]`; yeni GET accounts/account-status yetenekleri zorunlu. Client ve eski mobile API 1 aralık içindedir. Router'ın Hub gereksinimi yoktur. Kaynak `pyproject.toml` Hub sürümü kullanıcı lock dosyasını değiştirmemek için korunur; imzalı paket sürümünün otoritesi release manifestidir.
- Windows 10 1903+ x64 CPU host profili desteklenir. ARM64/32-bit/başka platform ve CUDA host profili açık reddedilir. Modeller ve LLM/STT/TTS motor paketleri host paketlerinden bağımsızdır; modele CPU/CUDA otomatik ikamesi veya model indirmesi yapılmaz. Flutter paketi mevcut web dağıtımıdır; native Windows Flutter EXE üretildiği iddia edilmez.
- Güven: python-tuf 6.0.0 ile root/targets/snapshot/timestamp, tutarlı snapshot ve SHA256 hedefleri. Üretim CLI'sı en az 2-of-3 offline root politikası ister; CI imzalama anahtarları root anahtarlarından ayrıdır. Başlangıç kökü ağ kataloğundan kabul edilmez. Kök döndürmede eski ve yeni eşikler, metadata süre/versiyonları TUF tarafından denetlenir. Orion ek olarak son doğrulanmış bileşen sequence/hash bilgisini tutar; yeni imzalı metadata içindeki ürün düşürme ve aynı sequence'e farklı byte ikamesi reddedilir. Çevrimdışı/hatalı kontrolde `check-unavailable`; son başarılı kontrol zamanı korunur. `checked`, `güncel` anlamına gelmez.
- Arşiv açma: tüm üyeler önce denetlenir; Windows cihaz/ADS/traversal, NUL/backslash, case çakışması, symlink/special/encrypted entry, dosya/dizin çakışması reddedilir. Varsayılan sınır 8 GiB / 100.000 üye; mevcut hedefe yazılmaz. Hash/uzunluk yeniden doğrulanır, paket içi manifest imzalı custom metadata ile aynı olmalıdır. Başlatma/aktivasyon bu API'de yoktur.
- Kurulum durumu: schema-1 bileşen kaydının opsiyonel `package {id,sha256,length,inventory_sha256}` alanı, TUF prepare sonrasında oluşturulan `state/installed-package.json` envanterini pinler. Bu yerel kurulum provenance'ıdır, yeni bir imza protokolü değildir. Başlatıcı ve kayıtlı Router incelemesi paket dosyalarını doğrular. Kayıt dışı seçilen klasör kendi makbuzuyla güven kazanmaz. Public başlangıç kökü `<registry-parent>/bootstrap/trusted-root.json` olarak korunur.
- Data/state ayrımı: kaynak kurulumlar yerinde kalır. Paket kodu `releases/<package-id>`; Hub DB, ayarlar, browser profilleri ve `files/security` ile seed/UUID/TLS, otomatik makbuzlar ve per-account/per-Router şifreli `.manual` dosyaları data alanındadır. Router DB/persistent/.env de data alanındadır. Runtime süreç kayıtları ve Windows Job iptal dosyaları dış state alanına gider. Python bytecode yazımı paket runtime'ında kapalıdır. PostgreSQL 16.15-1 yeni staging'de UTF-8 hazırlanır, ardından son ZIP hash/TUF imzası uygulanır; aktif/eski runtime yamalanmaz. FFmpeg/Redis/Python ve bağımlılık girdileri hash sabitlidir; tedarikçi hash'i bağımsız üretici imzası sayılmaz.
- CI: Windows aday üretim akışı exact peer commit ister, ağır derlemeleri ve ürün kabulünü sırayla çalıştırır. İmzalama akışı yalnız başarılı aday koşusu + protected environment + dışarıdan sağlanan üretim root/role keys ile çalışır; otomatik anahtar üretmez. Çıktılar artifact'tır; hosting/publish/push, Startup veya otomatik güncelleme etkinleştirmesi yoktur. İmzalı bootstrap ZIP ilk tesliminde güvenilen kanal gerekir; Authenticode imzası var denmez.
- Geri alma: yalnız bu görev commit'lerini uyumlu biçimde geri al; çalışmış fixture'ı önce kayıtlı lifecycle/DB sahipliğiyle kapat. Kalıcı data/seed/kimlik/manuel tercihleri otomatik silme. Paketli eski sürüme geçiş, legacy DB/runtime dönüşümü ve tam taşıma 06/08 işlem günlüğü/kurtarma kabulüne bırakılır. Yeni kurulum başarısız olursa data/kayıt açık onarma için kalır; yerinde yeniden kurma/silme yapılmaz.
- Açık kabul: SDK'sız temiz Windows VM, gerçek Windows logon/pencere/repair manuel denemesi, üretim kökü/anahtar sağlanması, protected GitHub CI'nin gerçekten koşması ve kontrollü yayın doğrulanmadı. Yerel developer-host testi bunların yerine yazılmaz. 01/03/04 borçları, watchdog son düzeltmesi ve önceki geçen 35 + 9 / 51 + 2 kanıtları korunur. 06–08 başlamadı; yeni yetki olmadan geçilmez.

### Otomatik test kanıtı (17 Ekim)

Komutlar Hub kökünden; ağır ürün/DB/Next koşuları sırayla yürütüldü. `.tmp` ve C AppData fixture çıktıları yerel/ignored kanıttır; bu MD kararlı özetidir. Setler örtüşür, toplanmaz.

| Kontrol | Sonuç / kanıt |
| --- | --- |
| TUF ve arşiv güvenliği | `.release/tools/Scripts/python.exe -m pytest scripts/release/test_release_packages.py -q --junitxml=.tmp/stage05-security.xml`: **38 geçti**. Gerçek Ed25519 imzaları, 2-of-3 offline root + PEM dosyasından production CLI imzalama, root rotasyonu, hash, metadata ve ürün rollback/substitution, expiry/offline, desteklenmeyen profil/mimari, zararlı ZIP, cache değişim yarışı, yerel inventory/code değişimi ve süreç kaydında paket kimliği. Üretim anahtarı kullanılmadı. |
| Paketli CLI konfigürasyonu | `services/hub/.venv/Scripts/python.exe -m pytest scripts/lifecycle/test_packaged_environment.py -q --junitxml=.tmp/stage05-packaged-config.xml`: **4 geçti**, 0,26 sn; hiçbir DB bağlantısı açılmadı. Paket default'u → data override → açık process env sırası; göreli root/registry otoritesi yazma/indirmeden önce reddedilir. |
| Hub/installer son regresyon | `services/hub/.venv/Scripts/python.exe scripts/lifecycle/run_baseline.py hub`: **51 test + 2 alt test geçti**, 68,59 sn; DB/pool girişimi 0/0. `baseline-6fvc3e83-hub.json`. Önceki 43,35 sn koşusu da geçti; setler toplanmaz. |
| Router kaynak regresyonu | `run_baseline.py router`: **9 geçti**, 2,79 sn; DB/pool girişimi 0/0; `baseline-b35whktz-router.json`. Bu satır gerçek paket/DB kabulü değildir. |
| Yol/başlatıcı/runtime | Launcher, registry, installation_paths, native_model_paths, Router recognition, windows_postgres seti: **86 test + 41 alt test geçti**, 45,55 sn; `.tmp/stage05-paths-launcher.xml`. |
| Makbuz/kayıt/Router bootstrap | Launcher + registry + Router recognition + Hub router_bootstrap: **70 test + 11 alt test geçti**, 52,19 sn; `.tmp/stage05-registry-receipts.xml`. |
| Windows Job/süreç kaydı son regresyon | `run_baseline.py lifecycle`: **40 geçti**, 102,14 sn; DB/pool girişimi 0/0; `baseline-ryu1tp8f-lifecycle.json`. Son paket kimliği kaydı değişikliğinden sonra çalıştırıldı. Önceki 96,78 sn (`baseline-svux5lxv-lifecycle.json`) ve 108,70 sn koşuları da geçti; setler toplanmaz. |
| Flutter | Flutter 3.38.5 / Dart 3.10.4, `flutter pub get --enforce-lockfile`, `flutter build web --release --no-wasm-dry-run`: geçti, 60,5 sn. |
| Router dashboard | Node/Next yerel `npm run build`: Next 16.2.6 build + TypeScript + 11 statik sayfa geçti. Mevcut dependency ağacı kullanıldı; CI'de `npm ci` zorunludur. |

İlk paket fixture'ı `stage05-4_6rpywq`, embedded Python `_pth` içinde `/` ayıracıyla yanlış import kökü üretti; ürün/DB hiç başlamadı. `_pth` Windows ayıracıyla kaynak üreticide düzeltildi. `stage05-qx2gkltq` gerçek yeni Unicode Hub initdb ve Router start/TLS/FFmpeg/API/DB sağlığını geçti fakat stop'ta Windows Job cancel yolunun eski code kökünde kaldığını yakaladı. Başarı sayılmadı. Canonical kaynak motoru ile yalnız bu fixture'ın kayıtlı Job grubu ve DB'si kontrollü kapatıldı: kalan çocuk `false`, DB owner `null`; sonuç/log korunur. Düzeltme yeni paket üretimine uygulandı; eski/aktif PostgreSQL dosyaları yerinde değiştirilmedi.

`stage05-vikb5qfp` paketli bağımsız Router start/stop'u geçti. Hub API/DB/worker sağlığını tamamladı fakat Flutter alt süreci hazır kalmadığı için launcher timeout'u başarısız sayıldı; kendi Hub/Router stop komutları 0 ile çıktı. İlk tanıda başlangıç bütçesi 180 sn denenmişti. Sonraki `stage05-1q9y6x3x` logu asıl nedeni yakaladı: embedded Python `_pth` izolasyonu `PYTHONIOENCODING` değişkenini kullanmadığından Flutter sunucusu Türkçe/Çince dizini stdout'a yazarken CP1254 `UnicodeEncodeError` ile kapanıyordu. Süre artırımı çözüm olmadı ve geri alındı; mevcut 60 sn ve tüm sağlık/DB/journal kriterleri korundu. Paket üretimine stdout/stderr/stdin UTF-8 ve bytecode yazmama ayarlarını kuran, process'i yeniden başlatmayan sourceless site hook eklendi. Türkçe/Çince stdout ve gerçek paket import smoke kontrolü artık final ZIP'ten önce zorunludur. Bu ilk timeout'lar tarihsel hata kanıtıdır; sonraki geçen sonuç yerine okunmaz.

`stage05-2jzr260v` UTF-8 düzeltmesiyle gerçek birleşik Hub/Router/Flutter `ready`, Flutter health ve Router TLS bootstrap'ını geçti; ayrı DB probe'u paketli bağımsız CLI'nin data override önceliğinin eksik olduğunu yakaladı ve başarılı kabul edilmedi. Varsayılan 5445'e probe başarısız oldu; salt okunur dinleyici denetiminde o portta DB yoktu. Bütün ürün/DB kurulum hedefleri C fixture içindeydi; kendi Hub/Router stop komutları 0 ile çıktı. Yeni packaged environment dalı data ayarlarını default'lardan önce tutar, açık process değerini korur; kaynak yükleme davranışı değiştirilmez. Test probe'ları ayrıca açık fixture ayarlarıyla ve DML'den önce `Postgres.ready()` veri/süreç/sunucu eşleşmesiyle çalışır.

Ek güvenlik: doğrulanan cache dosyası yeniden açılmıyor. İmzalı uzunlukla sınırlı kopya özel, exclusive snapshot'a alınır; aynı stream hash doğrulaması ve ZIP açımına verilir. Cache'in arada değiştirildiği adversarial test geçti. Windows TEMP ACL'si başka gruplara yazma verdiğinde private executable staging reddedildi; test fixture'ları özel Orion AppData'ya alındı, ACL kontrolü gevşetilmedi. Registry parent'ı da private doğrulanır; root/registry yolu symlink/junction çözülerek bu kontrolün önünden geçirilmez.


### Son gerçek paket sonucu

`C:/Users/krsta/AppData/Local/Orion/lifecycle-tests/stage05-3g_jzpon/result.json`: **success true**. Son aday ZIP'ler gerçek TUF doğrulaması ve geçici test köküyle yeni Türkçe/Çince C AppData alanına kuruldu. System32-only PATH ve her bileşenin bundled Python'ı ile bağımsız Router start/health/stop, birleşik Hub/Router/Flutter ready, Flutter HTTP health ve gerçek Router TLS bootstrap geçti. Gerçek PostgreSQL'deki `Türkçe 中文` satırı, kullanıcı seed/kimlikleri, otomatik makbuzlar ve per-account/per-Router encrypted `.manual` tercihi stop/restart sonrasında korundu. Model sentinel'i ve bütün paket dosyalarının hash'leri değişmedi. Son cleanup Hub/Router stop exit **0/0**. Bu geliştirici bilgisayarındaki paket entegrasyonudur; `clean_machine: false`, güven kökü `ephemeral-test-only` olarak kayıtlıdır.

Son adaylar `.tmp/stage05-release-candidates` altında (ZIP'ler ignored; imzalı üretim yayını değildir):

| Paket | Byte | SHA256 |
| --- | ---: | --- |
| Hub 0.1.1 | 127866762 | `96b08060ad0b950bd3da7523fb9055252531c619d3232f0c2d8e176635a256d3` |
| Router 0.1.3 | 172723005 | `b673a1e38b8e3dbe0e35d9b570cd921ffc21ceb4c288ac043143514679eb26f0` |
| Flutter web Client 1.0.0 | 31557232 | `d9c43ac75336c0d9dcea4dfdb7139d5ade76e981b2d15df01bb65a2c67918f81` |

Aynı fixture'ın `entry-result.json` ek kontrolü de **success true**: gerçek OS PowerShell ile `bootstrap/Orion.ps1 -Background -NonInteractive`, System32-only PATH ve paketli Python kullanıldı; hazır olma, gerçek DB satırı/şifreli tercih, stop **0**, tüm kod hash'lerinin değişmezliği ve iki tercihin kapalı kalması doğrulandı. Pencere veya logon denemesi değildir (`gui_logon: false`). Gelecek CI fixture'ı birleşik start/restart için bu PowerShell girişini kullanır. Son salt okunur süreç denetiminde tüm Stage05 fixture'larından kalan ürün süreci **0**. Startup snapshot'ı gizli `desktop.ini` dahil **5/5 aynı SHA256**, `Orion.lnk` yok; otomatik güncelleme ve Windows başlangıcı açılmadı.

Son statik kontroller: Hub 32, Router 14 değişen/yeni Python kaynağı AST parse geçti; 11 vendored motor dosyası birebir eşleşti; bootstrap wrapper `--check`, üç depo diff whitespace kontrolü ve 6 MD'nin yerel bağlantıları geçti. İki CI YAML dosyası önceki parse kontrolünden geçti; GitHub üzerinde koşulmadı. Router görev commit'i `b79f85c`, Flutter görev commit'i `c8cd108`; Hub görev commit'i bu kaydı içerir. Push yapılmadı.

## 03’ten gelen runtime hazırlık girdisi

04 öncesi kullanıcı talebiyle PostgreSQL Unicode kaynak kurulumu ön koşulu dar kapsamda düzeltildi: [Unicode takip kaydı](03-unicode-postgres-followup.md). 05 bu girdiyi paket üretimine uyguladı. Sabit özetli üretici 16.15-1 arşivinden staging’de uygulama bazında UTF-8 manifestli sunucu runtime’ı hazırlanır; makbuzda üretici ve türetilmiş EXE özetleri ayrıdır. Dönüşüm son paket hash/imzasından öncedir; kullanıcıdaki aktif runtime yerinde yamalanmaz. Kaynak kurulumunun SHA256 kontrolü imzalı sürüm metaverisinin veya temiz VM kabulünün yerine geçmez. Eski runtime/DB dönüşümü ayrıca doğrulanacaktır.
