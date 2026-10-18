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
5. **Güncelleme ekranları (bu aşamada eklendi):** "Şimdi güncelle"ye basınca Orion penceresi **kapanmalı**, küçük bir "Orion güncelleniyor, lütfen bekleyin (birkaç dakika sürebilir)." penceresi çıkmalı (adım metni: Servisler kapatılıyor… → Veriler yedekleniyor… → Yeni sürüm yerleştiriliyor… → Yeni sürüm denetleniyor… → Orion yeniden başlatılıyor…, geçen süre sayacı). Bitince bu pencere kapanıp Orion penceresi **kendiliğinden** açılmalı ve bir iletişim kutusu göstermeli: "Güncellendi: hub 0.1.8, router 0.1.5, client 1.0.5." (başarısızsa "Güncelleme uygulanamadı, önceki sürümle devam ediliyor." + kısa neden). İleti bir kez gösterilir; pencereyi yeniden açınca tekrar çıkmaz.
6. **Kilit yarışı (asıl hata):** güncelleme sürerken (küçük pencere açıkken) tepsi simgesine tıkla ve masaüstü kısayoluna çift tıkla. Beklenen: güncelleme **yine de tamamlanır**; ikinci açılışlar bekler (aynı "güncelleniyor" durumu), araya girip servisleri geri başlatmaz. `updates\apply-now.json` sonunda `phase: completed`, `error: null`. Başarısız olursa `updates\watcher.log` son satırlarında `apply-now` hatası ve `file.py:satır` konumu olmalı (URL/yol/anahtar **yok**).

### 4b. Yenile düğmesi: açık durumlar ve tek işlemde üç bileşen

Önkoşul: akışta Hub, Router ve Client için daha yeni imzalı sürüm var (ör. kurulu Hub 0.1.7 / Router 0.1.4 / Client 1.0.4 iken yayında 0.1.8 / 0.1.5 / 1.0.5).

1. Ayarlar > Orion güncellemeleri > yenile. Sırayla: "Güncellemeler denetleniyor…" (ilerleme çubuğu) → "Yeni sürüm bulundu, indiriliyor… (1/3)" (çubuk dolar) → "Hazır: Şimdi güncelle". Hiçbir adımda kart sessizce eski halinde kalmamalı.
2. "Bekleyen güncelleme" altında **üç** paket (hub, client, router) birlikte görünmeli (tek işlem). "Şimdi güncelle" → tek yeniden başlatma; sonra üçü de yeni sürümde. Eskiden Hub/Client ayrı, Router ayrı turdaydı.
3. Güncel durumda yenile: "Güncel: yeni sürüm yok." Otomatik güncellemeler kapalıyken yenile: "Otomatik güncellemeler kapalı olduğu için denetlenmedi…". Ağı kes ve yenile: kırmızı Türkçe neden ("Güncelleme sunucusuna ulaşılamadı…"), ayrıntı `updates\watcher.log`.
4. Yenile sırasında bekçinin ölmediğini doğrula: `Get-CimInstance Win32_Process -Filter "Name='python.exe'" | ? CommandLine -like '*update_automatic.py*watch*'` yenile bittikten sonra da bir satır döndürmeli (eskiden elle denetim sırasında bekçi sessizce çıkıyordu).

### 4c. Kapatma güvenilirliği ve eski istek

