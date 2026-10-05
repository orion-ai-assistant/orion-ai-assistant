# Faz 4: Sohbet içeriği (dosya ekleri + YouTube)

## Bağlam (bu dosya tek başına yeterli, önceki sohbeti bilmiyorsun)

Orion: yerel AI platformu. Bileşenler: **Hub** (backend + web dashboard, sohbet/akış/SSE/dosya/auth), **Router** (OpenAI uyumlu ağ geçidi), **Flutter istemci** (Web, Android, iOS). Hub dashboard tasarımı referanstır.

## Çalışma kuralları
- Önce repoyu incele; önceki fazlar commit edilmiş olabilir, onları bozma.
- **Sadece bu fazın kapsamını yap.** "Yapılmayacak" listesindekilere dokunma, başka faz işi ekleme.
- Minimal, mevcut yapıyı kullanan kod yaz; yeni sistem icat etme.
- Ayrı branch/PR. Bitirince "Bitti kriteri" maddelerini tek tek kontrol edip sonucu raporla (geçti/kaldı).
- Emin olmadığın bir şeyi varsayma, raporda belirt.

## Kapsam
İkisi de mesaj yazma ve render akışına dokunduğu için birlikte yapılır. Sadece Flutter istemci.

## Yapılacaklar
- **Artı menüsü ve ekler**
  - "Dosya / Görsel ekle" doğrudan çoklu dosya seçiciyi açar (ikinci panel yok). Telefonda mevcut kamera özelliği ayrı "Fotoğraf çek" seçeneği olarak kalır. "Fonksiyonlar" mevcut araç seçimini açmaya devam eder.
  - Seçilen ekler Hub'daki gibi ~80 px kare kartlar: görselde küçük önizleme, diğerlerinde simge + dosya adı; her kartta kaldırma düğmesi.
  - Mevcut 20 MB sınırı korunur; desteklenmeyen ikili belgeler seçimden sonra açıkça bildirilir.
  - Seçim iptalinde taslak değişmez; seçim ekranındayken sohbet değişirse ek eski sohbete eklenmez.
- **YouTube**
  - Hub'daki YouTube desteği Flutter'a taşınır. `watch`, `youtu.be`, `shorts`, `embed` linkleri tanınır (örn. `youtube_player_iframe`, Web/Android/iOS destekler; uygun değilse alternatif öner).
  - Mevcut Markdown genişletme mekanizmasına bileşen eklenir; **Markdown motoru değiştirilmez**.
  - **Önce thumbnail + oynat butonu; tıklayınca iframe yüklenir** (kullanıcı istemeden Google'a istek gitmesin). Otomatik oynatma kapalı.
  - Ekran dışına çıkınca dispose; sohbet değişince oynatma durur; gelen her metin parçasında oynatıcı yeniden oluşturulmaz.
  - Gömmeye izin verilmeyen videoda "YouTube'da aç" bağlantısı. Web'de CSP/iframe ayarlarını not et. Telefonda YouTube'un minimum oynatıcı boyutlarına uy.
  - Kod bloğundaki URL'ler dönüşmez; ham mesaj metni korunur.
  - Kullanıcı mesajında ve asistan yanıtında çalışır.
- YouTube bileşeni bir bayrakla kapatılabilir olsun.

## Yapılmayacak
PDF/Office metin çıkarımı, diğer video siteleri, doğrudan video dosyası bağlantıları (normal link kalır), kenar çubuğu/giriş işleri (Faz 2–3), backend güvenlik (Faz 1).

## Bitti kriteri
- Dosya seç / iptal / kaldır, 20 MB üstü, desteklenmeyen tür, seçim sırasında sohbet değişimi senaryoları çalışıyor.
- Dört YouTube link biçimi doğru çözülüyor; kod bloğundaki link oynatıcıya dönüşmüyor; engelli videoda yedek bağlantı çıkıyor.
- Çok sayıda YouTube linkli uzun sohbette kaydırma akıcı; akış sırasında oynatıcı yeniden yüklenmiyor.
- Markdown akış/kaydırma regresyon testleri geçiyor; `flutter analyze` + testler temiz; Web ve telefon derlemesi çalışıyor.
- Ekran görüntüleri: ek menüsü, ek kartları, video (thumbnail ve oynatılan hâl).
