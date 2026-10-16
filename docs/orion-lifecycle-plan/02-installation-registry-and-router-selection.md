# Aşama 02 — Kurulum kaydı ve eski kurulumları tanıma

Durum: BAŞLANMADI. Bu dosyada test sonuçları ve tamamlanma kanıtı henüz yoktur.

[Ana plan](../orion-installation-lifecycle-implementation-plan.md) · [Ortak sözleşmeler](shared-contracts.md)

## Ön koşullar ve okunacaklar

Ön koşul aşamalar: [01](01-process-lifecycle.md)

16 Ekim 2026 kullanıcı kararı: 01'in çekirdek uygulaması ve son 40 yaşam döngüsü testi temel alınarak bu aşamanın geliştirmesine devam edilebilir; kalan ürün/UI/OS kabul borcu 01'de açık kalır. Kullanıcı manuel denemelerini tek tuşla başlatma akışı hazır olduğunda yapacak. Bu, 01'in tamamlanması veya yayın onayı değildir. Başlamadan [oturum devir kaydını](02-session-handoff.md) oku; testleri agent çalıştırır. Bu devir oturumunda 02 koduna veya AppData kurulumuna başlanmadı.

Ortak kararlar; bölüm 2 kurulum kaydı ve 2.1 Router doğrulaması; 01 sonuç kaydı.

Ön koşulların yalnızca durum etiketini değil, sonuç kaydını ve mevcut koddaki karşılığını doğrula. İlgisiz gelecek aşamaların tamamını okumak veya uygulamak gerekmez. Repo talimatları ayrıca geçerlidir.

## Bu aşamadaki iş ve kabul koşulları

Sürümlemeli kayıt okuyucu/yazıcı, kimlik doğrulama ve eski Router kaydı uyarlayıcısı eklenir. Kaynak depo/geliştirme modu açıkça ayrılır; yerel değişiklikleri otomatik resetleme yasaktır. Kayıtlı yol önceliklidir, tanınmış eski konumlar kontrollü keşif içindir; PATH/port sırası hedef seçmez. Bozuk kayıt korunur ve onarma sunulur; yeni boş kurulumla örtülmez.

Kabul/test: yalnız eski Router, yalnız eski Hub, ikisi birlikte, farklı kökler, iki Router adayı, geçersiz kayıt, eksik disk, yanlış ürüne işaret eden yol, Türkçe/boşluklu yol, aynı kullanıcıda development ve kullanıcı kurulumu. Router UUID/SPKI ve DB içerikleri aynı kalır. Kayda alma hiçbir veri taşımaz.

Ek test: Gözat ile seçilen gerçek/eksik/taklit/kapalı/uyumsuz Router ve mevcut TLS kimliği uyuşmazlığı. Yalnız klasör doğrulanan Router'a `sağlıklı` denmez. Keyfi klasör seçimi betik çalıştırmaz. Bağımsız Router değişimi eski DB'yi yeni DB ile birleştirmez veya eski süreci yetkisiz kapatmaz.

## Sonraki aşamaya devredilecek çıktı

Sürümlemeli kayıt şeması ve uyarlayıcılar; Router keşif/seçim/doğrulama sonuçları; bozuk/eski kayıt davranışı.

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
