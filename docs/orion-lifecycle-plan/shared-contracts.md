# Ortak kararlar ve teknik sözleşmeler

Durum: tasarım sözleşmesidir; uygulanmış özellik listesi değildir. Her aşamada bu dosyanın ürün kararları ve ilgili teknik bölümleri okunur. Aşamaya özel uygulama/test/sonuç bilgisi ilgili aşama dosyasında tutulur. Ana giriş: [ana plan](../orion-installation-lifecycle-implementation-plan.md).

17 Ekim 2026 Aşama 05 uygulama eki: schema-1 bileşen kaydı isteğe bağlı `package: {id, sha256, length, inventory_sha256}` alanını kabul eder. Bu yalnız `distribution=packaged` için, TUF ile doğrulanmış yeni kurulumdan sonra yazılır. `state/installed-package.json` dosya envanteri kayıt içindeki hash'e bağlıdır; kendiliğinden oluşturulmuş yerel makbuz imza sayılmaz. Başlatıcı/kayıtlı Router incelemesi envanteri denetler; kayıt dışı paket klasörüne güven vermez. Başlangıç TUF kökü güvenilen kurucudan gelir ve `<registry-parent>/bootstrap/trusted-root.json` olarak korunur. Paket arayüzü ve veri alanı ayrıntıları [05 sonuçlarında](05-packaging-and-release-metadata.md) ve [araç rehberinde](../../scripts/release/README.md) yer alır. Üretim kökü/temiz VM/yayın kabulü açık; otomatik güncelleme açılmaz.

## 1. Ürün kararları

17 Ekim 2026 Aşama 07 eki: Kullanıcı 06 kabul borcu korunarak yalnız 07 geliştirmesini yetkilendirdi. Stable kontrol/hazırlama ve Flutter durum/tercih kartı vardır; [07 arayüz ve sonuç kaydı](07-automatic-updates-and-flutter.md). Ayrı retained bootstrap watcher'ı dış launcher başlatır; Hub Job'ında breakaway açılmaz. Yeni kurulumun otomatik güncelleme tercihi kabul/yayın eksikleri nedeniyle hâlâ kapalıdır. Hazır sürüm yalnız sonraki normal launcher açılışında ve 06'nın **bütün servisler kapalı** kapısında uygulanır. Canlı drain açılmadı. Registry şeması 1; Hub tercihi Router'a yayılmaz. Normal telefon oturumu durum okuyabilir ama PC tercihi/aktivasyon yetkisi kazanamaz. Bekleyen işlemden sonra değişen bool tercihler yayın/rollback'te korunur; diğer registry alanları eski/yeni işlem snapshot'ıyla aynı olmalıdır. 08 uygulanmadı. Aşağıdaki 06 eki o aşamanın tarihsel devridir.

17 Ekim 2026 Aşama 06 eki: Flutter web kalır; tek komut kurulum ana tercihidir. 06 helper CLI ve işlem şeması [06 kaydında](06-update-helper-and-recovery.md), işletimi [paket rehberindedir](../../scripts/release/README.md). Paketli süreç kayıtları kod kökünü içerdiğinden güncellenen sürümün `state_path` değeri `<component>/state/releases/<package-id>` olur; önceki state ve tüm `data_path`/bileşen UUID'leri korunur. Schema-1 registry değişmez. Yardımcı eski runtime'ı yamalamaz veya eski Job/supervisor kimliğini yeni köke sahiplenmez. 05 taban sürümü için aktivasyon yalnız servisler kapalıyken yapılır; çalışan kullanım `waiting_for_idle` durumunda kalır. Otomatik canlı drain ve 07 zamanlayıcısı etkin değildir.

