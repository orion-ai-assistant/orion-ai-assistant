# Aşama 02 — Kurulum kaydı ve eski kurulumları tanıma

Durum: TAMAMLANDI (kayıt/seçim/doğrulama çekirdeği ve yerel API). 16 Ekim 2026 test ve sonuç kaydı aşağıdadır; 01’in açık kabul borcu sürer.

[Ana plan](../orion-installation-lifecycle-implementation-plan.md) · [Ortak sözleşmeler](shared-contracts.md)

## Ön koşullar ve okunacaklar

Ön koşul aşamalar: [01](01-process-lifecycle.md)

16 Ekim 2026 kullanıcı kararı: 01'in çekirdek uygulaması ve son 40 yaşam döngüsü testi temel alınarak bu aşamanın geliştirmesine devam edilebilir; kalan ürün/UI/OS kabul borcu 01'de açık kalır. Kullanıcı manuel denemelerini tek tuşla başlatma akışı hazır olduğunda yapacak. Bu, 01'in tamamlanması veya yayın onayı değildir. Başlamadan [oturum devir kaydını](02-session-handoff.md) oku; testleri agent çalıştırır. Bu devir oturumunda 02 koduna veya AppData kurulumuna başlanmadı.

Ortak kararlar; bölüm 2 kurulum kaydı ve 2.1 Router doğrulaması; 01 sonuç kaydı.

Ön koşulların yalnızca durum etiketini değil, sonuç kaydını ve mevcut koddaki karşılığını doğrula. İlgisiz gelecek aşamaların tamamını okumak veya uygulamak gerekmez. Repo talimatları ayrıca geçerlidir.

## Bu aşamadaki iş ve kabul koşulları

Sürümlemeli kayıt okuyucu/yazıcı, kimlik doğrulama ve eski Router kaydı uyarlayıcısı eklenir. Kaynak depo/geliştirme modu açıkça ayrılır; yerel değişiklikleri otomatik resetleme yasaktır. Kayıtlı yol önceliklidir, tanınmış eski konumlar kontrollü keşif içindir; PATH/port sırası hedef seçmez. Bozuk kayıt korunur ve onarma sunulur; yeni boş kurulumla örtülmez.

Kabul/test: yalnız eski Router, yalnız eski Hub, ikisi birlikte, farklı kökler, iki Router adayı, geçersiz kayıt, eksik disk, yanlış ürüne işaret eden yol, Türkçe/boşluklu yol, aynı kullanıcıda development ve kullanıcı kurulumu. Router UUID/SPKI ve DB içerikleri aynı kalır. Kayda alma hiçbir veri taşımaz.

Ek test: Gözat ile seçilen gerçek/eksik/taklit/kapalı/uyumsuz Router ve mevcut TLS kimliği uyuşmazlığı. Yalnız klasör doğrulanan Router'a `sağlıklı` denmez. Keyfi klasör seçimi betik çalıştırmaz. Bağımsız Router değişimi eski DB'yi yeni DB ile birleştirmez veya eski süreci yetkisiz kapatmaz.

## Sonraki aşamaya devredilecek çıktı

Sürümlemeli kayıt şeması ve uyarlayıcılar; Router keşif/seçim/doğrulama sonuçları; bozuk/eski kayıt davranışı.

Uygulanan arayüzlerin gerçek dosya yolları, giriş noktaları, şema sürümleri ve hata sonuçları aşağıdaki sonuç kaydına yazılır. Sonraki model tasarım varsayımı yerine bu kaydı ve kodu esas alır.

## Kapsam sınırı

Yalnız bu aşamanın işini uygula. Ön koşulda eksik sözleşme varsa dar kapsamlı düzeltmeyi kaydet; başka aşamanın tüm özelliğini sessizce üstlenme. Gerçek kullanıcı verisinde hata enjeksiyonu yapma. Aşama testleri geçmeden sonraki aşamaya geçme; 16 Ekim 2026 güncel kullanıcı talimatıyla sonuçları kaydet, aşama commit’ini oluştur ve sıradaki aşamaya devam et. Ana plan tablosundaki durum da güncellenir.

