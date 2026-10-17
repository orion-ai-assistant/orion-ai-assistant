# Orion kurulum ve güncelleme — ana uygulama rehberi

## 17 Ekim 2026 güncel Aşama 06 kararı

Son kullanıcı yalnız 06 geliştirmesine devam edilmesini istedi. Aşağıdaki 05 oturum yetki sınırları tarihsel kayıttır; güncel kapsam 06'dır. 07–08, push, Windows başlangıcı ve otomatik güncelleme yetkisi yoktur. Önceki 01/03/04/05 açık kabulleri korunur. Flutter **web istemcisi** olarak kalır; native Flutter Windows EXE hedeflenmez. Ana dağıtım tercihi tek PowerShell komutu; isteğe bağlı görsel kurucu aynı doğrulanan motoru çağırabilir. Üretim yayın/güven kökü henüz yoktur.

06'nın kalıcı işlem/SDK'sız ayrı yardımcı/kurtarma uygulaması ve sınırları [06 sonuç kaydında](orion-lifecycle-plan/06-update-helper-and-recovery.md); kullanıcı bugün [izole manuel web denemesini](orion-lifecycle-plan/06-manual-test.md) yapabilir. Geliştirme testleri temiz VM/güç kesintisi veya gerçek kullanıcı pencere/logon kabulünün yerine geçmez.

Tarih: 17 Ekim 2026. Aşama 00/02 tamamlandı; 01/03/04 DOĞRULAMA EKSİK. Aşama 05 paket üretimi, TUF güveni ve paketli kod/data/state arayüzleri uygulandı; **05 DOĞRULAMA EKSİK:** temiz Windows VM ve üretim güven kökü/yayın kabulü açık. 06 yardımcı/kurtarma uygulandı, **DOĞRULAMA EKSİK**; 07–08 başlamadı. [06 sonuç kaydı](orion-lifecycle-plan/06-update-helper-and-recovery.md), [05 sonuç kaydı](orion-lifecycle-plan/05-packaging-and-release-metadata.md), [04 sonuç kaydı](orion-lifecycle-plan/04-launcher-and-autostart.md).

Bu dosya yeni oturumların giriş noktasıdır. Ayrıntılı plan aşama dosyalarına ayrılmıştır. Önce bu rehberi, sonra ortak kararları ve yalnız seçilen aşamanın gerekli belgelerini oku. Güncel kullanıcı talimatı: yalnız 06, testleri kendin sırayla çalıştır, sonuçları MD'ye kaydet, görev değişikliklerini yerel commit et ve dur; push yapma. Windows başlangıcı ve otomatik güncelleme etkinleştirilmez. Computer Use ve manuel pencere/logon kontrolü bu oturumun ön koşulu değildir. Eksik kabulü geçmiş veya yayını onaylanmış gösterme.

**Önceki 04 takip kararı (tarihsel):** kullanıcı Computer Use'u durdurdu; pencere/gerçek logon manuel kabulünü Aşama 05 sonrasına erteledi. C AppData gerçek kaynak kurulum/izole Startup/Desktop çağrıları geçti ve test süreçleri kapatıldı. 04 oturumu kod testi/sonuç commit'iyle bitti; sonraki oturumda yalnız 05'e geçiş yetkisi verildi. [Önceki 05 promptu](orion-lifecycle-plan/05-session-prompt.md). 01/03/04 açık borçları görünür kalır; 05 geliştirmesi bu ertelenmiş manuel kontrollerle bloke edilmedi.

17 Ekim ek kullanıcı talebiyle Router çevrimdışı hesap hazırlama ve Router'a özel manuel sanal anahtar akışı uygulandı; [arayüz/sonuç kaydı](router-account-access-followup.md) 05 oturumunda okundu ve endpoint/data bağımlılıkları paket sözleşmesine işlendi. Önceki açık kabul etiketleri değişmez.

## Şimdi nereden başlanacak?

**Güncel 05 devri:** [05 sonuç kaydını](orion-lifecycle-plan/05-packaging-and-release-metadata.md) ve [paket araçları/güven işletimini](../scripts/release/README.md) oku. Paket geliştirmesi 06–08 yetkisi değildir. SDK'sız PATH ile development bilgisayarında gerçek ZIP testi, temiz VM kabulünün yerine geçmez. Aşağıdaki 04/03 özetleri tarihsel devir bağlamıdır; son watchdog düzeltmesi ve geçen 35 + 9 / 51 + 2 sonuçları korunur.

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
| 05 | [Hazır paket ve sürüm metaverisi](orion-lifecycle-plan/05-packaging-and-release-metadata.md) | 02, 03 | DOĞRULAMA EKSİK |
| 06 | [Güncelleme yardımcısı ve kurtarma](orion-lifecycle-plan/06-update-helper-and-recovery.md) | 01, 02, 03, 05 | DOĞRULAMA EKSİK |
| 07 | [Otomasyon ve Flutter ekranı](orion-lifecycle-plan/07-automatic-updates-and-flutter.md) | 04, 06 | BAŞLANMADI |
| 08 | [Router geçişi ve kontrollü yayın](orion-lifecycle-plan/08-router-transition-and-rollout.md) | 04, 05, 06, 07 | BAŞLANMADI |