1. Orion açıkken tepsi > "Çık" (ve ayrıca `powershell -File %LOCALAPPDATA%\Orion\bootstrap\Orion.ps1 -Stop`, `-NonInteractive` **olmadan**). Beklenen: Hub, Router, Redis, Postgres, web istemcisi, tepsi simgesi, Orion penceresi ve (birkaç saniye içinde) `update_automatic.py watch` bekçisi kapanır. Doğrula: `Get-Process python,powershell | ? { $_.Path -like '*Orion*' }` boş. Normal Chrome pencerelerin **kapanmamalı**.
2. Kapanamayan bir şey olursa sessiz kalmamalı: "Orion tamamen kapatılamadı; bazı servisler çalışmaya devam ediyor olabilir" iletisi ve çalışan servis adları; `updates\watcher.log` içinde `stop.incomplete`.
3. "Şimdi güncelle" isteği beklerken (Hub boşta bekliyor) "Çık" de, sonra masaüstü kısayoluyla aç. Beklenen: kartta **dönen çubuk yok**, "Devam eden işlerin bitmesi bekleniyor" yok; `updates\apply-now.json` `cancelled`. Kısayola ilk tıklamada pencere açılıp **kapanmamalı**.
4. Router panelinde (`http://127.0.0.1:20128/dashboard`) "Güncellemeleri Denetle": birkaç saniye içinde Türkçe sonuç ("Router güncel…", "Yeni sürüm bulundu…" veya "başka bir güncelleme zaten hazır…"). Önceki başarısız Router denemesi düğmeyi 6 saat engellememeli; Hub kurulumunda bulunan Router sürümü Hub/Client ile **aynı** bekleyen işleme girer.

### 4d. Şimdi güncelle: gerçek bekçi yolu, ilerleme ve yetki düzeyi

