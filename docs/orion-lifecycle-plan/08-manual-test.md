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

## Gerçek makine kabulü (elle, 18 Ekim 2026 eklemeleri)

Aşağıdakiler otomatik testlerde yalnız sahte komut çalıştırıcı/sahte süreçlerle denendi. Gerçek UAC, Windows güvenlik duvarı ve tepsi simgesi **senin makinende** doğrulanmalı. Çalışan kurulumunun üzerine gerçek kurulum yapma; temiz bir hesap/VM ya da `get-orion.ps1` ile **ayrı bir kullanıcı** kullan. Mevcut kurulumunu denemek için yalnız 3–5. bölümler güvenlidir.

### 1. Güvenlik duvarı kuralı (`install_new.py`, `services/shared/windows_firewall.py`)

Yalnız temiz makine/VM'de (Orion kurulu değilken):

1. Yönetici olmayan PowerShell'de `irm https://raw.githubusercontent.com/orion-ai-assistant/orion-ai-assistant/main/get-orion.ps1 | iex` çalıştır.
2. Kurulum sırasında **bir** UAC penceresi çıkmalı (kural ekleme). Hayır dersen kurulum **bitmeli**; yalnızca ilk açılışta Windows güvenlik duvarı sorusu çıkabilir. Not et: UAC çıktı mı, kaç kez?
3. Kurulum bitince (yönetici PowerShell):
   ```powershell
   Get-NetFirewallRule -DisplayName 'Orion*' | Format-List DisplayName,Enabled,Direction,Action,Profile
   Get-NetFirewallRule -DisplayName 'Orion*' | Get-NetFirewallPortFilter | Format-List Protocol,LocalPort
   ```
   Beklenen: iki kural — `Orion private network (TCP)` ve `Orion private network discovery (UDP)` (UDP 5353) — Inbound, Allow, yalnız **Private** profil, yalnız yerel alt ağ. Kurallar port tabanlıdır (program yolu yok), bu yüzden Hub/Router güncellemesiyle `python.exe` yolu değişse bile yeni güvenlik duvarı sorusu çıkmamalı.
4. Orion'u başlat (masaüstü kısayolu). Beklenen: "Python'a izin ver" tipi Windows güvenlik duvarı penceresi **çıkmaz** (özel ağdayken). Çıkarsa ekran görüntüsü al; hangi `python.exe` yolunun ve hangi portun sorulduğunu not et (kural TCP portunu kapsamıyorsa buradan anlaşılır). Güncelleme sonrası (yeni `releases\...` yolu) soru tekrar çıkmamalı: bunu bir güncellemeden sonra da dene.
5. Telefondan aynı Wi-Fi'de Hub'a bağlan: çalışmalı. Public olarak işaretli bir ağda bağlantı **kurulmamalı**.
6. Kaldırma: kuralı elle sil (`Remove-NetFirewallRule -DisplayName 'Orion*'`) ve yeniden dene; kurulum aracı kuralı tekrar eklemeyi teklif etmeli (UAC).

### 2. Kurulum sonrası temizlik denetimi

`Get-Process python,powershell | ? { $_.Path -like '*Orion*' }` — Orion kapalıyken boş olmalı (3. bölümdeki "Çık" sonrası dahil).

### 3. Tepsi simgesi (yalnız tam Hub kurulumu)

1. Orion'u başlat: saat yanında Orion simgesi (gizli simgeler okunda olabilir).
2. Pencereyi kapat: simge ve servisler yerinde kalmalı; simgeye **sol tık** pencereyi geri açmalı (yeni pencere değil, mevcut).
3. Sağ tık menüsü: "Orion'u aç", "Çık". "Çık": simge **hemen** kaybolmalı, birkaç saniyede Hub/Router/Redis/Postgres kapanmalı, pencere kapanmalı. Sonra yukarıdaki 2. bölümdeki komutla artık süreç kalmadığını doğrula; `bootstrap\tray.exit` dosyası kalmamalı.
4. Görev Yöneticisi'nden tepsi PowerShell'ini (komut satırında `Orion-Tray.ps1`) öldür, Orion'u yeniden başlat: tek simge çıkmalı (ikinci başlatma kendiliğinden çıkar). Router-only kurulumda simge **olmamalı**.

### 3b. Ürün kimliği: Orion.exe (Başlangıç uygulamaları adı + simgesi)

Bootstrap 0.1.1 (sequence 2) ve sonrası güncellemeden sonra, Orion bir kez açıldığında:

