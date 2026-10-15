# Orion güncelleme ve açılış stratejisi — öneri

Durum: 15 Ekim 2026 için tasarım notudur. Güncelleme sistemi bu çalışmada uygulanmadı. Yönetim girişindeki geri bağlantısı/varsayılan şifre ipucu ayrı, tamamlanmış bir arayüz değişikliğidir.

## Ana karar

Kullanıcı tek ürün olarak Orion'u görmeli. Flutter ana ekranında tek bir **Güncellemeler** alanı ve **Güncelle ve yeniden başlat** işlemi olmalı. Hub, Flutter ve Router depoları ayrı kalabilir; son kullanıcıya bağımsız ve uyumsuz güncellemeleri elle eşleştirme sorumluluğu verilmemeli.

Hub ile PC'deki Flutter dağıtımı, birlikte test edilmiş bir Orion masaüstü sürümü olarak planlanmalı. Paketleri ayrı olabilir; yalnızca değişen paket indirilir. Router bağımsız ürün/kurulum olarak kalır ve kendi sürümünü korur. Ana güncelleme ekranı Router'ı da gösterir fakat sadece gerektiğinde, o Router için yetki varsa günceller. Aynı sürüm numaraları zorunlu değildir; ilan edilmiş protokol uyumluluğu zorunludur.

## Açılış ve yaşam döngüsü

Installer ilk kurulumda bileşenleri ve kullanıcı tercihlerini kaydeder: Hub, Router, seçili STT/TTS servisleri, modeller, donanım profili ve otomatik başlatma. Sonrasında normal kullanıcı installer veya yönetim paneline girmek zorunda kalmaz.

Küçük bir Orion başlatıcısı/masaüstü yöneticisi kurulmalıdır. Windows kullanıcı oturumunun açılması ve masaüstü kısayolu aynı başlatıcıyı kullanır. Yönetici tek kopya çalışır, seçili kurulumları bulur, bağımlılık sırasına göre hazırlar ve mevcut servisleri çoğaltmadan Flutter'ı açar. Flutter penceresinin otomatik görünmesi ayrı tercih olabilir. Oturum açılmadan hizmet çalıştırmak, etkileşimli tarayıcı/masaüstü erişiminden ayrı bir Windows hizmeti seçeneğidir; ilk sürüm için kullanıcı oturumu başlangıcı yeterlidir.

Kurulum kaydı her bileşenin kalıcı kimliğini, tam kurulum yolunu, dağıtım türünü, çalışan sürümünü ve kontrollü başlatma yöntemini içermelidir. Port numarası veya PATH sırası hangi kurulumu güncelleyeceğimizi belirlememelidir. Geliştirme kopyası ile kullanıcı kurulumu aynı bilgisayarda bulunabilir; işlem öncesinde hedef kimlik/yol/çalışan süreç eşleşmesi doğrulanmalıdır. Sertifikalar ve Router/Hub UUID'leri güncelleme boyunca korunur.

## Güncellemeyi kim yapar?

Flutter yalnızca kullanıcı arayüzüdür. Hub normal çalışırken sürüm bilgisini arayüze iletebilir; dosyaları değiştiren asıl görev Hub'ın içinde çalışan bir thread olmamalıdır. Güncelleme sırasında Hub ve Flutter kapanabileceği için, işi ayrı Orion güncelleme yardımcısı yürütmelidir. Başlatıcı onu başlatır; ilerleme ve sonuç kalıcı dosya/günlük üzerinden izlenir. Yardımcının kendisi mümkün olduğunca küçük ve seyrek güncellenen bir başlangıç katmanına dayanır.

Masaüstü yöneticisi/güncelleme yardımcısı yerel ve dar kapsamlı olmalıdır: imzalı katalogdaki kayıtlı bileşenleri günceller; istemciden genel komut, keyfi dosya yolu veya çalıştırılacak betik kabul etmez. Flutter'da düğme görünmesi yönetici yetkisi vermez. Sunucu aynı yerel sahip yetkisini ayrıca denetler.

## Dağıtım ve uyumluluk

Normal kullanıcı kurulumu GitHub `main` dalına gelen her commit'i otomatik çekmemelidir. Stable ve isteğe bağlı beta kanallarında, testlerden geçen etiketli sürümler ve hazır paketler yayımlanmalıdır. GitHub Releases dağıtım yeri olabilir; kullanıcıda Git, Flutter SDK veya uygulamayı derleyecek araçlar bulunması gerekmemelidir.

Bir Orion sürüm kaydı şu bilgileri bağlar:

- Orion ürün sürümü ve paketlerin tam Hub/Flutter/Router sürümleri.
- Desteklenen API/protokol aralıkları ve veritabanı şema uyumu.
- İşletim sistemi, mimari ve CPU/CUDA gibi uygun çalışma profilleri.
- İndirme adresi, boyut, özet, doğrulanabilir imza ve yayımlama bilgisi.
- Gereken boş disk alanı, yeniden başlatma ihtiyacı ve geri dönüş kısıtları.