- İlk hedef Windows'ta kullanıcıya özel yerel kurulumdur. İlk sürüm Windows oturum açılışında çalışır; sistem hizmeti değildir. Docker ve geliştirme kurulumları açıkça tanımlanır, kapsam dışı özellikler yanlışlıkla destekleniyormuş gibi gösterilmez.
- Varsayılan ana konum `%LOCALAPPDATA%\Orion` olur. İlk kurulumda Gözat ile örneğin `D:\Orion` seçilebilir. D sürücüsü kendiliğinden seçilmez. Mevcut kurulumun konumu yeni varsayılandan önce gelir.
- İlk ekranda hiçbir seçim yapılmazsa programlar, veriler, modeller ve indirmeler AppData altında kalır. Modeller varsayılan olarak seçilen ana kökün `Models` klasöründedir. Gelişmiş ayarlarda model, büyük indirme/önbellek, motor/runtime paketleri ve yedek konumları ayrı seçilebilir; örneğin uygulama AppData'da, büyük içerikler `D:\Orion` altında olabilir. Hiçbir sürücü kullanıcı adına otomatik seçilmez. Konumlar kurulumdan sonra ayarlardan da değiştirilebilir; taşıma uygulama tarafından yönetilir.
- Hub, Router ve Flutter ayrı paket/sürüm kimliklerini korur. Router tek başına kurulabilir, çalıştırılabilir ve güncellenebilir. Ortak güncelleme ekranı bu bağımsızlığı değiştirmez. Hub ve Flutter protokol uyumu planlanır; zorunlu aynı sürüm numarası kullanılmaz.
- Mevcut Router algılanır ve yerinde kaydedilir. Yeni Orion kurulumu ikinci Router oluşturmaz veya eskisini sessizce taşımaz. Birden fazla aday varsa kullanıcıya kimliği ve yolu gösterilip seçim yaptırılır.
- Router ekranında `Mevcut Router'ı kullan`, `Yeni Router kur` ve seçili yerel kurulum için `Konumu taşı` ayrı işlemlerdir. Mevcut Router klasörü Gözat ile de seçilebilir. Bağlanmak, yönetmek ve taşımak ayrı yetkilerdir; uzak Router için yerel klasör taşıma seçeneği sunulmaz.
- Otomatik güncelleme yeni kurulumda varsayılan açıktır. Mevcut kullanıcının kapattığı tercih korunur. Bu tercih arka planda kontrol/indirme/hazırlama ve güvenli zamanda uygulamayı kapsar; aktif kullanım sırasında habersiz kapatma yetkisi vermez.
- Bu varsayılan nihai ürün davranışıdır: Aşama 5–6 paket doğrulama ve kurtarma kabul koşulları geçmeden otomatik güncelleme etkinleştirilmez. Mevcut güvencesiz Router güncelleyicisi bu tercihe bağlanmaz.
- Windows ile başlatma ve Flutter penceresini gösterme ayrı tercihlerdir; kurulumda açıkça gösterilir. Başlangıç kısayolu kullanıcı tercihine göre oluşturulur, yükleyici çalıştırıldığı için kendiliğinden etkinleşmez.
- Kaynak depodaki her commit kullanıcı sürümü değildir. İmzalı sürüm kaydıyla ilan edilen stable paketler, isteğe bağlı beta kanalı kullanılır. Kullanıcıda Git, Python/Flutter SDK veya derleme araçları bulunması gerekmez.

## 2. Konum ve veri düzeni

```text
%LOCALAPPDATA%\Orion\
  install.json                 # Bu Windows kullanıcısının kurulum dizini; sır içermez
  bootstrap\                   # Küçük başlatıcı, kurtarma ve yardımcı giriş noktası

<seçilen ana kök>\
  Hub\
    releases\<paket-kimliği>\  # Kod ve o sürüme ait çalışma ortamı; yerinde değiştirilmez
    data\                     # DB, ayarlar, kimlikler, konuşmalar, tarayıcı profilleri
    state\                    # Aktif/önceki sürüm, işlem günlüğü ve süreç kayıtları
  Router\
    releases\<paket-kimliği>\
    data\                     # Router DB, persistent, TLS anahtarları ve kalıcı UUID
    state\
  Client\releases\<paket-kimliği>\
  Models\
  Downloads\                  # Büyük indirmeler ve yeniden üretilebilir önbellek
  staging\                    # Doğrulanmamış veya hazırlanan paketler
  backups\
  logs\
```

Bu düzen yeni kurulum hedefidir. Eski kayıtlı Router kendi konumunda kalabilir; kayıt her bileşen için gerçek kod, veri ve durum yollarını tutar. Bir bileşenin konumu ortak kökten tahmin edilmez. Varsayılan kök ile kayıt konumu aynı olduğunda döngüsel yönlendirme oluşmaz. Başlatıcı D diski bulunamadığında hata/onarma gösterebilir; aynı isimli boş yeni kurulum oluşturmaz.