## Sonuç ve test kaydı

- Durum: TAMAMLANDI.
- Başlangıç / bitiş tarihi: 16 Ekim 2026.
- Çalışılan depo: Hub; Router ve Flutter kaynakları bu aşamada değişmedi.
- Dosyalar / arayüz / test / korunma kanıtı: aşağıdaki uygulama kaydı.
- Çalıştırılamayanlar: yeni görsel klasör seçici ve manuel UI kabulü bu çekirdek/API aşamasında yok; paketli kurulum 05’e bağlı. Gerçek OS güç kesilmesi kanıtı verilmedi.
- Geri alma / sonraki aşama: aşağıda; Aşama 03 geliştirmesine uygun. Aşama 01 açık kabulü tamamlanmış sayılmaz.

## 16 Ekim 2026 uygulama kaydı

Yeni `services/shared/installation_registry.py` şema v1 otoritesidir: `Registry.read/write/update/read_backup`, `new_registry`, `component_record`, `import_legacy_installations`. Kayıt UUID’si, bileşen UUID/tür/dağıtım/yolları, bağlantı/yönetim ilişkisi, tercihler ve isteğe bağlı public kimlik yolları/SPKI/port/lifecycle kimlikleri tutulur; özel anahtar, parola, bootstrap token yazılmaz. Yeni kayıtta otomatik güncelleme tercihi saklanır; güncelleyici etkinleştirilmez. Eski Hub/Router manifestleri yerinde okunur ve korunur. Kayıt bozuksa yeni kayıt veya başka Router ile örtülmez. Aynı dizinde fsync + replace, önceki geçerli `.bak` ve süreçler arası `InstanceLock` ile güncelleme vardır. Beklenmedik alan/sürüm ve yinelenen JSON alanı reddedilir. Gerçek güç kesilmesi bu atomik yazım testleriyle geçmiş sayılmaz.

`services/shared/router_installation.py`: `inspect_router_folder`, `discover_router_candidates`, `select_router_candidate`, `registered_router_root`, `find_router_script`. Yalnız açık adaylar/tanınmış legacy konumlar; PATH veya port önceliği yoktur. `ready`, `closed-unverified`, `incompatible`, `missing-broken`, `access-denied`, `identity-mismatch` ayrıdır. Kaynak sürüm/ürün/giriş noktaları, P-256 public sertifika ve UUID/SPKI okunur; seçili klasörün kodu çalıştırılmaz. İmzasız kaynak kurulumunun tanınması yayımlayıcı imzası kanıtı değildir. Packaged dağıtım 05 imza doğrulayıcısı gelene kadar uyumsuz döner.

`services/shared/router_verification.py:verify_running_router` yalnız zaten yetkilendirilmiş DB adapter’ı ile çalışan ürünü doğrular: yaşam döngüsü OS süreç/başlangıç bilgisi ve giriş noktası, dinleyici sahipliği, loopback TLS/peer SPKI, UUID/v1 API, aynı PG cluster/user/port ve uygulama DB’sinin salt okunur tablo denetimi. Kendiliğinden başlatmaz. Yaşam döngüsündeki `postgres` yönetim DB’si ile `orion_router` uygulama şeması ayrıdır; aynı cluster doğrulanır, şema gerçek uygulama DB’sinde sınanır.

CLI: `python -m services.shared.installation_control [--registry MUTLAK_YOL] status|import-legacy|inspect-router|register-router`. `import-legacy --hub-manifest ... --router-manifest ...` ve `register-router MUTLAK_KLASOR [--distribution development]` açık kullanıcı işlemleridir. Farklı kuruluma/kimliğe sessiz değiştirme yoktur; mevcut tercihler ve kimlikler korunur. Kayıt yazmak bağlantı güvenini değiştirmez, DB birleştirmez, eski Router’ı durdurmaz veya yönetim yetkisi üretmez. Installer `/api/installation` GET ve `/api/installation/router/{inspect,register}` POST bu arayüzü kullanır; mevcut same-origin/loopback güven sınırı korunur. Görsel klasör seçici henüz eklenmedi; bu aşamanın seçim girişleri CLI ve yerel API’dir.

