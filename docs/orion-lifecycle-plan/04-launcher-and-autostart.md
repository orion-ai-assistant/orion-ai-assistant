# Aşama 04 — Başlatıcı ve Windows başlangıcı

Durum: BAŞLANMADI. Bu dosyada test sonuçları ve tamamlanma kanıtı henüz yoktur.

[Ana plan](../orion-installation-lifecycle-implementation-plan.md) · [Ortak sözleşmeler](shared-contracts.md)

## Ön koşullar ve okunacaklar

Ön koşul aşamalar: [01](01-process-lifecycle.md), [02](02-installation-registry-and-router-selection.md), [03](03-installation-paths-and-migration.md)

Ortak kararlar; 2.3 süreç yönetimi ve 2.4 OS sınırları; 01–03 sonuç kayıtlarındaki kararlı arayüzler.

Ön koşulların yalnızca durum etiketini değil, sonuç kaydını ve mevcut koddaki karşılığını doğrula. İlgisiz gelecek aşamaların tamamını okumak veya uygulamak gerekmez. Repo talimatları ayrıca geçerlidir.

## Bu aşamadaki iş ve kabul koşulları

Küçük sabit giriş noktası kayıt üzerinden ortak yaşam döngüsü katmanını çağırır. Masaüstü ve kullanıcı Başlangıç klasörü aynı girişe gider. Servis otomatik başlatma ile pencere gösterme ayrılır. Tekrar tıklama mevcut pencereye yönlendirir veya yalnız pencereyi açar. Başlatıcı hata/onarma için Hub'a bağımlı değildir. Hub/Router/yardımcı kilitleriyle aynı anda start/stop/update çakışmaları önlenir.

Kabul/test: temiz Windows oturumunda başlatma, art arda tıklama, oturum açılışı ve elle açılışın çakışması, tercih aç/kapat, pencereyi kapatınca servis politikasına uyma, D diski yokken anlaşılır hata, Hub açılamazken günlük/onarma erişimi. Gerçek Windows VM oturumuyla doğrulanır.

## Sonraki aşamaya devredilecek çıktı

Tek başlatıcı giriş noktası; kullanıcı başlangıç kısayolu ve pencere tercihleri; yerel hata/onarma davranışı.

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
