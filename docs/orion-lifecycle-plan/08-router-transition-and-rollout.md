# Aşama 08 — Router geçişi, kaldırma ve kontrollü yayın

Durum: DOĞRULAMA EKSİK. Kaynak kodu ve izole fixture testleri tamamdır; temiz VM, Windows logon/güç kesintisi, gerçek legacy kurulum ve üretim yayını kabulleri açıktır.

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

- Durum: DOĞRULAMA EKSİK. Başlangıç/bitiş: 17 Ekim 2026 (aynı gün; kullanım limiti nedeniyle iki oturuma bölündü).
- Çalışılan depolar ve değişen dosyalar: Hub `orion-ai-assistant` (yeni: `services/shared/legacy_conversion.py`, `package_maintenance.py`, `release_acceptance.py`, `scripts/release/manage_installation.py`, `test_rollout_lifecycle.py`; değişen: `update_helper.py`, `update_health.py`, `update_gate.py`, `automatic_updates.py`, `launcher.py`, `launcher_entry.ps1`, `migration_host.py`, `native_services.py`, `publish_metadata.py`, `build_bootstrap.py`, `install_new.py`, `install.ps1/sh`, `update_routes.py`); Router `orion-router` (yeni `core/update_gate.py`; `core/updater.py`, `bin/update.py`, `bin/prod.py`, `main.py`, `api/admin.py`, `core/lifespan.py`, `install.ps1/sh`, `release/package.json`); Flutter `orion-ai-assistant-flutter` (`updates_card.dart`: Router otomatik güncelleme, Windows ile başlat, arayüzü göster anahtarları).
- Uygulanan arayüzler / şemalar / hata sonuçları: Router Git/pip yerinde güncelleme üretim yolundan çıkarıldı; Router imzalı paket hazırlama kuyruğuna bağlı ve tercihi Hub'dan bağımsız (`router_automatic_updates`). `manage_installation.py` eylemleri: `plan-conversion`, `convert`, `plan-uninstall`, `uninstall`, `plan-cleanup`, `purge-code`, `restore-registration`; her uygulama eylemi plandan gelen `--expected-revision` ister. Legacy dönüşüm yalnız durmuş servislerle, açık sahip onayıyla; orijinal ağaç/veri korunur, kopya kesilirse açık geri alma gerekir, başarısız sağlıkta kayıt ve kaynak koruması geri yüklenir. Dönüştürülmüş kökte eski kurucu/`update` başlatması `.orion-converted.json` ile reddedilir. Kaldırma varsayılan olarak veri/model/bootstrap/kurtarma dosyalarını korur; kod temizliği ayrı `purge-code`; çalışan, son sağlıklı veya kurtarma sürümü silinmez. Yayın: `publish_metadata.py` artık `--channel beta|stable` ve `--acceptance` ister; `release_acceptance.verify_acceptance` rapor, paket SHA-256 bağlama ve kanıt dosyası özetlerini doğrular (stable ek olarak `windows_logon`, `abrupt_shutdown`, `active_use_and_remote_clients` ister; test-only rapor reddedilir). Flutter: `PUT /api/v1/updates` `router_automatic_updates`, `start_with_windows`, `show_client` alanlarını kabul eder.
- Test komutları, ortam ve sonuçlar: `.release/tools/Scripts/python.exe -m pytest scripts/release/test_rollout_lifecycle.py`: **13 geçti** (bağımsız Router imzalı güncelleme ve tercih, başarısız sağlıkta geri alma, dönüşüm koruma/kesinti/reddetme, kaldırma/temizleme veri koruması, aktif/kurtarma sürümü temizlenmez, Router köprüsü yalnız kendi kurulumu, stable kabul reddi). `scripts/release` + `scripts/lifecycle` tam koşusu: araç venv'inde `redis`/`orion` modülü olmadığından 7 test ortam nedeniyle hata verdi; Hub venv (`PYTHONPATH=services/hub/src;<repo>`) ile aynı 3 dosya **58 geçti**. `test_release_packages.py`: **38 geçti** (imzalama CLI testi yeni zorunlu `--channel/--acceptance` için geçerli beta kabul raporu üretecek şekilde güncellendi; kapı gevşetilmedi). Router (`dev/test-orion-router` içinden `test_dashboard_build`, `test_local_installation`, `test_native_lifecycle`): **24 geçti**; değişen Router modülleri `py_compile` temiz. Flutter `updates_card_test`: geçti; `chat_presentation_test.dart` içinde `failed question and response each explain model exclusion` kırık (sohbet/model dışlama; 08 dosyalarına bağlı değil, kullanıcının önceki değişikliği, dokunulmadı).
- Veri / kimlik / hedef dışı süreç korunması kanıtı: testler yalnız geçici registry/fixture ve sentinel veri kullanır; gerçek servis, Startup, otomatik güncelleme, gerçek kullanıcı verisi ve mevcut deneme kurulumu değiştirilmedi. Dönüşüm/kaldırma testleri veri kimliği ve sentinel dosyalarının korunmasını sınar.
- Çalıştırılamayan testler ve nedeni: temiz Windows VM, gerçek legacy Router/Hub/birleşik kurulum dönüşümü, geliştirme ile yan yana kurulum, bir sonraki sürüme gerçek yükseltme, VM zorla kapatma, gerçek Windows logon, uzak istemcili aktif kullanım, beta/stable yayın ve gerçek üretim imza kökü: bu ortamda yok/yetkilendirilmedi.
- Kalan riskler / kapsam dışı durumlar: yukarıdaki kabuller yapılmadan stable yayın kapısı (acceptance) geçilemez; bu bilinçli tasarımdır. Önceki Flutter `router_settings_test.dart` (07 kaydındaki) ve `chat_presentation_test.dart` kırıkları ayrı borç. `build/` (CMake) kullanıcıya ait, göreve alınmadı. Push yapılmadı.
- Geri alma yöntemi: 08 değişikliklerini (Hub, Router, Flutter) ayrı commit'lerle birlikte geri al; kullanıcı verisi, release/state, yedek ve dönüştürülmüş kurulumların `.orion-converted.json` işaretini silme; açık dönüşüm varsa önce `manage_installation.py restore-registration` kullan.
- Plan veya bağımlılık değişikliği: yok; yeni zorunlu yayın parametreleri `scripts/release` kullanımını etkiler.
- Sonraki aşamaya uygunluk: plan bu aşamayla biter; geriye kod değil kabul/yayın doğrulaması kalır. Bkz. [08 deneme rehberi](08-manual-test.md).