Hub `kernel/router_bootstrap.py` canonical kaydı eski locator’lardan önce okur; kayıt yerel Router içermiyorsa eskiye geri dönmez. Manager ve installer aynı wrapper çözümleyiciyi kullanır. `native_services.router_root` açık geliştirme scriptini kullanıcı kurulumuna yönlendirmez. Geliştirme ve kurulu bileşenler ayrı kayıtlıdır; mevcut UUID/TLS ve phase01 süreç kimlikleri yenilenmez.

### Ölçülen kanıtlar

- Ön entegrasyon seti: 54 passed, 11 subtests; DB düzeltmesi sonrası 56 passed, 13 subtests (35.28 s), `.tmp/lifecycle/results/stage02-unit.xml`. Son inceleme sonrası **61 passed, 13 subtests**, 39.87 s; `results/stage02-final.xml`. Yeni HTTP testleri, canonical doğrudan okuyucuda pin kontrolü ve aynı anahtar/farklı CN reddi dahildir.
- İlk Hub/installer regresyonu: 48 passed, 2 subtests, 30.02 s; `results/baseline-fv6x14yl-hub.{json,xml}`. Beklenmedik DB bağlantı/pool denemesi 0/0.
- Gerçek Router: `python scripts/lifecycle/run_product_acceptance.py router --reuse .tmp/lifecycle/product-aogd4vfe` son koşusu başarılı; 3 eşzamanlı start, repeat-start, iki aç/kapat, aynı UUID/SPKI ve kontrollü PG kapanışı. Yeni stage02 kanıtı süreç/TLS/API/uygulama şeması tamamlanınca `ready`. İlk iki deneme DB şemasını yönetim DB’sinde aradığı için başarısız; `previous-*` arşivleri saklandı, geçer sayılmadı.
- Gerçek AppData konum testi: `python scripts/lifecycle/run_registry_acceptance.py --appdata C:/Users/krsta/AppData/Local`; İlk `stage02-k93u1afh/result.json`, son kaynakla `C:/Users/krsta/AppData/Local/Orion/lifecycle-tests/stage02-p33ci918/result.json` başarılı. İki Türkçe/boşluklu ve ayrı kimlikli fixture, yerinde kayıt, pin uyuşmazlığı reddi, sentetik DB/ayar/model/profil/kimlik hash’leri aynı. Gerçek `%LOCALAPPDATA%` sandbox içinde başka dizine yönlendiğinden açık gerçek kullanıcı AppData yolu kullanıldı. Mevcut üç public manifestin önce/sonra hash’leri aynı; ürün süreci başlatılmadı. Bu, tam runtime/paket kurulumu değildir.
- Sandbox alt süreç token’ı restrictive Windows Job/mutex erişimini reddetti; ilk baseline `baseline-up6vw12d-hub` çalışamadı. Test izolasyonu korunarak normal kullanıcı bağlamında tekrar çalıştırıldı. Bu teknik izin sınırı ürün hatası veya geçmiş test olarak yazılmadı.

Geri alma: çalışan ürünlerde veri/state silmeden yalnız bu aşamanın kod commit’ini geri al; canonical kaydı gerçek kullanıcıda değiştirmedik. İzole AppData fixture’ı ve ignored test logları kanıt için korundu. Kullanıcıya ait başlangıç diff’leri görev commit’ine eklenmez. 01 açık ürün/UI/OS kontrolleri hâlâ açık; bu aşama yayın onayı değildir.

Son Hub/installer regresyonu: **48 passed, 2 subtests**, 45.81 s; `results/baseline-yqft4nbq-hub.{json,xml}`, DB bağlantı/pool denemesi 0/0. Son Python AST ve `git diff --check` geçti. TestClient/httpx ile aiohttp sınıf mirası deprecation uyarıları mevcut; dependency/lock güncellenmedi. 02’de ürüne yeni start/stop davranışı eklenmediğinden 01’in 40 testlik motor seti gereksiz tekrarlanmadı; gerçek Router regresyonu yukarıdadır.