1. Temiz kurulum + hazır güncelleme ile "Şimdi güncelle". `updates\watcher.log` içinde `apply-now.stopping` → `apply-now.resume` → `apply-now.finished` görünmeli; `stop.incomplete ... Package is not registered at this path` **olmamalı** (bekçi, bootstrap klasöründen çalıştığı için eskiden ilk `get_env` içe aktarmasında düşüyordu).
2. Kurulumdan hemen sonra (Orion yeni açılmışken) güncelleme uygulanırken "uncoordinated process" hatasıyla `failed` olmamalı; en kötü ihtimalle "Servislerin kapanmasını bekliyor" kalır ve sonra uygulanır.
3. Yenile: kart "Güncellemeler denetleniyor…" → birkaç saniyede "Yeni sürüm bulundu, indiriliyor…" → "(1/2) · 85,3 / 198,0 MB · 1,4 MB/sn" ve **tek** çubuk bayta göre ilerler (yaklaşık 2 sn'de bir değişir), bitince "Hazır: Şimdi güncelle".
4. Güncelleniyor penceresi: adımlar gerçekten ilerler (Servisler kapatılıyor → Veriler yedekleniyor → Yeni sürüm yerleştiriliyor → Yeni sürüm denetleniyor → Orion yeniden başlatılıyor); sonunda sonuç iletisi ("Güncellendi: …" / "Güncelleme uygulanamadı…") pencerede görünür, sonra Orion açılır.
5. Yönetici/normal karışımı: Orion'u yönetici PowerShell'den veya "Yönetici olarak çalıştır" ile başlat (`Orion.exe` / `Orion.ps1`). Beklenen: ileti **yok**; Orion kendini normal haklarla yeniden açar (Görev Yöneticisi > Ayrıntılar > Yükseltilmiş sütunu: python/powershell süreçleri "Hayır"). UAC kapalıysa (her şey yükseltilmiş) 60 sn içinde ikinci denemede "Orion yönetici olarak başlatılamaz" iletisi, döngü yok. Yönetici başlatılmış eski servisler varken normal kısayol: "Orion yönetici olarak çalışıyor… tepsiden Çık, sonra normal açın" iletisi, Onarma penceresi yok.

### 4e. Şimdi güncelle: hız ve gerçek ilerleme çubuğu

Değişiklik: yedek yalnız **değişen** bileşenlerin verisini alır (Hub değişiyorsa hepsi, yani eski davranış); dosyalar paralel kopyalanır/özetlenir (kopya başına tek açma + fsync); değişmeyen bileşenin kodu tam yeniden özetlenmez (boyut+zaman damgası önbelleği; önbellek yoksa tam özet); güncelleme penceresindeki çubuk artık belirlidir.

1. Yalnız Client+Router güncellemesi ile "Şimdi güncelle". Toplam süre eskiden ~2:20 idi; beklenen: belirgin kısa (yedek ~1 dk yerine ~10 sn). Hub verisi için `updates\<işlem>ackup-hub-*` klasörü **oluşmamalı**; `backup-client-*` / `backup-router-*` olmalı. Hub de değişiyorsa `backup-hub-*` da olmalı.
2. Güncelleme penceresi: çubuk dönmez, soldan sağa **ilerler** (Servisler kapatılıyor ~%3 → Veriler yedekleniyor %10-45 dosya sayısıyla → yerleştirme ~%50 → denetleme %55-95 → yeniden başlatma %97) ve geri gitmez.
3. `updates\watcher.log` içinde `update.timing step=... seconds=...` satırları (`verify.start`, `stopped.enter`, `backup.hash-source.*`, `backup.copy.*`, `backup.hash-copy.*`, `switch`, `health.probe`, `health.stop`, `verify.final`); aynı süreler `journal.json` içindeki `timings` alanında. Yavaşlık bildirirken bu satırları ekle.
4. Gevşetilen değişmezler: (a) yedek kopyası kopyadan hemen sonra tam özetle kanıtlanır, kesintisiz akışta `switching` öncesi ayrı bir yedek doğrulaması yoktur (kaynak 1, kopya 1 özet; karşılaştırılır); yalnız kesintiden (çökme/yeniden başlatma) sonra devam edilirse yedek tam özetle yeniden kanıtlanır. (b) Kaynak veri için kopya sonrası ikinci/üçüncü özet yerine boyut+mtime karşılaştırması (servisler durmuş ve kilitli). (c) Değişmeyen bileşenin kodu `quick` doğrulanır; değişen bileşenler ve geri alma her zaman tam özetlenir. Geri alma veriyi zaten yedekten geri yüklemez.

### 5. Hub/Router paneli düğmeleri

Ayarlar'da Hub kartında "Hub yönetimini aç", Router bağlıyken Router kartında "Router yönetimini aç" görünmeli; tıklayınca adres çubuksuz uygulama penceresinde açılır. Telefon oturumunda bu düğmeler görünmez.

### 6. Güncelleme sürerken ikinci açılış (Onarma penceresi çıkmamalı)

Bir güncelleme açılışta uygulanırken (veya "Şimdi güncelle" sırasında) masaüstü kısayoluna tekrar tıkla. Beklenen: kırmızı "Orion - Onarma / Repair" penceresi **değil**, "Orion güncelleniyor, birkaç dakika sürebilir" bekleme penceresi; güncelleme bitince Orion kendiliğinden açılır. Güncelleme geri alınırsa "Güncelleme uygulanamadı; Orion önceki sürümle açılıyor" iletisi ve eski sürümle açılış. Onarma penceresi yalnız gerçek `repair_required`/bozuk günlük durumunda çıkar (30 dk bekleme sınırı sonrası da eski davranış).

### 7. Güncelleme sonrası eski arayüz kalmamalı

Client yeni sürüme güncellendikten sonra uygulama penceresinde (eski sürümdeki) kaldırılmış anahtarlar (ör. "Router otomatik güncellemeleri") **görünmemeli**. Kontrol: Chrome'da `chrome://serviceworker-internals` veya DevTools > Application > Service Workers: Orion adresi için etkin worker olmamalı. Paket artık `--pwa-strategy=none` ile derlenir (service worker yok); eski kurulumlardaki kayıtlı worker, sunucunun kendini silen worker'ı sunmasıyla ilk açılışta kaldırılır. "Şimdi güncelle" sonrası sayfa önbelleksiz yenilenir.

## İki feed, tek başına Router, bileşen ekleme, kaldırma (19 Ekim 2026 ekleri)

Yeni otomatik testler (Hub repo kökünden, `.tmp/stage05-tools`; işletim sistemi ACL'i yüzünden gerekirse `LOCALAPPDATA`'yı özel bir klasöre yönlendir):

```powershell
.tmp/stage05-tools/Scripts/python.exe -m pytest scripts/release/test_multi_feed.py scripts/release/test_add_components.py scripts/release/test_user_uninstall.py scripts/release/test_publish_feeds.py scripts/release/test_automatic_updates.py scripts/release/test_bootstrap_update.py scripts/lifecycle/test_product_identity.py -q
```

Gerçek makine denemeleri (**temiz hesap/VM**; çalışan kurulumunun üzerinde deneme). Önce yayın sırası: **Router feed'i, sonra Hub feed'i** (`publish_release.py --feed router`, ardından `--feed hub`; bootstrap `bootstrap.json` sequence 5).

1. **Router tek başına.** Normal PowerShell: `irm https://raw.githubusercontent.com/orion-ai-assistant/orion-router/main/get-orion-router.ps1 | iex`. Beklenen: yalnız `%LOCALAPPDATA%\Orion\Router`; masaüstünde **Orion Router** kısayolu (simge Orion); Görev Yöneticisi > Başlangıç/Ayrıntılar'da açıklama "Orion Router"; Router paneli açılır, normal yetkiyle çalışır (yönetici pencereden başlatırsan kısayol üzerinden de-elevate açılır); en fazla **bir** UAC (güvenlik duvarı, reddedebilirsin) ve kural yalnız Router TLS portu (+ UDP 5353) içerir. Ayarlar > Uygulamalar'da "Orion Router" girdisi var. Router panelinde **Güncellemeleri denetle** Router feed'inden çalışır (Hub yok).
2. **Router kurulu, sonra Hub.** Aynı hesapta `irm .../orion-ai-assistant/main/get-orion.ps1 | iex`. Beklenen: "Mevcut Orion kurulumu bulundu", Hub + web istemcisi eklenir, **Router'a dokunulmaz** (veri/kimlik/sürümleri aynı; `Router\data` ve `releases` değişmedi, `install.json`'da router `component_id` aynı); kısayol/Startup/Uygulamalar girdisi "Orion AI Assistant"a döner, eski "Orion Router.lnk" kalmaz; Orion otomatik başlatılmaz, masaüstü kısayolundan aç. Aynı komutu ikinci kez çalıştırınca hiçbir şey değişmez (zaten kurulu mesajı).
3. **Hub kurulu, sonra Router.** `get-orion-router.ps1`: Router eklenir, Hub/Client dokunulmaz. (Hub'ı `-NoRouter` ile kurup bunu dene.)
4. **Çakışma/geri alma.** Ekleme sırasında (ör. Hub kurulumu `local_db_setup` aşamasında) işlemi öldür veya `Hub` klasörünü kilitle: sonraki ekleme/çalıştırma `updates\add-journal.json` ile registry'yi eski haline döndürür, yalnız bu işlemin yarattığı klasörü siler; mevcut bileşenler ve veri bozulmaz. Bekleyen güncelleme varken ekleme Türkçe mesajla reddedilir.
5. **Feed'ler arası güncelleme.** Hub+Router kuruluyken iki feed'e de yeni sürüm yayınla: Ayarlar > Orion güncellemeleri > **Yenile** her iki feed'i kontrol eder, **tek** hazır güncelleme ve **tek** yeniden başlatma ile Hub, Client ve Router'ı birlikte günceller. Router feed'ine erişimi kes (hosts/çevrimdışı): Hub/Client güncellemesi yine hazırlanır, `GET /api/v1/updates`'te `feed_errors` görünür. Router panelinde "Şimdi güncelle" de aynı işlemi kullanır.
6. **Kaldırma.** Ayarlar > Uygulamalar > Orion AI Assistant > Kaldır (veya tepsi menüsü "Orion'u kaldır"). Seçenekler: yalnız Hub / yalnız Router / her şey; "Verilerimi koru" varsayılan **açık**, modeller yalnız "her şey"de sorulur. Beklenen: Orion kapanır, masaüstü ve Startup kısayolları gider, güvenlik duvarı için en fazla bir UAC (reddedersen kaldırma yine biter, mesaj kural kaldığını söyler; "yalnız Hub"da kural Router portuna daralır), tepsi/watcher kalmaz, Uygulamalar girdisi son bileşenle birlikte gider. Veri korunduysa `Hub\data` / `Router\data` / `Models` yerinde kalır; sonra yeniden kurunca kurulum aynı Hub sırlarıyla/sohbetlerle devam eder. Geliştirici kayıtların (`hub-installation.json`, `router-installation.json`) ve `lifecycle-tests` klasörü değişmemelidir.
7. **Başlangıç adı hatası.** Kurulum/güncelleme/kaldırma sonrası: `Get-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\StartupFolder'` içinde `Orion*.lnk` değeri yalnızca mevcut bir bağlantıya ait olmalı (eski `Orion.lnk` kalıntısı silinir). Görev Yöneticisi > Başlangıç yalnız "Orion AI Assistant" (Router-only'de "Orion Router") göstermeli, bir süre sonra "Orion.exe" olmamalı. (Gözlem: ad, hedef exe'nin dosya açıklamasından gelir; kalıntı StartupApproved değeri hayalet satır yapıyordu.)

## Güncelleme kartı ve açılışta hazır güncelleme (18 Ekim 2026 ek)

Otomatik: `scripts/release/test_automatic_updates.py -k "launch or retried"`, `scripts/release/test_helper_settle.py`, `services/hub/tests/test_update_routes.py -k activity`, Flutter `updates_card_test.dart`.

1. **Kart doğru durumu gösterir.** Yenile'ye bas; kontrolü hangi işlem başlatırsa başlatsın (Hub'ın `check --now` süreci ya da bekçi) kart `Yeni sürüm bulundu, indiriliyor… (1/3) · x / y MB` göstermeli; indirme sürerken asla "Güncellemeler denetleniyor…" ve belirsiz çubuk kalmamalı. Hazır olunca "Hazır: Şimdi güncelle". `GET /api/v1/updates` yanıtında `activity` (`checking/found/downloading/ready/idle`) `updates/automatic.json` ve journal ile uyumlu olmalı.
2. **Bootstrap kendini yenilerken kontrol.** Yeni bootstrap'lı bir yayın varken Yenile'ye bas: `updates/watcher.log`'da `check.retry` görünebilir, kart hata ("CheckProcessFailed/PackageError") yerine bulundu/indiriliyor'a geçmeli.
3. **Hazır güncellemeyle açılış.** Güncelleme hazırken (`staged`/`waiting_for_idle`): (a) Orion çalışırken ikinci kez aç. Bu durumda "Orion güncelleniyor" penceresi çıkmamalı, açılış gecikmemeli (Hub saniyeler içinde hazır, kartta "Hub bağlantısı yok" kalmaz); `watcher.log`'da `startup.apply-skipped ... busy=<exe>:<pid>` olmalı, `update.timing verify.start` olmamalı; hazır güncelleme durmalı. (b) Tepsiden Çık'tan hemen sonra aç: açılış önce çıkışın bitmesini bekler (birkaç saniye, `updates/quitting.json` o sırada vardır), sonra (c) gibi davranır; `startup.apply-skipped` artık çıkmamalı. (c) Orion tamamen kapalıyken (bir dakika sonra) aç: pencere çıkar ve güncelleme uygulanır (`startup.apply`, `startup.applied`). Çıkışı sürerken geride kalan işlem `busy=` ile adlanır; kalıyorsa bu satırı bildir.
4. **"Şimdi güncelle" değişmedi.** Kartta Şimdi güncelle: önceki gibi bekçi üzerinden uygulanır (`apply-now.*` satırları).