Durum değerleri: `BAŞLANMADI`, `DEVAM EDİYOR`, `DOĞRULAMA EKSİK`, `TAMAMLANDI`. Gerekli test çalıştırılamadıysa aşama tamamlandı sayılmaz; ortam eksikliği ve kalan doğrulama kaydedilir. Aşama tablosu ile kendi dosyasındaki durum birlikte güncellenir. 00/02 tamamlandı; 01/03/04/05 açık kabulleri kendi sonuç kayıtlarındadır; 06 güncel sonuç kaydından izlenir, 07–08 başlamadı.

## Her oturumun çalışma yöntemi

17 Ekim 2026 güncel kapsam: kullanıcı 01/03/04/05 kabul borçları korunarak yalnız 06 geliştirmesini yetkilendirdi. Önceki kapsam istisnaları tarihsel kayıttır. Bu oturumda 06 test/belge/yerel commit sonrasında durulur; 07–08'e geçilmez.

1. Repo talimatlarını ve bu ana rehberi oku. Kullanıcı bir aşama belirttiyse onu seç; belirtmediyse burada belirtilen sıradaki işi seç. Ön koşulları tamamlanmamış aşamayı uygulama; yalnız yukarıdaki 16 Ekim kullanıcı kararı, 01'in açık kabul borcuyla 02 geliştirmesine devam etmek için kayıtlı istisnadır. Sonraki 16 Ekim kullanıcı talimatı 03 geliştirmesini de yetkilendirdi; yayın kabulü veya eksik testlerin tamamlanması anlamına gelmez.
2. Ortak ürün kararlarını, seçilen aşama dosyasını ve orada belirtilen teknik sözleşmeleri oku. Ön koşulların sonuç kayıtlarını incele; başka aşamaya ait ayrıntıları yalnız arayüz ihtiyacı varsa aç.
3. Kod ve testlerde mevcut durumu doğrula. Belgede planlanan arayüzün gerçekten uygulanmış olduğunu varsayma. Aşamalar arası uyumsuzluk varsa kapsamı ve bağımlılıkları güncelle.
4. Seçilen aşamayı küçük değişikliklerle uygula; yalnız gerekli ön koşul düzeltmelerini ayrıca belirt. Başka aşama özelliğini kendiliğinden etkinleştirme. Gerçek kullanıcı verisini test amacıyla taşıma/silme; ACL mirasını otomatik değiştirme.
5. Aşamanın kabul testlerini çalıştır. Komut, ortam, sonuç, çalıştırılamayan kontroller ve korunma kanıtını aşama dosyasına yaz. Geçmeyen/çalıştırılamayan zorunlu kabul koşulunu geçmiş gösterme.
6. Sonuç kaydına değişen dosyaları, kararlı arayüzleri, geri alma yöntemini ve sonraki aşamanın bilmesi gerekenleri ekle. Ortak sözleşme değiştiyse ilgili bağlantıları güncelle.
7. Ana tablodaki durumu ve sıradaki aşamayı güncelle; sonucu kaydet ve aşama commit’ini oluştur. Son kullanıcı talimatıyla bu oturumda 06 commit’inden sonra dur; gerçek test/ortam engellerini ve tamamlanmamış kabulü açık bırak. Push yetkisi verilmedi.

## Bağımlılıklar neden var?

Her iş aynı dosyaya veya aynı oturuma bağlı değildir. Örneğin paket üretimi ile Windows başlangıcı farklı işlerdir; ikisi de kayıt/konum sözleşmelerini kullanır. Güncelleme yardımcısı ise hem paket doğrulamasına hem güvenilir süreç yönetimine ihtiyaç duyar. Ayrı dosyalar bu bağımlılıkları kaldırmaz; ilgili arayüz ve bitiş kanıtını açık hale getirir.

Konum taşıma 03 içinde kendi kalıcı günlüğü ve kurtarma testleriyle tamamlanabilir; 06'daki güncelleyiciyi bekleyerek döngüsel bağımlılık oluşturmaz. 06 daha sonra taşıma sözleşmesiyle ortaklaştırma yapabilir. Legacy runtime güvenle taşınamıyorsa 03 bu sınırı açıkça raporlar; 05/08 paketli dönüşümü bitmeden o taşıma yolu kullanıcıya açılmaz. Bu eksik özellik tamamlandı diye kaydedilmez.

## Yeni oturumda verilecek görev

> Önce [06 sonuç kaydını](orion-lifecycle-plan/06-update-helper-and-recovery.md), [manuel deneme rehberini](orion-lifecycle-plan/06-manual-test.md) ve [yardımcı işletimini](../scripts/release/README.md) oku. Önceki 01/03/04/05 ve 06 açık kabullerini geçmiş sayma. 07–08 için yeni kullanıcı kapsamı gerekir; Computer Use/Windows başlangıcı/otomatik güncelleme etkinleştirme yetkisi yoktur.

## Belge otoritesi ve geçmiş

Uygulama için ana giriş bu dosyadır. Ortak ürün/teknik kuralların kaynağı [ortak sözleşmeler](orion-lifecycle-plan/shared-contracts.md), aşamaya özel kapsam ve testlerin kaynağı ilgili aşama dosyasıdır. Eski [tasarım önerisi](orion-update-strategy-proposal.md) gerekçe ve kaynak olarak korunur; farklılıkta bu güncel belge kümesi esas alınır. Planlar uygulama kanıtı değildir; tamamlanma yalnız test ve kodla doğrulanır.
