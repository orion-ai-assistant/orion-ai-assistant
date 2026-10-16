# Aşama 07 — Sessiz otomasyon ve Flutter deneyimi

Durum: BAŞLANMADI. Bu dosyada test sonuçları ve tamamlanma kanıtı henüz yoktur.

[Ana plan](../orion-installation-lifecycle-implementation-plan.md) · [Ortak sözleşmeler](shared-contracts.md)

## Ön koşullar ve okunacaklar

Ön koşul aşamalar: [04](04-launcher-and-autostart.md), [06](06-update-helper-and-recovery.md)

Ortak kararlar; 04 başlatıcı ve 06 güncelleme yardımcısı sonuç kayıtlarındaki arayüzler.

Ön koşulların yalnızca durum etiketini değil, sonuç kaydını ve mevcut koddaki karşılığını doğrula. İlgisiz gelecek aşamaların tamamını okumak veya uygulamak gerekmez. Repo talimatları ayrıca geçerlidir.

## Bu aşamadaki iş ve kabul koşulları

Otomatik güncelleme tercihi stable kanalda kontrol/indirme/hazırlamayı sessiz yapar. İlk uygulamada hazır sürüm sonraki güvenli açılışta uygulanır. Kullanım sırasında otomatik geçiş, boşta ve iş kabulü kilidi güvenilir biçimde doğrulandıktan sonra etkinleştirilir. Aktif ses, sohbet üretimi, araç görevi, dosya aktarımı ve kaydedilmemiş taslak kullanım işaretidir. Uzak bağlı istemciler de hesaba katılır; Flutter penceresinin kapalı olması Hub'ın boşta olması anlamına gelmez.

Kullanıcı çalışıyorsa güncelleme bekler. Yeniden başlatmayı kendisi isterse kısa özet ve erteleme sunulur. Her kontrol/indirme için bildirim verilmez; anlamlı hata ve gerekli kullanıcı işlemi gösterilir. Ayarlarda otomatik güncelleme, sürümler, son başarılı kontrol, bekleyen geçiş ve geçmiş görünür. Ayar kapatılınca yeni otomatik geçiş başlamaz; başlamış kritik geçiş güvenli sınıra tamamlanır. Ağ kullanımına yönelik duraklatma/ölçülü bağlantı politikası açıkça belirtilir.

Router'ın bağımsız güncelleme tercihi korunur; Hub ayarı izinsiz biçimde Router ayarını açmaz. İlk kapsamda telefon durum görebilir ve istek iletebilir, PC güncellemesini normal Hub hesabıyla başlatamaz. Hub bağlantısı kesilince son bilinen durum/zaman gösterilir; başarı tahmin edilmez.

Kabul/test: kesintisiz kullanım sırasında indirme, yeni iş ile boşta geçiş yarışı, uzaktan aktif istemci, taslak korunması, erteleme, tercih kapatma, uygulama yeniden açılınca kalan durum, internetin gidip gelmesi, telefon bağlantısızlığı. Sadece değişen ve yetkili bileşen yeniden başlar.

## Sonraki aşamaya devredilecek çıktı

Otomasyon tercihleri; Flutter durum/hata/erteleme gösterimi; aktif iş ve uzak istemciyle güvenli geçiş sözleşmesi.

Uygulanan arayüzlerin gerçek dosya yolları, giriş noktaları, şema sürümleri ve hata sonuçları aşağıdaki sonuç kaydına yazılır. Sonraki model tasarım varsayımı yerine bu kaydı ve kodu esas alır.

## Kapsam sınırı

Yalnız bu aşamanın işini uygula. Ön koşulda eksik sözleşme varsa dar kapsamlı düzeltmeyi kaydet; başka aşamanın tüm özelliğini sessizce üstlenme. Gerçek kullanıcı verisinde hata enjeksiyonu yapma. Aşama testleri geçmeden sonraki aşamaya geçme; bu aşama bittiğinde sonuçları raporlayıp dur. Ana plan tablosundaki durum da güncellenir.

## Sonuç ve test kaydı

- Durum: BAŞLANMADI.
- Başlangıç / bitiş tarihi: —
- Çalışılan depolar ve değişen dosyalar: —
- Uygulanan arayüzler / şemalar / hata sonuçları: —
- Test komutları, ortam ve sonuçlar: —
- Veri / kimlik / hedef dışı süreç korunması kanıtı: —
- Çalıştırılamayan testler ve nedeni: —
- Kalan riskler / kapsam dışı durumlar: —
- Geri alma yöntemi: —
- Plan veya bağımlılık değişikliği: —
- Sonraki aşamaya uygunluk: Henüz doğrulanmadı.