Hub/Flutter uyumsuzsa tek işlem planında birlikte hazırlanırlar. Router mevcut sürümüyle uyumluysa ona dokunulmaz. Farklı bilgisayardaki Router ile aynı anda dosya değiştirme garantisi verilemez; uzaktan yükseltmeler geriye uyumlu geçiş aralığı, açık yetki ve kontrollü sıra gerektirir.

Paketler yayımlanmadan önce CI'da üretilir. Python çalışma zamanı/bağımlılıkları her sürüm için ayrılır; aktif global ortamda pip ile yerinde güncelleme yapılmaz. Flutter web hazır `build/web`, masaüstü istemcisi hazır çalıştırılabilir paket olarak gelir. Sadece arayüz değiştiğinde büyük modeller tekrar indirilmez. STT/TTS motoru ve model dosyaları ayrı sürümlenir; mevcut model uyumluysa korunur.

İmzalı güncelleme metaverisi, dosya özeti ve güvenilen yayımlama kimliği doğrulanmalıdır. Sadece HTTPS veya dosyayla aynı yerden indirilen SHA256 değeri, yayıncının kimliğini tek başına kanıtlamaz. Anahtar yenileme, eski güvensiz sürüme yönlendirme ve metaveri eskimesi tasarıma dahildir. TUF bu metaveri doğrulamasını ele alır; GitHub artifact attestations derlemenin kaynağına ek kanıt sağlar. Bunlar kurulum işlemini otomatik olarak kesintiye dayanıklı hale getirmez.

## Kesintiye dayanıklı işlem

Aktif sürümün dosyalarının üzerine yazmak yerine, her sürüm ayrı bir dizine hazırlanır. Ayarlar, konuşmalar, modeller, veritabanları ve sertifika/anahtarlar sürüm dizinlerinden ayrıdır. Son çalışan sürüm, güncelleme tamamlanana kadar saklanır.

1. **Denetle:** hedef kurulum, izin, uyumluluk, disk alanı ve güncelleme kilidi doğrulanır. Aynı bileşene iki güncelleme başlayamaz.
2. **İndir:** yeni paket ayrı geçici alana alınır. Bu sırada mevcut Orion kullanılmaya devam edebilir. Ağ kesilirse destekleniyorsa indirme devam eder; son dosya mutlaka yeniden doğrulanır.
3. **Hazırla:** imza/özet doğrulanır, paket ayrı sürüm dizinine açılır, bağımlılıklar hazır edilir. Aktif sürüme henüz dokunulmaz.
4. **Bakım durumuna geç:** etkilenen bileşenler yeni iş kabul etmez. Aktif sohbet/ses/araç işlemlerinin tamamlanması beklenir. Uzun süren bir iş varsa kullanıcıya bekletme/erteleme bilgisi verilir. Diğer bağımsız servisler gereksiz yere durdurulmaz.
5. **Veriyi koru:** gerekiyorsa tutarlı veritabanı yedeği alınır; şema geçişi planı ve disk alanı doğrulanır.
6. **Geçişi kaydet ve uygula:** kalıcı güncelleme günlüğü, hedef ve önceki sürüm bilgisi yazılır. Başlatıcı yalnızca eksiksiz hazırlanmış sürüm setine geçer. Süreçler sadece portlarına bakılarak öldürülmez; kayıtlı sahiplikleri doğrulanır.
7. **Sağlığı doğrula:** yeni Hub'ın hazır olması, veritabanı/şema, worker ve Flutter/API sürüm uyumu kontrol edilir. Router etkilenmişse eski UUID/SPKI korunmalıdır. Ücretli bir bulut isteği veya harici sağlayıcının geçici erişilebilirliği zorunlu sağlık testi yapılmaz.
8. **Tamamla:** ancak bu kontrollerden sonra sürüm başarılı ve son çalışan sürüm olarak kaydedilir; iş kabulü açılır. Eski paketler belirlenen saklama sınırı içinde daha sonra temizlenir.

Elektrik kesilirse başlangıçtaki yardımcı kalıcı günlüğü okur. Eksik indirmeyi çalıştırmaz. Geçiş öncesi kesintide son sağlam sürüm açılabilir; geçiş sonrası ama sağlık kontrolü öncesi kesintide hedef tekrar doğrulanır veya geri dönülür. Kullanıcıya bozuk bir yeni sürümün sonsuz açılış döngüsü gösterilmez; deneme sayısı sınırlanır ve onarma ekranı kalır.

Veritabanı geri dönüşü ayrıca planlanmalıdır. Eski kodu geri seçmek, yeni şemanın eski kodla çalışacağı anlamına gelmez. Öncelik geriye uyumlu şema geçişleridir. Uyumsuz geçişte ya doğrulanmış veri yedeğiyle kontrollü geri dönüş uygulanmalı ya da kurtarma/onarım gerekliliği açıkça gösterilmelidir. Sağlık kontrolü bitene kadar yeni kullanıcı işlerini açmamak bu riski azaltır. Yarım kalmış dış etkili araç işlemleri otomatik tekrar edilmemelidir.

