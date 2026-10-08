# Router sağlayıcı ayarları · 8 Ekim 2026

Hub web ve Flutter aynı sırayı kullanır: cihaz bağlama/QR, Orion Router,
Orion Hub panel bağlantısı, arayüz ve ses seçenekleri.
Router kartında seçili bağlantının adı bir kez ve aktif anahtar sayısı gösterilir.
Router sayısı ve otomatik erişim anahtarı açıklaması kaldırıldı. Buradan Router
seçilmez; Hub ayarlarının bildirdiği hedefe kişisel sağlayıcı kaydı gönderilir.
Diğer bağlı Router özetleri sağlayıcı detaylarında görünmeye devam eder.

Sağlayıcı sayıları ve kişisel anahtar formu varsayılan olarak kapalı açılır
bölümlerdedir. Özetlerde ortak ve size özel sayıları satırların sağında ayrı
rozetlerdir; uzun listeler en fazla 300 piksel yükseklikte kaydırılır.
Adres kopyalama yalnız simgedir. Durumlar, kapatılan kişisel anahtarlar ve
hesap değişiminde yazılmış sırların temizlenmesi korunur.

Hub yönetim bağlantısı ayrı Orion Hub kartındadır. Mevcut
`/api/v1/pair/capabilities` yanıtının yerel yönetim yeteneği ve doğrulanmış
loopback `/panel` adresi kullanılır. Uzaktan bağlanan kullanıcıya yönetimin
Hub bilgisayarından açıldığı açıklanır; telefona localhost adresi verilmez.

Doğrulama: sağlayıcı kaydı/hesap değişimi, kapalı listeler, Router hedefinin
Hub ayarlarından yenilenmesi ve dar telefon ekranı widget testleri;
değişen iki Flutter ekranında statik analiz. Web tasarımı 20 sağlayıcılı
yalıtılmış örnek önizlemede kontrol edildi.
