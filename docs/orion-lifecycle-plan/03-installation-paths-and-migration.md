# Aşama 03 — Installer konum seçimi ve veri ayrımı

Durum: BAŞLANMADI. Bu dosyada test sonuçları ve tamamlanma kanıtı henüz yoktur.

[Ana plan](../orion-installation-lifecycle-implementation-plan.md) · [Ortak sözleşmeler](shared-contracts.md)

## Ön koşullar ve okunacaklar

Ön koşul aşamalar: [02](02-installation-registry-and-router-selection.md)

Ortak kararlar; bölüm 2 veri düzeni, 2.2 taşıma sözleşmesi ve 2.4 OS sınırları; 02 sonuç kaydı.

Ön koşulların yalnızca durum etiketini değil, sonuç kaydını ve mevcut koddaki karşılığını doğrula. İlgisiz gelecek aşamaların tamamını okumak veya uygulamak gerekmez. Repo talimatları ayrıca geçerlidir.

## Bu aşamadaki iş ve kabul koşulları

Konum seçimi indirmeden önce sunulur; mevcut `install.ps1` önce sabit konuma clone edip sonra seçim yaptırmaz. Küçük ilk kurucu, görsel kurulum ve sessiz kurulum parametreleri aynı doğrulamayı kullanır. Yeni kurulum seçilen köke oluşturulur; komutlar, kısayollar, günlükler ve yardımcılar kayıtlı yolları kullanır. Model konumu varsayılanı seçilen kökü izler.

Eski kurulum için iki ayrı işlem vardır: (a) mevcut konumda kullanmaya devam etmek; (b) kullanıcı seçerse kontrollü taşımak/dönüştürmek. Çalışan DB'nin dizini doğrudan kopyalanmaz. Kaynak durdurularak tutarlı kopya veya PostgreSQL destekli yedek yöntemi kullanılır. Aynı PostgreSQL ana sürümü ve runtime uyumu denetlenir. Kopya hedefte doğrulanır, kimlikler ve örnek veri kontrol edilir, kayıt ancak sonra değiştirilir. Eski kaynak hemen silinmez; iki kopyanın aynı anda yazması engellenir. Kesinti günlüğü sonraki açılışta toparlanır.

Kabul/test: varsayılan/özel kök, yeniden kurulum, eski bağımsız Router'ı koruma, dolu/geçersiz/yazılamayan hedef, disk dolması ve C–D kopyasında kesinti, açık DB, ayar/model/tarayıcı profili korunması, şifre ve sertifika eşleşmesi. Başarısız taşıma kaynağı değiştirmez. Özel hedefteki başlatıcı sabit AppData kod yolunu kullanmaz.

Ek test: yalnız model/indirme/runtime/Router taşıma; paylaşılan modeli iki bileşenin kullanması; aktif model yüklemesi; her taşıma adımında kesinti ve tekrar açılış; hedef hash hatası; kullanıcıya ait hedef dosyasıyla çakışma; junction/symlink; kopyadan sonra kaynak temizliğinin kesilmesi; Python runtime'ın yeni yolda çalışması. Güncelleme/taşıma aynı anda yapılamaz. Büyük dosya kopyasında kaynak değiştirilirse işlem farkı algılar ve eski doğrulama sonucunu kullanmaz.

## Sonraki aşamaya devredilecek çıktı

Gerçek konum çözümleme arayüzü; yeni kurulum ve yeniden kurulum akışı; taşıma günlüğü, kurtarma ve temizleme sözleşmesi.

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
