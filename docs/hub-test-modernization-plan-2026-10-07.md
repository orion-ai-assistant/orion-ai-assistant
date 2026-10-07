# Hub: 21 başarısız testi güncel sisteme uyarlama planı

Tarih: 7 Ekim 2026. Proje: `D:/krstlcm Workspace/DevProjects/orion-ai-assistant`.

## Amaç ve durum

Bu belge yeni oturumda sorunlu Hub testlerini düzeltmek için uygulanacak plandır. **Bu belge hazırlanırken test veya uygulama kaynakları değiştirilmedi; 21 test henüz düzeltilmedi.** Kullanıcının isteği, tasarım revizyonuyla birlikte devam edecek yeni oturuma ayrı bir test düzeltme belgesi bırakmaktır.

21 başarısızlık üç dosyada: bağlantı 11, adres seçimi 5, katalog 5. Aynı 21 test sağlayıcı izinleri çalışmasından önceki Hub commit'i `2956d108e84a3904995f4cc7c0bf44a59a521598` kaynaklarıyla da başarısız oldu. Dolayısıyla bu sonuçlar son özelliklerin kanıtlanmış regresyonu değildir. Testlerin önemli bölümü önceki HTTP/discovery akışına göre yazılmış; güncel HTTPS, UUID/SPKI ve kalıcı güven kaydı akışına uyarlanmamış.

Ancak amaç yalnız yeşil sonuç almak değildir. Eski testin koruduğu güvenlik veya ürün davranışı hâlâ gerekli ise o davranışı koruyan yeni test yazılmalı; uygulama onu karşılamıyorsa gerçek hata olarak düzeltilmeli. Özellikle bozuk seçim kaydı ve owner login 401 sonucu ayrıca incelenmeli.

Genel işlevsel devir: [router-hub-flutter-handoff-2026-10-07.md](<D:/krstlcm Workspace/DevProjects/orion-ai-assistant/docs/router-hub-flutter-handoff-2026-10-07.md>). Router, Hub ve Flutter commit'leri, tasarım işleri ve geçen regresyon testleri o belgede bulunur.

## Güncel sistemde esas alınacak sözleşme

- Router kimliği kalıcı UUID ve owner tarafından onaylanmış SPKI parmak izidir. IP/adres kimlik değildir.
- mDNS yalnız adres bulur; yeni Router'a kendiliğinden güven sağlamaz. Aynı PC bootstrap'ı doğrulanmış yerel kurulum kimliğini kullanır ve ayrı test edilir.
- `RouterAssociation.refresh()` önce `bootstrap_local_router()`, ardından `resolve_trusted_routers()` çalıştırır. Sonra `SecurityStore().routers()` ve kayıtlı seçimi okur. Eski `manager.check` artık yoktur; `directory.list` refresh kararının kaynağı değildir.
- Yeni Router seçimi `select()` içinde HTTPS adresi, UUID ve parmak iziyle `verify_router()` üzerinden doğrulanır. Başarılı seçim `identity_verified: true` döner. Kimlik doğrulaması ile Hub hesabı/enrollment erişimi ayrı aşamalardır.
- `get_router_base_urls()` kayıtlı seçimi güven tablosuyla eşleştirir. UUID bulunamaz veya kayıtlı pin farklıysa fail closed olur. Seçim yokken tek güvenilir Router kullanılabilir; birden fazla güvenilir Router açık seçim gerektirir.
- Güven/seçim yokken fallback `ROUTER_HOST` ve `ORION_ROUTER_TLS_PORT` (varsayılan 9443) ile HTTPS adresidir. Bu adresin üretilmesi o adrese güven verildiği anlamına gelmez; gerçek transport yine pin ister. Eski `ROUTER_BASE_URL` ve `ROUTER_PORT=20128` bu fonksiyonun öncelik sözleşmesi değildir.
- URL doğrulaması `services.shared.router_connection.validate_url()` içindedir. HTTPS origin, isteğe bağlı port ve kabul edilen `/v1` son eki; kullanıcı/parola/query/fragment/yabancı path reddedilir. `localhost` 127.0.0.1'e normalize edilir.
- Katalog `router_session()` kullanır. Gerçek transport `RouterSession` / `RouterConnector`, HTTPS ve SPKI doğrulamasını uygular, redirect takip etmez.
- Global model/voice katalog cache'i ile hesap kapsamlı izin kataloğu ayrı davranışlardır. `get_account_catalog()` için izin kararları global katalog cache'inden paylaşılmaz.