16 Ekim 2026 kullanıcı tercihi bu ortak `Orion` üst klasörünü doğrular: Hub, Router ve Client ayrı alt klasörler ve bağımsız kimliklerle yer alır. D sürücüsündeki geliştirme deposu ile AppData kullanıcı/test kurulumu aynı kullanıcıda yan yana bulunabilir; kayıt ve hedef seçimi bunları açıkça ayırır. Ayrı aktif kurulumlar aynı süreç durumunu, DB/veri dizinini veya kopyalanmış TLS/Router kimliğini paylaşmaz. Mevcut bir kurulumu yerinde kaydetmek ise kimliğini yenilemez. Agent'ın izole AppData test kurulumuna ilişkin yetki ve sınırlar [devir kaydındadır](02-session-handoff.md); bu tercih mevcut verilerin taşındığı veya yeni düzenin uygulandığı anlamına gelmez.

`install.json` şema sürümü, kurulum UUID'si, ana kök, her bileşenin kalıcı UUID'si/türü/yolları, dağıtım biçimi (`legacy-source`, `packaged`, `development`, `docker`, `remote`), sahiplik/yönetim ilişkisi ve tercihleri içerir. Son sağlıklı sürüm ve güncelleme aşamaları bileşen durumunda tutulur. Mutlak ve normalize yollar kullanılır. Mevcut Router'ın `%LOCALAPPDATA%\Orion\router-installation.json` kaydı geçiş boyunca desteklenir; eski tüketiciler kırılmadan tek kayıt otoritesine geçilir. Sırf JSON'da yol bulunması, o hedefi değiştirme yetkisi değildir.

Kayıt yazımı aynı birimde geçici dosya, diske yazımı tamamlama ve değiştirme ile yapılır; önceki geçerli kayıt korunur. Güç kesintisi davranışı ayrıca sınanır. C ve D arasında dosya taşımanın atomik olduğu varsayılmaz. Paket geçişine katılan dosyalar hedefle aynı birimde hazırlanır.

AppData varsayılanı kullanıcıya özel kurulum için uygundur; sınırsız izin, yeterli disk veya bozulmama garantisi değildir. Seçilen konumda yazma denemesi, boş alan, başka kurulumla çakışma, güvenli dosya sistemi ve yol doğrulaması yapılır. İlk kapsam yerel sabit disklerdir; ağ/çıkarılabilir konumlar desteklenmiyorsa açık hata verilir. Varsayılan ACL'ler gereksiz yere yeniden yazılmaz; özel konumda başka kullanıcıların değiştirebildiği çalıştırılabilir/anahtar alanları kabul edilmez. Klasörün gizli olması güvenlik sınırı değildir.

DB ve Python bağımlılıkları için sabit küçük boyut varsayılmaz. Konuşmalar, dosyalar, tarayıcı profilleri, loglar ve yedekler büyüyebilir. Kurulum/güncelleme alan hesabı paket, açılmış içerik, önceki sürüm, model ve gerekli yedeği kapsar. PostgreSQL çalıştırılabilir dosyası sürümlü runtime'da, veri dizini kalıcı alandadır. PostgreSQL ana sürüm yükseltmesi sıradan kod güncellemesine dahil edilmez.

### 2.1. Mevcut Router seçimi ve doğrulaması

Klasör adı veya açık port Router kanıtı sayılmaz. Seçim sırasında önce içerik çalıştırılmadan paket/depo kimliği, sürüm metaverisi, giriş noktaları, veri konumları ve sahiplik incelenir. İmzalı paket doğrulaması paketli dağıtım için zorunludur; eski kaynak kurulumunda bunun bulunmadığı açıkça gösterilir. Kullanıcının seçtiği klasördeki keyfi betik, sırf doğrulama amacıyla otomatik çalıştırılmaz.

Çalışan Router varsa süreç yolu/başlangıç zamanı, kayıtlı kimlik ve TLS/SPKI güven ilişkisi karşılaştırılır. Ağdan dönen yeni sertifika kendiliğinden güvenilir ilan edilmez. Router kapalıysa yalnız dosya kontrolünden `sağlıklı çalışıyor` sonucu üretilmez; `kurulum tanındı, çalışma doğrulanmadı` gösterilir. Kullanıcı yerel yönetimi yetkilendirirse kontrollü başlatma ve yerel, ücretli sağlayıcı çağrısı gerektirmeyen sağlık testi yapılır. Yetkili DB erişimi, şema ve API uyumu kontrol edilir. Router UUID'si, eşleşmeler ve sertifikalar yenilenmez.