## Ek: retained bootstrap kendi kendini güncelliyor (dogfooding bulgusu)

- Sorun: `Bootstrap-Installer` kurulum anında donuyordu; güncelleme makinesinin yeni özellikleri (ör. Ayarlar yenile düğmesinin `check --now` bayrağı) mevcut kurulumlara ulaşamıyordu. Eski bootstrap argparse hatasıyla (kod 2) sessizce çıkıyordu.
- Çözüm: bootstrap paketi imzalı TUF hedefi (`component: bootstrap`, hedef `orion-bootstrap-windows-x64.zip`, kimlik `scripts/release/bootstrap.json`). Yalnız `ReleaseClient` güven yoludur. `services/shared/bootstrap_update.py`: daha yeni `sequence` + aktif güncelleme/kurtarma yok -> yeni kardeş klasörde hazırla -> doğrula (zorunlu dosyalar, imzalı metadata uyumu, kök dosyası kurulu olanla birebir, kendi python.exe'si içe aktarıp `--help` yanıtlar) -> `automatic-runtime.json` işlem kilidi altında atomik yeniden yönlendirilir. Eski watcher devreder; yenisi 60 sn içinde başlamazsa otomatik geri dönülür. Yeni bootstrap bir başarılı kontrol yapana kadar eskisi (silinmeden) kalır; yalnız mekanizmanın oluşturduğu eski klasör silinir.
- Görünürlük: Hub'ın tetiklediği `check --now` sıfır dışı çıkarsa `automatic.json` `error` = `BootstrapIncompatible` (kod 2) / `CheckProcessFailed`; bootstrap kendi hatası `bootstrap_error`; `GET /api/v1/updates` `bootstrap` alanı.
- Sınır: bu özellikten önceki bir bootstrap bunu benimseyemez; bir kez `get-orion.ps1` ile yeni bootstrap kurulmalı. Kök değişimi bu yoldan yapılmaz (kök uyuşmazlığı reddedilir).
- Testler: `scripts/release/test_bootstrap_update.py` (yeni benimsenir, eski/aynı sıra reddedilir, kurcalanmış paket reddedilir, aktif işlemde reddedilir, başlamayan yeni bootstrap geri döner, kök uyuşmazlığı reddedilir, yayın kapısı); Hub `test_update_routes.py`. Gerçek bir yayın/yeni bootstrap ile uçtan uca denenmedi.
