# Orion Router / Hub / Flutter — oturum devri, 7 Ekim 2026

## Yeni oturumun amacı ve kullanıcının son kararı

İşlevsel uygulama üç projede tamamlandı ve aşağıdaki commit'lerle kaydedildi. Kullanıcı eklenen arayüzlerin yalnız yazı ve butonlardan oluştuğunu, mevcut Orion Router tasarımıyla uyumlu olmadığını belirtti. **Sıradaki çalışma tasarım ve kullanıcının tek tek anlatacağı davranış revizyonlarıdır.** Kullanıcı yeni oturumda ekran görüntüleri ve talimatlar verecek. Bu devir oturumunda tasarım revizyonuna başlanmadı.

Yeni oturumda önce bu belgeyi, geçerli Git durumunu ve uygulanacak `AGENTS.md` dosyalarını oku. Kullanıcının yeni görsellerini ve açıklamalarını mevcut işlevlerle eşleştir. Önceki oturumdaki tasarımı nihai veya görsel olarak doğrulanmış kabul etme. Router'daki yerleşik bileşenleri, sayfa düzenini, renkleri, boşlukları, dialogları ve dil sistemini temel al. Yetkilendirme ve sır saklama davranışlarını tasarım değişikliği sırasında koru.

Bu belge 7 Ekim'deki sağlayıcı anahtarı izinleri, Hub görünümü ve kullanım istatistikleri çalışmasını kapsar. Daha eski QR, ses, cihaz eşleştirme ve TLS çalışmalarının ayrıntıları için aynı dizindeki `faz6-oturum-devri-2026-10-05.md` ve `faz6-oturum-devri-2026-10-06.md` belgeleri ayrıca okunabilir; onların eski worktree bilgileri bu çalışmanın checkout'ları değildir.

## Checkout ve commit bilgileri

Üç çalışma alanı da mevcut `main` dalındadır. Yeni worktree açılmadı. Başlangıçta üç Git çalışma ağacı temizdi. Önceki talimat commit atmamaktı; kullanıcının son açık isteğiyle aşağıdaki uygulama commit'leri oluşturuldu. Remote'a push yapılmadı.

| Proje | Checkout | Uygulama commit'i | Başlangıç commit'i |
| --- | --- | --- | --- |
| Router | `D:/krstlcm Workspace/DevProjects/orion-router` | `f3ca84919b4207549e13b5838553beba1e52ab3e` | `21e7b80efd544cf3ee472ea278c5b7a950e7dce3` |
| Hub | `D:/krstlcm Workspace/DevProjects/orion-ai-assistant` | `cb29a19cfdfaf66e53ffd8ce4df1296f66e2a15c` | `2956d108e84a3904995f4cc7c0bf44a59a521598` |
| Flutter | `D:/krstlcm Workspace/DevProjects/orion-ai-assistant-flutter` | `e647a5748013a9c829787c3d8f433439c0c1e67c` | `3a1c45a075553b0b8952933709d6bd0be724bbcd` |

Commit başlıkları:

- Router: `feat: add provider key access policies and usage reporting` — 26 dosya, 1022 ekleme / 141 silme.
- Hub: `feat: expose account-scoped Router summaries and catalogs` — 5 dosya, 162 ekleme / 11 silme.
- Flutter: `feat: show Router provider access summaries in settings` — 2 dosya, 243 ekleme / 9 silme.

Bu devir belgesi uygulama commit'lerinden sonra ayrı bir dokümantasyon commit'ine alınır. Kendi commit hash'i belge içinde kendine referans olarak tutulmaz; Hub Git geçmişinden bulunabilir.

## Korunacak ürün ve güvenlik kararları

- İsimler yalnız görüntüleme içindir. Yetki, kişisel kayıtlar ve raporlar Hub UUID, sanal anahtar UUID ve sağlayıcı anahtarı UUID üzerinden bağlanır. Aynı kullanıcı adı farklı Hub'larda, aynı Hub adı farklı UUID'lerde olabilir. Aynı sağlayıcı etiketi de yasaklanmaz.
- Görünen sahip etiketi `Hub adı [kısa UUID] · kullanıcı adı` biçimindedir. Birleşik başlık veritabanındaki kullanıcı `name` alanına yazılmaz. Bağımsız, `hub_id` olmayan sanal anahtarlar devam eder.
- Yazılmış anahtarın ham değeri listelere, yanıtların sır görüntüleme alanlarına veya loglara konmaz. Arayüzde yeniden gösterme/kopyalama kaldırıldı; yönetici yeni değer yazarak değiştirebilir. Testler redaksiyon ve doğrulama hatalarındaki sır sızıntısını da kapsar.
- Hesap, Hub ve Router yönetici şifreleri ayrı kavramlardır. Flutter sağlayıcı işlemleri için Router yönetici şifresi istemez ve göndermez.
- Şifre değişikliğinde mevcut Hub/sanal anahtar yetkileri gereksiz iptal edilmez. Yeniden kayıt bütçeyi, kullanımı veya yöneticinin pasifleştirme kararını sıfırlamaz.
- “Tüm Hub erişimlerini iptal et”, yeni PAKE, yeni ağlar arası keşif, audit log veya Orion Panel'e yeni kimlik/yetki durum arayüzü eklenmedi.
- Önceden kurulu HTTPS/WSS, kalıcı UUID/SPKI ve güven kaydı akışı korunur. Başarısız eski testleri geçirmek için LAN HTTP, pin atlama veya güveni kendiliğinden geri verme eklenmemeli.