## Kaynak ve referans dosyaları

| Dosya | Kullanım |
| --- | --- |
| [test_router_connection.py](<D:/krstlcm Workspace/DevProjects/orion-ai-assistant/services/hub/tests/test_router_connection.py>) | Düzeltilecek 11 eski test. |
| [test_router_address.py](<D:/krstlcm Workspace/DevProjects/orion-ai-assistant/services/hub/tests/test_router_address.py>) | Düzeltilecek 5 eski adres testi. |
| [test_catalog_partial.py](<D:/krstlcm Workspace/DevProjects/orion-ai-assistant/services/hub/tests/test_catalog_partial.py>) | Düzeltilecek 5 katalog testi. |
| [api/router_connection.py](<D:/krstlcm Workspace/DevProjects/orion-ai-assistant/services/hub/src/orion/api/router_connection.py>) | `RouterAssociation`, `Selection`, `select`, `status`, owner Router erişimi. |
| [services/shared/environment.py](<D:/krstlcm Workspace/DevProjects/orion-ai-assistant/services/shared/environment.py>) | `get_router_base_urls()`. |
| [services/shared/router_connection.py](<D:/krstlcm Workspace/DevProjects/orion-ai-assistant/services/shared/router_connection.py>) | Seçim JSON'u, URL doğrulama, eski `selected_router_url()` yardımcı fonksiyonu. |
| [kernel/pinned_router.py](<D:/krstlcm Workspace/DevProjects/orion-ai-assistant/services/hub/src/orion/kernel/pinned_router.py>) | TLS/SPKI transport, `verify_router`, güvenilir Router adres çözümlemesi. |
| [kernel/local_security.py](<D:/krstlcm Workspace/DevProjects/orion-ai-assistant/services/hub/src/orion/kernel/local_security.py>) | SQLite güven deposu ve adres/pin güncelleme. |
| [kernel/router_models.py](<D:/krstlcm Workspace/DevProjects/orion-ai-assistant/services/hub/src/orion/kernel/router_models.py>) | Katalog refresh/cache/timeout/concurrency davranışı. |
| [kernel/router_accounts.py](<D:/krstlcm Workspace/DevProjects/orion-ai-assistant/services/hub/src/orion/kernel/router_accounts.py>) | `catalog_credentials`, enrollment ve hesap kapsamlı Router çağrıları. |
| [api/owner_routes.py](<D:/krstlcm Workspace/DevProjects/orion-ai-assistant/services/hub/src/orion/api/owner_routes.py>) | Owner login, credential fingerprint ve admin/people scope kontrolü. |
| [test_router_selection_security.py](<D:/krstlcm Workspace/DevProjects/orion-ai-assistant/services/hub/tests/test_router_selection_security.py>) | Yeni Router'da açık pin gerekliliği, bilinen Router pin kullanımı ve canlı doğrulama hatası için örnek. |
| [test_router_bootstrap.py](<D:/krstlcm Workspace/DevProjects/orion-ai-assistant/services/hub/tests/test_router_bootstrap.py>) | Geçici security dizini ve gerçek yerel TLS fixture örneği. |
| [test_local_owner_onboarding.py](<D:/krstlcm Workspace/DevProjects/orion-ai-assistant/services/hub/tests/test_local_owner_onboarding.py>) | Owner login fixture'ı için incelenecek mevcut referans. |

## Ortak fixture düzeltmeleri

