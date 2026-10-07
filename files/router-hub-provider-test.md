# Router erişimi ve kişisel sağlayıcı testi

Bu not Hub, Router ve Flutter arasındaki kullanıcı erişimi ve kişisel sağlayıcı akışını test etmek içindir.

## Başlatma

Önce güncel kaynakla Orion Router’ı, ardından Hub API/public/mobile ve worker süreçlerini yeniden başlat. Router başlangıcında yeni tablolar eklenir; mevcut anahtarlar ve sağlayıcı havuzu korunur. Flutter’ı güncel kaynakla çalıştır. Hub web için sayfayı yenile.

## Elle kontrol

1. Orion Panel → Router bağlantısı ekranını aç. Aynı PC’deki Router’ın mevcut yerel sertifika doğrulaması çalışmaya devam eder.
2. Router şifresi `orion` ise erişim otomatik kurulur. Şifre değişmişse bu ekranda Orion Router şifresini gir. Şifre Hub’da kalıcı olarak tutulmaz; Hub’a ayrı bağlantı yetkisi verilir.
3. Yeni kullanıcı oluştur. Router panelindeki sanal anahtar listesinde kullanıcı adına ait kayıt oluşmalı. Router geçici olarak kapalıysa hesap kaydı yine tamamlanır; sonraki ayar/model erişiminde anahtar oluşturma tekrar denenir. Mevcut kullanıcılar da ilk erişimlerinde otomatik kayıt alır.
4. Kullanıcı hesabıyla Hub web veya Flutter → Ayarlar → Orion Router bölümünü aç. Bağlı Router görünmeli; Router erişim anahtarı kopyalamak gerekmemeli.
5. Bir sağlayıcı seçip onun API anahtarını kaydet. Ekranda yalnızca sağlayıcının kayıtlı olduğu görünür; gerçek anahtar geri gösterilmez. Sağlayıcı anahtarının çalıştığını Router’da tanımlı uygun modelle bir istek yaparak kontrol et. Kaydetme, sağlayıcı hesabında canlı bir doğrulama isteği yapmaz ve yeni model tanımı oluşturmaz.
6. İkinci kullanıcıyla giriş yap. İlk kullanıcının kişisel sağlayıcı kaydı görünmemeli. Ortak Router sağlayıcıları mevcut politikaya göre kullanılmaya devam eder.
7. Router panelinden kullanıcının sanal anahtar limitini değiştir veya anahtarı kapat. Tekrar giriş/yeniden bağlantı bu limiti veya kullanım sayacını sıfırlamamalı.
8. Sağlayıcıyı kaldır. Yalnızca o kullanıcının kişisel kaydı kaldırılmalı.

## Bağlantı kapsamı

Router seçme/değiştirme Orion Panel’dedir; Hub web ve Flutter bağlantıyı değiştiremez. Hesap erişimi Hub kimliği, Router kimliği ve kullanıcı kaydına bağlıdır. Aynı Router farklı Hub’lara ayrı kullanıcı erişimleri verir. Açık sağlayıcı formundaki Router değişmişse kaydetme reddedilir.

Bu değişiklik yerel dosyadan kimlik doğrulamayı korur. Başka bilgisayardaki ilk Router bağlantısında mevcut UUID/SPKI doğrulaması devam eder; ağlar arası keşif veya yeni PAKE eşleştirme protokolü eklenmedi.
