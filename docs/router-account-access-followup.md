# Router hesap erişimi — 17 Ekim 2026 takip kaydı

Kullanıcı 05 öncesi, Router kapalıyken açılan hesapların sonradan hazırlanmasını ve Flutter'da isteğe bağlı Router sanal anahtarı ekleme/kapama/kaldırma akışını istedi. Bu takip yalnız bu işi ve ilgili kimlik/hedef sınırlarını ele alır; Aşama 05 başlamadı. Computer Use kullanılmadı, mevcut kurulu Hub/Router çalıştırılmadı veya değiştirilmedi. D development depoları kurulum/taşıma/silme hedefi yapılmadı; ilgisiz kullanıcı diff'leri korunur.

## Kullanıcı sorularının karşılığı

Önceki AppData test penceresinde yalnız Hub/DB/Flutter kuruluydu. Canonical kayıtta Router yoktu; kullanıcı Router'ını o fixture'a bağlamak veya başlatmak özellikle yapılmadı. Bu nedenle "Router bağlı değil" bekleniyordu. Kayıtlı aynı-PC Router hazırsa yerel dosya kimliği + gerçek TLS UUID/SPKI kontrolüyle otomatik güven/bağlantı yolu korunur. Bilinmeyen bir LAN servisi sadece portu/ismi nedeniyle kabul edilmez. Router'ın varsayılan/ayarlanmış yönetici parolasıyla ilk Hub yetkilendirmesi başarısızsa PC sahibinin Panel'den bir defa yetkilendirmesi gerekir; uygulama farklı Router'a habersiz geçmez.

Flutter Router'a doğrudan hesap anahtarıyla bağlanmaz. Flutter Hub'a kendi kullanıcı oturumu ile bağlanır; Hub seçili Router'a kullanıcı erişimini ekler. Otomatik anahtar Hub'ın `router-access-seed` girdisi, Hub UUID'si, Router UUID'si ve hesabın oluşturulma zamanını içeren subject kimliğiyle deterministik türetilir. Router yalnız SHA256 özetini tutar. Aynı kullanıcı adı başka Hub/Router'da veya silinip yeniden açıldığında aynı anahtar olmaz. Hub seed'i sırdır; paket içine/loga konmaz, kalıcı kullanıcı verisiyle korunur.

Yeni manuel alan, sağlayıcının OpenAI/OpenRouter anahtarı değildir: **Router sanal anahtarıdır** (`sk-orion-...`). Her Hub hesabı + Router UUID için ayrı, şifreli bir override saklanır. Otomatik anahtar/hesap kaydı silinmez. Router A'daki manuel anahtar Router B'ye taşınmaz; B kendi otomatik hesabını/varsa B'ye ait manuel tercihini kullanır. A'ya dönülürse A'nın tercihi yerinde kalır.

## Kalıcı bekleyen hesaplar ve silme/kapama

Yeni bir ikinci hesap kuyruğu yerine mevcut kalıcı `orion_users` DB'si ve Router hesap envanteri arasındaki fark kullanılır. Router kapalıyken kayıt normal Hub DB'sinde kalır. `provisioning_loop`, normal Hub API ömründe ilk anda ve her döngü sonunda 15 saniye bekleyerek seçili Router'a bağlanmayı dener; aktif kullanıcılar için eksik Router kayıtlarını hazırlar. Yeni Hub süreci de aynı DB'den devam eder; önceki process'in `_pending` sözlüğüne veya kullanıcının tekrar giriş yapmasına gerek yoktur. Pasif/onaysız Hub hesapları bu arka plan taramasına alınmaz.

Router'da otomatik kullanıcı anahtarı **tamamen silinirse**, Hub yetkisi hâlâ aktif olduğu sürece envanter farkı veya istek öncesi kimlik kontrolü onu aynı deterministik anahtarla yeniden kaydedebilir. Router'ın anahtar cache'i yeni key-id için temizlenir. Hard delete, eski Router satırının kullanımı/kişisel sağlayıcı kayıtlarını geri getiren bir yedekleme değildir. Kalıcı erişim engellemek için anahtarı veya Hub'ı **devre dışı bırakmak** gerekir. Mevcut pasif bayrağı, bütçe ve kullanım upsert ile sıfırlanmaz. Hub yetkisi yoksa uygulama bunu admin yetkisine yükselterek geçmez.