## İki katmanlı izin: uygulanan davranış

İzin sağlayıcı adına değil, **tekil sağlayıcı anahtarına** bağlıdır. Örneğin bir OpenRouter anahtarına erişim başka OpenRouter anahtarlarına erişim sağlamaz.

| Katman | Alan / değerler | Anlam |
| --- | --- | --- |
| A, ortak sağlayıcı kaydı | `scope = all / selected` | Bu anahtarı hangi sanal anahtarların kullanabileceği. `all` gelecekte oluşanları da kapsar. `selected` ve boş seçim kimseyi kapsamaz. |
| B, sanal anahtar | `provider_key_mode = unrestricted / only_selected` | Bu hesabın hangi upstream anahtarlarını kullanabileceği. Varsayılan `unrestricted`. `only_selected` yeni eklenen sağlayıcı anahtarlarına otomatik açılmaz. |
| Kişisel sağlayıcı kaydı | A katmanı sahibiyle sınırlı | Başkasına açılamaz; B katmanı sahibinin kullanımını ayrıca daraltabilir. |

Efektif erişim A **ve** B, aktif sağlayıcı kaydı, aktif sanal anahtar ve varsa aktif Hub şartlarını birlikte gerektirir. B ekranı A'yı genişletemez. Kurallar her rota seçiminde ve upstream denemesi öncesinde veritabanından tekrar okunur; izin sonucunun önbelleği yoktur.

Chat/stream, embeddings, STT, TTS, dosya yükleme ve hesap model kataloğu bu kurallara bağlandı. Retry/fallback reddedilmiş anahtarı yeniden kullanmaz; izinli başka rota varsa onu kullanabilir. İzinli rota yoksa `Bu model için erişiminiz yok, Router yöneticisine başvurun.` mesajı üretilir. HTTP API yollarında ilgili 403 hatalarının genel 500'e dönüşmesi engellendi; chat stream hata metnini akış üzerinden gösterebilir.

Bir kullanıcı aynı sağlayıcı için kişisel kayıt oluşturduysa hatalı, okunamayan veya pasif kişisel anahtar yerine o sağlayıcının ortak anahtarına sessiz geçiş yapılmaz. Kullanıcı pasif kaydı yeniden yazarak aktifleştiremez veya silerek yöneticinin kararını aşamaz. Yönetici yeni değer yazabilir, etkin/pasif yapabilir veya silebilir. Yönetici yeni değer yazınca `admin_updated` işareti görünür; sahibi yeni değer yazınca güncelleme bildirimi temizlenir, pasiflik kaldırılmaz.

Yerel sağlayıcılar upstream anahtarı tüketmez. Admin/system çağrıları ortak havuzu kullanabilir; eski config/ortam anahtarı fallback'i yalnız system bağlamında kalır. Kullanıcı çağrısı bu yola düşmez. Gelen Router kimlik doğrulama sırrı upstream sağlayıcısına gönderilmez. Mevcut kimlik doğrulama önbelleğinin 5 saniyelik TTL'i ayrı bir mekanizmadır; Hub/sanal anahtar güncellemesinde temizlenir ve sağlayıcı izinleri bu önbelleğe alınmaz.

## Router: dosya haritası

Aşağıdaki yollar `D:/krstlcm Workspace/DevProjects/orion-router` köküne aittir.

