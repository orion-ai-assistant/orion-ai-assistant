# Aşama 03 oturum devri — 16 Ekim 2026

[Ana rehber](../orion-installation-lifecycle-implementation-plan.md) · [03 kapsamı ve sonuçları](03-installation-paths-and-migration.md) · [Ortak sözleşmeler](shared-contracts.md) · [02 sonuçları](02-installation-registry-and-router-selection.md)

## Kullanıcının son kararı

Bu oturum Aşama 02’den sonra 03’ün uygulanmasına devam etti. Kullanıcı son mesajında kapsamı **03’te commit atıp durmak** olarak daralttı; devamı ayrı oturumda olacak. Önceki “bütün aşamaları limit bitene kadar yap” talimatı artık otomatik 04’e geçme yetkisi değildir. 04–08’e başlanmadı.

**03 DOĞRULAMA EKSİK.** Kod ve çekirdek testleri var; tam ürün/DB/runtime taşıması ve uçtan uca yeni kurulum/başlatıcı kabulü yok. Bu eksikler sırf commit atıldı diye tamamlanmış sayılmaz. 01’in açık ürün/UI/OS kabul borcu da sürüyor. Manuel testleri kullanıcıya rutin iş olarak devretme; kullanıcı kendi denemesini tek tuşla başlatma hazır olduğunda yapacak.

## Commit ve çalışma ağacı

- Başlangıç Hub `5c09b68`, Router `972e81d` korunur.
- Aşama 02 Hub commit’i **`4e2019c`**: sürümlü kayıt, Router seçimi ve gerçek süreç/TLS/API/DB doğrulaması.
- 03 kodu, sonuçları ve bu devir dosyası ayrı Hub commit’i olarak korunur; tam kimlik için `git log -2 --oneline` kontrol et. Push istenmedi ve yapılmadı.
- Router ve Flutter kaynaklarında bu oturumun yeni commit’i yok.
- Kullanıcının önceden var olan Hub `docs/orion-update-strategy-proposal.md`, `services/hub/uv.lock` durumu ile Flutter `main_app/app/test/chat_presentation_test.dart` diff’i görev dışında kaldı. Reset/clean etme.

## Kararlı arayüzler

- `services/shared/installation_registry.py`: şema v1, `Registry.read/write/update/read_backup`, `component_record`, `new_registry`, `import_legacy_installations`. İsteğe bağlı `locations` modeller/indirmeler/runtime/yedek konumlarını tutar. `ORION_INSTALLATION_REGISTRY` yalnız açık mutlak override. `operation_lock(path)` taşıma ve model işlemleriyle ortak kilittir. Kimlikler/sırlar yeniden üretilmez.
- `installation_paths.py`: `canonical_path`, `validate_destination`, `validate_private_directory`, `resolve_location`, `registered_component_paths`. Yerel sabit NTFS/ReFS, reparse/junction, dolu/çakışan hedef, disk/yazma/ACL kontrolleri; ürün ACL’sini değiştirmez.
- `scripts/bootstrap_installation.py` canonical kaynak planlayıcıdır. `scripts/generate_bootstrap_wrappers.py` bunu ve kayıt/yol kodunu `install.ps1`/`install.sh` içine gömer. İlgili kaynak değişince generator çalıştır ve `--check` yap. Script companion dosyası veya ağ helper’ı gerektirmez. Legacy locator varsa önce açık import gerekir; bozuk kayıt veya mevcut kaynak üstünde reset/fetch/init yapılmaz. Windows `-Browse` isteğe bağlıdır; gerçek UI kabulü yapılmadı.
- `bootstrap_registration.py:register_bootstrap`: başarılı local setup sonrası kayıt/config, mevcut UUID/tercih/sırları korur. Manager installer venv’inden çağırır. Config hatasında kayıt değişmez; kayıt hatasında yalnız kendi config yazımını geri alır. Bu, OS güç kesilmesinde iki dosyanın tek atomik işlem garantisi değildir.
- `model_locations.py:models_directory`: canonical kaydın ilgili kurulu Hub’ına ait native servis için `locations.models/<service-id>`; başka development ağacına yönlenmez. Legacy kayıtta locations yoksa service/models korunur. Docker manifest yolları korunur; Docker model konumu taşıması/özel shared model kökü tamamlanmış değildir.
- Installer model liste/indir/sil ve yeni model-servis başlatma ortak operation lock kullanır. Native tüketiciye `ORION_NATIVE_MODELS_DIR` aktarılır. Gerçek modeller/GPU/ses yüklenmedi; llama/embedding komut üretimi sentetik dosya ve fake Popen ile doğrulandı.
- `content_migration.py`: `registry_revision(record)`, `begin_content_migration(registry,name,destination,expected_revision=...,quiescence_gate=...,fault=...)`, `resume_content_migration(registry,journal_path,quiescence_gate=...,fault=...)`. Yalnız Models/Downloads; güvenilir host tüm okuyucu/yazıcıları işlem boyunca duraklatmış olmalı. Callback tam True değilse reddeder. Ürüne bağlı gerçek drain gate’i ve ayar ekranı henüz yok; buton açma.
- Journal `registry.parent/migrations/<UUID>.json`; SHA256 envanter, hedef marker’ı, CAS switch, `repair-required`. Kaynak silinmez. Tamamlanan hedefe yeni yazı geldiyse otomatik eskiye dönülmez. Yarım dosya/hash sorunu sessiz toparlanmış sayılmaz.
- Hub installer’ın tehlikeli Router “.git eksikse sil/WMIC terminate/remote installer çalıştır” yolu kaldırıldı. Var olan Router yerinde kullanılır. Yeni Router paket provisioning’i henüz yok; açık hata verir. Bağımsız Router deposunun standalone installer’ı bu korumayla güncellenmiş değildir.