Sonuçlar en az `hazır`, `kapalı/doğrulanmadı`, `uyumsuz sürüm`, `eksik/bozuk kurulum`, `erişim yok` ve `kimlik uyuşmazlığı` olarak ayrılır. Seçili Router kullanılamıyorsa yeni bir kopya sessizce kurulmaz. Mevcut Router'ı başka Router ile değiştirmek veri taşıma/birleştirme değildir; yeni bağlantı güveni ve etkin işlerin bitmesi ayrıca yönetilir. Aynı UUID'nin iki aktif veri kopyası olarak çalıştırılmasına izin verilmez.

### 2.2. Sonradan konum değiştirme sözleşmesi

Ayarlarda her alanın gerçek konumu, boyutu, kullanılan ve gereken disk alanı gösterilir. Kullanıcı tüm kurulumu, yalnız Router'ı veya yalnız büyük içerikleri seçebilir. Varsayılanlar basit kalır; ayrı yollar Gelişmiş bölümündedir. Program/runtime taşıması bağımlılıklarıyla birlikte ele alınır; Python sanal ortamının her yola taşınabilir olduğu varsayılmaz. Gerekirse hedefte doğrulanmış runtime yeniden hazırlanır. Eski kaynak kurulumunda güvenli taşıma desteklenmiyorsa önce desteklenen paketli yapıya dönüşüm gerekir; yarım taşıma yapılmaz.

Taşıma kalıcı işlem UUID'si ve günlükle yürütülür:

1. Kaynak sahipliği, hedef çakışması, disk/yazma izinleri ve aktif indirme/güncelleme/taşıma kilitleri doğrulanır. Kaynakla hedefin aynı olması, iç içe olması, junction/symlink ile hedef dışına çıkış ve başka bileşen verisine işaret etmesi reddedilir.
2. Yeni işler/indirmeler kontrollü duraklatılır. Kullanımdaki model veya DB dosyası taşınırken ilgili kullanıcı işlerindeki güvenli sınır beklenir. Yalnız etkilenen bileşenler durdurulur; DB için tutarlı durdurma/yedek şartı korunur.
3. Kaynak silinmeden hedefte geçici alana kopyalanır. Dosya envanteri, boyut ve içerik hash'leri doğrulanır. Modeller doğrulanmadan kayıtta yeni konuma geçirilmez; yarım indirmeler tamamlanmış model sayılmaz. Önbellek kaybı ile kullanıcı verisi kaybı ayrı ele alınır.
4. İlgili yol ayarları, başlatıcılar, Router kaydı ve eski uyumluluk kayıtları kalıcı işlem planına göre güncellenir. Birden çok kaydın tek atomik yazım olduğu varsayılmaz; tek otorite ve yeniden oynatılabilir adımlar kullanılır. DB/runtime ve model yükleme sağlığı doğrulanır, sonra yeni konum etkin ilan edilir.
5. Başarısızlıkta veri yazımı açılmadan eski konuma dönülür veya onarma durumu kalır. Yeni DB'ye yazım başladıktan sonra eski kopya otomatik etkinleştirilmez. Kaynak, başarı doğrulanıp saklama/temizleme politikası uygulanana kadar tutulur. Temizlik sadece işlem envanterindeki sahipli dosyalara uygulanır; kullanıcının D klasörü veya ortak model havuzu topluca silinmez.

Kopyalama sırasında kapanmada günlük okunur; kaynak ve hedef incelenmeden işlem devam etmez. Eksik D diski yerine boş veri/model kökü oluşturulmaz. Konum taşıma, kesintiye dayanıklılık kabul testleri geçmeden kullanıcıya etkin özellik olarak sunulmaz. Bu özellik Aşama 3'te uygulanır ve Aşama 6'daki ortak işlem modeline uyarlanır; o aşamaya kadar güvencesiz bir klasör taşıma düğmesi eklenmez.

### 2.3. PID/port yerine önerilen yaşam döngüsü sözleşmesi