## Kesinti senaryoları: elektrik kesintisi, pencereyi kapatma, Görev Yöneticisi (18 Ekim 2026 ek)

Otomatik kanıt (gerçek paketlerle, isteğe bağlı, her yayında değil; birkaç dakika): eski kabul edilmiş paket setini sandbox'a kurar, yenisini hazırlar, güncellemeyi indirme / yedekleme / geçiş / sağlık denetimi sırasında **tüm işlem ağacını öldürerek** keser, Orion'u normal girişten yeniden açar ve sonucu denetler (tamamlandı ya da eski sürüm, `repair_required` yok, güncellemeden önce yazılan PostgreSQL satırı yerinde, Çık birkaç saniye):

```powershell
.tmp/stage05-tools/Scripts/python.exe scripts/release/drill_interruptions.py --previous-artifacts <eski set> --update-artifacts <yeni set> --inputs .tmp/inputs --scratch-base $HOME\.orion-lifecycle-tests
```

Birim testleri: `scripts/release/test_update_visibility.py -k cut_off`, `test_stop_reliability.py`, `test_version_skew.py`, `test_postgres_recovery.py`.

Drill'in bulduğu ve düzeltilen üç hata (üçüncüsü gerçek PC'de görülen `rolled_back reason=PermissionError`; drill artık journal'ı güncelleme penceresi gibi okuyarak ve `clean` senaryosuyla bunu yeniden üretiyor: Windows, journal okunurken onun yerine yazmayı reddediyordu, yazma artık 10 s'ye kadar yeniden dener): (1) geçiş/sağlık denetimi sırasında kesilen güncellemeden sonra açılış, artık var olmayan uygulayıcıyı 30 dakika bekliyor, sonra Onarma penceresi açıyordu; (2) birlikte öldürülen PostgreSQL'in ilk açılışı (çökme kurtarma) Windows'ta `server.log` paylaşım ihlali yüzünden 30 s takılıyor, güncelleme `repair_required` ile bitiyordu. Düzeltmeler bekleyen bootstrap'ta (kurtarma, PostgreSQL toparlama) ve Hub'da (log veri klasörünün dışında, Çık hızı); kullanıcıya ulaşması için yeni bootstrap + Hub yayını gerekir (sürüm artışları yayın sahibinde).