1. Her test geçici dizin kullansın. `ORION_SECURITY_DIR`, `ORION_ROUTER_CONNECTION_FILE` ve gerektiğinde bootstrap'ın baktığı `LOCALAPPDATA` yalnız bu dizine yönelsin. Gerçek owner SQLite veritabanı, installer registry, Redis veya Router bağlantı JSON'u okunup yazılmasın.
2. Unit testte ağ ve bootstrap açıkça mock'lansın. Gerçek TLS integration testinde yalnız fixture HTTPS sunucusu çalışsın. Testlerin makinede Router kurulmuş olmasına göre sonucu değişmemeli.
3. Mock, sembolün **kullanıldığı namespace** üzerinde kurulsun. Refresh için `orion.api.router_connection.resolve_trusted_routers` ve `SecurityStore`; içeriden import edilen bootstrap için `orion.kernel.router_bootstrap.bootstrap_local_router`. Discovery local identity için `orion.kernel.router_bootstrap.local_router_identity` ayrıca izole edilsin.
4. HTTPS fixture kaydı en az `id`, `name`, `url`, `fp` içersin. Unit fixture'da sabit sahte pin kullanılabilir; gerçek TLS testinde pin fixture sertifikasının SPKI'sinden türetilsin.
5. Patch'ler `addCleanup` / `addAsyncCleanup` ile geri alınsın. Global `local_app.state.panel_app.state` değişiklikleri eski değerlerine döndürülsün. Task'lar iptal edilip await edilsin; sonraki testte açık socket/task kalmasın.
6. Testler hem tek tek hem bir arada koşsun; sıra ve gerçek makine ayarına bağımlılık kontrol edilsin. Eski beklentiyi değiştirmek tek başına yeterli değildir; assertion yeni davranışın güvenlik/ürün amacını sınamalı.

## 11 bağlantı testinin tek tek dönüşümü

Eski dosyada HTTP fixture `RECORD`, `manager.check = AsyncMock`, `directory.list` ve yalnız `connection_path` patch'i birlikte kullanılıyor. Bu set refresh'i artık izole etmiyor. Aşağıdaki testlerin bazıları doğru sınırı sınamak için farklı testlere bölünebilir; toplam test sayısının tam 21 kalması şart değildir.

