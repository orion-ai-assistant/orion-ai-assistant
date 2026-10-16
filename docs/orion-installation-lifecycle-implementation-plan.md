# Orion kurulum ve güncelleme — ana uygulama rehberi

Tarih: 16 Ekim 2026. Aşama 00/02 tamamlandı; 01/03 DOĞRULAMA EKSİK. Aşama 04 kaynak başlatıcı ve Windows başlangıcı çekirdeği uygulandı; gerçek Unicode Hub/DB/Flutter sunucusu ve izole Startup testleri geçti. **04 DOĞRULAMA EKSİK:** temiz Windows VM/logon ve gerçek pencere/onarım OS kabulü açık. 05–08 başlamadı. [04 sonuç kaydı](orion-lifecycle-plan/04-launcher-and-autostart.md).

Bu dosya yeni oturumların giriş noktasıdır. Ayrıntılı plan aşama dosyalarına ayrılmıştır. Önce bu rehberi, sonra ortak kararları ve yalnız seçilen aşamanın gerekli belgelerini oku. Son kullanıcı talimatı: yalnız 04, testleri kendin sırayla çalıştır, sonuçları MD'ye kaydet, görev değişikliklerini commit et ve dur; push yapma. Hesabın Windows başlangıcı otomatik etkinleştirilmez. Eksik kabulü geçmiş veya yayını onaylanmış gösterme.

## Şimdi nereden başlanacak?

**Güncel 04 devri:** [04 oturum devrini](orion-lifecycle-plan/04-session-handoff.md) ve [04 sonuç kaydını](orion-lifecycle-plan/04-launcher-and-autostart.md) oku. Kaynak kurulum tek PowerShell girişi, Desktop/Startup linkleri, ayrı başlangıç/pencere tercihleri, yerel hata/log/onarma erişimi ve mevcut pencereye yönlendirme kodu var. Gerçek Unicode Hub/PG/Flutter sunucusu testi geçti. Temiz VM/logon, gerçek HWND/pencere kapanışı ve WinForms görsel kabulü açık; bu nedenle 04 tamamlandı sayılmaz. 01/03 borcu korunur. Yalnız 04 commit'inden sonra durulur; sonraki aşamaya geçilmez. Aşağıdaki 03 giriş paragrafı tarihsel devir bağlamıdır.

**Önceki 03 devir özeti:** offline gate, konum ekranı/API, source setup/reinstall ve özel kök gerçek Hub/PG/TLS testleri geçti (142 test + 70 alt test). Windows 10 1903+ PostgreSQL 16.15 runtime ile Türkçe/Unicode yeni Hub/Router kurulumu da gerçek testten geçti. Tam DB/runtime taşıması ve mevcut eski runtime dönüşümü 05/08 bağımlılığıyla kapalı kalır. Ayrıntılar [03 devir kaydında](orion-lifecycle-plan/03-session-handoff.md), [Unicode takip kaydında](orion-lifecycle-plan/03-unicode-postgres-followup.md) ve [03 sonuçlarındadır](orion-lifecycle-plan/03-installation-paths-and-migration.md). 01'in son 40 yaşam döngüsü testi ve Windows Job düzeltmesi geçerlidir; kalan ürün/OS kabulü [01 sonuçlarında](orion-lifecycle-plan/01-process-lifecycle.md) açık kalır. Hyper-V normal ürün testlerinin ön koşulu değildir; temiz VM/logon kanıtı ayrı OS kabulüdür. [02 sonuçları](orion-lifecycle-plan/02-installation-registry-and-router-selection.md), [00 sonuçları](orion-lifecycle-plan/00-inventory-and-test-environment.md) ve [envanter](orion-lifecycle-plan/00-current-inventory.md) korunur.

## Ortak kararlar

