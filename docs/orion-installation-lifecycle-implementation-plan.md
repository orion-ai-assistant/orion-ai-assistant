# Orion kurulum ve güncelleme — ana uygulama rehberi

Tarih: 16 Ekim 2026. Durum: Aşama 00 tamamlandı; Aşama 01 DOĞRULAMA EKSİK. Windows Job sahipliğiyle hızlı ebeveyn ölümü/torun açığı giderildi; son yaşam döngüsü setinde 40 test geçti. Aşama 02 kayıt/seçim/doğrulama çekirdeği tamamlandı (61 test + 13 alt test, gerçek Router ve AppData kayıt fixture’ı). Aşama 03 çekirdeği uygulandı, DOĞRULAMA EKSİK; kullanıcı bu oturumda 03 commit’inden sonra durmayı istedi.

Bu dosya yeni oturumların giriş noktasıdır. Ayrıntılı plan aşama dosyalarına ayrılmıştır. Önce bu rehberi, sonra ortak kararları ve yalnız seçilen aşamanın gerekli belgelerini oku. Son kullanıcı talimatı: bu oturumda 03 üzerinde çalış, test/sonuç kaydını ve ayrı commit’ini oluştur, sonra dur; devamı başka oturumda olacak. Önceki tüm aşamalara kesintisiz devam talimatı bu kararla daraltıldı. Eksik kabulü geçmiş veya yayını onaylanmış gösterme.

## Şimdi nereden başlanacak?