| Eski test | Güncel testin koruyacağı davranış / gerekli değişiklik |
| --- | --- |
| `test_single_router_is_checked_and_uuid_persists_for_worker` | Tek **güvenilir** Router, seçim yokken UUID/pin ile kaydedilir ve worker aynı güvenilir HTTPS adresini kullanır. Bootstrap/resolver mock, geçici SecurityStore ve gerçek seçim JSON'u. `manager.check` assertion'ı kaldırılır; HTTPS kimlik kontrolü select/bootstrap testlerinde ayrıca korunur. |
| `test_multiple_routers_are_not_automatically_selected` | İki güvenilir kayıt + seçim yokken refresh seçim yapmaz ve JSON oluşturmaz; adres fonksiyonu açık seçim ister. Yalnız iki keşif ilanı da kendi başına güven/otomatik seçim sağlamaz; bunun için ayrı discover/select testi yazılabilir. |
| `test_existing_unique_hostname_migrates_to_its_same_uuid` | Eski env hostname'inden otomatik UUID/güven üretme beklentisi kaldırılır. Kayıtlı UUID ve pinin hostname/IP güncellemesinde korunması sınanır. Legacy migration ürün gereksinimi varsa ayrıca tanımlanır; sırf test geçsin diye TOFU eklenmez. |
| `test_failed_api_check_does_not_create_an_association` | Doğru hedef `select()` / `verify_router` başarısızlığıdır. UUID veya pin doğrulaması başarısızsa 409; `trust_router` ve `write_connection` çağrılmamalı, mevcut seçim değişmemeli. Enrollment erişim başarısızlığı ise ayrı `access_ready` testidir; doğrulanmış kimliği yok saymayabilir. |
| `test_saved_router_updates_ip_but_never_changes_uuid` | Aynı UUID/pin için güven deposunun adresi güncellenince worker yeni HTTPS adrese gider; başka UUID'ye veya pine geçmez. Resolver fixture'ı adresi aynı kayıtta güncellesin. `refresh()` dönüşü ve kalıcı seçim kimliği kontrol edilsin. |
| `test_offline_saved_router_and_expired_endpoint_fail_closed` | Güvenilir Router'ın offline olması başka Router/env fallback'ine geçişe yol açmaz. Güncel resolver offline olunca eski adresi tutabilir; `url=None` beklenmesi zorunlu değil. Gerçek istek başarısızlığı ve aynı kimliğin korunması sınansın. Eski 75 saniye TTL'i `selected_router_url()` davranışıdır; yeni güven yolu ile karıştırılmasın. TTL ürün şartı hâlâ isteniyorsa ayrı değerlendirme gerekir. |
| `test_conflicting_uuid_is_not_selected` | Çakışan discovery kaydı `select()` tarafından 409 ile reddedilir; `verify_router`, trust ve seçim yazımı gerçekleşmez. Refresh artık discovery conflict bayrağına bakmıyor; test doğru endpoint'e taşınır. |
| `test_explicit_environment_and_manual_selection_are_preserved` | İki ayrı test: kayıtlı UUID/pin env değişse de korunur; yeni manuel seçim HTTPS + `manual_id` + pin doğrulaması gerektirir. HTTP manual ve UUID'siz güven üretme beklentisi kaldırılır. |
| `test_corrupt_selection_is_not_deleted_or_replaced` | **Potansiyel gerçek davranış sorunu; aşağıdaki bölüm öncelikli.** Bozuk dosya geçici dizinde oluşturulur; 0/1/2 güvenilir Router ile denenir. Güncel refresh exception'ı yutup tek trusted kaydı seçebilir. Eski testi sadece buna razı olacak şekilde değiştirme; fail closed gereksinimini kararlaştır ve gerekiyorsa kaynak düzelt. |
| `test_owner_selection_requires_admin_scope_and_mount_stays_local` | Login doğrulama/fingerprint ve Redis fixture'ı tutarlı kurulmalı. Anonim ve people scope Router yönetimini açamaz; admin scope açabilir. Başarılı doğrulanmış seçim `identity_verified=true` bekler. Kötü Origin ve yerel mount sınırı korunur. Global app state geri alınır; live bootstrap/enrollment kullanılmaz. |
| `test_real_identity_and_api_key_checks_happen_in_order` | Artık olmayan `get_runtime_settings` / `manager.check` patch'leri ve HTTP sunucusu kaldırılır. Geçici HTTPS sunucusunda `/api/v1/tls/identity` ve gerçek SPKI kullan. Yanlış pin/UUID reddedilsin; pin hatasında sunucu HTTP handler'ına auth verisi ulaşmasın. Sonraki Hub enrollment/account çağrıları ayrı fixture'la test edilir; eski `/v1/models` + admin bearer sırası yeni sözleşme diye korunmaz. |

### Owner login 401 için uygulanacak inceleme

`owner_routes.login()` `valid_admin_key()` kullanır, session kaydına `fingerprint()` yazar; middleware daha sonra aynı credential fingerprint'i karşılaştırır. Eski test yalnız `ADMIN_API_KEY` env değerini değiştiriyor. Runtime credential/cache veya farklı ayar kaynağı varsa bu yeterli olmayabilir.

- `api/security.py` içindeki credential çözümlemesi ve fingerprint cache'i incelensin. Gerçek sırrı terminale yazdırma.
- Login handler'ının kullandığı `valid_admin_key` ve fingerprint sınırları test sırrıyla tutarlı mock'lansın veya geçici gerçek ayar fixture'ı kurulsun. Middleware'in fingerprint okuması aynı değer olmalı.
- Hatalı şifrenin 401 dönmesi, login olmadan Router erişiminin reddi ve people scope'un admin olamaması ayrıca assert edilsin. Her password'a true dönen mock ile anlamlı auth testleri kaldırılmasın.
- Router `status()` çağrısının `ensure_connection` / `request_router` bağımlılıkları mock'lansın; scope testinin gerçek enrollment'a çıkması engellensin.

### Bozuk seçim kaydı için uygulanacak inceleme