İlk hedef kullanıcıya özel Windows kurulumudur; ortak çekirdek macOS/Linux uyarlamasına hazır tutulur. Varsayılan her şey AppData'dadır; kullanıcı konum seçebilir ve sonraki aşamada kontrollü taşıyabilir. Hub/Router/Flutter bağımsız paket kimlikleri taşır. Eski Router bulunduğu yerde korunur. PID ve port tek başına sahiplik kanıtı değildir. Veriler ve kimlikler güncellemede korunur. Aktif kullanım habersiz kesilmez. Otomatik güncellemenin nihai varsayılanı açık olsa da paket doğrulama ve kurtarma testleri tamamlanmadan etkinleştirilmez.

Bağlayıcı ayrıntılar: [ortak kararlar ve teknik sözleşmeler](orion-lifecycle-plan/shared-contracts.md). Ürün kararları her aşamada okunur; teknik bölümler aşamanın okuma listesine göre seçilir. Ortak kural burada veya aşama dosyalarında farklı biçimlerde yeniden tanımlanmaz; değişiklik gerekiyorsa sözleşme ve etkilediği aşamalar birlikte güncellenir.

## Aşamalar ve ilerleme

Numaralar önerilen çalışma sırasıdır; ön koşul sütunu teknik bağımlılıkları gösterir. Her aşamanın kapsamı, testleri, kabul koşulları ve doldurulacak sonuç kaydı kendi dosyasındadır.

| Aşama | Belge / iş | Teknik ön koşul | Durum |
| --- | --- | --- | --- |
| 00 | [Envanter ve test ortamı](orion-lifecycle-plan/00-inventory-and-test-environment.md) | — | TAMAMLANDI |
| 01 | [Güvenli başlatma/kapatma](orion-lifecycle-plan/01-process-lifecycle.md) | 00 | DOĞRULAMA EKSİK |
| 02 | [Kurulum kaydı ve Router seçimi](orion-lifecycle-plan/02-installation-registry-and-router-selection.md) | 01 | TAMAMLANDI |
| 03 | [Konum seçimi ve kontrollü taşıma](orion-lifecycle-plan/03-installation-paths-and-migration.md) | 02 | DOĞRULAMA EKSİK |
| 04 | [Başlatıcı ve Windows başlangıcı](orion-lifecycle-plan/04-launcher-and-autostart.md) | 01, 02, 03 | DOĞRULAMA EKSİK |
| 05 | [Hazır paket ve sürüm metaverisi](orion-lifecycle-plan/05-packaging-and-release-metadata.md) | 02, 03 | BAŞLANMADI |
| 06 | [Güncelleme yardımcısı ve kurtarma](orion-lifecycle-plan/06-update-helper-and-recovery.md) | 01, 02, 03, 05 | BAŞLANMADI |
| 07 | [Otomasyon ve Flutter ekranı](orion-lifecycle-plan/07-automatic-updates-and-flutter.md) | 04, 06 | BAŞLANMADI |
| 08 | [Router geçişi ve kontrollü yayın](orion-lifecycle-plan/08-router-transition-and-rollout.md) | 04, 05, 06, 07 | BAŞLANMADI |

Durum değerleri: `BAŞLANMADI`, `DEVAM EDİYOR`, `DOĞRULAMA EKSİK`, `TAMAMLANDI`. Gerekli test çalıştırılamadıysa aşama tamamlandı sayılmaz; ortam eksikliği ve kalan doğrulama kaydedilir. Aşama tablosu ile kendi dosyasındaki durum birlikte güncellenir. 00/02 tamamlandı; 01/03/04 açık kabulleri kendi sonuç kayıtlarındadır; 05–08 başlamadı.

## Her oturumun çalışma yöntemi

16 Ekim 2026 son kapsam istisnası: kullanıcı 01/03 açık kabul borçları korunarak yalnız 04 geliştirmesini açıkça yetkilendirdi. Bu oturumda 04 uygulanıp test/belge/commit sonrasında durulur; bu istisna 05'e geçiş yetkisi değildir.

