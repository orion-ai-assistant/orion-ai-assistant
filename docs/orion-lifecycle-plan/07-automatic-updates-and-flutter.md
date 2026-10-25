# Aşama 07 — Sessiz otomasyon ve Flutter deneyimi

Durum: DOĞRULAMA EKSİK. 17 Ekim 2026 otomasyon, güvenli açılış entegrasyonu ve Flutter web kartı uygulandı. İzole kod testleri aşağıdadır; yeni tam ürün paketiyle uçtan uca kabul, temiz VM, gerçek kullanım ve güç kesintisi açık kalır. Kullanıcı 06'nın açık kabulleri korunarak yalnız 07 geliştirmesini yetkilendirdi; 08'e geçilmedi.

[Ana plan](../orion-installation-lifecycle-implementation-plan.md) · [Ortak sözleşmeler](shared-contracts.md)

## Ön koşullar ve okunacaklar

Ön koşul aşamalar: [04](04-launcher-and-autostart.md), [06](06-update-helper-and-recovery.md)

Ortak kararlar; 04 başlatıcı ve 06 güncelleme yardımcısı sonuç kayıtlarındaki arayüzler.

Ön koşulların yalnızca durum etiketini değil, sonuç kaydını ve mevcut koddaki karşılığını doğrula. İlgisiz gelecek aşamaların tamamını okumak veya uygulamak gerekmez. Repo talimatları ayrıca geçerlidir.

## Bu aşamadaki iş ve kabul koşulları

Otomatik güncelleme tercihi stable kanalda kontrol/indirme/hazırlamayı sessiz yapar. İlk uygulamada hazır sürüm sonraki güvenli açılışta uygulanır. Kullanım sırasında otomatik geçiş, boşta ve iş kabulü kilidi güvenilir biçimde doğrulandıktan sonra etkinleştirilir. Aktif ses, sohbet üretimi, araç görevi, dosya aktarımı ve kaydedilmemiş taslak kullanım işaretidir. Uzak bağlı istemciler de hesaba katılır; Flutter penceresinin kapalı olması Hub'ın boşta olması anlamına gelmez.

Kullanıcı çalışıyorsa güncelleme bekler. Yeniden başlatmayı kendisi isterse kısa özet ve erteleme sunulur. Her kontrol/indirme için bildirim verilmez; anlamlı hata ve gerekli kullanıcı işlemi gösterilir. Ayarlarda otomatik güncelleme, sürümler, son başarılı kontrol, bekleyen geçiş ve geçmiş görünür. Ayar kapatılınca yeni otomatik geçiş başlamaz; başlamış kritik geçiş güvenli sınıra tamamlanır. Ağ kullanımına yönelik duraklatma/ölçülü bağlantı politikası açıkça belirtilir.

Router'ın bağımsız güncelleme tercihi korunur; Hub ayarı izinsiz biçimde Router ayarını açmaz. İlk kapsamda telefon durum görebilir ve istek iletebilir, PC güncellemesini normal Hub hesabıyla başlatamaz. Hub bağlantısı kesilince son bilinen durum/zaman gösterilir; başarı tahmin edilmez.

Kabul/test: kesintisiz kullanım sırasında indirme, yeni iş ile boşta geçiş yarışı, uzaktan aktif istemci, taslak korunması, erteleme, tercih kapatma, uygulama yeniden açılınca kalan durum, internetin gidip gelmesi, telefon bağlantısızlığı. Sadece değişen ve yetkili bileşen yeniden başlar.

## Sonraki aşamaya devredilecek çıktı

Otomasyon tercihleri; Flutter durum/hata/erteleme gösterimi; aktif iş ve uzak istemciyle güvenli geçiş sözleşmesi.

Uygulanan arayüzlerin gerçek dosya yolları, giriş noktaları, şema sürümleri ve hata sonuçları aşağıdaki sonuç kaydına yazılır. Sonraki model tasarım varsayımı yerine bu kaydı ve kodu esas alır.

## Kapsam sınırı

