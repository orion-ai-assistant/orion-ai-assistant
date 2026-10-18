# Orion yayın kılavuzu: imzalama, derleme, kabul testi, yayın, kurulum

Bu belge, Orion'un Windows paketlerini imzalayıp GitHub'a yayınlamak isteyen geliştiriciler ve agent'lar
içindir. Ayrıntılı teknik başvuru: [scripts/release/README.md](../scripts/release/README.md).

## 1. Genel resim

| Bileşen | Kaynak | Sürüm dosyası | Yayınlandığı feed |
| --- | --- | --- | --- |
| Hub | `orion-ai-assistant` | `scripts/release/hub.json` | Hub feed: `orion-ai-assistant/orion-ai-assistant`, tag `feed` |
| Client (Flutter web) | `orion-ai-assistant-flutter` | `release/package.json` | Hub feed |
| Router | `orion-router` | `release/package.json` | Router feed: `orion-ai-assistant/orion-router`, tag `feed` |
| Bootstrap (kurucu/güncelleyici) | `orion-ai-assistant` | `scripts/release/bootstrap.json` | İki feed'de de |

Üç depo yan yana durmalı: `DevProjects/orion-ai-assistant`, `DevProjects/orion-router`,
`DevProjects/orion-ai-assistant-flutter`. Yayın komutları Hub deposunun kökünden çalışır.