Güncel refresh, `read_connection()` `RuntimeError` verince `previous=None` ile devam ediyor. Tek güvenilir Router varsa aynı bozuk dosya üzerine yeni seçim yazma olasılığı var. Bu kaynak davranışı görülüyor; bu oturumda düzeltme veya canlı etki doğrulaması yapılmadı.

Dosyanın hiç olmaması ile dosyanın bozuk olması aynı senaryo değildir. Eski testin “bozuk kayıt sessizce değiştirilmesin” amacı hâlâ gerekli olabilir. Önce izole testle 0/1/2 trusted varyantı doğrulansın. Fail closed hedefi korunacaksa bozuk kayıt üzerine otomatik yazım yapılmamalı; refresh/status kontrollü hata verebilmeli ve test buna göre düzeltilmeli. Ürün gereksinimi belirlenmeden yalnız exception assertion'ını kaldırma.

## 5 adres testinin tek tek dönüşümü

`setUp` içindeki `selected_router_url` patch'i yeni `get_router_base_urls()` yolunu etkilemiyor. Geçici SecurityStore ve seçim dosyası kullanılmalı veya `read_connection` ve `SecurityStore` gerçek lookup sınırlarında mock'lanmalı.

| Eski test | Güncel test / beklenti |
| --- | --- |
| `test_explicit_hostname_url_is_used_without_ip_configuration` | Güvenilir hostname kaydı ve aynı UUID/pin seçiminde HTTPS hostname kullanılır. `_router_urls('/v1/chat/completions')` URL'yi doğru birleştirir. Env HTTP hostname'i pinli Router'a dönüştürülmez. |
| `test_explicit_url_overrides_legacy_host_and_port` | Kayıtlı güvenilir UUID/pin adresi legacy env değerlerinden önce gelir. Seçim kaldırılırsa yeni fallback ayrı test edilir. `ROUTER_BASE_URL`'ye eski önceliği geri verme. |
| `test_existing_native_and_docker_defaults_remain_usable_before_mdns_setup` | Güven/seçim yok: native `ROUTER_HOST=router` → `https://127.0.0.1:9443`; Docker → `https://router:9443`. `ORION_ROUTER_TLS_PORT` override ayrıca test edilir. Adres üretimini gerçek güvenli bağlantı kuruldu diye yorumlama. |
| `test_custom_hostnames_are_already_supported_by_legacy_configuration` | Güven/seçim yokken `ROUTER_HOST=orionrouter.local` ve TLS portundan HTTPS hostname üretilmesi sınanır. Kaydedilmiş trusted hostname varyantı da kapsanır. |
| `test_bad_or_sensitive_urls_are_rejected_without_printing_credentials` | `ROUTER_BASE_URL` artık okunmayan giriş olduğundan negatif URL assertion'ları `validate_url()` ve `Selection` sınırına taşınır. HTTP, file, credentials, query, fragment, bozuk port ve yabancı path reddedilsin. Hata **metni/yanıtı/logu** sır içeriyor mu kontrol et; Pydantic exception nesnesinin ham input gösterimiyle güvenli API yanıtını karıştırma. |

Eksik güncel senaryolar eklenmeli: seçilmiş UUID trust'ta yok; seçilmiş fp güven kaydıyla farklı; iki trusted var ve seçim yok; tek trusted ve seçim yok; seçim başka Router'a kendiliğinden geçmiyor. `validate_url('https://localhost:9443/v1/')` normalizasyonu ayrıca sınanabilir.

## 5 katalog testinin tek tek dönüşümü

Temel hata: `catalog.aiohttp.ClientSession` patch'i `catalog.router_session()` çağrısını karşılamıyor. Fake Session doğrudan `orion.kernel.router_models.router_session` üzerinden verilmeli. `_fetch_section()` içeriden `orion.kernel.router_accounts.catalog_credentials` import eder; bu async sınır da izole edilmeli. Örneğin fixture prefix `/api/v1/hubs/catalog/` ve sahte, sır olmayan bir auth header dönebilir. Fake `get` helper'ın `headers` ve context-manager davranışı gerçek call şekline uymalı.