Yalnız bu aşamanın işini uygula. Ön koşulda eksik sözleşme varsa dar kapsamlı düzeltmeyi kaydet; başka aşamanın tüm özelliğini sessizce üstlenme. Gerçek kullanıcı verisinde hata enjeksiyonu yapma. Aşama testleri geçmeden sonraki aşamaya geçme; bu aşama bittiğinde sonuçları raporlayıp dur. Ana plan tablosundaki durum da güncellenir.

## Sonuç ve test kaydı

- Tarih: 17 Ekim 2026. Hub ve Flutter kaynakları değişti; Router deposu değişmedi. Computer Use, gerçek Startup/otomatik güncelleme etkinleştirme ve push yapılmadı. Başlangıçtaki Hub tasarım önerisi/uv.lock/build ve Flutter sohbet testi değişiklikleri korunur ve görev commit'lerine alınmaz.
- Girişler: Hub `services/shared/automatic_updates.py`, `scripts/release/update_automatic.py`; `launcher.py` güvenli açılış ve dış watcher girişi; `update_helper.py` otomatik admission kontrolü ve tercih koruması. API `services/hub/src/orion/api/update_routes.py`, `main.py`, `access_boundary.py`. Flutter `features/settings/updates_card.dart`, `settings_page.dart`. Bootstrap üreticisi/ilk paket kurucusu ve paket güvenlik CI test listesi güncellendi.
- Kalıcı sözleşme: schema-1 registry'deki `preferences.automatic_updates` tek Hub/Client tercihidir. `updates/policy.json` schema 1: `paused: bool`, `deferred_until: Unix UTC seconds`. `updates/automatic.json` schema 1: `last_attempt`, `last_success`, güvenli hata sınıfı. Mevcut işlem günlükleri korunur; fazlara isteğe bağlı `updated_at` eklendi. Geçmiş en son 10 günlükten okunur; eski tarih alanı olmayan günlüklerde zaman bilinmiyor kalır.
- Kontrol: stable feed ve ayrı bootstrap yokken ağdan indirme veya hayali yayın adresi yoktur. Güvenilen kurucunun teslim ettiği `bootstrap/release-source.json` yalnız HTTPS metadata/target adresleri ve Hub/Client hedef adları içerir. TUF imza/hash/sequence/uyumluluk kuralları değişmez; cache her kontrolde yeni Updater ile açılır. Stable hedefleri yerel kurucu yapılandırması belirler; Router hedefi reddedilir. Yeni kurulumda tercih hâlâ kapalıdır; nihai varsayılanı açmak yayın/kabul işidir.
- Zamanlayıcı: dış launcher, kurulmuş ayrı bootstrap Python'ından `watch` başlatır. Watcher Hub Job'ının çocuğu olarak başlatılmaz; servis içinden breakaway açılmaz. Kurulum başına OS watcher/kontrol kilitleri; 5 dakikada bir yerel uyanma, en fazla 6 saatte bir ağ kontrolü/başarısız deneme. Servisler kapalı doğrulandığında watcher çıkar. Tarayıcı/Hub kapanırken başlamış hazırlama aktivasyona dönüşmez. OS logoff/power-loss dayanıklılığı ayrıca kabul ister.
- Aktivasyon: yalnız normal launcher'ın servis başlatma işlemlerinden **önce**, retained bootstrap'ta `startup`. İşlem `staged`/`waiting_for_idle` ise tercih ve erteleme operation kilidi altında tekrar okunur. 06'nın kapalı-servis/native kilit/admission/backup/health/rollback mekanizması aynen kullanılır. Çalışan servis varsa `waiting_for_idle`; sohbet/ses/araç/dosya/uzak istemci durdurma komutu yoktur. Pencereyi kapatmak boşta kanıtı değildir. Kritik geçiş/onarma varsa normal açılış kapalı kalır; açık `update_local.py resume/rollback` onarması korunur.
- Tercihler: indirme sırasında kapatma/duraklatma isteği operation kilidi meşgulse 409 ile yeniden deneme ister; başlamış indirme güvenli hazırlama sınırına tamamlanabilir. Kapalı tercih yeni hazırlama veya geçiş başlatmaz. Ağ duraklatması yeni kontrol/indirmeleri engeller, önceden hazırlanmış sürümün güvenli açılışını engellemez. Ölçülü ağ otomatik algılanmaz; kart bunu açıkça belirtir. 24 saat erteleme yalnız aktivasyonu erteler; arka plan hazırlamayı engellemez.
- Dar 06 ön koşul düzeltmesi: bekleyen işlemin registry karşılaştırmasında yalnız doğrulanmış bool tercihleri farklı olabilir; kök, kimlik, bileşen, veri/state ve envanter değişimi hâlâ `repair_required`. Yayın/geri alma güncel tercihleri korur. Eski `show_client` değişikliğini yabancı kurulum sayan test gerçek kök değişikliğine çevrildi; tercih koruması ayrıca sınandı. Registry/journal çoklu yazımıyla eski tercihe geri dönüş açılmadı.
- API: oturum gerektiren `GET /api/v1/updates`: paket kimlikleri/sürümler, son deneme/başarı, pending, geçmiş, hata, politika, gözlem zamanı ve `can_manage`. Disk yolları, yedek envanteri ve sırlar dönmez. `PUT` yalnız `automatic_updates?: strict bool`, `paused?: strict bool`, `defer_hours?: 0|24`; bilinmeyen alanlar reddedilir. Gerçek desktop peer ve uygun origin gerekir; mobile listener, telefon, forwarding header ve yabancı origin normal kullanıcı JWT'siyle yetki kazanamaz. URL, komut, root veya keyfi yol HTTP'den alınmaz. 409 tercih/kurulum meşgul; 503 okunamayan durum. Router bağımsız tercihi bu endpoint'e bağlı değildir.
- Flutter: Ayarlar'da kart; sessiz 30 saniyelik durum yenileme, tercih, sürümler, pending hedef/faz, geçmiş, hata, ağ duraklatma ve 24 saat erteleme. Hub bağlantısı gidince son bilinen durum ve zamanı korunur, kontrol düğmeleri kapanır; başarı tahmin edilmez. Hub/hesap değişiminde eski snapshot ve gecikmiş yanıtlar atılır. Aktif form/sohbet taslağına, ses veya araç işine müdahale etmez. Native Windows Flutter EXE eklenmedi.
- Test kanıtları aşağıdadır. İzole imzalı fixture'lar kullanıldı; ürün servisleri bu oturumda başlatılmadı. Önceki manuel deneme kurulumu değiştirilmedi. Kullanıcı data/kimlik/model yollarına taşıma/temizlik yapılmadı; veri koruma testleri yeni fixture sentinel'leri ve önceki helper regresyonlarıyla sınırlıdır.
- Açık kabul: yeni 07 Hub+Client+retained bootstrap setiyle gerçek tam paket/DB/launcher denemesi; Windows süreç ağacında dış watcher'ın Hub kapanmasında yaşaması; gerçek aktif sohbet/ses/araç/transfer/uzak istemci ve tarayıcı taslağı; temiz Windows VM/logon, VM zorla kapatma, üretim kökü/imzalama/hosting ve GitHub CI. Bunlar kod testlerinden geçmiş sayılmaz. 06 ve önceki 01/03/04/05 borçları korunur. Bağımsız Router güncellemesi, legacy geçiş, uninstall/cleanup ve yayın 08'dir.
- Geri alma: yalnız 07 görev commit'lerini birlikte geri al; verileri, eski release/state, TUF cache, işlem günlükleri veya yedekleri silme. Başlamış kritik 06 işlemini eski launcher ile aşma; retained helper ile açık onarma kullan. Kaynak geri alma kullanıcıya dağıtılmış paketi kendiliğinden değiştirmez.
- Sonraki aşama: 08 uygulanmadı ve kabul edilmedi. [07 deneme rehberi](07-manual-test.md), mevcut [yerel ürün denemesi](06-manual-test.md).