| Dosya | Ne değişti / amacı |
| --- | --- |
| [database/manager.py](<D:/krstlcm Workspace/DevProjects/orion-router/database/manager.py>) | İzin alanları ve ilişkiler, kişisel kayıtlara sabit ID/durum, geçmişte kimlik snapshot'ları, upstream kaynak/birim alanları, tek seferlik legacy aktarımı, log redaksiyonu. |
| [core/key_policy.py](<D:/krstlcm Workspace/DevProjects/orion-router/core/key_policy.py>) — yeni | Ortak/kişisel adaylar, A ve B kesişimi, kuralı atomik değiştirme, her denemede kontrol, gerçek seçilen upstream ID/kaynağı için ContextVar. |
| [core/secret_guard.py](<D:/krstlcm Workspace/DevProjects/orion-router/core/secret_guard.py>) — yeni | İstek kapsamındaki sırları ve hassas JSON alanlarını maskeleme; log mesajı/traceback redaksiyonu; doğrulama yanıtlarından sır içerebilen input/context'i çıkaran `SecretRoute`. |
| [core/router/routing_services.py](<D:/krstlcm Workspace/DevProjects/orion-router/core/router/routing_services.py>) | Kullanıcı anahtar havuzunu izinli kayıtlarla sınırlar; kişisel kaydın ortak fallback'i engellemesi; local/system ayrımı. |
| [core/dependencies.py](<D:/krstlcm Workspace/DevProjects/orion-router/core/dependencies.py>) | Router auth sırrını redaksiyon bağlamına kaydeder, eski upstream seçim bağlamını temizler. |
| [core/router/runners/chat.py](<D:/krstlcm Workspace/DevProjects/orion-router/core/router/runners/chat.py>) | Her chat/stream denemesinde izin, izinli fallback, çıktı/hata sır redaksiyonu; auth-header fallback'i kaldırıldı. |
| [core/router/runners/embeddings.py](<D:/krstlcm Workspace/DevProjects/orion-router/core/router/runners/embeddings.py>) | Deneme öncesi izin ve gerçek upstream seçimi; eksik kullanım bilgisi sahte sıfır yapılmaz. |
| [core/router/runners/stt.py](<D:/krstlcm Workspace/DevProjects/orion-router/core/router/runners/stt.py>) | İzin kontrolü; süre tokenmış gibi hesaplanmaz, gerçek provider tokenları varsa korunur, süre ayrı tutulur. |
| [core/router/runners/tts.py](<D:/krstlcm Workspace/DevProjects/orion-router/core/router/runners/tts.py>) | İzin kontrolü ve eksik usage ayrımı; karakter/token birimi telemetry'ye taşınır. |
| [core/router/telemetry.py](<D:/krstlcm Workspace/DevProjects/orion-router/core/router/telemetry.py>) | Seçilen upstream ID/kaynak bağlamını log task'larına aktarır; usage birimi/miktarı ve bilinmeyen maliyet ayrımı. |
| [dynamic_router.py](<D:/krstlcm Workspace/DevProjects/orion-router/dynamic_router.py>) | Dosya yüklemesinde aynı yetkilendirme, seçilen anahtar telemetry'si ve sonuç/hata redaksiyonu. |
| [api/admin.py](<D:/krstlcm Workspace/DevProjects/orion-router/api/admin.py>) | Hub sahip metadatası, sır döndürmeyen sanal anahtar oluşturma, cache invalidation, log pagination/detail redaksiyonu, yeni yönetim router'ı, global token istatistiğinde birim ayrımı. |
| [api/key_management.py](<D:/krstlcm Workspace/DevProjects/orion-router/api/key_management.py>) — yeni | Yöneticiye Hub aktivasyonu, A/B izinleri, kişisel kayıt yönetimi ve bütün geçmiş kullanım API'si. |
| [api/hubs.py](<D:/krstlcm Workspace/DevProjects/orion-router/api/hubs.py>) | Kayıt yenilemede durum/bütçe korunur; hesap kapsamlı sayı özeti, kişisel yönetim ve katalog; LAN dashboard adresi; güvenli validation. |
| [api/embeddings.py](<D:/krstlcm Workspace/DevProjects/orion-router/api/embeddings.py>), [api/files.py](<D:/krstlcm Workspace/DevProjects/orion-router/api/files.py>), [api/speech.py](<D:/krstlcm Workspace/DevProjects/orion-router/api/speech.py>), [api/transcriptions.py](<D:/krstlcm Workspace/DevProjects/orion-router/api/transcriptions.py>) | `HTTPException` korunur; erişim reddi genel provider hatası gibi 500'e çevrilmez. |
| [dashboard/components/KeyAccess.tsx](<D:/krstlcm Workspace/DevProjects/orion-router/dashboard/components/KeyAccess.tsx>) — yeni | `ownerLabel`, `KeyAccess`, `KeyUsage`, `HubManagement`, `PersonalKeyManagement`. Yeni arayüzlerin çoğu burada; tasarım revizyonunun ana dosyası. |
| [dashboard/app/key-pool/page.tsx](<D:/krstlcm Workspace/DevProjects/orion-router/dashboard/app/key-pool/page.tsx>) | Ortak kayıt düzenleme dialoguna A katmanı; kişisel kayıt yönetimi; sır formunun submit/kapatma/oturum değişiminde temizliği. |
| [dashboard/app/keys/page.tsx](<D:/krstlcm Workspace/DevProjects/orion-router/dashboard/app/keys/page.tsx>) | Hub·kullanıcı etiketleri, bağlı Hub yönetimi; detay/düzenle dialogunda B ve kullanım; sanal anahtar oluştururken write-only giriş; eski raw-success/reveal/copy kaldırıldı. |
| [dashboard/app/logs/page.tsx](<D:/krstlcm Workspace/DevProjects/orion-router/dashboard/app/logs/page.tsx>) | 100 satırlık ileri/geri pagination ve Hub·kullanıcı gösterimi; liste bütün kullanım toplamı olarak sunulmaz. |
| [dashboard/components/AppContext.tsx](<D:/krstlcm Workspace/DevProjects/orion-router/dashboard/components/AppContext.tsx>) | Logout sırasında unauthorized olayıyla sır taslaklarının temizlenmesi. |
| [dev/test-orion-router/test_key_policy.py](<D:/krstlcm Workspace/DevProjects/orion-router/dev/test-orion-router/test_key_policy.py>) — yeni | 14 izin/rapor/gizlilik regresyon testi; PostgreSQL geçici şema ve rollback. |
| [dev/test-orion-router/test_hub_accounts.py](<D:/krstlcm Workspace/DevProjects/orion-router/dev/test-orion-router/test_hub_accounts.py>) | Mevcut 9 testi yeni kişisel-ID/izin havuzu davranışına uyarlar. |
| [docs/provider-key-access.md](<D:/krstlcm Workspace/DevProjects/orion-router/docs/provider-key-access.md>) — yeni | Kısa ürün/politika ve API kullanım notu. |

### Veritabanı ve migrasyon ayrıntıları

Migrasyon mevcut startup `_ensure_tables` akışındadır. Transaction ve `pg_advisory_xact_lock(724109850)` ile eşzamanlı başlangıçlar korunur. Ayrı elle SQL migrasyon dosyası oluşturulmadı.