Hub seed'i kaybolduysa veya Router'da aynı hesap subject'i farklı anahtar özetiyle bulunuyorsa 409 korunur; otomatik hash değiştirip yönetici kararını ezmez. Bu durumlar normal restart veya manuel override kapama değildir; kontrollü veri/kimlik onarımı gerekir.

## Anahtar sırası ve tekrar çalıştırma sınırı

1. Etkin manuel anahtar varsa önce o; yoksa otomatik kullanıcı anahtarı.
2. Model/STT/TTS işi gönderilmeden önce, aynı Router'a salt okunur `/api/v1/hubs/account-status` kimlik kontrolü yapılır.
3. Bu ön kontrolde manuel anahtar 401 alırsa veya kimlik kontrolünün bağlantısı koparsa otomatik anahtar denenir. Otomatik anahtar 401 alırsa önce Hub delegasyonu doğrulanır; yalnız yetkili Hub eksik kullanıcı kaydını yeniden hazırlayabilir.
4. 402 bütçe, 403 devre dışı erişim, TLS/pin hatası ve 5xx başka anahtarla aşılmaz. Model isteğinin kendi 401'i veya işi gönderdikten sonraki timeout yeniden yürütülmez; sağlayıcı hatası yanlışlıkla Router anahtarı hatası sayılıp ikinci ücretli çağrı yapılmaz. Multipart/ses gövdesi yalnız bir kez tüketilir.
5. Manuel anahtar kapatılırsa saklanır ama kullanılmaz; kaldırılırsa yalnız Hub'daki o override silinir. Otomatik hesap/Router anahtarı ve diğer Router'ların tercihleri korunur. Bu işlem Router yöneticisinin uzaktaki sanal anahtarını silmez.

## Kod ve arayüzler

- Hub `kernel/router_access.py`: `AccountRoute`, `AccountKey`, `current_route`; Fernet ile şifreli manuel tercih. Fernet anahtarı mevcut seed'den HKDF-SHA256, ayrı `orion-router-manual-credential-v1` amacıyla türetilir. Dosya `<security_dir>/router-access-<Router UUID>-<subject UUID>.manual`; şifreli JSON `{key, enabled}`. Otomatik `.json` makbuzu ayrı kalır. Bozuk manuel dosya açıkça yeniden girilerek değiştirilebilir veya kaldırılabilir; raw key GET cevabında yoktur.
- `router_accounts.ensure_account(..., force=False)` tekrar güvenli kayıt; `reconcile_accounts`, `provisioning_loop`. `registry.list_active_users` kalıcı DB kaynağı. Normal private API lifespan provisioner'ı sahiplenir ve kapatır; OS Startup/güncelleme ayarı açılmaz.
- `pinned_router.RouterSession`: request-local account/Router snapshot ve TLS hedef kimliği. Eşzamanlı kullanıcılar aynı session'ı paylaşsa da context ayrıdır. Adres seçimden sonra değiştiyse eski anahtar yeni hedefe gönderilmez. Aynı adrese kayıtlı iki Router varsa pin seçiminde yalnız adres değil beklenen UUID/SPKI kullanılır. Seçili Router yokken normal kullanıcı ayarına admin/global anahtarı devralınmaz.
- Hub `GET/PUT/DELETE /api/v1/router/access`: aktif oturum hesabı için durum, masked manuel ipucu, Router UUID ile kayıt/enable/disable ve kaldırma. PUT `{router_id, key?: sk-orion-..., enabled: bool}`; DELETE `?router_id=...`. Stale Router 409; global hesap 403. User-id gövdesi kabul edilmez; validation cevabı girilen sırrı yansıtmaz. Mobil/private sınırları hesap endpoint'ine uygun tutulur.
- Router `GET /api/v1/hubs/accounts`: TLS + Hub delegasyonu, yalnız kendi subject-id/aktif bayrakları. `GET /api/v1/hubs/account-status`: TLS + virtual-key auth; anahtar/bütçe/Hub pasifliği doğrulanır. POST accounts var olan budget/usage/aktif bayrağını korur ve credential cache'ini yeniler.
- Flutter `RouterAccessCard`: ayarlarda "Router sanal anahtarım" ve isteğe bağlı manuel alan, masked durum, kullan aç/kapat, kaldır. Giriş ve kaydetme sonrası raw key temizlenir; hesap/Hub/Router değişiminde eski form veya gecikmiş cevap yeni bağlama taşınmaz. Eski Router'a bağlanmamış genel key input'u normal sohbet ayarlarından çıkarıldı. Sağlayıcı anahtarı kartı ayrı kaldı.