PID ve port kaldırılmaz; görevleri ayrılır. Port bağlantı/çakışma teşhisidir, sahiplik kanıtı değildir. PID süreç bulmak içindir, tek başına kapatma yetkisi değildir. Süreç kaydı bileşen/kurulum UUID'si, çalışma örneği UUID'si, PID, OS başlangıç zamanı, gerçek yürütülebilir yol, beklenen giriş noktası, kullanıcı kimliği ve aktif sürüm kimliğini içerir. Ortak Python yürütülebiliri kullanan iki süreç yalnız exe yoluyla ayırt edilmez. Komut satırı/süreç bilgisi okunamıyorsa sahiplik doğrulanamadı kabul edilir; erişim hatası `çalışmıyor` diye yorumlanmaz. Sırlar kayda, komut satırına veya loga yazılmaz.

Bir bileşenin yaşayan supervisor'ı tek örnek kilidini ömrü boyunca tutar. Başlat/durdur/güncelle/taşı işlemleri için ayrıca sınırlı süreli işlem kilidi vardır. Kilit adları kullanıcı ve kanonik bileşen kimliğine göre türetilir; Hub ile bağımsız Router birbirini gereksiz engellemez. Kilit sırası sabittir. Windows'ta kullanıcıya erişimi sınırlı adlandırılmış mutex; macOS/Linux'ta OS destekli dosya kilidi uyarlayıcısı kullanılabilir. Tek başına kilit dosyasının varlığı veya `threading.Lock` süreçler arası tek örnek garantisi değildir. Abandoned mutex/kilit sahibi çökmesi, veri temiz demek değildir; kayıt ve yaşayan alt süreçler uzlaştırılır. Supervisor çöktüyse eski çocuklar sahipliği doğrulanmadan çoğaltılmaz veya benimsenmez.

Başlatma: kilitle -> kaydı ve mevcut süreçleri doğrula -> bağımlılıkları hazırla -> süreci başlat ve OS süreç referansını tut -> kimlikli yerel hazır olma yanıtını doğrula -> durumu kaydet. Süreç oluşması veya port açılması tek başına başarı değildir. Hazır olma yanıtındaki UUID tek başına güven kanıtı sayılmaz; yerel süreç sahipliği ve yetkili kanal/TLS ilişkisiyle birlikte değerlendirilir. PostgreSQL'de hedef veri dizinine yönelik `pg_ctl` durumu, süreç sahipliği ve beklenen DB'ye yetkili bağlantı/şema denetimi birlikte kullanılır; `pg_isready` tek başına doğru DB/hesap kanıtı değildir.

Durdurma: yeni işleri kapat -> aktif işleri güvenli sınırda bitir -> doğrulanmış bileşene kontrollü kapanma isteği -> çocukların çıkışını doğrula -> DB'yi durdur. Zaman aşımında yalnız doğrulanmış OS süreç referansına sınırlı zorlama yapılır. Kontrol ile sinyal arasında PID değişmesi riski OS uyarlayıcısında ele alınır; Windows'ta doğrulanmış process handle üzerinden bekleme/sonlandırma tercih edilir. `taskkill /IM postgres.exe`, salt port sahibini öldürme ve tüm Python süreçlerini tarayıp isimle kapatma üretim yolunda bulunmaz. Süreç çıkışı doğrulanmadan başarılı durma bildirilmez.

Windows'ta yeni uygulama çocukları ayrı Job Object altında oluşturulur. Kullanıcı SID'sine sınırlı DACL ve kullanıcı/kanonik kök/bileşen kimliği/çocuk çalışma UUID'sine bağlı ad kullanılır. Standart kütüphaneli host `-I -S` ile çalışır; uygulamayı başlatmadan önce gruba girer. Uygulama ve CreateProcess ile doğan alt süreçleri böylece doğumda grubu miras alır; breakaway açılmaz. WMI/uzak servis üzerinden bağımsız süreç oluşturmak bu sözleşmenin kapsamı değildir ve böyle bir süreç otomatik sahiplenilmez.

Job handle'ı hem supervisor hem uygulamayı bekleyen host tarafından tutulur. Supervisor tek başına çökerse yaşayan host kurtarmaya olanak verir; iki denetleyici de kaybolursa son handle kapanışındaki OS koruması yalnız bu grubu sonlandırır. Kontrollü kapanma süresinden sonra gerekiyorsa doğrulanmış, kapsamı eşleşen Job handle'ı üzerinden gruba sınırlı zorlama uygulanır; güncel üyelik boşalmadan DB durdurulmaz. Geçmişte görülmüş PID listesi güncel üyelik yerine kullanılmaz.

