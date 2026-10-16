# Aşama 08 — Router geçişi, kaldırma ve kontrollü yayın

Durum: BAŞLANMADI. Bu dosyada test sonuçları ve tamamlanma kanıtı henüz yoktur.

[Ana plan](../orion-installation-lifecycle-implementation-plan.md) · [Ortak sözleşmeler](shared-contracts.md)

## Ön koşullar ve okunacaklar

Ön koşul aşamalar: [04](04-launcher-and-autostart.md), [05](05-packaging-and-release-metadata.md), [06](06-update-helper-and-recovery.md), [07](07-automatic-updates-and-flutter.md)

Ortak kararlar; 04–07 sonuç kayıtları; eski Router keşfi/taşıması için 02–03 arayüz kayıtları.

Ön koşulların yalnızca durum etiketini değil, sonuç kaydını ve mevcut koddaki karşılığını doğrula. İlgisiz gelecek aşamaların tamamını okumak veya uygulamak gerekmez. Repo talimatları ayrıca geçerlidir.

## Bu aşamadaki iş ve kabul koşulları

Router'ın mevcut güncelleme ekranı/CLI'si yeni bağımsız işlem modeline bağlanır. Eski Git/pip/build yerinde güncelleme üretim yolundan çıkarılır. Legacy kaynak kurulumu, ön denetim ve gerektiğinde sahip onaylı bir defalık dönüştürmeyle paketli kurulum olur; otomatik güncelleme bu dönüşümü gizlice gerçekleştirmez. Tek başına kurulan Router aynı sözleşmelerle çalışır ve Hub gerektirmez.

Kaldırma varsayılan olarak kullanıcı verilerini/modelleri korur. Veri silme açık ve ayrı işlemdir; seçilmiş ortak model kökü başka bileşen kullanıyorsa otomatik silinmez. Eski sürüm temizliği çalışan, son sağlıklı veya kurtarma için gereken sürümü silmez. Yeni kurucu eski kurucu tarafından tekrar hard reset uygulanmasını engelleyecek sürüm/geçiş denetimi içerir.

Kabul: sıfır kurulum, yalnız eski Router, yalnız eski Hub, birleşik legacy kurulum, geliştirme ile yan yana kurulum ve paketli kurulumdan bir sonraki sürüme yükseltme uçtan uca geçer. Önce test VM'leri, sonra isteğe bağlı beta, ardından stable yayınlanır. Her yayın için eski sürümden yükseltme ve başarısız yeni sürümden kurtarma kanıtı bulunur.

## Sonraki aşamaya devredilecek çıktı

Legacy Router dönüşümü; bağımsız Router güncelleme akışı; kaldırma/temizleme politikası; beta/stable yayın kontrolü.

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
