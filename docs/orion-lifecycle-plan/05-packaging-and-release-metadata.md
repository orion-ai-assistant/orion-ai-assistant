# Aşama 05 — Hazır paketler ve sürüm uyumluluğu

Durum: BAŞLANMADI. Bu dosyada test sonuçları ve tamamlanma kanıtı henüz yoktur.

[Ana plan](../orion-installation-lifecycle-implementation-plan.md) · [Ortak sözleşmeler](shared-contracts.md)

## Ön koşullar ve okunacaklar

Ön koşul aşamalar: [02](02-installation-registry-and-router-selection.md), [03](03-installation-paths-and-migration.md)

16 Ekim 2026 son kullanıcı kararı: yeni oturumda yalnız 05 geliştirmesine geçilecek. 01/03/04 açık kabul borçları korunur; Computer Use ve manuel pencere/logon kontrolleri 05 sonrasındaki kullanıcı denemesine ertelendi. 03'ün eski runtime/DB dönüşümünde 05/08'e bağımlı eksikleri ve 04'ün bu manuel kontrolleri, 05 kod/paket geliştirmesini döngüsel biçimde bloke etmez. Temiz bilgisayar/SDK'sız paket kabulü ise kanıt olmadan geçmiş sayılmaz. [Hazır oturum promptu](05-session-prompt.md).

Ortak kararlar; bölüm 2 veri/runtime düzeni; 02–03 sonuç kayıtlarındaki kayıt ve yol sözleşmeleri.

Ön koşulların yalnızca durum etiketini değil, sonuç kaydını ve mevcut koddaki karşılığını doğrula. İlgisiz gelecek aşamaların tamamını okumak veya uygulamak gerekmez. Repo talimatları ayrıca geçerlidir.

## Bu aşamadaki iş ve kabul koşulları

CI her desteklenen Windows mimarisi/profili için hazır Hub runtime, Flutter ve bağımsız Router paketlerini üretir. Yeni kurulum SDK'sız temiz VM'de denenir. Sürüm kaydı paket kimliği, tam sürüm, API aralığı, veri şeması, platform, boyut, hash, imza, hedef adres ve geri dönüş politikasını içerir. İmza güven kökü, anahtar yenileme, eskimiş metaveri ve sürüm düşürme koruması belirlenir; doğaçlama güvenlik protokolü yazılmaz. Aynı hedef sürümün değişmez paketleri indirilir. Model ve motor sürümleri ayrıdır.

Kabul/test: yalnız Hub/yalnız Flutter/yalnız Router değişimi, uyumsuz Hub–Flutter çifti, eski mobil istemci, CPU/CUDA profil seçimi, imza/hash hatası, arşivden hedef dışına çıkma, yanlış mimari, internet yokluğu, eski metaveri. Kontrol edilemedi ile güncel ayrıdır; son başarılı kontrol zamanı korunur. Paket değişiminde modeller yeniden indirilmez.

## Sonraki aşamaya devredilecek çıktı

CI paket çıktıları; güvenilen sürüm metaverisi ve uyumluluk şeması; SDK içermeyen temiz Windows VM kurulum kanıtı.

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


## 03’ten gelen runtime hazırlık girdisi

05’in CI/paket çalışması başlamadı. Ancak 04 öncesi kullanıcı talebiyle PostgreSQL Unicode kaynak kurulumu ön koşulu dar kapsamda düzeltildi: [Unicode takip kaydı](03-unicode-postgres-followup.md). Sabit özetli üretici 16.15-1 arşivinden staging’de uygulama bazında UTF-8 manifestli sunucu runtime’ı hazırlanır; makbuzda üretici ve türetilmiş EXE özetleri ayrıdır. 05 bu dönüşümü paket üretiminde, son imzadan önce yapmalı; hazırlama kodunu kullanıcıdaki aktif runtime’ı yerinde yamalamaya çevirmemelidir. Kaynak kurulumunun SHA256 kontrolü imzalı sürüm metaverisinin veya temiz VM kabulünün yerine geçmez. Eski runtime/DB dönüşümü ayrıca doğrulanacaktır.