Job kapsamını içeren başlatma niyeti host oluşturulmadan önce kalıcı yazılır. Bu kısa aralıkta çocuk `process` alanı `null` olabilir; hazır sayılmaz. Supervisor PID kaydını bitiremeden çökerse kurtarma aynı job kapsamına dayanır. Başka kökün/job'un kaydı, yalnız adı biliniyor diye yönetim yetkisi vermez. Eski job'sız kayıtlar geriye uyumlu sahiplik denetiminden geçer; bu kayıtlara yeni doğumda-sahiplik garantisi geriye dönük verilmez.

### 2.4. Windows öncelikli, taşınabilir mimari

Ortak çekirdek; kurulum kayıtları, bağımlılık sırası, sahiplik politikası, durum makinesi, paket uyumu, taşıma/güncelleme günlüğü ve sağlık sonuçlarını yönetir. İşletim sistemi farkları küçük uyarlayıcı arayüzlerinde tutulur: `Paths`, `InstanceLock`, `ProcessController`, `Autostart`, `LocalControlChannel`, `DurableFiles`. Bunlar tasarım sorumluluklarıdır; ilk değişiklikte gereksiz çok sayıda paket/sınıf oluşturma zorunluluğu değildir.

Windows uygulaması AppData/Known Folders, kullanıcı başlangıç kısayolu, process handle ve Windows kilitlerini kullanır. Gelecekte macOS Application Support/Caches ve LaunchAgent; Linux XDG konumları ve kullanıcı oturumu başlatma yöntemi aynı sözleşmelerin arkasından eklenir. `.exe`, ters bölü, sabit sürücü harfi, PowerShell ve taskkill ortak çekirdeğe yayılmaz. Çalıştırmalar argv listesiyle yapılır; keyfi shell komutu kabul edilmez. Dosya adı büyük/küçük harf, ayırıcı, symlink, kilitli dosya ve atomik değiştirme davranışları platformdan bağımsız varsayılmaz.

Süreç keşfinde sabitlenmiş ve desteklenen bir psutil sürümü değerlendirilebilir; PID tekrar kullanımına karşı uygulama kimliği/OS handle denetiminin yerine geçmez. Ortak çekirdek testleri taşınabilir yazılır; gerçek macOS/Linux desteği ilgili işletim sistemlerinde entegrasyon testleri geçmeden ilan edilmez. Windows için gereken değişiklik şimdi uygulanır, diğer platformların tüm entegrasyonları ilk aşamaya yüklenmez.

## 3. Mevcut kodda doğrulanan başlangıç durumu

- `manager.py`: Hub PID kaydı ve veri dizinine yönelik `pg_ctl stop` mevcut. Buna karşılık hata/son kontrol yollarında bütün `postgres.exe` süreçlerini veya port sahibini hedefleyen kapatma var. Docker keşfi geniş Orion ad/etiket eşleşmeleri kullanıyor.
- `installer/app/services.py`: `.service.pid`/`hub.pid` ve dizin temelli arama mevcut. PID tekrar kullanımı doğrulanmıyor. Router başlatılırken `.orion.prod.lock` ve `.orion.pid` dosyaları süreç sahipliği doğrulanmadan silinebiliyor. Genel yerel servis başlatma yolu tek başına eşzamanlı başlatma kilidi sağlamıyor.
- `manager.py` bazı servislerde yalnızca portu kontrol ediyor; port açık olması doğru kurulumun hazır olduğunu kanıtlamıyor. PostgreSQL başlangıcı başarısız olduğunda Hub başlatılmaya devam edilebiliyor.
- Bazı PostgreSQL başlatma komutlarında `-F` var. Kalıcı kullanıcı verisinde dayanıklılık ayarları denetlenmeli; fsync kapalı çalışma üretim varsayılanı olmamalı.
- Hub ve Router Windows kurucuları AppData altında kaynak depo kuruyor. Üretilen Router komutlarında da sabit AppData yolu bulunuyor; yalnızca ilk hedef değişkenini değiştirmek yeterli değil.
- Router için zaten kurulum kaydı/provenans mekanizması var (`services/shared/router_installation.py`, Router `core/local_installation.py`). Sıfırdan ikinci, birbiriyle çelişen keşif mekanizması kurulmaz.
- Router `core/updater.py` ve `bin/update.py`: main sürüm kontrolü, çalışan klasörde Git/pip/build, hard reset geri yolu, bellekte durum ve yeni sürüm sağlığı görülmeden başarı. Arayüz/iş akışı değerlendirilebilir; bu haliyle kesinti güvenli otomatik güncelleme değildir.