- **İmza:** [TUF](https://theupdateframework.io/) metadata, tek bir Ed25519 anahtar (eşik 1). Aynı anahtar
  root, targets, snapshot ve timestamp'i imzalar; iki feed de aynı `trusted-root.json`'u kullanır.
- **"Yeni mi?" kararı:** İmzalı `sequence` sayısıyla verilir. `version` (ör. `0.2.5`) yalnızca etikettir.
- **Feed biçimi:** GitHub Release asset'leri:
  - `N.root.json`, `N.targets.json`, `N.snapshot.json`, `timestamp.json`
  - Hash önekli paketler: `<sha256>.orion-router-code-windows-x86_64-cpu.zip`, `<sha256>.orion-router-runtime-<hash>.zip`, ...
  - Düz `orion-bootstrap-windows-x64.zip`: kurulum betiğinin indirdiği kopya.
  - `timestamp.json` en son yüklenir; yeni sürüm ancak o yüklenince görünür.
- **Bölünmüş paketler:** Hub ve Router iki parça yayınlanır:
  - **Kod paketi** (birkaç MB).
  - **Runtime katmanı** (Python + kütüphaneler + PostgreSQL vb., 120-170 MB). İçerik adresli, yani içerik
    değişmedikçe ne yeniden derlenir ne yüklenir ne indirilir. Yeni bir Python kütüphanesi eklenirse
    katmanın hash'i değişir ve otomatik olarak yeni katman çıkar.

## 2. Bir kerelik kurulum (yeni geliştirici PC'si)

PowerShell, Hub deposunun kökünde:

```powershell
# Yayın araçlarının Python ortamı
uv venv .tmp/stage05-tools --python 3.11
uv pip install --python .tmp/stage05-tools/Scripts/python.exe --require-hashes -r scripts/release/requirements.txt
uv pip install --python .tmp/stage05-tools/Scripts/python.exe pytest

# Sabitlenmiş girdiler (Python, PostgreSQL, Redis, FFmpeg arşivleri; hash'leri inputs.json'da)
.tmp/stage05-tools/Scripts/python.exe scripts/release/download_inputs.py .tmp/inputs

# GitHub CLI oturumu (yükleme için)
gh auth login
```

Ayrıca gerekenler:
- Flutter SDK (`flutter build web`).
- Node.js / npm (Router paneli, Next.js).
- İsteğe bağlı: `scripts/dev/setup-defender-exclusions.ps1`. Yönetici PowerShell'de, yalnızca geliştirme
  PC'sinde çalıştırılır; derleme ve kabul testini hızlandırır.

### İmza anahtarı

Anahtar **zaten varsa** yeniden oluşturma. Yeni anahtar, mevcut tüm kurulumların yeni sürümlere
güvenmemesi demektir.

```powershell
# Yalnızca projenin ilk kurulumunda bir kez:
.tmp/stage05-tools/Scripts/python.exe scripts/release/release_keys.py init
# Parolanın doğru olduğunu denemek için (hiçbir şeyi değiştirmez):
.tmp/stage05-tools/Scripts/python.exe scripts/release/release_keys.py check
```

`init` iki dosya üretir:
- `~/.orion-release/release-key.pem`: parolayla şifrelenmiş özel anahtar. **Git'e asla girmez.**
  **Yedekle:** kaybolursa kurulu Orion'lar yeni sürümlere güvenemez.
- `scripts/release/trusted-root.json`: açık kök. Commit edilir ve kurucuyla dağıtılır.

Anahtarı başka bir PC'ye taşımak için `release-key.pem` dosyasını güvenli bir yolla aynı konuma kopyala.
Parolayı aynı kişi girer.

Parola kuralları:
- `release.cmd` parolayı bir kez, en başta sorar; yanlışsa derlemeye başlamadan durur.
- Ortam değişkeni `ORION_RELEASE_KEY_PASSPHRASE` yalnızca yayın betiklerinin kendi alt süreçlerine
  aktarılır. Kalıcı olarak ayarlama.
- **Agent'lar için:** Parolayı asla sen girme, sorma, kaydetme, loglama. Kullanıcı `release.cmd`'yi kendi
  terminalinde çalıştırır.

### Metadata ömrü

Tek anahtarlı metadata bir yıl geçerlidir. Kod değişmese de yılda en az bir kez yayın yapılmalıdır.

## 3. Yayından önce: sürüm numaralarını yükselt

Değişen her bileşenin sürüm dosyasında `version`, `sequence` ve `package_id` alanlarını birlikte yükselt:

```json
"package_id": "orion-router-0.2.5-windows-x86_64-cpu",
"version": "0.2.5",
"sequence": 21,
```

Kurallar:
- **`sequence` sadece artar.** Aynı `sequence` ile **farklı baytlar** asla yayınlanamaz: `package_gate`
  reddeder. Kodu değişen bir bileşenin numarası mutlaka yükseltilmelidir.
- **Değişmeyen bileşenin numarasına dokunma.** Paketler bayt bayt tekrarlanabilir (aynı commit + aynı girdiler
  = aynı sha256), bu yüzden değişmeyen bileşen için hiçbir şey yüklenmez.
- **Bootstrap:** `scripts/release/bootstrap.json`. Yalnızca kurucu/güncelleyici kodu (`services/shared`,
  `installer`, `scripts/release` içindeki paylaşılan betikler) davranış olarak değiştiyse yükselt.
  `sequence` aynı kalırsa en son hazırlanan bootstrap aynen yeniden kullanılır ve yüklenmez.
- **Sürüm biçimi:** `0.x.y` kalabilir. Paketli kurulumlarda büyük sürüm (major) kuralı yoktur; uyumluluğu
  manifest'teki `api` ve `requires` alanları belirler.
- **Test işareti (isteğe bağlı):** Güncellemenin gerçekten geldiğini gözle görmek için harfi değiştir:
  - Router paneli: `orion-router/dashboard/components/DashboardLayout.tsx` ("Güncelleme test işareti: X").
  - Flutter: `main_app/app/lib/features/settings/settings_page.dart`.

  Not: Router panelini değiştirmek Next.js derlemesini tetikler (~1,5-2 dk).

Sürüm yükseltmelerini kendi depolarında commit et. Derleme, commit edilmiş/izlenen dosyalardan yapılır;
yeni bir kaynak dosyası en azından `git add` ile izlenmeli.

## 4. Yayın komutları

Hepsi Hub deposunun kökünden:

| Komut | Ne yapar | Ne zaman |
| --- | --- | --- |
| `.\release.cmd` | Derle → **tam kabul testi** → imzala → iki feed'i yayınla | Kullanıcıların alacağı her yayın (varsayılan) |
| `.\release.cmd --no-accept` | Derle → imzala → yayınla, **kabul testi yok** (`acceptance_skipped: true` ile imzalanır) | Yalnızca hızlı geliştirici denemeleri |
| `.\release.cmd --feeds router` | Sadece Router feed'ini yayınlar; Hub feed'ine dokunmaz | Router-only güncelleme |
| `.\release.cmd --feeds hub` | Sadece Hub/Client feed'ini yayınlar | Hub/Client-only güncelleme |
| `.\release.cmd --no-publish` | Derle + kabul testi, hiçbir şey yüklemez, parola sormaz | Yayından önce doğrulama |
| `.\release.cmd --dry-run` | Her şeyi hazırlar ve imzalar ama yüklemez | İmza/hazırlık denemesi |
| `.\release.cmd --force-accept` | Aynı paket seti daha önce geçmiş olsa da kabul testini yeniden çalıştırır | Şüphe varsa |

Seçenekler birleşebilir, ör. `.\release.cmd --feeds router --no-accept`.

`release.cmd` şunu çalıştırır: `.tmp\stage05-tools\Scripts\python.exe scripts\release\release.py`.

Adım adım ne olur:
1. **Parola** sorulur ve anahtar açılır.
2. **`build_release.py`:** Önbellekli ve paralel derleme. Flutter web, Router paneli ve üç paket
   (`.tmp/candidates/<set>`). Kaynağı değişmeyen adım atlanır ("skipped").
3. **`accept_packages.py --mode full`:** Paketleri izole bir klasörde
   (`~/.orion-lifecycle-tests/stage05-*`) gerçekten kurar ve şunları dener:
   - TUF doğrulama ve güvenli açma
   - Hub `initdb`
   - Router'ın tek başına çalışması
   - Flutter sağlık kontrolü
   - Veritabanı kaydının yeniden başlatmadan sonra kalması
   - Dosyaların imzalı ZIP ile aynı olması

   Aynı set daha önce geçtiyse sonuç yeniden kullanılır. Bilgisayar boşsa kabul testi Flutter derlenirken
   başlar (`--overlap-acceptance auto|on|off`).
4. **`publish_release.py`:** Önce `--feed router`, sonra `--feed hub`. Her feed için metadata imzalanır ve
   eksik asset'ler yüklenir. İki feed'in `timestamp.json`'u en sonda arka arkaya yüklenir, böylece iki feed
   aynı anda görünür hale gelir.

Ara dosyalar:
- `.tmp/build-cache`: önbellek, silinebilir.
- `.tmp/candidates`: derlenen paket setleri.
- `.tmp/release/<feed>-v<N>`: hazırlanan feed sürümü.

Tipik süreler (sahibin PC'si):

| Adım | Süre |
| --- | --- |
| Hiçbir şey değişmediyse | ~10 sn |
| Flutter | ~65-75 sn |
| Router paneli | ~1,5-2 dk |
| Kod paketi | birkaç sn |
| Yeni runtime katmanı | 35-80 sn, yalnızca kütüphane/girdi değişince |
| Tam kabul testi | ~85-95 sn |
| Yükleme | GitHub'a ~1,3 MB/sn: kod paketi saniyeler, runtime katmanı (yalnızca değiştiyse) 2+ dk |

### Kabul testi: ne zaman atlanabilir?

`--no-accept` ile yayınlanan paket imzalıdır ama kurulup çalıştığı **kanıtlanmamıştır**. Bunu yalnızca kendi
PC'nde hızlı deneme için kullan. Kullanıcıların güvendiği her yayın tam kabul testinden geçmeli. Ayrı bir
ön kontrol modu (`build_release.py --accept fast`) vardır, ama sonucu yayınlanamaz.

## 5. Yayından sonra: doğrulama

```powershell
# Feed'de son yüklemeler (timestamp.json en son ve iki feed'de neredeyse aynı anda olmalı)
gh release view feed --repo orion-ai-assistant/orion-router --json assets --jq '.assets[] | "\(.updatedAt) \(.name)"'
gh release view feed --repo orion-ai-assistant/orion-ai-assistant --json assets --jq '.assets[] | "\(.updatedAt) \(.name)"'
```

Kurulu bir PC'de:
- **Güncelleme günlüğü:** `%LOCALAPPDATA%\Orion\updates\watcher.log`. Örnek satırlar:
  `check.start`, `check.update-found`, `check.staged`, `apply-now.*`, `update.timing`, `update.admitted`.
  Hata satırı `error where=... type=...` biçimindedir.
- **Son denetim sonucu:** `%LOCALAPPDATA%\Orion\release-cache*\check.json`.
- **Arayüz:** Ayarlar > "Orion güncellemeleri" kartı sürümleri gösterir (hub, client, router, güncelleyici).
  Router panelindeki "Denetle" düğmesi gerçek bir denetim başlatır.

## 6. Kurulum (sıfırdan)

Hub + web istemcisi + Router (normal kurulum). **cmd**:

```bat
curl -L -o get-orion.cmd https://raw.githubusercontent.com/orion-ai-assistant/orion-ai-assistant/main/get-orion.cmd && get-orion.cmd
```

**PowerShell**:

```powershell
irm https://raw.githubusercontent.com/orion-ai-assistant/orion-ai-assistant/main/get-orion.ps1 | iex
```

Seçenekler: `-NoRouter`, `-WithRouter` (mevcut kuruluma Router ekler), `-NoStart`, `-NoStartWithWindows`,
`-NoAutoUpdate`, `-NoFirewall`. Örnek: `get-orion.cmd -NoStartWithWindows`.

Yalnızca Router:

```bat
curl -L -o get-orion-router.cmd https://raw.githubusercontent.com/orion-ai-assistant/orion-router/main/get-orion-router.cmd && get-orion-router.cmd
```

Kurulum `%LOCALAPPDATA%\Orion` altına, kullanıcı başına yapılır (yönetici gerekmez; güvenlik duvarı kuralı
için bir kez UAC çıkar). Kurulu bir sisteme aynı komut eksik bileşeni **ekler**, gerisine dokunmaz.

Kaldırma iki yoldan yapılır:
- Ayarlar > Uygulamalar > "Orion AI Assistant" > Kaldır.
- Tepsi simgesi > "Orion'u kaldır".

Seçenekler: Hub'ı, Router'ı ya da hepsini kaldırmak; verileri ve modelleri korumak ya da silmek. Korunan
veriler bir sonraki kurulumda aynen benimsenir. Kayıt dosyası: `%LOCALAPPDATA%\Orion-uninstall.log`.

## 7. Bilinen durumlar ve çözümleri

| Belirti | Neden | Çözüm |
| --- | --- | --- |
| Yayın "bytes differ under a published sequence" diye reddediyor | Aynı `sequence` ile farklı içerik | Bileşenin `version`/`sequence`/`package_id`'sini yükselt |
| Kurulu PC'de `LengthOrHashMismatchError`, kart "yeni sürüm yok" diyor | Yarıda kalan bir turun bıraktığı eski `N.snapshot.json`/`N.targets.json` (Ekim 2026'da düzeltildi: bu sürümün metadata'sı artık sha256 farklıysa her zaman yeniden yüklenir) | Yerel `.tmp/release/<feed>-vN/repository/metadata/` içindeki doğru dosyayı `gh release upload feed <dosya> --repo ... --clobber` ile yükle; hash'i `timestamp.json`'daki ile karşılaştır |
| Yeni sürüm birkaç dakika görünmüyor | GitHub CDN eski kopyayı sunuyor | Birkaç dakika bekle; `curl -sL <url> \| sha256sum` ile kontrol et |
| Bir feed güncellendi, diğeri bir tur sonra geldi | İki feed aynı anda görünür olmayabilir (CDN) | Sonraki denetimde düzelir |
| Derleme "Sources changed while the package was being built" | autocrlf'li checkout (Ekim 2026'da düzeltildi) | Güncel `main`'i kullan |
| Kaldırma "Resolve the pending update/conversion before maintenance" | İndirilmiş ama uygulanmamış güncelleme (yeni sürümlerde kaldırma bunu iptal eder) | Eski kurulumda önce "Şimdi güncelle", sonra kaldır |
| `Lifecycle busy or mutex inaccessible` (log'da) | Başka bir işlem (güncelleme/denetim) kilidi tutuyor | Kendiliğinden geçer; sürekli ise tepsiden Orion'u kapatıp aç |

## 8. Agent'lar için kurallar

- **Parola:** İmza parolasını asla girme, isteme veya saklama. Kullanıcı `release.cmd`'yi çalıştırır.
- **Kullanıcının izni gerekenler:** Push, dal birleştirme ve feed'e elle yükleme. Hepsi dışarıya açık
  işlemlerdir.
- **Gerçek kurulum:** Gerçek `%LOCALAPPDATA%\Orion` kurulumuna test yazma. Testler
  `~/.orion-lifecycle-tests` altında çalışır.
- **Paralel oturumlar:** Kendi worktree'nin `.tmp` klasörünü kullan. Ana checkout'un `.tmp/build-cache`
  klasörünü paylaşma veya ona link verme.
- **Commit:** Kullanıcının commit edilmemiş dosyalarına dokunma. Commit'lerde `-c commit.gpgsign=false` kullan.
- **Yayından önce:** `.\release.cmd --no-publish` (veya kullanıcıya bırak) ile tam kabul testini geçir. Hızlı
  testler: `pytest scripts/release/test_release_build_cache.py scripts/release/test_publish_feeds.py`.
