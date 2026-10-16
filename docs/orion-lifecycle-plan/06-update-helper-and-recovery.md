# Aşama 06 — Kesintiye dayanıklı güncelleme yardımcısı

Durum: BAŞLANMADI. Bu dosyada test sonuçları ve tamamlanma kanıtı henüz yoktur.

[Ana plan](../orion-installation-lifecycle-implementation-plan.md) · [Ortak sözleşmeler](shared-contracts.md)

## Ön koşullar ve okunacaklar

Ön koşul aşamalar: [01](01-process-lifecycle.md), [02](02-installation-registry-and-router-selection.md), [03](03-installation-paths-and-migration.md), [05](05-packaging-and-release-metadata.md)

Ortak kararlar; 2.2 taşıma günlüğüyle uyum, 2.3 kilitler, 2.4 kalıcı dosya/OS sınırları; 01, 02, 03 ve 05 sonuç kayıtları.

Ön koşulların yalnızca durum etiketini değil, sonuç kaydını ve mevcut koddaki karşılığını doğrula. İlgisiz gelecek aşamaların tamamını okumak veya uygulamak gerekmez. Repo talimatları ayrıca geçerlidir.

## Bu aşamadaki iş ve kabul koşulları

Hub dışında çalışan yerel yardımcı, yalnız kayıtlı ve yetkili bileşenleri günceller. Genel komut/betik/keyfi dosya yolu kabul etmez. Hub/Flutter kapanınca işlem kaybolmaz. Güncelleme, başlatma, durdurma ve kaldırma ortak kilit kurallarını kullanır. Yardımcı ve bootstrap'ın kendi güncellemesi de ayrı hazırlama/kurtarma yolu ile tasarlanır; çalışan tek kurtarma kopyasının üzerine yazılmaz.

Kalıcı durum makinesi:

`checking -> downloading -> verifying -> staged -> waiting_for_idle -> draining -> backing_up -> switching -> health_check -> completed`

Hata yolları: `failed`, `rollback_pending`, `rolled_back`, `repair_required`. Her geçişte önceki/hedef paket, işlem UUID'si, doğrulama sonucu ve aşama kaydedilir. Disk ve güç kesintisinde kaydın dayanıklılığı sınanır. Hazırlama sırasında aktif dosya ve aktif Python ortamı değişmez. Geçiş öncesinde yeni iş kabulü atomik biçimde kapatılır, aktif işler tekrar denetlenir; yalnızca boşta kontrolünden sonra araya yeni iş sızamaz. Şema değişimi için tutarlı yedek ve geri dönüş uyumu gerekir.

Geçiş yalnız tamamlanmış sürüm setine yapılır. Sağlık denetimi Hub API, worker, DB şeması, istemci/API uyumu ve etkilenen Router kimliğini kapsar. Ücretli bulut çağrısı zorunlu test değildir. Sağlık doğrulanana kadar yeni kullanıcı işi açılmaz. Geriye uyumsuz şemada yalnız eski kodu seçmek yasaktır. Yeni kullanıcı verisi yazıldıktan sonra eski yedeği sessizce geri yüklemek yasaktır. Tekrar başlatma sayısı sınırlıdır; onarma erişilebilir kalır. Dış etkili yarım araç işi otomatik tekrar edilmez.

Kabul/test: her durumdan önce/sonra yardımcıyı sonlandır; her kalıcı yazım sınırında hata enjekte et; ardından tekrar başlat. Eksik paket çalışmaz; geçiş öncesi eski sağlıklı sürüm korunur; geçiş sonrası sağlık veya kontrollü geri dönüş tamamlanır. DB şema hatası, bozuk yedek, kilitli dosya, disk dolması, iki güncelleme, update/start/stop/remove çakışması, sağlık zaman aşımı ve yardımcı çökmesi sınanır. Gerçek güç kesintisi ayrıca VM zorla kapatma ile denenir; yalnız süreç öldürme testi güç kesintisi kanıtı sayılmaz.

## Sonraki aşamaya devredilecek çıktı

Yerel yardımcı arayüzü; kalıcı güncelleme durumları ve sağlık/geri dönüş sonuçları; hata enjeksiyonu ve VM kapanma kanıtı.

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