- `router_provider_key_pool.scope`: eski ortak kayıtlarda `all`.
- `router_virtual_keys.provider_key_mode`: eski sanal anahtarlarda `unrestricted`.
- `router_user_provider_keys`: mevcut `(key_id, provider)` yapısı tutuldu; UUID `id`, `label`, `is_active`, `admin_updated` eklendi. UUID için unique index var.
- `router_provider_key_access`: ortak `provider_key_id` ile `virtual_key_id` ilişkisi; silmelerde FK cascade.
- `router_virtual_key_access`: B seçimi; `provider_key_id` ortak veya kişisel ID olabilir. Bu nedenle upstream tarafında tek tablo FK'sı yok. Silinmiş upstream ID'lerinin B ilişkilerinde temizlenmesi ayrıca uygulanmadı; yeni kayıt yeni UUID aldığı için eski seçim ona aktarılmaz.
- Ortak kayıtların `(provider, label)` uniqueness kısıtı kaldırıldı; aynı etiket farklı kayıtlar için kullanılabilir.
- `router_request_logs`: `upstream_key_source`, `recorded_key_id`, `recorded_hub_id`, `usage_unit`, `usage_amount` eklendi. Önceden var olan `upstream_key_id` gerçek seçimi taşır. Silinen hesap/anahtarın geçmişi snapshot kimliğiyle raporlanabilir.
- Tarihsel kayıtta elde bulunan sanal anahtar/Hub ilişkisi kopyalanır. Eski upstream kaynak ve usage birimi güncel ayarlardan tahmin edilmez; geçmişte kaybolmuş bilgi geri üretilemez.
- Legacy config sağlayıcı anahtarları bir defa havuza aktarılır; `router_configs.provider_api_keys_migrated` kalıcı işaretidir. Zaten temsil edilen sağlayıcı tekrar eklenmez; yeniden başlangıç pasif veya yeniden adlandırılmış kaydı geri açmaz.

Canlı veritabanına bu oturumda migrasyon çalıştırılmadı; üretim servisleri bu görev için yeniden başlatılmadı. Kaynak kodunun commit edilmesi canlı süreçlerin yeni kodu yüklediği anlamına gelmez.

### Yönetici API haritası

`api/key_management.py` router'ı `api/admin.py` tarafından `/dashboard` altında eklenir ve tamamı `verify_admin` ister.

| Yol | İşlem |
| --- | --- |
| `GET /dashboard/api/hubs` | Hub metadata listesi. |
| `PUT /dashboard/api/hubs/{hub_id}` | `is_active` güncelleme, sanal auth cache invalidation. |
| `GET/PUT /dashboard/api/provider-key-pool/{id}/access` | A: `scope`, `selected` sanal anahtar UUID listesi. |
| `GET/PUT /dashboard/api/keys/{id}/access` | B: `mode`, `selected` upstream UUID listesi. GET kullanılabilir/kullanılamaz adayları ve gerekçelerini de verir. |
| `GET /dashboard/api/personal-provider-keys` | Sahip Hub/sanal anahtar metadata'sıyla kişisel kayıtlar; sır yok. |
| `PUT/DELETE /dashboard/api/personal-provider-keys/{id}` | Yönetici write-only değiştirme, aktivasyon veya silme. |
| `GET /dashboard/api/usage` | Bütün geçmiş üzerinde filtreli özet ve günlük UTC grupları. |
| `GET /dashboard/api/logs?limit=100&offset=0` | Ayrı istek listesi; maksimum limit 500. |

`usage` filtreleri: `start` dahil, `end` hariç, `hub_id`, `key_id`, `provider`, `model`, `upstream_key_id`. Tarihler timezone içermeli. Girdi/çıktı/düşünme/toplam, istek sayısı, başarı/başarısız/bekleyen, maliyet ve eksik bilgi sayısı döner. Gruplama capability ve birime göredir. STT saniyesi, TTS karakteri, LLM tokenları aynı toplamda birleştirilmez. Sıfır gerçek değer, NULL bilinmeyen değer olarak korunur. Karışık gruplardaki bilinen kısmi toplamlar eksik kayıt sayısıyla birlikte gelir.

UI şu an yalnız sanal anahtar ve başlangıç/bitiş tarihi filtrelerini sunuyor; diğer filtreler API'de mevcut. Günlük grafik CSS çubuklarından oluşuyor. Bitiş günü UTC 00:00'da hariçtir; tüm gün dahil anlamına gelmez. Tarih seçiminin kullanıcının beklentisine uygun sunulması tasarım revizyonunda değerlendirilmeli.

Bağımsız sanal anahtar oluşturma artık önceden hazırlanmış `sk-orion-...` değerinin password input'a girilmesini ister; yalnız hash saklanır, başarı yanıtında ham sır verilmez. Hub hesaplarının deterministik otomatik anahtar üretimi korunur. Bu oluşturma deneyimi değiştiği için yeni oturumda özellikle kullanıcıya gösterilecek akışlardan biridir.

## Hub: dosya haritası ve veri akışı

Aşağıdaki yollar `D:/krstlcm Workspace/DevProjects/orion-ai-assistant` köküne aittir.