## Testler ve gerçek kapsam

Tüm komutlar Hub kökünden; `.tmp/policy-test-runtime/Scripts/python.exe` mevcut Python 3.11.2 runtime’ıdır. Dependency indirme veya lock yenileme gerekmedi. Ağır ürün/DB/Next koşularını sırayla çalıştır.

| Koşu | Sonuç |
| --- | --- |
| 02 son set | 61 passed, 13 subtests; `.tmp/lifecycle/results/stage02-final.xml` |
| 03 ilk birleşik set | 69 passed, 53 subtests, 23.08 s; `stage03-unit.xml` |
| Birleşik 02–03 regresyonu | **133 passed, 70 subtests**, 62.59 s; `stage02-03-final.xml`. Ürün motorları/model/GPU çalıştırmaz |
| Son legacy locator bootstrap düzeltmesi | **14 passed**, 1.51 s; `stage03-bootstrap-final.xml`; önceki birleşik koşuyla örtüşür, test sayıları toplanmaz |
| Hub/installer regresyonu | 48 passed, 2 subtests, 36.34 s; `baseline-ynnoj0ff-hub.{json,xml}`; beklenmedik DB/pool girişimi 0/0 |
| Gerçek Router (02) | `product-aogd4vfe/result.json` son koşu başarılı: süreç/TLS/UUID/API/uygulama DB şeması, eşzamanlı start/repeat-start/iki stop; eski DB-probe hataları `previous-*` altında |
| AppData kayıt fixture’ı (02) | `C:/Users/krsta/AppData/Local/Orion/lifecycle-tests/stage02-p33ci918/result.json`; mevcut public manifestler ve fixture verileri/kimlikleri korunmuş |
| Gerçek C→D içerik kopya/kurtarma (03) | `C:/Users/krsta/AppData/Local/Orion/lifecycle-tests/stage03-6sfck09_/result.json`; D hedefi `.tmp/stage03-cross-volume-qmj4tpcd`; sentetik Models, verified sınırında crash/resume, kaynak/config/UUID korunmuş |

Standalone Windows/Git Bash ve private ACL fixture testleri için normal kullanıcı bağlamında `ORION_BOOTSTRAP_PRIVATE_FIXTURE=1` kullanıldı; son 03 koşusunda skip yok. Fixture oluşturulurken yalnız yeni test kökünün DACL’si daraltıldı. Sandbox Job/mutex/TLS/ACL kısıtları gerçek ürün hatası diye sunulmadı; gereken koşular yetkili normal kullanıcı bağlamında yapıldı. Ürün veya kullanıcı dizinine ACL rewrite yok.