## Flutter ve telefon deneyimi

Ana ekrandaki bildirim kısa olabilir: **“Orion güncellemesi hazır — işlerin bitince güncelle.”** Ayrıntı açılırsa hangi bilgisayardaki hangi bileşenlerin değişeceği, sürümler, boyut ve beklenen kesinti görülür. Ortalama kullanıcıya tek bir onay yeterli olmalıdır; uyum planını sistem çıkarır.

Telefon sürümünde iki ayrı bilgi gösterilmelidir:

| Bildirim | Hedef | Varsayılan yetki |
| --- | --- | --- |
| Bu telefondaki Orion uygulaması güncel değil | Android/iOS istemcisi | Platformun uygulama güncelleme yolu |
| Bağlı bilgisayarda Orion/Router güncellemesi var | Kimliği bilinen PC/Router | Durumu gör; normal kullanıcı bilgisayarı güncelleyemez |

Telefondan “bilgisayarda güncelleme iste” önerisi gönderilebilir; ilk sürümde son onay bilgisayar sahibinden PC'de alınmalıdır. İleride uzaktan bakım istenirse ayrı, açık ve iptal edilebilir sahip yetkisi tasarlanır. Normal Hub hesabı veya cihaz eşleştirmesi bu yetkiyi kendiliğinden sağlamaz. Router başka kullanıcı/bilgisayara aitse Router sahibinin ayrı onayı gerekir; model kullanma anahtarı güncelleme yetkisi değildir.

Hub güncellenirken telefona canlı ilerleme aktarmak her zaman mümkün olmayabilir. İstemci son bilinen güncelleme durumu ve zamanını gösterir, bağlantı dönünce sonucu doğrular. Bağlantı yokken tahmini yüzde veya doğrulanmamış başarı gösterilmez. Yerel ayrıcalıklı güncelleme yardımcısı sırf bu amaçla internete açılmaz.

Telefon native uygulaması Hub ile aynı anda zorunlu güncellenmemelidir. Desteklenen API aralığı sayesinde kısa bir sürüm farkı tolere edilir. Android dağıtımına göre Play in-app updates veya normal mağaza akışı; iOS için mağaza güncellemesi kullanılır. Web istemcisi sunucudan yeni hazır sürümü alır; açık sohbetin ortasında zorunlu sayfa yenilemesi yerine güvenli anda yeni sürüme geçiş sunulur.

## Mevcut Router güncelleyicisi

`core/updater.py` bugün güncelleme durumunu bellekte tutuyor, `main` dalından sürüm kontrol ediyor, aktif klasörde Git/pip/build işlemleri yapıyor ve yeniden başlayan sürümün sağlığını görmeden başarı yazıyor. Fast-forward başarısızlığında hard reset yolu da var. `bin/update.py` benzer bir CLI akışı sunuyor.

Sürüm denetimi, bildirim, ilerleme ekranı ve yönetim yetkileri değerlendirilebilir; fakat bunları Flutter'a bağlamak tek başına elektrik kesilmesi/geri dönüş gereksinimini karşılamaz. Dosya değiştiren bölümün yeni yardımcının işlem modeline taşınması gerekir. Güncelleme denetlenemediğinde de “güncelsiniz” sonucu verilmemeli; denetlenemediği ve son başarılı kontrol zamanı gösterilmelidir. Geliştirici depolarındaki yerel değişiklikler otomatik silinmemeli; geliştirici modu tüketici paket güncellemesinden ayrılmalıdır.

## Uygulama sırası ve kabul koşulları

İlk aşama kurulum kimliği/konumu ve başlatıcıyı kesinleştirmek, sonra hazır paket + sürüm uyumluluk kaydını üretmektir. Ardından kalıcı işlem günlüğü, hazırlama/geçiş/geri dönüş uygulanır. Flutter güncelleme ekranı bu temelin üzerine gelir. Router entegrasyonu ve telefondaki bildirim/ayrı yetki son aşamalardır.

Her kritik aşamada süreç sonlandırma ve makine kapanması benzetilmelidir: yarım indirme, yanlış imza, yetersiz disk, kilitli dosya, paket geçişi, şema değişimi ve yeni sürümün açılamaması. Ayrıca iki eşzamanlı güncelleme, yanlış kurulum hedefi, korunmuş sertifika/UUID, eski mobil istemci, internet yokluğu ve tekrar başlayan bir işlemin dış etkileri çoğaltmaması test edilmelidir.

## Kaynaklar

- TUF güncelleme metaverisi ve güven modeli: https://theupdateframework.io/docs/overview/
- GitHub Releases: https://docs.github.com/en/repositories/releasing-projects-on-github/about-releases
- GitHub build provenance: https://docs.github.com/en/actions/how-tos/secure-your-work/use-artifact-attestations/use-artifact-attestations
- Android uygulama içi güncelleme: https://developer.android.com/guide/playcore/in-app-updates