**Sıradaki iş: 03’ün kalan kabulü ve ürün entegrasyonu.** [03 oturum devir kaydını](orion-lifecycle-plan/03-session-handoff.md) ve [03 sonuçlarını](orion-lifecycle-plan/03-installation-paths-and-migration.md) oku. Son kullanıcı kararı: 03 değişikliklerini commit’le ve dur; 04’e bu oturumda başlanmayacak. 02 sonuçları [aşama kaydında](orion-lifecycle-plan/02-installation-registry-and-router-selection.md). Önce [Aşama 02 oturum devir kaydını](orion-lifecycle-plan/02-session-handoff.md) ve [01'in son Windows Job sonuç kaydını](orion-lifecycle-plan/01-process-lifecycle.md) oku. 16 Ekim 2026 kullanıcı kararıyla, Aşama 01'in kalan kabul doğrulamaları açık tutularak Aşama 02 geliştirmesine devam edilebilir. Kullanıcı kendi manuel denemelerini tek tuşla başlatma akışı hazır olduğunda yapacak; agent aşama testlerini kendisi çalıştıracak. Bu istisna 01'i tamamlanmış veya ürünü yayına hazır yapmaz. Hızlı ebeveyn ölümü/torun testi ve son 40 yaşam döngüsü testi geçti; eski başarısız tablo tarihsel kayıttır. Hyper-V normal ürün testlerinin ön koşulu değildir. [00 sonuçları](orion-lifecycle-plan/00-inventory-and-test-environment.md) ve [envanter](orion-lifecycle-plan/00-current-inventory.md) korunur.

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
| 04 | [Başlatıcı ve Windows başlangıcı](orion-lifecycle-plan/04-launcher-and-autostart.md) | 01, 02, 03 | BAŞLANMADI |
| 05 | [Hazır paket ve sürüm metaverisi](orion-lifecycle-plan/05-packaging-and-release-metadata.md) | 02, 03 | BAŞLANMADI |
| 06 | [Güncelleme yardımcısı ve kurtarma](orion-lifecycle-plan/06-update-helper-and-recovery.md) | 01, 02, 03, 05 | BAŞLANMADI |
| 07 | [Otomasyon ve Flutter ekranı](orion-lifecycle-plan/07-automatic-updates-and-flutter.md) | 04, 06 | BAŞLANMADI |
| 08 | [Router geçişi ve kontrollü yayın](orion-lifecycle-plan/08-router-transition-and-rollout.md) | 04, 05, 06, 07 | BAŞLANMADI |

Durum değerleri: `BAŞLANMADI`, `DEVAM EDİYOR`, `DOĞRULAMA EKSİK`, `TAMAMLANDI`. Gerekli test çalıştırılamadıysa aşama tamamlandı sayılmaz; ortam eksikliği ve kalan doğrulama kaydedilir. Aşama tablosu ile kendi dosyasındaki durum birlikte güncellenir. Aşama 00 tamamlandı; 01'in kalan kabul kontrolleri sonuç kaydındadır; 02 tamamlandı; 03 çekirdeği uygulandı ancak kabul eksik; 04–08 başlamadı.

## Her oturumun çalışma yöntemi

1. Repo talimatlarını ve bu ana rehberi oku. Kullanıcı bir aşama belirttiyse onu seç; belirtmediyse burada belirtilen sıradaki işi seç. Ön koşulları tamamlanmamış aşamayı uygulama; yalnız yukarıdaki 16 Ekim kullanıcı kararı, 01'in açık kabul borcuyla 02 geliştirmesine devam etmek için kayıtlı istisnadır. Sonraki 16 Ekim kullanıcı talimatı 03 geliştirmesini de yetkilendirdi; yayın kabulü veya eksik testlerin tamamlanması anlamına gelmez.
2. Ortak ürün kararlarını, seçilen aşama dosyasını ve orada belirtilen teknik sözleşmeleri oku. Ön koşulların sonuç kayıtlarını incele; başka aşamaya ait ayrıntıları yalnız arayüz ihtiyacı varsa aç.
3. Kod ve testlerde mevcut durumu doğrula. Belgede planlanan arayüzün gerçekten uygulanmış olduğunu varsayma. Aşamalar arası uyumsuzluk varsa kapsamı ve bağımlılıkları güncelle.
4. Seçilen aşamayı küçük değişikliklerle uygula; yalnız gerekli ön koşul düzeltmelerini ayrıca belirt. Başka aşama özelliğini kendiliğinden etkinleştirme. Gerçek kullanıcı verisini test amacıyla taşıma/silme; ACL mirasını otomatik değiştirme.
5. Aşamanın kabul testlerini çalıştır. Komut, ortam, sonuç, çalıştırılamayan kontroller ve korunma kanıtını aşama dosyasına yaz. Geçmeyen/çalıştırılamayan zorunlu kabul koşulunu geçmiş gösterme.
6. Sonuç kaydına değişen dosyaları, kararlı arayüzleri, geri alma yöntemini ve sonraki aşamanın bilmesi gerekenleri ekle. Ortak sözleşme değiştiyse ilgili bağlantıları güncelle.
7. Ana tablodaki durumu ve sıradaki aşamayı güncelle; sonucu kaydet ve aşama commit’ini oluştur. Son kullanıcı talimatıyla bu oturumda 03 commit’inden sonra dur; gerçek test/ortam engellerini ve tamamlanmamış kabulü açık bırak. Push yetkisi verilmedi.

## Bağımlılıklar neden var?

Her iş aynı dosyaya veya aynı oturuma bağlı değildir. Örneğin paket üretimi ile Windows başlangıcı farklı işlerdir; ikisi de kayıt/konum sözleşmelerini kullanır. Güncelleme yardımcısı ise hem paket doğrulamasına hem güvenilir süreç yönetimine ihtiyaç duyar. Ayrı dosyalar bu bağımlılıkları kaldırmaz; ilgili arayüz ve bitiş kanıtını açık hale getirir.

Konum taşıma 03 içinde kendi kalıcı günlüğü ve kurtarma testleriyle tamamlanabilir; 06'daki güncelleyiciyi bekleyerek döngüsel bağımlılık oluşturmaz. 06 daha sonra taşıma sözleşmesiyle ortaklaştırma yapabilir. Legacy runtime güvenle taşınamıyorsa 03 bu sınırı açıkça raporlar; 05/08 paketli dönüşümü bitmeden o taşıma yolu kullanıcıya açılmaz. Bu eksik özellik tamamlandı diye kaydedilmez.

## Yeni oturumda verilecek görev

> [03 devir kaydındaki devam promptunu](orion-lifecycle-plan/03-session-handoff.md) kullan. Önce 03 kalanlarını tamamla; kullanıcı bu oturumda 03 commit’inden sonra durulmasını istedi. 01 ve 03 açık kontrollerini geçmiş sayma; 04’e geçiş ayrı oturum/kapsam kararıdır.

## Belge otoritesi ve geçmiş

Uygulama için ana giriş bu dosyadır. Ortak ürün/teknik kuralların kaynağı [ortak sözleşmeler](orion-lifecycle-plan/shared-contracts.md), aşamaya özel kapsam ve testlerin kaynağı ilgili aşama dosyasıdır. Eski [tasarım önerisi](orion-update-strategy-proposal.md) gerekçe ve kaynak olarak korunur; farklılıkta bu güncel belge kümesi esas alınır. Planlar uygulama kanıtı değildir; tamamlanma yalnız test ve kodla doğrulanır.