URL fixture'ları `https://router.test:9443` ve gerekirse ikinci HTTPS origin olsun. Network unit testte fake session'a gider; pin/HTTPS davranışını burada bypass edip geçmiş sayma, gerçek transport testleri ayrıca çalışsın. Aynı session'a birden fazla async context girişinin fixture'da desteklendiği kontrol edilsin.

Global cache fixture'ında `_catalog_cache`, `_catalog_cached_at`, `_model_catalog_cache`, `_model_cached_at`, `_model_refresh_task`, `_catalog_sections`, `_catalog_generation` testler arasında izole edilsin. Açık refresh task'ı temizlemeden yalnız değişkeni None yapmak yeterli değildir. `clear_catalog_cache()` generation davranışı test ediliyorsa onu ayrıca koru.

| Eski test | Korunacak anlamlı assertion |
| --- | --- |
| `test_unreachable_local_tts_does_not_block_other_catalogs` | Local TTS timeout olurken models ve cloud voices gelir; local unavailable işaretlenir ve local bölüm refresh başına yalnız bir kere denenir. Fake credential/session sınırları güncel olsun. Geniş wall-clock sınırı yerine async timeout/event ile deterministik ilerleme mümkünse tercih edilir. |
| `test_outage_preserves_previous_voices_and_recovers` | İlk local ses listesi cache'e gelir; outage eski listeyi korur; recovery yeni sesi getirir ve unavailable temizlenir. Üç refresh aynı izole test kapsamındadır. |
| `test_cold_model_save_never_requests_voice_services` | `validate_model_updates` soğuk cache'te yalnız models + model-groups çağırır. Voice ve local-TTS çağrıları sıfır olmalı. |
| `test_poll_and_first_save_share_model_request` | Concurrent model polling ve ilk save tek in-flight model refresh paylaşır. Her bölüm bir kere çağrılır. Event/barrier ile iki caller'ın gerçekten eşzamanlı olduğu güvenceye alınsın. |
| `test_validation_finishes_while_voice_refresh_is_pending` | Voice event ile bekletilirken model validation tamamlanır ve full refresh pending kalır. `voice_started.wait()` da sınırlı timeout ile beklenmeli; test sonsuza kadar asılı kalmamalı. Finally'de event release, task cancel/await ve session cleanup kesin yapılmalı. |

Yeni izin sistemine ait `get_account_catalog()` testleri bu global katalog testlerine karıştırılmasın. Kişinin account credentials'ı kullanması, global admin/cache fallback olmaması ve izin değişikliğinin sonraki çağrıda görünmesi `test_router_accounts.py` ile birlikte doğrulansın.

## Uygulama sırası

1. Üç repo Git durumunu ve genel devir belgesini oku. Bu işin kaynak değişiklik kapsamı öncelikle Hub; tasarım revizyonunu bu test güncellemesine karıştırma.
2. Geçici security/connection/bootstrap ve credential fixture'larını kur. Gereksiz ortak helper soyutlaması ekleme; mevcut bootstrap/selection testleri örnek alınabilir.
3. Katalog 5 testini güncel session/credential sınırıyla düzelt, tek başına çalıştır.
4. Adres 5 testini yeni trust/selection/TLS sözleşmesine dönüştür, negatif URL testlerini doğru sınırda tut.
5. Bağlantı 11 testini refresh/select/transport/owner alt sorumluluklarına göre uyarlayıp böl. Owner 401 ve bozuk kayıt incelemesini yap.
6. Fixture düzeltmesine rağmen gerekli davranış başarısızsa gerçek kaynak hatasını en küçük değişiklikle düzelt; önce regresyon testi, sonra kaynak düzeltmesi. Değişiklik gerekçesini raporla.
7. Üç dosyayı beraber, ilgili mevcut güvenlik/hesap regresyonlarıyla birlikte tekrar çalıştır. Sonunda başarısız/skip sayısını açıkça belirt; değişen test sayısını raporla.

## Çalıştırma ve kabul