Son AST kontrolü 23 Python dosyasında geçti. Süreç denetimi `.tmp/lifecycle/results/stage03-final-audit.json` içinde kendi test kapsamındaki kalan süreçler `[]`; kullanıcı süreçlerine sinyal gönderilmedi. Ignored test logları commit’e dahil değildir; MD sonuçları korunur.

## Kalan iş ve güvenli devam

03’ün tam kabulü verilmeden 04’ü tamamlandı sayma. Önce şu açıklıkları somut kod/test ile ele al:

1. Yeni kaynak kurulumunun gerçek uçtan uca kurulması, tekrar kurulması ve özel kökten açılması. Yerel commit’ler henüz upstream main’de değildir; Git clone mock’ları gerçek kurulum başarısı değildir. Ara kaynak yerleşimi `Hub` altındadır; immutable releases/data/state ayrımı 05 ile uyumlandırılmalı.
2. Gerçek tüketici/yazıcı sahipliğini tutan drain/quiescence gate, ortak modelin aktif kullanım sınırı, kullanıcıya onarma/taşıma arayüzü. `lambda: True` yalnız kendi boş/sentetik fixture’ında kullanıldı; gerçek kullanıcı ürünü için kullanma.
3. Tutarlı PostgreSQL yedeği/durdurma ve hedef şema/DB/UUID/TLS/profil doğrulaması; Hub/Router/runtime taşıma desteği. Legacy venv taşınabilir varsayılmaz; güvenli dönüşüm yoksa 05/08 bağımlılığını açık tut, dosyaları taşıyıp tamamlandı deme.
4. Gerçek native model yükleme/ses/GPU, Browse UI ve özel hedefte başlatıcı doğrulaması. Model kaynakları/dependency’ler izinsiz indirilmedi. 01’in aktif sohbet/araç/ses kapanışı, manuel tarayıcı izin akışı, tam UI eşzamanlılığı ve OS kabulü hâlâ açık.
5. Gerçek OS/güç kesme kanıtı için uygun izole ortam; ana Windows oturumunu kapatma. Sentetik exception/crash testleri güç kesintisi değildir.

AppData’da ayrı test yetkisi sürüyor; gerçek `%LOCALAPPDATA%` sandbox içinde başka yere yönlenebiliyor. Mevcut AppData kayıtlarını/dizinlerini önce oku; üzerine yazma. Benzersiz fixture, ayrı port/DB/Redis/TLS/profile/state kullan. Kullanıcı verisini hata fixture’ına kopyalama, eski kimliği yeni canlı kopyaya çoğaltma. Test işi kullanıcıya devredilmez. Otomatik güncelleme/Windows başlangıcı etkinleştirilmedi.

## Sonraki oturum için prompt

> `docs/orion-installation-lifecycle-implementation-plan.md`, `docs/orion-lifecycle-plan/03-session-handoff.md` ve Aşama 03 sonuç kaydını oku. Önce üç repoda status/log ve repo kurallarını kontrol et. Aşama 02’nin kayıt/seçim çekirdeği commit’li; Aşama 03 DOĞRULAMA EKSİK. Önce 03’ün kalan uygulama ve kabul açıklarını tamamla; mevcut çekirdeği yeniden yazma, geçmeyen/çalıştırılmayan testleri geçmiş sayma. Legacy DB/runtime taşıması güvenle desteklenmiyorsa 05/08 dönüşüm bağımlılığını açıkça kaydet. Aşama 01 açık kabul borcunu koru. Testleri kendin çalıştır; bağımsız işlerde paralel agent kullanabilirsin, ağır ürün/DB/Next koşularını sırayla çalıştır. Ayrı AppData test izni var; mevcut veriler/kimlikler/servisler korunacak. Manuel denememi tek tuşla başlatma hazır olduğunda yapacağım. Sonuçları MD dosyalarına kaydet ve yalnız görev dosyalarını ayrı yerel commit’e al; push yapma. Bu oturumda önce 03 kalanlarını bitirip sonucu raporla; 04’e başlamadan yeni kapsamı birlikte belirleyelim.
