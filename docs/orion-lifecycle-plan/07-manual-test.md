# Aşama 07'yi deneme ve günlük başlatma

17 Ekim 2026. 07 kaynak uygulaması vardır; gerçek hesaba dağıtılmadı. 06'nın hazır manuel test kurulumu eski paketleri kullanır, yeni güncelleme kartını içermez. Bu oturum o kurulumun dosyalarını/ayarlarını değiştirmedi veya servislerini başlatmadı. Üretim indirme adresi/halka açık son kurulum sürümü yoktur.

## Şimdi Router + Hub + web istemcisini açmak

Önceden hazırlanmış izole yerel denemeyi normal PowerShell'de aç:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "C:\Users\krsta\AppData\Local\Orion\lifecycle-tests\stage05-3g_jzpon\Try-Orion.ps1"
```

Tarayıcı açmadan başlatmak için `-Background` ekle. Mevcut bu fixture'ın web adresi `http://127.0.0.1:63418/`. Launcher kayıtlı ve yönetimine izin verilen yerel Router'ı, Hub'ı ve Flutter web sunucusunu hazırlar; Router'ı ayrı terminalden başlatman gerekmez. Model/sağlayıcı ayarı ayrıca gerekebilir.

Kontrollü durdur:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "C:\Users\krsta\AppData\Local\Orion\lifecycle-tests\stage05-3g_jzpon\Try-Orion.ps1" -Stop
```

Sekmeyi kapatmak servisleri durdurmaz. Bu komutlar yalnız mevcut izole fixture içindir; gerçek hesabının Startup/otomatik güncelleme tercihlerini açmaz. Ayrıntılar [06 rehberinde](06-manual-test.md).

## 07'nin yeni davranışını izole testte doğrulamak

Hub repo kökünden, mevcut test araçlarıyla:

```powershell
.tmp/stage05-tools/Scripts/python.exe -m pytest scripts/release/test_automatic_updates.py -q
```

Bu testler kendi geçici registry, veriler, TUF anahtarları ve paketlerini oluşturur. Aktif kullanım sırasında hazırlama, tercih kapatma, 24 saat erteleme, internet kesintisi/geri gelmesi, watcher kapanışı ve kesilmiş indirmenin sonraki açılışta toparlanması sınanır. Testin native sağlık adaptörü sahtedir; gerçek Router/Hub/DB açmaz. Sandbox dosya replace engeli varsa normal PowerShell kullanıcı bağlamında çalıştır; ACL/güven denetimini kapatma.

Flutter repo `main_app/app` dizininden:

```powershell
flutter test --no-pub test/updates_card_test.dart test/settings_refresh_test.dart test/settings_connection_race_test.dart
```

Yeni **Orion güncellemeleri** kartı kaynak web build'inde Ayarlar'ın altındadır. Kaynak/runtime teslim edilmemiş kurulumda otomasyonun hazır olmadığını söyler. Eski manuel fixture'a yeni build'i veya helper dosyalarını elle kopyalama: kayıtlı paket envanteriyle uyuşmaz. 07'nin canlı ürün ekranını denemek için yeni sürüm/sequence taşıyan Hub ve Client paketleri ile ayrı retained bootstrap'tan **yeni disposable kurulum** hazırlanıp kabul edilmelidir; bu oturum bu uçtan uca paket kabulünü tamamladı diye iddia etmez.

## Yeni paket kabulünde uygulanacak kontrol listesi

1. [Paket rehberine](../../scripts/release/README.md) göre yalnız fixture'a ait TUF kökü, HTTPS repository ve ayrı bootstrap hazırlanır. `release-source.json` stable Hub/Client hedefleri içerir; üretim adresi uydurulmaz. Gerçek kullanıcı kayıt/Startup alanı kullanılmaz.
2. Yeni registry ile ilk kurulum yapılır; otomatik güncelleme ve Startup kapalı başlar. Dış bootstrap klasörü korunur; kendi çalışan kopyasının üzerine kurulmaz.
3. Bir normal launch ile Router/Hub/web hazır olur. PC'deki karttan yalnız bu fixture için otomatik güncelleme açılır. Başka cihaz normal oturumla durumu okuyabilmeli, tercih yazamamalıdır.
4. Yeni imzalı ve uyumlu stable sürüm sunulur. Kontrol sıklığı 6 saattir; fixture denemesinde ayrı bootstrap `update_automatic.py check` aynı kalıcı throttle'a uyar. Sohbet/ses/araç/transfer/uzak istemci açıkken indirme aktif paket veya veriyi değiştirmemelidir.
5. Hazır sürümü 24 saat ertele; kapat/aç sonrası erteleme korunmalı. Tercih kapalıyken otomatik aktivasyon olmamalı. Ağ duraklatması yeni indirmeyi durdurur; ölçülü bağlantı elle yönetilir.
6. Kullanıcı uygun anda fixture'ı kontrollü durdurur. Sonraki normal açılışta helper kapalı servisler altında geçiş/sağlık yapar, ardından launcher normal servislere geçer. Servis hâlâ çalışıyorsa bekler; zorla kapatmaz.
7. Veri/UUID/TLS/modeller ve ayarlar hash/DB denetimleriyle karşılaştırılır. Hata halinde 06 journal/rollback/onarma yolu kullanılır; yedek verinin üzerine kendiliğinden geri yüklenmez. Tamamlanınca yalnız fixture servisleri kapatılır.

## 08'e kalan kurulum işleri

Tek PowerShell komutuyla kurulum motoru ve tek günlük launcher temeli mevcuttur. İnternetten herkese açık kurulum için üretim güven kökü, imzalama, yayın adresleri ve temiz makine kabulü gerekir. Bağımsız Router güncellemesi, eski kurulum geçişi ve kaldırma/temizlik 08 kapsamındadır. Görsel kurucu gelecekte aynı motoru kullanabilir; Flutter native Windows EXE yapılmaz. 01/03/04/05/06'nın açık OS ve ürün doğrulamaları korunur.
