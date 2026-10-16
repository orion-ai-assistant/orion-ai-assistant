# Aşama 00 — Davranış envanteri ve güvenli test ortamı

Durum: TAMAMLANDI — 16 Ekim 2026. Envanter ve izole test düzeni hazır; uygulama yaşam döngüsü davranışı değiştirilmedi.

[Ana plan](../orion-installation-lifecycle-implementation-plan.md) · [Ortak sözleşmeler](shared-contracts.md)

## Ön koşullar ve okunacaklar

Ön koşul aşamalar: Yok; ilk aşama.

Ortak kararlar; mevcut durum envanteri; test matrisi. Bu aşama keşif ve test düzeniyle sınırlıdır, yaşam döngüsü davranışı değiştirilmez.

Ön koşulların yalnızca durum etiketini değil, sonuç kaydını ve mevcut koddaki karşılığını doğrula. İlgisiz gelecek aşamaların tamamını okumak veya uygulamak gerekmez. Repo talimatları ayrıca geçerlidir.

## Bu aşamadaki iş ve kabul koşulları

Hub/Router/installer/Flutter başlangıç, durdurma, kaldırma, sıfırlama, güncelleme ve veri yolları listelenir. Yerel ve Docker girişlerinin farkları belgelenir. Eski kurulumlar için yapay veri, ayar, UUID, sertifika ve model örnekleri hazırlanır. Değişiklik öncesi mevcut testler çalıştırılır; başlangıç hataları yeni hatalardan ayrılır.

Kabul: her kalıcı verinin sahibi/konumu, başlatma girişleri, eski varsayılanlar ve mevcut Router kayıt biçimi belli. Ayrı geçici köklerde iki kurulum ve yabancı PostgreSQL süreçlerini temsil eden test düzeni var. Üretim verisine dokunulmadan test çalıştırılabiliyor.

## Sonraki aşamaya devredilecek çıktı

Kod girişleri ve veri sahipliği haritası; mevcut test komutları/başlangıç sonuçları; iki kurulum ve yabancı servis için izole fixture düzeni.

Uygulanan arayüzlerin gerçek dosya yolları, giriş noktaları, şema sürümleri ve hata sonuçları aşağıdaki sonuç kaydına yazılır. Sonraki model tasarım varsayımı yerine bu kaydı ve kodu esas alır.

## Kapsam sınırı

Yalnız bu aşamanın işini uygula. Ön koşulda eksik sözleşme varsa dar kapsamlı düzeltmeyi kaydet; başka aşamanın tüm özelliğini sessizce üstlenme. Gerçek kullanıcı verisinde hata enjeksiyonu yapma. Aşama testleri geçmeden sonraki aşamaya geçme; bu aşama bittiğinde sonuçları raporlayıp dur. Ana plan tablosundaki durum da güncellenir.

## Sonuç ve test kaydı

- Durum: TAMAMLANDI (yalnız Aşama 00 kapsamı).
- Başlangıç / bitiş tarihi: 16 Ekim 2026 / 16 Ekim 2026, Europe/Istanbul.
- Çalışılan depolar: Hub `f90459a`, Router `ef73f82`, Flutter `93d4dc4`. Router ve Flutter yalnız okundu/test edildi. Başlangıçta Hub tasarım önerisi ve henüz izlenmeyen plan belgeleri, Flutter `chat_presentation_test.dart` değişikliği vardı; bunlar geri alınmadı.
- Değişen dosyalar: bu dosya, ana rehber, yeni [mevcut durum envanteri](00-current-inventory.md), `scripts/lifecycle/test_environment.py`, `test_test_environment.py`, `run_baseline.py`. Uygulama kaynak dosyası değişmedi.
- Plan veya bağımlılık değişikliği: yok. Sonraki aşama 01; başlatılmadı.
- Sonraki aşamaya uygunluk: envanter, başlangıç test sonuçları ve tekrar kullanılabilir fixture hazır. 01 kendi güvenli süreç kabul testlerini ayrıca uygulamalıdır.

### Envanter ve arayüzler

[Kod girişleri, veri sahipliği, eski varsayılanlar, yerel/Docker farkları ve mevcut kayıt şemaları](00-current-inventory.md) ayrı kaydedildi. Router kaydı v1 ve Hub kaydı v1 korunuyor; birleşik `install.json` uygulanmış gibi gösterilmedi.

