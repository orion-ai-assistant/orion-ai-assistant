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
Router ve Hub kartlarında başlık solda, yönetimi açan bağlantı sağdadır;
kopyalama düğmesi yoktur. Kişisel anahtarın son dört karakteri maskeli
gösterilir; kısa veya çözülemeyen anahtar tamamen maskelenir. “Kayıtlı”
etiketi kaldırıldı. Durumlar, kapatılan kişisel anahtarlar ve hesap değişiminde
yazılmış sırların temizlenmesi korunur.

Web ayarlarına eklenen kartlar flex yerleşiminde küçülmez; QR kartının
altında tam yükseklikleriyle görünür. Başka bilgisayardan erişim kartında
başlık ve ağ bağlantısı tek yatay satırdadır.

Hub yönetim bağlantısı ayrı Orion Hub kartındadır. Mevcut
`/api/v1/pair/capabilities` yanıtının yerel yönetim yeteneği ve doğrulanmış
loopback `/panel` adresi kullanılır. Uzaktan bağlanan kullanıcıya yönetimin
Hub bilgisayarından açıldığı açıklanır; telefona localhost adresi verilmez.

Doğrulama: sağlayıcı kaydı/hesap değişimi, kapalı listeler, Router hedefinin
Hub ayarlarından yenilenmesi ve dar telefon ekranı widget testleri;
değişen iki Flutter ekranında statik analiz. Web tasarımı 20 sağlayıcılı
yalıtılmış örnek önizlemede kontrol edildi.

Ayarlar yalnız ilk uygulama açılışında bir kez, ayarlara girişte ve sayfa
görünürken 5 saniyede bir alınır. Flutter oturum belleği Hub, hesap ve
bağlantı kimliğine bağlıdır; ham Router sırrı önbelleğe alınmaz. Web ve
Flutter aynı veride kontrolleri yeniden kurmaz; yazılmakta olan alanlar
korunur. Başlıktaki yenile yalnız elle başlatılan istekte meşgul görünür;
kartın ayrı Router yenile düğmesi kaldırıldı.

Orion Hub Panel bağlantısının altında yalnız Hub cihazında açıldığı
belirtilir. Panelin HTML girişine kullanıcı tıklamasıyla gelen GET belge
gezinmesi kabul edilir; çapraz kaynak API çağrıları ve yazma istekleri
engellenmeye devam eder. Eski panel testi, artık bulunmayan sohbet dönüş
linki yerine mevcut yönetim menülerinin /panel önekini doğrular.