### Test kaydı

Komutlar repo kökünden, Flutter komutları `orion-ai-assistant-flutter/main_app/app` içinden çalıştırıldı. Ağır paket/DB/build çalışmaları eşzamanlı çalıştırılmadı. TUF testleri yalnız ephemeral imzalı yeni fixture'larda yürütüldü; gerçek ürün açılmadı.

| Kontrol | Sonuç / kanıt |
| --- | --- |
| Yardımcı + otomasyon + launcher | `.release/tools/Scripts/python.exe -m pytest scripts/release/test_automatic_updates.py scripts/release/test_update_helper.py scripts/lifecycle/test_launcher.py -q --junitxml=.tmp/stage07-helper-final.xml`: **109 geçti**, 72,35 sn. Faz/kalıcı yayın kesintileri, veri/tercih koruması, rollback ve OS kilitleri dahil. |
| Son otomasyon | Aynı Python ile `scripts/release/test_automatic_updates.py`: **16 geçti**, 8,28 sn; `.tmp/stage07-automatic-final.xml`. Son ek watcher kapanışı ve kesilmiş indirme/yeni süreçten yeniden hazırlama dahil. Önceki setle örtüşür, toplanmaz. Sağlık/native hizmetleri bu testte sahtedir. |
| API ve erişim sınırları | Hub venv + `PYTHONPATH=services/hub/src;<repo>`; `test_update_routes.py test_flutter_browser_access.py test_pc_audio_access.py`: **13 geçti**, 2,91 sn; `.tmp/stage07-boundaries-final.xml`. Telefon durum okur ama yazamaz; auth/origin/forwarding/mobile sınırı ve validation. Gerçek Hub/DB yok. |
| Flutter widget | `flutter test --no-pub test/updates_card_test.dart test/settings_refresh_test.dart test/router_access_card_test.dart test/settings_connection_race_test.dart`: **12 geçti**, yaklaşık 5 sn. Son bilinen çevrimdışı durum, geç yanıt/Hub değişimi, erteleme, read-only telefon, mevcut ayar taslağı/bağlantı koruması; `.stage07-final-tests.log` ignored. |
| Flutter analiz | `dart analyze lib/features/settings/updates_card.dart lib/features/settings/settings_page.dart test/updates_card_test.dart`: **No issues found**. |
| Flutter web build | Flutter 3.38.5 / Dart 3.10.4, `flutter build web --release --no-wasm-dry-run --no-pub`: **başarılı**, 81,4 sn; `.stage07-web-build.log` ignored. Yalnız development build çıktısı; mevcut manuel paket envanterine kopyalanmadı, yayınlanmadı. |
| Statik / ortak motor | Değişen 12 Python kaynağının AST kontrolü ve canonical/Router 11 dosya eşitliği geçti. Hub/Flutter `git diff --check` temiz. Python güvenlik ve hedefli Flutter testleri CI işine eklendi; GitHub üzerinde çalıştırılmadı. |

