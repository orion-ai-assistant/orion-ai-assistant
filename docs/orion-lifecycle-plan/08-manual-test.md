# Aşama 08'i hızlı deneme

17 Ekim 2026. 08 kaynak uygulaması vardır; gerçek hesaba dağıtılmadı, Startup ve otomatik güncelleme açılmadı.

## Hızlı izole testler

Hub repo kökünden:

```powershell
.tmp/stage05-tools/Scripts/python.exe -m pytest scripts/release/test_rollout_lifecycle.py scripts/release/test_release_packages.py -q
```

Redis/Hub modülü isteyen yaşam döngüsü testleri Hub venv ile:

```powershell
$env:PYTHONPATH="$PWD\services\hub\src;$PWD"
services\hub\.venv\Scripts\python.exe -m pytest scripts/lifecycle/test_process_lifecycle.py scripts/lifecycle/test_migration_host.py scripts/lifecycle/test_installation_integration.py -q
```

Router deposundan (`orion-router`):

```powershell
.venv\Scripts\python.exe -m pytest dev/test-orion-router/test_dashboard_build.py dev/test-orion-router/test_local_installation.py dev/test-orion-router/test_native_lifecycle.py -q
```

Router `.venv` içinde pytest yoksa araç ortamının `Lib\site-packages` yolunu `PYTHONPATH`'e ekle.

Flutter `main_app/app` içinden:

```powershell
flutter test --no-pub test/updates_card_test.dart
```

## Kurulum, açılış ve ayarlar

Yeni anahtarlar Ayarlar > Orion güncellemeleri kartındadır: Router otomatik güncellemeleri, Windows açılışında başlat, açılışta arayüzü göster. Hepsi PC oturumundan değişir; telefon yalnız okur. Tercihler kayıt/onayla yazılır, kurulum kendiliğinden Startup açmaz.

## Legacy dönüşüm ve kaldırma (elle, plan önce)

```powershell
python scripts/release/manage_installation.py --registry <registry.json> plan-uninstall
python scripts/release/manage_installation.py --registry <registry.json> uninstall --expected-revision <plandaki revizyon>
```

Kaldırma veri, model ve kurtarma dosyalarını korur. Eski kod temizliği ayrıdır: `plan-cleanup` sonra `purge-code --package-id ... --expected-revision ...`. Legacy dönüşüm için `plan-conversion --destination <yol>` ve ardından `convert`. Gerçek kurulumunda önce yedek al.

## Hâlâ açık kabuller

Temiz Windows VM, gerçek legacy dönüşüm, yan yana kurulum, sonraki sürüme yükseltme, VM zorla kapatma, Windows logon, uzak istemcili aktif kullanım ve beta/stable yayın. Stable yayın bu kanıtlar olmadan `publish_metadata.py` tarafından reddedilir.