| Dosya | Ne değişti / amacı |
| --- | --- |
| [services/hub/src/orion/api/router_providers.py](<D:/krstlcm Workspace/DevProjects/orion-ai-assistant/services/hub/src/orion/api/router_providers.py>) | Doğrulanmış kullanıcıdan hesap kimliği; seçili veya zaten bağlı güvenilir Router UUID hedefi; çoklu Router özeti; aktif anahtar toplamları; offline seçili Router'ın diğer bağlı Router'ları gizlememesi; write-only validation. |
| [services/hub/src/orion/kernel/router_models.py](<D:/krstlcm Workspace/DevProjects/orion-ai-assistant/services/hub/src/orion/kernel/router_models.py>) | Kullanıcı hesap anahtarıyla, izin sonucunu cache'lemeyen hesap kataloğu. Mevcut genel discovery katalog cache'i ayrı kalır. |
| [services/hub/src/orion/api/routes.py](<D:/krstlcm Workspace/DevProjects/orion-ai-assistant/services/hub/src/orion/api/routes.py>) | Normal kullanıcı `/api/v1/models` cevabı hesap kataloğundan alınır; global mod mevcut katalog yolunu korur. |
| [services/hub/src/orion/api/ui/js/router_providers.js](<D:/krstlcm Workspace/DevProjects/orion-ai-assistant/services/hub/src/orion/api/ui/js/router_providers.js>) | Router listesi/sayıları, kişisel kayıt durumu, yönetici güncellemesi/pasifleştirmesi, LAN linki/kopyala, tek kişisel formun hedef Router seçimi ve oturum değişiminde sır temizliği. |
| [services/hub/tests/test_router_accounts.py](<D:/krstlcm Workspace/DevProjects/orion-ai-assistant/services/hub/tests/test_router_accounts.py>) | Hesap kapsamı, çoklu Router toplamı, başkasının ID'sinin etkisizliği, validation sır güvenliği, offline Router ve cache'siz hesap kataloğu testleri. |

İncelenen fakat bu görevde değiştirilmeyen kritik dosyalar: `kernel/router_accounts.py`, `api/router_connection.py`. Önceki enrollment ve güven akışı bunlarda devam ediyor.

Hub API `GET /api/v1/router/providers` ile kendi hesabının durumunu verir. PUT `/api/v1/router/providers/{provider}` gövdesi `api_key` ve hedef `router_id` içerir; DELETE hedef UUID query parametresiyle çalışır. Başkasının kullanıcı ID'sini istemciden almaz. `global` kullanıcı kişisel anahtar kaydedemez. Hedef Router Hub'ın güven kayıtları ve mevcut bağlantısıyla doğrulanır; rastgele UUID veya URL'ye yönlendirme yapılamaz.

Hub → Router çağrıları kişinin Router hesap anahtarıyla yapılır. Router'ın `/api/v1/hubs/providers` yanıtı sağlayıcı başına etkin, A ve B geçen anahtar sayılarını verir:

- `all` veya birden fazla sanal anahtara seçili ortak kayıt: **ortak**.
- Yalnız bu sanal anahtara seçili ortak kayıt ve kullanıcının kişisel kaydı: **size özel**.
- Pasif/yetkisiz kayıtlar sayılmaz. Başkasının kayıt ID'si, sahibi veya etiketi kullanıcı özetine döndürülmez.
- `active_keys` Router'lar arasındaki aktif anahtar sayılarının toplamıdır. Mevcut `active_routers`, aktif anahtar sayısı sıfırdan büyük Router'ların sayısıdır; yalnız erişilebilir/bağlı Router sayısıyla aynı kavram değildir.

Router `/api/v1/hubs/account-catalog/{models|model-groups|voices}` hesap yetkilerine göre filtrelenir. Pasif kişisel kayıt o sağlayıcının ortak kataloğuna sessiz geçiş sağlamaz. Yerel sağlayıcılar kendi anahtarsız mantığıyla kalır. Hub yönetim kataloğu ayrı olarak korunur.

Router LAN adresini `https://{erişilebilir adres}:{TLS_PORT}/dashboard` biçiminde bildirir; varsayılan TLS portu 9443'tür. Hub yeni sekme, Flutter varsayılan harici tarayıcı açar. Loopback `localhost` / `127.0.0.1` adresinde link yerine “Router bu cihazda çalışıyor” gösterilir. LAN adresinin gösterilmesi tarayıcıdaki self-signed sertifika uyarısını ortadan kaldırmaz; bu görev sertifika dağıtımı yapmadı.

## Flutter: dosya haritası

Flutter uygulama kökü `D:/krstlcm Workspace/DevProjects/orion-ai-assistant-flutter/main_app/app`.

| Dosya | Ne değişti / amacı |
| --- | --- |
| [lib/features/settings/router_provider_card.dart](<D:/krstlcm Workspace/DevProjects/orion-ai-assistant-flutter/main_app/app/lib/features/settings/router_provider_card.dart>) | Bağlı Router'ların kart bilgileri, ortak/özel sayılar, kişisel kayıt ve yönetici durumları, LAN aç/kopyala, loopback metni; tek formda Router seçimi; Hub/Router değişiminde ve submit'te sır temizliği, eski işlemin yeni seçime yazmasını önleme. |
| [test/router_provider_summary_test.dart](<D:/krstlcm Workspace/DevProjects/orion-ai-assistant-flutter/main_app/app/test/router_provider_summary_test.dart>) — yeni | LAN/loopback, yönetici durumları ve hedef Router değişiminde form sırrının temizliği widget testleri. |

`settings_page.dart` ve `core/network/orion_api.dart` incelendi ancak bu görevde değiştirilmedi. Mevcut Hub hesap transport'u kullanıldı. `url_launcher` zaten bağımlılıktı; yeni paket eklenmedi. APK veya tam Flutter Web release build yapılmadı. Gerçek telefon/tarayıcı görsel kontrolü yapılmadı.

## Tasarım revizyonunun başlayacağı noktalar