1. Repo talimatlarını ve bu ana rehberi oku. Kullanıcı bir aşama belirttiyse onu seç; belirtmediyse burada belirtilen sıradaki işi seç. Ön koşulları tamamlanmamış aşamayı uygulama; yalnız yukarıdaki 16 Ekim kullanıcı kararı, 01'in açık kabul borcuyla 02 geliştirmesine devam etmek için kayıtlı istisnadır. Sonraki 16 Ekim kullanıcı talimatı 03 geliştirmesini de yetkilendirdi; yayın kabulü veya eksik testlerin tamamlanması anlamına gelmez.
2. Ortak ürün kararlarını, seçilen aşama dosyasını ve orada belirtilen teknik sözleşmeleri oku. Ön koşulların sonuç kayıtlarını incele; başka aşamaya ait ayrıntıları yalnız arayüz ihtiyacı varsa aç.
3. Kod ve testlerde mevcut durumu doğrula. Belgede planlanan arayüzün gerçekten uygulanmış olduğunu varsayma. Aşamalar arası uyumsuzluk varsa kapsamı ve bağımlılıkları güncelle.
4. Seçilen aşamayı küçük değişikliklerle uygula; yalnız gerekli ön koşul düzeltmelerini ayrıca belirt. Başka aşama özelliğini kendiliğinden etkinleştirme. Gerçek kullanıcı verisini test amacıyla taşıma/silme; ACL mirasını otomatik değiştirme.
5. Aşamanın kabul testlerini çalıştır. Komut, ortam, sonuç, çalıştırılamayan kontroller ve korunma kanıtını aşama dosyasına yaz. Geçmeyen/çalıştırılamayan zorunlu kabul koşulunu geçmiş gösterme.
6. Sonuç kaydına değişen dosyaları, kararlı arayüzleri, geri alma yöntemini ve sonraki aşamanın bilmesi gerekenleri ekle. Ortak sözleşme değiştiyse ilgili bağlantıları güncelle.
7. Ana tablodaki durumu ve sıradaki aşamayı güncelle; sonucu kaydet ve aşama commit’ini oluştur. Son kullanıcı talimatıyla bu oturumda 04 commit’inden sonra dur; gerçek test/ortam engellerini ve tamamlanmamış kabulü açık bırak. Push yetkisi verilmedi.

## Bağımlılıklar neden var?

Her iş aynı dosyaya veya aynı oturuma bağlı değildir. Örneğin paket üretimi ile Windows başlangıcı farklı işlerdir; ikisi de kayıt/konum sözleşmelerini kullanır. Güncelleme yardımcısı ise hem paket doğrulamasına hem güvenilir süreç yönetimine ihtiyaç duyar. Ayrı dosyalar bu bağımlılıkları kaldırmaz; ilgili arayüz ve bitiş kanıtını açık hale getirir.

Konum taşıma 03 içinde kendi kalıcı günlüğü ve kurtarma testleriyle tamamlanabilir; 06'daki güncelleyiciyi bekleyerek döngüsel bağımlılık oluşturmaz. 06 daha sonra taşıma sözleşmesiyle ortaklaştırma yapabilir. Legacy runtime güvenle taşınamıyorsa 03 bu sınırı açıkça raporlar; 05/08 paketli dönüşümü bitmeden o taşıma yolu kullanıcıya açılmaz. Bu eksik özellik tamamlandı diye kaydedilmez.

## Yeni oturumda verilecek görev

> [04 oturum devir kaydını](orion-lifecycle-plan/04-session-handoff.md) kullan. 04 kaynak çekirdeğini yeniden yazma; eksik temiz VM/logon ve gerçek pencere kabulünü geçmiş sayma. 01/03 borcunu koru. 05–08'e geçiş ayrıca kullanıcı talimatı gerektirir.

## Belge otoritesi ve geçmiş

Uygulama için ana giriş bu dosyadır. Ortak ürün/teknik kuralların kaynağı [ortak sözleşmeler](orion-lifecycle-plan/shared-contracts.md), aşamaya özel kapsam ve testlerin kaynağı ilgili aşama dosyasıdır. Eski [tasarım önerisi](orion-update-strategy-proposal.md) gerekçe ve kaynak olarak korunur; farklılıkta bu güncel belge kümesi esas alınır. Planlar uygulama kanıtı değildir; tamamlanma yalnız test ve kodla doğrulanır.