Kendi bilgisayarında (önce önemli bir şey yazmadığın bir anda; verilerin korunur ama deneme sırasında Orion birkaç dakika kullanılamaz). Her denemeden önce kartta **Hazır: Şimdi güncelle** görünmeli:

1. **Güncelleme penceresini kapatma.** "Şimdi güncelle"ye bas; "Orion güncelleniyor" penceresi çıkınca Görev Yöneticisi > Ayrıntılar'da `python.exe` ve `postgres.exe` işlemlerini (Orion klasöründen çalışanları) **Görevi sonlandır** ile kapat. Sonra masaüstü kısayolundan Orion'u aç. Beklenen: Onarma penceresi **çıkmaz**; birkaç saniye "Orion güncelleniyor" görünür ve ya yeni sürümle açılır ya da "Yeni sürüm çalışmadı; önceki sürüme dönüldü" der. 30 dakika bekleme olmaz. `updates/watcher.log`'da `startup.recover` ve `startup.recovered outcome=completed|rolled_back` satırları.
2. **Elektrik kesintisi.** Aynısını "Şimdi güncelle"den birkaç saniye sonra bilgisayarı güç düğmesine basılı tutarak kapatıp dene (ya da sanal makinede "Power off"). Açılışta Orion Windows ile başlıyorsa kendiliğinden, değilse kısayoldan aç: 1. adımdaki gibi. Sohbetlerin, ayarların ve Router bağlantısı yerinde olmalı.
3. **İndirme sırasında.** Yenile'ye bas; kart "indiriliyor" derken tepsiden **Çık**. Yeniden aç: Orion eski sürümle hemen açılır; kartta güncelleme bir sonraki denetimde yeniden indirilir (yarım indirme engel olmaz).
4. **Çık hızı.** Orion açıkken tepsiden **Çık**: tepsi simgesi ve uygulama penceresi birkaç saniyede kaybolur; Görev Yöneticisi'nde Orion klasöründen çalışan `python.exe`/`postgres.exe` en geç ~10 saniyede kalmamalı (eskiden 20 s'yi aşıyordu). Çık'tan hemen sonra açınca Orion bekler, sonra normal açılır.
5. **Sürüm uyumsuzluğu.** `bootstrap
elease-source.json` her zaman tek feed'li eski biçimde (schema 1) kalır; tüm feed'ler `bootstrap
elease-sources.json`'dadır. Bu dosyalardan biri okunamazsa kart kaybolmaz, "Güncelleme kaynağı tanınmadı" der; sürümler ve geçmiş yine görünür.