Mevcut yeni arayüzler işlevsel fakat kullanıcının belirttiği tasarım sorunu açık ve bekleyen iştir. Özellikle `KeyAccess.tsx` içinde native `select`, `input`, `button`, `details`, kısa metinler ve basit border sınıfları var. Metinlerin çoğu doğrudan Türkçe yazılmış; mevcut dil sistemiyle bütünleştirme ayrıca gerekiyor.

| Ekran / konum | Şu an eklenen | Revizyonda birlikte incelenecek |
| --- | --- | --- |
| Router → Sanal anahtarlar | Üstte Hub yönetimi, sahip etiketleri | Mevcut liste/kart düzeni, durumlar, Hub'ların yeri ve bilgi hiyerarşisi. |
| Router → Sanal anahtar düzenle/detay | B izni, kullanılabilir/kullanılamaz listeler, kullanım ve günlük grafik aynı scrollable dialogda | İzin düzenleme ve istatistiğin kullanıcı tarafından kolay bulunması, dialog genişliği/taşma, filtreler, grafik tasarımı. |
| Router → Anahtar havuzu → düzenle | A kapsamı ve sanal anahtar checkbox listesi | Uyumlu çoklu seçim, Hub gruplaması/arama ihtiyacı, boş seçim uyarısı ve izin gerekçeleri. |
| Router → Anahtar havuzu | Kişisel anahtar yönetimi `details` bloğu | Sahip/durum bilgisi, aktif/pasif/değiştir/sil aksiyonlarının sunumu. |
| Router → Loglar | Pagination ve Hub·kullanıcı etiketleri | Mevcut tablo ve navigasyon görünümüyle uyum. |
| Hub → Sağlayıcı ayarları | Router seçimi, sayı/durum metinleri, LAN linkleri, tek form | Mevcut Hub görünümü ve mobil taşma; Router/sağlayıcı durumlarının okunması. |
| Flutter → Ayarlar → sağlayıcı kartı | Aynı özetler, Router seçimi, kişisel form | Mevcut Flutter tema/kart/form düzeni; gerçek cihazdaki davranış. |

Router'ın mevcut [dashboard/components/ui](<D:/krstlcm Workspace/DevProjects/orion-router/dashboard/components/ui>) dizininde Button, Input, Switch, Select, Dialog, Table, Badge, Textarea bulunur. `AppContext`, mevcut sayfalar ve global stiller örnek alınmalı. Yeni bir stil sistemi varsayılmamalı; kullanıcı görselleriyle istenen yerleşim kararlaştırılmalı.

Router [dashboard/AGENTS.md](<D:/krstlcm Workspace/DevProjects/orion-router/dashboard/AGENTS.md>) Next değişikliklerinden önce kurulu sürümün `node_modules/next/dist/docs/` dokümanını okumayı ister. Bu uygulama sırasında okundu; yeni oturumda yeni Next değişikliğinin ilgili dokümanı tekrar kontrol edilmeli. Kurulu sürüm Next 16.2.6, React 19.2.4 idi.

Tasarım çalışması sırasında korunacak ayrıntılar: UUID tabanlı sahiplik, boş seçimin erişimi kapatması, A'nın B tarafından açılamaması, sırların formdan temizliği, yalnız metadata görüntüleme, kişisel pasif kaydın ortak fallback'i kapatması, NULL/sıfır ve ses birimlerinin ayrımı. Kullanıcı yeni davranış revizyonu istediğinde bunları bilinçli olarak yeniden değerlendirmek gerekir; yalnız görünüm değişikliği diye atlanmamalı.

## Çalıştırılan doğrulamalar

Bu sonuçlar ilgili dar test setlerinindir; üç deponun bütün testlerinin geçtiği iddia edilmez.

| Kontrol | Sonuç / kapsam |
| --- | --- |
| Router | 35 geçti: yeni key-policy 14 + Hub accounts 9 + router regressions 8 + pricing units 4. PostgreSQL testleri gerçekten çalıştı, skip sonucu başarı diye sayılmadı. |
| Hub ilgili regresyon seti | 46 geçti, ayrıca 9 subtest. Accounts, provider auth, selection security, masking, bootstrap ve model defaults. |
| Flutter ilgili widget seti | 15 geçti: provider card, provider summary, router settings ve settings connection race. |
| Dashboard | TypeScript `tsc --noEmit` ve `npm run build` geçti; 10 statik sayfa üretildi. |
| Hub JavaScript | `node --check` ve `settings_router_key.cjs` geçti. |
| Üç repo | `git diff --check` geçti. LF/CRLF uyarıları var, içerik hatası olarak sayılmadı. |
| Ek Hub testleri | Aşağıda açıklanan 21 başarısızlık, başlangıç sürümünde de aynı şekilde tekrarlandı. |

Router testleri için production PostgreSQL'e bağlanmak yerine localhost:55439 üzerinde ayrı geçici cluster başlatıldı. Testlerin her biri random şema oluşturup transaction rollback yaptı. Test cluster'ı durduruldu; `scratch/policy-postgres` ve `scratch/policy-db-verification` geçici dizinleri hedef doğrulandıktan sonra kaldırıldı. Test veritabanı şu an çalışır durumda değildir. Son embedding/TTS eksik usage ve STT gerçek usage düzeltmesinden sonra 4 pricing + 8 routing testi tekrar geçti; PostgreSQL 14 testi bu son runner düzeltmesinden önce geçti. Bu zaman farkı tekrar test planlanırken bilinmeli.