Mevcut ayrı Python 3.11 test ortamı `.tmp/policy-test-runtime` kullanılabilir. Eski `services/hub/.venv` eksik Python 3.14 kurulumuna bağlıydı; onu bu test işi için kendiliğinden değiştirme. Örnekler Hub repo kökünde:

```powershell
$env:PYTHONPATH = 'D:/krstlcm Workspace/DevProjects/orion-ai-assistant/services/hub/src;D:/krstlcm Workspace/DevProjects/orion-ai-assistant'
& 'D:/krstlcm Workspace/DevProjects/orion-ai-assistant/.tmp/policy-test-runtime/Scripts/python.exe' -m pytest services/hub/tests/test_catalog_partial.py services/hub/tests/test_router_address.py services/hub/tests/test_router_connection.py -q
```

Bu komut **fixture'lar izole edildikten sonra** kullanılmalı. Eski bağlantı testleri şu anda gerçek trust/bootstrap durumunu dışarıda bırakmıyor; başlangıç başarısızlıklarını yeniden kaydetmek için çalıştırılırsa önce bütün security/bootstrap yollarını geçici hedeflere yönlendir.

İlgili regresyon seti:

```powershell
& 'D:/krstlcm Workspace/DevProjects/orion-ai-assistant/.tmp/policy-test-runtime/Scripts/python.exe' -m pytest services/hub/tests/test_router_accounts.py services/hub/tests/test_router_provider_auth.py services/hub/tests/test_router_selection_security.py services/hub/tests/test_router_api_key_masking.py services/hub/tests/test_router_bootstrap.py services/hub/tests/test_model_defaults.py -q
```

Owner login/yerel yönetim kaynaklarına dokunulursa ilgili owner onboarding ve erişim sınırı testleri de çalıştırılmalı. Gerçek TLS pin sırasını sınayan yeni integration testi varsa çalıştırıldığı ayrıca belirtilmeli; unit mock testine “gerçek TLS doğrulandı” denmemeli.

Kabul şartları:

- Eskimiş fixture/sözleşme uyumsuzluğu giderilir; güncel karşılıklar başarısız değil, gerçekten çalışarak geçer. Skip/xfail, assertion silme veya test devre dışı bırakma düzeltme sayılmaz.
- Aynı sonuç testler tek tek ve beraber çalışırken alınır; gerçek kullanıcı trust/JSON/Redis verisi okunmaz veya değiştirilmez.
- Kimlik doğrulama, UUID/pin eşleşmesi, revoke/fail closed, scope ve Origin sınırları korunur. Yanlış kimlik/pin/şifre için negatif testler kalır.
- Bozuk kayıt ve offline davranışında neyin eski beklenti, neyin gerçek uygulama hatası olduğu raporlanır.
- Global katalog concurrency/cache/partial-outage anlamı ve account kataloğunun uncached izin davranışı birlikte korunur.
- Sırlar response/log'a yazılmaz; TLS/pin kaldırılmaz, trust-all/TOFU/LAN HTTP geri eklenmez.
- Son rapor eski 21 testin hangi yeni testlere dönüştüğünü, yapılan kaynak düzeltmelerini ve geçen/başarısız/skip sayılarını içerir.

## Yeni oturuma aktarılacak görev

> `D:/krstlcm Workspace/DevProjects/orion-ai-assistant/docs/hub-test-modernization-plan-2026-10-07.md` belgesini oku ve burada listelenen 21 eski Hub testini güncel HTTPS + UUID/SPKI + güven deposu + account/catalog sistemine uyarlayarak düzelt. Mock'ları doğru sınırda kur, tüm kalıcı/veri/ağ bağımlılıklarını izole et. Eski güvenlik amaçlarını kaybetme; özellikle bozuk seçim kaydı ve owner login 401 davranışını incele. Gerekli davranışı karşılamayan gerçek kaynak hatası varsa regresyon testiyle birlikte düzelt. İlgili güvenlik/hesap testlerini tekrar çalıştır ve son test sonuçlarını açıkça raporla. Genel işlevsel devir belgesi de aynı docs dizinindedir. Tasarım revizyonunu ayrıca vereceğim görseller ve talimatlarla yapacağız.