İlk sandbox TUF koşusu atomik Windows replace erişim engeli verdi; normal kullanıcı bağlamında aynı disposable fixture testleri çalıştırıldı. İlk erteleme testinde fixture saati helper'a ulaşmıyordu; saat enjeksiyonu düzeltildi. İlk Flutter test kaynağındaki fazla parantezler düzeltildi. İlk helper regresyonunda tercih değiştirmeyi yasaklayan eski beklenti çıktı; yukarıdaki dar sözleşme değişimiyle kök/tercih ayrı sınandı. Bunlar ilk başarısız koşulardır, başarılı kabul diye gizlenmedi.

Ek eski Flutter `router_settings_test.dart` koşusu 7 başarısız test verdi; eski API anahtarı alanını görünür varsayıyor. İlk başarısız vaka 07 öncesi HEAD `settings_page.dart` dosyasının ayrı geçici kopyasıyla **aynı `Bad state: No element`** sonucunu verdi (`.stage07-baseline.log`). Asıl dosyalar ve kullanıcının sohbet testi değiştirilmedi; geçici kaynak/test kopyaları kaldırıldı. Bu önceki Router ayar testi borcudur, yeni kart testinin başarısı olarak sayılmadı.

Flutter görev commit'i: `704cbf9`. Router görev commit'i yoktur; depo temiz kaldı. Hub görev commit'i bu kaydı içerir ve Git geçmişinden bulunur. Commit imzalama yalnız ilgili komutta `commit.gpgsign=false`; kalıcı Git ayarı değiştirilmedi. Push yapılmadı.
