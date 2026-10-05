# Faz 2: Kabuk ve gezinme (Flutter + Hub)

## Bağlam (bu dosya tek başına yeterli, önceki sohbeti bilmiyorsun)

Orion: yerel AI platformu. Bileşenler: **Hub** (backend + web dashboard, sohbet/akış/SSE/dosya/auth), **Router** (OpenAI uyumlu ağ geçidi), **Flutter istemci** (Web, Android, iOS). Hub dashboard tasarımı referanstır.

## Çalışma kuralları
- Önce repoyu incele; önceki fazlar commit edilmiş olabilir, onları bozma.
- **Sadece bu fazın kapsamını yap.** "Yapılmayacak" listesindekilere dokunma, başka faz işi ekleme.
- Minimal, mevcut yapıyı kullanan kod yaz; yeni sistem icat etme.
- Ayrı branch/PR. Bitirince "Bitti kriteri" maddelerini tek tek kontrol edip sonucu raporla (geçti/kaldı).
- Emin olmadığın bir şeyi varsayma, raporda belirt.

## Kapsam
Kenar çubuğu, üst çubuk, bağlantı göstergesi ve arama aynı yerleşimi paylaştığı için birlikte yapılır. Hedef: Flutter Web, Android, iOS; ortak yenilikler Hub dashboard'ına da uygulanır.

## Yapılacaklar
1. **Ortak tasarım token'ları:** renk, boşluk, yarıçap tek kaynaktan; Hub'ın mevcut renk/kart/boşlukları referans.
2. **Kenar çubuğu**
   - 1000 px ve üzeri: mevcut 260 px panel açılıp kapanır, tercih cihazda saklanır.
   - Dar ekran: varsayılan kapalı, içerik üstüne açılır.
   - Kapatma simgesi "Orion" başlığının sağında; kapalıyken açma simgesi ana başlığın solunda.
   - Telefonda sohbet seçince kapanır; dışarı dokunma, kapatma simgesi ve Escape ile de kapanır.
   - Açma/kapama mevcut sohbeti, taslağı veya devam eden yanıtı sıfırlamaz.
3. **Bağlantı göstergesi**
   - Ana içerik üst çubuğunun sağında nokta + metin: Bağlanıyor / Bağlı / Yeniden bağlanıyor / Bağlantı yok.
   - Flutter'ın mevcut SSE durumları kullanılır; boşta durum için hafif `/health` ping veya heartbeat. Gösterge "Hub'a erişiliyor mu"yu ifade eder; model/Router hataları ayrı gösterilir.
   - Telefonda kompakt, dokununca açıklama; renge tek başına güvenme.
   - Otomatik yeniden deneme korunur, yazılmış mesaj silinmez.
4. **Sohbet araması**
   - "Yeni sohbet" altında "Sohbetlerde ara"; panel kapalıyken üst çubuktaki arama simgesi.
   - Web'de en fazla 600 px pencere, telefonda tam ekran.
   - İlk sürüm yalnızca sohbet adına göre arar. **Türkçe normalizasyon açıkça yapılacak:** İ/I/ı/i ve ç/c, ş/s, ğ/g, ö/o, ü/u (Dart'ın toLowerCase'i yetmez).
   - Sonuçta sohbet adı + son güncelleme tarihi/saat, en güncel üstte. Boş aramada son sohbetler; eşleşme yoksa açıklayıcı boş durum.
   - Enter seçer, Escape kapatır, Web'de Ctrl/Cmd+K (metin alanıyla çakışma kontrol edilir).
   - Mevcut `/api/v1/chats` kullanılır; çevrimdışı önbellekte `updated_at` korunur. Sonuç seçimi mevcut sohbet açma akışını kullanır.
   - Erişilebilirlik: focus trap, ikon etiketleri, klavye navigasyonu.
5. **Küçük yerleşim düzeltmeleri**
   - Hub'daki görünür "Orion Hub" başlığı ve sayfa başlığı "Orion" olur.
   - Kenar çubuğu alt grubu (sohbet/ayarlar + kullanıcı kartı): yanlarda 14–16 px, altta 16 px + güvenli alan; uzun kullanıcı adı kısalır, çıkış düğmesi erişilebilir.
   - Mesaj kutusu alt payı: klavye kapalıyken 20 px + güvenli alan; telefonda klavye açıkken 8 px; klavye yüksekliği iki kez eklenmez. Hub'ın 20 px payı korunur.
6. Kenar çubuğu bir bayrakla kapatılabilir olsun (sorun çıkarsa geri alınır).

## Yapılmayacak
Mesaj içeriğinde arama, giriş ekranı/sunucu ayarı/token (Faz 3), dosya/video (Faz 4), backend güvenlik işleri (Faz 1).

## Bitti kriteri
- Geniş/dar Web, Android ve iOS'ta (klavye açık/kapalı, yatay ekran) düzen bozulmuyor.
- Kenar çubuğu tercihi yeniden açılışta hatırlanıyor; sohbet/taslak/akış durumu korunuyor.
- Birim testi: "ISPARTA", "ısparta", "İstanbul", "istanbul", "çay"/"cay" aramaları doğru.
- Bağlantı kopup gelince gösterge doğru durumları geçiyor.
- `flutter analyze` + testler temiz; Web ve telefon derlemesi çalışıyor.
- Ekran görüntüleri (Flutter + Hub, Web + telefon): kenar çubuğu açık/kapalı, arama, bağlantı durumları.