Fixture API: `scripts/lifecycle/test_environment.py` içinde `temporary_lab()` context manager'ı, `LifecycleLab.installations` (iki bağımsız geçici kök), `environment(index)`, `processes`, `reused_pid(pid)`, `snapshot(root)`. Süreçler yalnız `FakeProcess` veri tanımlayıcılarıdır; OS PID veya sinyal hedefi değildir. Aynı portta yabancı PostgreSQL, farklı başlangıç zamanı/kimlikle tekrar kullanılan PID ve iki ayrı kurulum vardır. Gerçek PostgreSQL cluster/runtime değildir; `PG_VERSION` yanındaki `SYNTHETIC` dosyası bunu açıklar.

Yapay veri: SQLite örnek hesap/ayar/geçmiş/taslak, env, Redis sentinel, tarayıcı Preferences, model ve yarım indirme, Hub/Router UUID, yeni üretilmiş eşleşen test anahtarı/sertifika çiftleri, bootstrap proof, eski locator ve PID/lock dosyaları. SQLite üretim şeması veya PostgreSQL yedeği değildir. Sertifikalar test için her koşuda üretilir; kullanıcı kimliği kopyalanmaz. Fixture testleri geçici kökleri temizler, ortam değişkenlerini geri yükler.

`run_baseline.py` mevcut interpreter bağımlılıklarını kullanır; paket indirme/kurulum yapmaz. `hub|router|fixtures` setlerini 90 saniye üst sınırı olan alt süreçte çalıştırır, timeout'ta kod 124 döner. Her koşu `.tmp/lifecycle/baseline-*` altında yeni kök ve ayrı APPDATA/LOCALAPPDATA/XDG/security/seçim/legacy UUID/TEMP/TMP kullanır; pytest cache ve plugin otomatik yükleme kapalıdır. Hub/Router test worker'ında `asyncpg.connect/create_pool` yasaktır ve çağrı sayısı çıkışa yazılır. Bu koruma seçili test setleri içindir; keyfi tüm repo testlerine veya her ağ/DB kütüphanesine güvenlik sınırı sağladığı iddia edilmez.

### Test komutları ve sonuçları

Windows, PowerShell, Python 3.11.2 (`.tmp/policy-test-runtime/Scripts/python.exe`, mevcut geliştirme bağımlılıkları), Flutter 3.38.5 / Dart 3.10.4 (SDK cache sürüm kaydı). Ortamlara bağımlılık eklenmedi. Komutlar Hub kökünden, Flutter komutu kendi `main_app/app` dizininden çalıştırıldı.

```powershell
& .tmp/policy-test-runtime/Scripts/python.exe scripts/lifecycle/run_baseline.py hub
& .tmp/policy-test-runtime/Scripts/python.exe scripts/lifecycle/run_baseline.py router
& .tmp/policy-test-runtime/Scripts/python.exe scripts/lifecycle/run_baseline.py fixtures
# Kardeş Flutter deposunda main_app/app:
flutter test --no-pub test/local_hub_installation_test.dart
# Hub kökünde:
git diff --check
```

| Kontrol | Sonuç | Kapsam / kanıt |
| --- | --- | --- |
| Mevcut Hub/installer başlangıç seti | **41 passed, 2 subtests passed**, 8.70 s; 0 fail/skip | `test_installer_browser_lifecycle.py`, `test_flutter_launcher.py`, `test_router_bootstrap.py`, `test_local_registration.py`, `installer/tests`; DB connect **0**, pool **0**. Bir mevcut aiohttp `RouterSession` kalıtım deprecation uyarısı |
| Mevcut Router başlangıç seti | **4 passed**, 0.39 s; 0 fail/skip | `dev/test-orion-router/test_local_installation.py`; locator/kimlik korunması ve farklı kök/kaçış reddi; DB connect **0**, pool **0** |
| Mevcut Flutter başlangıç seti | **1 passed**, 0 fail | `local_hub_installation_test.dart`: gerçek sertifikadan SPKI, kök dışına kaçan yol ve yanlış proje reddi; test kendi geçici dosyalarını kullanır |
| Yeni fixture düzeni | **7 passed**, 5.417 s | İki kök, ortam geri yükleme/temizlik, mevcut Hub locator okuyucusuyla uyum, diğer kurulum/yabancı veri hash'lerinin korunması, eşleşen TLS çiftleri, PID reuse/port çakışması temsili, salt okunur DB örnekleri, snapshot'ın ekleme/silmeyi yakalaması |
| Whitespace | `git diff --check` başarılı | Başlangıçtan mevcut tasarım önerisi için Git LF→CRLF bilgilendirmesi; hata değil |