1. Masaüstündeki Orion kısayolu **Orion simgesi** taşımalı (PowerShell simgesi değil); Özellikler'de hedef `...\Orionootstrap\Orion.exe`, açıklama "Orion AI Assistant".
2. Görev Yöneticisi > Başlangıç uygulamaları: "Windows açılışında Orion'u başlat" **açıksa** satırın adı "Orion AI Assistant", yayıncı "Orion", Orion simgesiyle görünmeli (artık `powershell.exe` yok). `shell:startup` klasöründe yalnız `Orion AI Assistant.lnk` olmalı. Ayar **kapalıysa** hiçbir Orion girdisi olmamalı (eski `Orion.lnk` de silinmiş olmalı); ayarı açıp kapatınca girdi gelip gitmeli.
3. Masaüstü kısayoluna çift tıkla: konsol/PowerShell penceresi **çakılmadan** Orion açılmalı. Tepsi simgesi artık Orion simgesi. Tepsi > Çık (veya `Orion.exe -Stop`) hâlâ her şeyi kapatmalı.
4. Masaüstü kısayolunu sil, Orion'u yeniden aç: kısayol **yeniden oluşmamalı** (yalnız var olan yenilenir).

### 4. "Şimdi güncelle"

Önkoşul: ayarlarda güncelleme kaynağı hazır ve bir sürüm hazırlanmış (Ayarlar > Orion güncellemeleri'nde "Bekleyen güncelleme: Güvenli açılışı bekliyor"). Yoksa yenile düğmesine bas.

1. Sohbette uzun bir yanıt başlat; aynı anda "Şimdi güncelle". Beklenen: "Devam eden işlerin bitmesi bekleniyor (sohbet/araç işi)…" ve iş **kesilmez**; yanıt bitince birkaç saniye sonra güncelleme başlar. Bekleme sırasında "İptal" çalışmalı; sonra yeni sohbet mesajı normal gitmeli.
2. İş yokken bas: "Güncelleniyor, birkaç dakika sürebilir" → sayfa bağlantı kaybı sırasında bu mesajı korumalı → Hub dönünce sayfa kendiliğinden yenilenmeli ve yeni sürümü göstermeli. Pencere açık kalır, tepsi simgesi geri gelir.
3. Telefondan aynı kartı aç: düğme **görünmemeli** (yalnız PC).
4. Başarısızlık denemesi (isteğe bağlı): `updates\<op>\health.log` ve kartta "Önceki sürümle devam ediliyor" mesajı; Orion önceki sürümle yeniden açılmış olmalı.

### 5. Hub/Router paneli düğmeleri

Ayarlar'da Hub kartında "Hub yönetimini aç", Router bağlıyken Router kartında "Router yönetimini aç" görünmeli; tıklayınca adres çubuksuz uygulama penceresinde açılır. Telefon oturumunda bu düğmeler görünmez.

### 6. Güncelleme sürerken ikinci açılış (Onarma penceresi çıkmamalı)

Bir güncelleme açılışta uygulanırken (veya "Şimdi güncelle" sırasında) masaüstü kısayoluna tekrar tıkla. Beklenen: kırmızı "Orion - Onarma / Repair" penceresi **değil**, "Orion güncelleniyor, birkaç dakika sürebilir" bekleme penceresi; güncelleme bitince Orion kendiliğinden açılır. Güncelleme geri alınırsa "Güncelleme uygulanamadı; Orion önceki sürümle açılıyor" iletisi ve eski sürümle açılış. Onarma penceresi yalnız gerçek `repair_required`/bozuk günlük durumunda çıkar (30 dk bekleme sınırı sonrası da eski davranış).

### 7. Güncelleme sonrası eski arayüz kalmamalı

Client yeni sürüme güncellendikten sonra uygulama penceresinde (eski sürümdeki) kaldırılmış anahtarlar (ör. "Router otomatik güncellemeleri") **görünmemeli**. Kontrol: Chrome'da `chrome://serviceworker-internals` veya DevTools > Application > Service Workers: Orion adresi için etkin worker olmamalı. Paket artık `--pwa-strategy=none` ile derlenir (service worker yok); eski kurulumlardaki kayıtlı worker, sunucunun kendini silen worker'ı sunmasıyla ilk açılışta kaldırılır. "Şimdi güncelle" sonrası sayfa önbelleksiz yenilenir.
