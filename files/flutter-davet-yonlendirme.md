# Yapılacak: Flutter davet bağlantısını uygulamada açma

Durum: **Bekliyor.** Kullanıcı bu işi sonraya bıraktı. Hub tarafı hazır;
Flutter uygulaması henüz davet bağlantısını karşılamıyor. Bu belge işi takip
eder; mevcut Faz 3'ün QR/eşleştirme kapsamını otomatik genişletmez.

## Hedef

PC sahibi Hub'da davet oluşturur ve bağlantıyı veya QR'ı paylaşır.
Desteklenen telefonda uygulama kurulu ve bağlantı ilişkisi etkinse davet
Flutter'da açılır; uygulama yoksa web dashboard'un kayıt ekranı açılır.
Kayıt tamamlanınca kullanıcı otomatik giriş yapar. Davet alan kişiden
yönetici şifresi istenmez; daveti oluşturan kişi yönetici şifresiyle giriş yapar.

## Mevcut durum

- Yerel yönetim: `localhost:8909`; genel dashboard/API: varsayılan port `8910`.
- PC'nin yerel IP'si otomatik bulunuyor. QR şu an yalnızca web bağlantısı açıyor.
- Bağlantı: `<hub-adresi>/dashboard/#invite=<token>`.
- Davet tek kullanımlık ve süreli; kullanıcı aktif oluşturuluyor.
- API ayrıntıları: [Hub davet dokümanı](../services/hub/docs/local-access-invitations.md).

## Hazırlanacak bilgiler

- Android paket kimliği ve imza sertifikası bilgileri; repodan/derleme yapılandırmasından doğrula.
- iOS desteklenecekse bundle kimliği ve Apple Team ID.
- Kalıcı HTTPS alan adı ve bu alan adının genel Hub portuna yönlendirilmesi.
- Flutter ve Hub'ın ortak kabul edeceği bağlantı/token biçimi. Mevcut fragment
  biçimini doğrulamadan değiştirme; davet sırrını loglara taşıma.
- Değişebilen yerel HTTP/IP adresiyle uygulama açılışını garanti etme.
  Alan adı yokken çalışan web davet akışını koru.

## Yapılacaklar

- [ ] Android/iOS bağlantı ilişkilendirmesini ve alan adındaki gerekli doğrulama dosyalarını hazırla.
- [ ] Flutter uygulaması kapalıyken veya zaten açıkken davet bağlantısını karşıla.
- [ ] Bağlantıdan Hub adresi ve davet tokenını oku; sunucu değişecekse mevcut oturumun davranışını açıkça ele al.
- [ ] `POST /api/v1/auth/invitations/preview` ile daveti doğrula; geçersiz, kullanılmış, iptal edilmiş veya süresi dolmuş daveti anlaşılır göster.
- [ ] `POST /api/v1/auth/register` isteğine `invitation_token` ekle; dönen access tokenı mevcut güvenli oturum mekanizmasına kaydet ve sohbete geç.
- [ ] Gerekirse Hub davet URL üretimini ortak biçime uyarla; web kayıt ve otomatik giriş akışını koru.
- [ ] Yanlış şifre/429 için sunucunun `Retry-After` değerini ve kalan süreyi Flutter'da göster.

## Bitti kontrolü

- Uygulama kurulu/kurulu değil durumları gerçek telefonda test edildi.
- QR ve doğrudan bağlantı, uygulama açık/kapalı durumlarında test edildi.
- Davetle kayıt ve otomatik giriş hem Flutter'da hem web'de çalışıyor.
- Kullanılmış/iptal edilmiş/süresi dolmuş davet yeniden hesap açamıyor.
- Yerel ağda web erişimi ve HTTPS alan adıyla uygulama açılışı ayrı doğrulandı.
- Yönetim sayfaları dış erişime açılmadı; Flutter analyze ve ilgili testler geçti.
