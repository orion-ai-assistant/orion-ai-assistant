# Faz 3: Giriş, sunucu ayarı ve oturum

## Bağlam (bu dosya tek başına yeterli, önceki sohbeti bilmiyorsun)

Orion: yerel AI platformu. Bileşenler: **Hub** (backend + web dashboard, sohbet/akış/SSE/dosya/auth), **Router** (OpenAI uyumlu ağ geçidi), **Flutter istemci** (Web, Android, iOS). Hub dashboard tasarımı referanstır.

## Çalışma kuralları
- Önce repoyu incele; önceki fazlar commit edilmiş olabilir, onları bozma.
- **Sadece bu fazın kapsamını yap.** "Yapılmayacak" listesindekilere dokunma, başka faz işi ekleme.
- Minimal, mevcut yapıyı kullanan kod yaz; yeni sistem icat etme.
- Ayrı branch/PR. Bitirince "Bitti kriteri" maddelerini tek tek kontrol edip sonucu raporla (geçti/kaldı).
- Emin olmadığın bir şeyi varsayma, raporda belirt.

## Kapsam
Giriş ekranı, sunucu adresi ve token ömrü aynı akışın parçaları. **Önkoşul: Faz 1 bitmiş olmalı** (auth koduna dokunacaksın; Faz 1'in değişikliklerini bozma).

## Yapılacaklar
- **Giriş/kayıt renkleri:** Mod seçici Material varsayılanı yerine Hub'ın yeşil vurgu ve koyu yüzey renkleriyle.
  - Seçili mod: hafif yeşil zemin, yeşil sınır, okunaklı yazı. Seçili olmayan: nötr yüzey.
  - Ana düğme: Orion yeşili + beyaz yazı.
  - Hover, basılı, devre dışı, yükleniyor durumları açık ve koyu temada tutarlı.
  - Ortak Markdown temasında genel değişiklik yapma.
- **Sunucu bağlantısı**
  - Giriş formundaki geniş bölüm kalkar; sağ üstte küçük "Sunucu bağlantısı" simgesi. Web'de pencere, telefonda alt panel; Hub ve Router adresleri düzenlenebilir.
  - Uygulama içi ayarlardaki adres alanları ana listeden çıkar, aynı simgeden açılır.
  - **"Bağlantıyı test et"** düğmesi; değişiklik yalnızca "Kaydet" ile uygulanır. Hub değişirse oturum kapanır ve giriş ekranına dönülür; iptal hiçbir şeyi değiştirmez.
  - Derleme varsayılanları ve kaydedilmiş adresler korunur.
  - Web'de HTTPS sayfadan `http://` Hub'a mixed-content engeli için açık uyarı göster.
- **Oturum yenileme (backend + client birlikte):** 24 saatlik token yerine kısa ömürlü access + refresh token; cihaz bazlı oturum iptali; güvenli yenileme.

## Yapılmayacak
E�leştirme/QR, tünel, kenar çubuğu/arama (Faz 2), sohbet içeriği (Faz 4).

## Bitti kriteri
- Giriş ekranı açık/koyu temada tüm durumlarda okunaklı.
- Yanlış adres test edilince kayıt öncesi hata veriyor; iptal sonrası eski adres duruyor; Hub değişince oturum kapanıyor.
- Access token süresi dolunca kullanıcı giriş ekranına düşmeden oturum yenileniyor (test).
- Bir cihazın oturumu iptal edilince yalnızca o cihaz çıkış yapıyor (test).
- Faz 1 testleri hâlâ geçiyor; `flutter analyze` + testler temiz.
- Ekran görüntüleri: giriş ekranı (açık/koyu), sunucu bağlantısı penceresi/paneli.