## Uyumluluk, geri alma ve 05 devri

Yeni Hub'ın hesap doğrulama/arka plan envanteri için Router'daki bu iki yeni endpoint gerekir. Hub ve Router source değişiklikleri birlikte korunmalı; 05 paket uyumluluk metaverisine bu bağımlılık işlenmeli. Eski Router endpoint'i eksikse bunu uyumsuz sürüm olarak ele al; global admin anahtarına sessiz düşme veya HTTP'ye dönme. 05, seed, TLS/UUID, makbuzlar ve `.manual` dosyalarını kullanıcı data alanında korumalı; bunları yeni pakette yeniden üretmemeli. Mevcut `.venv`/DB taşıması veya paket kurulumu bu takipte açılmadı.

Geri alma: kendi test süreçlerini kontrollü kapat, ardından Hub/Router API ve Flutter kart değişikliklerini uyumlu birlikte geri al. Kullanıcıların encrypted manuel dosyalarını veya seed/kimlikleri otomatik silme. Sadece manuel tercihi kaldırmak için mevcut API kullanılabilir; managed kayıt farklıdır. 01/03/04'ün ertelenmiş ürün/OS/taşıma kabulleri bu görevle tamamlandı sayılmaz. Push yapılmaz.

## Son doğrulama ve yerel commit'ler

17 Ekim 2026 sonuçları; ağır çalışmalar sırayla, mevcut kurulu kullanıcı ürününden ayrı C AppData test alanlarında yürütüldü:

| Kontrol | Sonuç |
| --- | --- |
| Hub birleşik regresyon paketi | 151 test + 21 alt test geçti; 31,17 sn. DB bağlantı/pool girişimi 0/0. |
| Router gerçek PostgreSQL | 11 test geçti; 10,50 sn. İzole PG durduruldu; DB owner null. |
| Kalıcı offline hesap | İlk Hub süreci Router kapalıyken gerçek DB'ye kullanıcı yazıp kapandı. Yeni Hub süreci oturum açılmadan eksik hesabı gerçek Router ASGI + PostgreSQL üzerinden oluşturdu. Silinen key yeniden oluştu; pasiflik/bütçe/kullanım korundu. |
| Flutter | Router erişimi ve mevcut sağlayıcı kartı toplam 6 test geçti; üç dosyanın analizinde sorun yok. |
| Windows başlangıcı | Gizli dosyalar dahil mevcut 5 Startup dosyasının SHA256 değeri önceki audit ile aynı; Orion.lnk yok. Otomatik başlangıç etkinleştirilmedi. |
| Kaynak sınırları | Üç depoda diff whitespace kontrolü temiz. İlgisiz kullanıcı değişiklikleri commit'e alınmadı. Computer Use kullanılmadı. |

Kanıt kökleri: `C:/Users/krsta/AppData/Local/Orion/lifecycle-tests/stage04-code-lk05cctw` (`output.log`, `result.json`) ve `router-access-pg-8oimnkmu` (`tests.log`, `offline.log`, `connected.log`, `result.json`). Bunlar yerel test çıktılarıdır; paket artefaktı değildir. ASGI birleşik senaryosu gerçek ağ üzerinden TLS kurulumu veya GUI/logon kabulü sayılmaz; pin/TLS ve istek izolasyonu ayrı otomatik regresyon testleriyle kapsandı.

İlk DB denemesinde eski voices test beklentisi mevcut API'nin `model_voice_groups` alanını içermiyordu; beklenti düzeltildi. Yeni süreç test harness'inde eksik Router pool init/close düzeltildi; o başarısız deneme başarılı kabul edilmedi. Son yukarıdaki sonuçlar bu düzeltmelerden sonradır. Mevcut aiohttp miras alma ve httpx kullanımdan kaldırma uyarıları kaldı.

Router commit: `c6b3aeb`; Flutter commit: `55db319`. Router GPG imzalaması parola beklediği için yalnız o commit'te `commit.gpgsign=false` kullanıldı; kalıcı Git ayarı değiştirilmedi. Hub commit'i bu belgeyi içerir ve Git geçmişinden bulunabilir. Push yapılmadı. Bu kaynak değişiklikleri mevcut kurulu uygulamaya dağıtılmadı; uyumlu Hub/Router ve yeniden derlenmiş Flutter gerektirir. Aşama 05 sonraki oturumun işidir; başlangıç metni [05-session-prompt.md](orion-lifecycle-plan/05-session-prompt.md).