## 5. Test matrisi ve kanıt

| Alan | Zorunlu senaryolar | Beklenen değişmez |
| --- | --- | --- |
| Sahiplik | İki Orion, yabancı PostgreSQL, PID yeniden kullanımı, port çakışması | Hedef dışındaki süreç/veri değişmez |
| Konum | AppData, D kökü, farklı Router kökü, boşluk/Türkçe, erişim reddi, kayıp disk | Kimlik/yol doğrulanmadan işlem olmaz |
| Kalıcı veri | DB, ayarlar, hesaplar, geçmiş, model hash'i, TLS/SPKI/UUID, tarayıcı profili | Güncelleme veri sıfırlamaz veya kimliği yenilemez |
| Eşzamanlılık | Başlat/başlat, güncelle/güncelle, başlat/durdur/güncelle/kaldır | Tek sahip, doğru kilit ve görünür sonuç |
| Kesinti | İndirme, açma, kayıt yazımı, yedek, şema, geçiş, sağlık sırasında kapanma | Geçerli eski/yeni sürüm veya açık onarma durumu |
| Güven | Yanlış imza, bozuk hash, eski katalog, arşiv yol kaçışı, yetkisiz istek | Paket çalıştırılmaz, aktif sürüm korunur |
| Kullanım | Yerel/uzak sohbet, ses, araç, taslak, pencere kapalıyken iş | Aktif iş habersiz kesilmez, dış etki tekrarlanmaz |
| Yayın | Eski/yeni API, bağımsız Router, farklı şema, CPU/CUDA, çevrimdışı | Uyum veya açıklanmış engel; sahte başarı yok |

Testler üç katmanda tutulur: geçici dizin ve sahte süreçlerle birim/entegrasyon; gerçek servislerle izole Windows VM; VM zorla kapatma ve yeniden oturum açılışıyla kurtarma. Kritik durum geçişlerinin hepsine hata enjeksiyonu noktası eklenir. Donanım/arızaların her olasılığına mutlak garanti verilmez; desteklenen koşullar ve test kanıtı belirtilir.

Her aşamanın bitiş kaydı: değişen giriş noktaları, çalıştırılan testler/sonuçları, veri/kimlik korunması kanıtı, destek dışı durumlar ve geri alma yöntemi. Yeni bir açık bulunmadıkça geçen testler gerekçesiz tekrarlanmaz.

## Kaynaklar

- Windows Job Object üyeliği, alt süreç mirası ve son handle kapanışı: https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects
- Job üyeliğini process handle ile denetleme: https://learn.microsoft.com/en-us/windows/win32/api/jobapi/nf-jobapi-isprocessinjob
- Doğrulanmış grubu OS handle üzerinden kapatma: https://learn.microsoft.com/en-us/windows/win32/api/jobapi2/nf-jobapi2-terminatejobobject

- Windows kullanıcıya özel bilinen klasörler: https://learn.microsoft.com/en-us/windows/win32/shell/knownfolderid
- PostgreSQL dosya sistemi yedeğinin tutarlılık koşulları: https://www.postgresql.org/docs/17/backup-file.html
- Windows süreç referansları: https://learn.microsoft.com/en-us/windows/win32/procthread/process-handles-and-identifiers
- Windows mutex ve terk edilmiş kilit durumu: https://learn.microsoft.com/en-gb/windows/win32/sync/mutex-objects
- psutil PID tekrar kullanımı davranışı: https://psutil.io/faq/
- PostgreSQL hazır olma kontrolünün sınırları: https://www.postgresql.org/docs/16/app-pg-isready.html
- İmza, sürüm metaverisi, mobil yetki ve uyumluluk ayrıntıları: [tasarım notu](../orion-update-strategy-proposal.md).
