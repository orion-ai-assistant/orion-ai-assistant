# Manuel web denemesi — 17 Ekim 2026

Şimdi deneyebilirsin. Bu bilgisayardaki başarılı Aşama 05 paket kurulumunun tamamı dosya envanterinden tekrar doğrulandı; hazırlık sonunda servisler kapalıydı. Bu **yerel test kurulumu** üretim sürümü değildir. Test imza anahtarları üretim güven kökü olarak kullanılmaz. Herkese açık indirme/tek komutla internetten kurulum için yayın adresleri ve gerçek üretim güveni henüz hazır değildir.

## Başlat / durdur

PowerShell'den normal tarayıcıda web istemcisini aç:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "C:\Users\krsta\AppData\Local\Orion\lifecycle-tests\stage05-3g_jzpon\Try-Orion.ps1"
```

Servisleri kontrollü kapat:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "C:\Users\krsta\AppData\Local\Orion\lifecycle-tests\stage05-3g_jzpon\Try-Orion.ps1" -Stop
```

Tarayıcı açmadan çalıştırmak için ilk komuta `-Background` ekle. Servisler hazırken adres: `http://127.0.0.1:63418/`. Sekmeyi kapatmak servisleri durdurmaz. Kapatmak için `-Stop` kullan; başka Python/PostgreSQL süreçlerine müdahale etme.

Bu wrapper sadece kendi işlem ortamında izole registry/AppData/portları kullanır. Windows Startup/Desktop kısayolu, PATH profili veya otomatik güncelleme tercihi oluşturmaz. D'deki development depolarını çalıştırma/temizlik hedefi yapmaz. Mevcut asıl kurulumun hesapları buraya taşınmaz; bu fixture'ın kendi test verileri/kimlikleri vardır. Gerçek sağlayıcıyla sohbet, ayrıca çalışan Router/model veya hesap yapılandırması gerektirebilir; hazır web arayüzü bütün modellerin indirildiği anlamına gelmez.

## Kısa deneme

1. Başlat; sayfanın açıldığını ve gezinmenin çalıştığını kontrol et.
2. Sekmeyi kapatıp komutu yeniden çalıştır; aynı test kurulumuna döndüğünü kontrol et.
3. Kendi test hesabınla normal kullanımını dene. Kimlik/anahtarları paylaşma.
4. `-Stop`, sonra tekrar başlat; ayar/hesap/geçmişinin korunduğunu kontrol et.
5. Sorun varsa fixture içindeki `Hub/state/hub/supervisor.log`, `product.log` ve launcher loglarını yerel incele; logların tamamını sır/kimlik kontrolü yapmadan yayımlama.

Bu manuel deneme Aşama 06 yardımcı testlerinden ayrıdır; yardımcı hata enjeksiyonu bu alanda yapılmaz. 06 kabulü ayrı yeni fixture'larda yapılır. Temiz Windows VM, gerçek Windows logon ve yayın kabulü hâlâ açıktır.

## Kurulum tercihi

Flutter web kalır. Önerilen asıl giriş, doğrulanmış kurucuyu çağıran tek PowerShell komutudur. Sonradan çift tıklamalı görsel kurucu eklenirse aynı motoru kullanır. Yalnız gizlice PowerShell açan bir EXE için ayrı ürün mimarisi kurulmaz; bu paketleme Windows'un dosya itibarı/imza uyarısını kendiliğinden çözmez. [Microsoft SmartScreen açıklaması](https://learn.microsoft.com/en-us/windows/apps/package-and-deploy/smartscreen-reputation).