Hub'ın mevcut `services/hub/.venv` ortamı eksik/bozuk Python 3.14 kurulumuna bağlıydı. Bu yüzden ayrı Python 3.11 test ortamı `.tmp/policy-test-runtime` oluşturuldu ve gerekli 54 bağımlılık o ortamda yüklendi. Mevcut uygulama venv'i ve lock dosyaları değiştirilmedi. Bu geçici ortam commit'e alınmadı.

Flutter SDK `D:/src/flutter`, pub cache `D:/flutter_pub_cache`. Windows sandbox'ta SDK yazma/lock ve Next SWC canonicalize erişim sorunları nedeniyle ilgili doğrulamalar yükseltilmiş erişimle çalıştırıldı. Bunlar uygulama kaynak hatası olarak raporlanmadı. Formatter çalıştırıldı. Testler aynı kaynaklarda doğrudan Dart Flutter-tools runner'ıyla tamamlandı.

### Tekrar çalıştırma örnekleri

Hub repo kökünde, geçici ortam hâlâ mevcutsa:

```powershell
$env:PYTHONPATH = 'D:/krstlcm Workspace/DevProjects/orion-ai-assistant/services/hub/src;D:/krstlcm Workspace/DevProjects/orion-ai-assistant'
& 'D:/krstlcm Workspace/DevProjects/orion-ai-assistant/.tmp/policy-test-runtime/Scripts/python.exe' -m pytest services/hub/tests/test_router_accounts.py services/hub/tests/test_router_provider_auth.py services/hub/tests/test_router_selection_security.py services/hub/tests/test_router_api_key_masking.py services/hub/tests/test_router_bootstrap.py services/hub/tests/test_model_defaults.py -q
node --check services/hub/src/orion/api/ui/js/router_providers.js
node --test services/hub/tests/settings_router_key.cjs
```

Dashboard dizininde mevcut bağımlılıklarla:

```powershell
npx tsc --noEmit
npm run build
```

Flutter `main_app/app` dizininde:

```powershell
& 'D:/src/flutter/bin/cache/dart-sdk/bin/dart.exe' 'D:/src/flutter/bin/cache/flutter_tools.snapshot' test --no-pub test/router_provider_card_test.dart test/router_provider_summary_test.dart test/router_settings_test.dart test/settings_connection_race_test.dart
```

Router testleri `dev/test-orion-router` altında. Önce `test_hub_accounts.py` fixture'ındaki `db_manager.get_postgres_url()` çözümlemesini kontrol et, ayrı geçici PostgreSQL hedefini açıkça ayarla. Canlı config URL'sini yanlışlıkla kullanma. PostgreSQL ulaşılamazsa bu testler skip olabilir; raporda skip'i belirt. Sonra key policy, Hub accounts, router regressions ve pricing units dosyalarını çalıştır. Burada kapatılmış test cluster'ına körlemesine bağlanılacak bir komut bırakılmadı.

## Hub'daki 21 başarısız test nedir?

**Bunlar 21 yeni canlı uygulama hatası gözlemi değil, üç test dosyasındaki başarısız assertion/fixture çalışmalarıdır.** Hepsi başlangıç Hub commit'i `2956d108...` kaynaklarıyla da tekrarlandı. Kök nedenlerin önemli bölümü eski test mock'larının güncel güvenilir Router / TLS akışıyla uyumsuzluğu. Bununla birlikte “hepsi önemsiz” veya “bağlantı akışında hiçbir sorun olamaz” sonucu çıkarılmamalı; güncel akışa uygun izole testler yazılarak kalan davranış farkları incelenmeli.

Karşılaştırma nasıl yapıldı: mevcut modüller import edildikten sonra bu çalışmada değişen `orion.kernel.router_models`, `orion.api.router_providers`, `orion.api.routes` modülleri, başlangıç Git HEAD kaynaklarıyla bellek içinde yeniden yüklendi. Dosyalar checkout edilmedi, yerel değişikliklere dokunulmadı. Aynı üç test dosyasında aynı 21 test yine başarısız oldu. Bağlantı, environment, pin ve güven kaydı kaynakları bu özellik çalışmasında zaten değiştirilmemişti.

### 11 bağlantı testi — `test_router_connection.py`

Eski testler `manager.check` ve discovery `directory.list` gibi yolları mock'luyor. Güncel `RouterAssociation.refresh()` ise `bootstrap_local_router()`, `resolve_trusted_routers()`, `SecurityStore().routers()` ve `read_connection()` akışını kullanıyor. Testlerin yalnız JSON seçim dosyasını izole etmesi gerçek SQLite güven kayıtları ve local bootstrap durumunu dışarıda bırakmıyor. Bu yüzden fixture Router UUID'si yerine makinedeki kayıt davranışı görülebiliyor.

Ek somut farklar: bir test artık var olmayan `orion.api.router_connection.get_runtime_settings` sembolünü patch'lediği için `AttributeError` veriyor; eski HTTP discovery/self/model istek sırası güncel HTTPS/pin akışına uymuyor. Owner login testinde beklenen 200 yerine 401 görüldü; bu auth/fixture varsayımı ayrıca incelenmeli. Corrupt seçim testinin beklediği exception ile güncel refresh'in `RuntimeError` yakalama davranışı farklı. Son iki farkın tamamını yalnız mock eskiliği diye kapatmak doğru olmaz.

Başarısız test adları:

1. `test_conflicting_uuid_is_not_selected`
2. `test_corrupt_selection_is_not_deleted_or_replaced`
3. `test_existing_unique_hostname_migrates_to_its_same_uuid`
4. `test_explicit_environment_and_manual_selection_are_preserved`
5. `test_failed_api_check_does_not_create_an_association`
6. `test_multiple_routers_are_not_automatically_selected`
7. `test_offline_saved_router_and_expired_endpoint_fail_closed`
8. `test_owner_selection_requires_admin_scope_and_mount_stays_local`
9. `test_real_identity_and_api_key_checks_happen_in_order`
10. `test_saved_router_updates_ip_but_never_changes_uuid`
11. `test_single_router_is_checked_and_uuid_persists_for_worker`

### 5 adres testi — `test_router_address.py`

`setUp` artık esas yolda kullanılmayan `services.shared.router_connection.selected_router_url` fonksiyonunu patch'liyor. Güncel `get_router_base_urls()` güvenilir Router kayıtlarını ve UUID/pin seçimini okuyor. Kaydedilmiş güvenli Router seçimi gerçek durumda üstün geliyor; testler bu kaydı izole etmeden eski `ROUTER_BASE_URL`, HTTP ve 20128 önceliklerini bekliyor. Güven kaydı yokken güncel fallback HTTPS ve TLS portu 9443 mantığı kullanıyor. Eski plaintext/legacy varsayımlarını geri ekleyerek test geçirilmemeli.

Başarısız test adları:

1. `test_bad_or_sensitive_urls_are_rejected_without_printing_credentials`
2. `test_custom_hostnames_are_already_supported_by_legacy_configuration`
3. `test_existing_native_and_docker_defaults_remain_usable_before_mdns_setup`
4. `test_explicit_hostname_url_is_used_without_ip_configuration`
5. `test_explicit_url_overrides_legacy_host_and_port`

### 5 katalog testi — `test_catalog_partial.py`

Testler `catalog.aiohttp.ClientSession` factory'sini patch'liyor. Güncel katalog `catalog.router_session()` üzerinden `RouterSession` alt sınıfı ve HTTPS/SPKI doğrulayan `RouterConnector` kullanıyor. Factory patch'i gerçek session yolunu yakalamıyor. Fixture URL'leri `http://router` / `http://fallback`; güvenli connector bunları reddediyor veya fake cevap gelmiyor. Sonuçlar katalog kullanılamıyor hatası, boş/None veri veya timeout oluyor.

Güncel contract'a uygun çözüm `catalog.router_session` sınırını fixture Session ile patch'lemek, gerekli credential/trust bağımlılıklarını izole etmek ve TLS testlerini ayrıca korumak. Katalog testlerini geçirmek için gerçek transport'u HTTP'ye açmak çözüm değil.

Başarısız test adları:

1. `test_cold_model_save_never_requests_voice_services`
2. `test_outage_preserves_previous_voices_and_recovers`
3. `test_poll_and_first_save_share_model_request`
4. `test_unreachable_local_tts_does_not_block_other_catalogs`
5. `test_validation_finishes_while_voice_refresh_is_pending`

`RouterSession` sınıfının `aiohttp.ClientSession`'dan türemesiyle ilgili deprecation uyarısı ayrıca mevcut. Bu bir uyarıdır, 21 başarısız testten biri değildir. Bu oturumda 21 eski test onarılmadı; kullanıcı önce commit ve devir istedi.

## Doğrulanmamış işler ve sonraki oturum için kontrol listesi

- Kullanıcının istediği Orion Router tasarım uyumu henüz yapılmadı. Ekran görüntüleriyle adım adım revizyon yeni oturumun birincil işi.
- Canlı provider çağrılarıyla bütün kullanıcı senaryoları, gerçek tarayıcı görsel QA'sı ve fiziksel Flutter cihaz testi bu görevde yapılmadı. Başarılı build ve widget testleri bunların yerine geçmez.
- Eski 21 Hub testi düzeltilmedi. Güvenli transport'u koruyarak fixture/contract güncellemesi ve kalan davranışların incelemesi ayrı takip işidir.
- Canlı servisler yeni kodla yeniden başlatılmadı, canlı PostgreSQL migration durumu kontrol edilmedi, release/APK üretilmedi.
- Yönetim ve rapor API'leri UI'dan daha kapsamlıdır. Tasarım revizyonunda API'de olup UI'da olmayan filtre/özetlerin nasıl gösterileceğini kullanıcıyla kararlaştır.
- Kullanım grupları UTC; speech birimleri provider'a göre değişebilir. UI'da karakter/saniye/token etiketlerinin gerçek API verisiyle eşleşmesi korunmalı.
- Yeni oturumun başlangıcında üç repo için Git status'u yeniden oku. Bu belgede kayıtlı temiz başlangıcı gelecekteki değişiklikler için varsayma.

Yeni oturuma verilebilecek kısa başlangıç talimatı:

> `D:/krstlcm Workspace/DevProjects/orion-ai-assistant/docs/router-hub-flutter-handoff-2026-10-07.md` belgesini ve üç projenin mevcut Git durumunu oku. İşlevsel çalışma commit edildi. Yeni arayüzleri mevcut Orion Router tasarımıyla uyumlu hale getireceğiz ve bazı davranışları revize edeceğiz. Ekran görüntülerimi ve tek tek talimatlarımı esas al; belgedeki UUID sahipliği, iki katmanlı izin, sır saklama ve geçmiş raporlama kararlarını koru. Eski 21 Hub testi başlangıç sürümünde de başarısız; bunları yeni eklenen özelliklerin kanıtlanmış regresyonu gibi sunma. Tasarım dışında değişiklik yapmadan önce kapsamı benim yeni talimatlarımla eşleştir.