Mevcut testler uygulama koduna değişiklik yapılmadan toplandı. Yeni fixture yardımcıları bu başlangıç setlerinin test gövdelerini değiştirmedi. Tüm repo/ML/model/ücretli sağlayıcı testleri çalıştırılmadı; bu sonuçlar yaşam döngüsüne ilişkin seçili başlangıç setidir.

### İlk denemeler ve düzeltilen test düzeni hataları

- İlk sandbox Hub denemesi `......F..` kısmi çıktısından sonra ilerlemedi; test özeti üretmedi ve görevdeki o komut Ctrl+C ile kesildi. Bu koşu başarı sayılmadı; tek F'nin kök nedeni tam traceback alınamadığı için belirlenmedi. Aynı mevcut set izinli sandbox dışı koşuda tamamen geçti; başlangıçta kalıcı bir ürün hatası doğrulanmadı.
- Router'ın ilk sandbox koşusunda **4 error**, UUID yayınlayan `os.link` için `WinError 5`. İzinli sandbox dışı koşuda 4/4 geçti; hard-link üretim davranışı değiştirilmedi.
- Flutter sandbox denemesi çıktı vermeden kaldı ve kesildi; izinli sandbox dışı `--no-pub` koşusu geçti. Sürüm CLI'si de sandbox içinde ilerlemediğinden SDK cache sürüm dosyası okundu.
- Yeni fixture'ın ilk koşusunda SQLite context manager'ın bağlantıyı kapatmadığı görüldü (Windows temizlikte `WinError 32`); `contextlib.closing` ile düzeltildi. Bu yeni test düzeni hatasıdır, ürün hatası değildir.
- Sandbox altında mevcut locator tüketicisinin `Path.resolve(strict=True)` çağrısı sentetik Router dizininde `WinError 5` verdi ve okuyucu `None` döndü. Kısa workspace scratch kökünde de tekrarlandı; uzun yol hatası olarak sınıflandırılmadı. Aynı test izinli sandbox dışı geçti. Fixture'ın tüm son testleri gerçek yol çözümlemesi korunarak çalıştırıldı; mock ile erişim başarılı gösterilmedi.

### Korunma kanıtı, sınırlar ve geri alma

Uygulama başlat/durdur/kaldır/sıfırla/güncelle komutları, Docker daemon/volume mutasyonları ve canlı DB erişimi çalıştırılmadı. Gerçek kullanıcı klasörleri taşınmadı/silinmedi; ACL mirası değiştirilmedi. Hash snapshot testleri sentetik diğer kurulum ve yabancı PostgreSQL sentinel'inin değişmediğini gösterir; gerçek yabancı OS sürecinin güvenli durdurulduğu iddiası değildir. Gerçek TLS örnekleri oluşturuldu; production kimlikleri okunmadı.

Bu aşamada gerçek servisli Windows VM, eşzamanlı OS süreç yönetimi, yeniden oturum açma/güç kesintisi, D sürücüsü kaybı, tam Docker entegrasyonu ve gerçek PostgreSQL veri bütünlüğü sınanmadı. Bunlar keşif/fixture kabulünün yerine geçmez; 01 ve ilgili sonraki aşamaların kabul testleridir. Yeni fixture'ı kullanmak gerçek DB cluster veya paket kurulumu üretmez.

Geri alma: yalnız bu çalışmanın üç `scripts/lifecycle` dosyasını ve ek envanter belgesini kaldırıp iki plan belgesindeki bu aşama durum/kayıt değişikliklerini geri al. Kullanıcının önceki plan/tasarım/Flutter değişikliklerini topluca reset etme. Üretim veri veya sürüm geri alma işlemi gerekmiyor.
