# Orion ikonları: Router düzeltmesi ve ikon değiştirme rehberi (31 Ekim 2026)

> İkon değiştireceksen önce kısa yol rehberi: [ikon-degistirme.md](ikon-degistirme.md).

Bu belge üç şeyi anlatır:

1. Router penceresinin ikonları neden kalitesizdi ve ne yapıldı.
2. İleride Router ya da Hub ikonunu değiştirirken kaliteli sonuç için ne yapmak gerekir.
3. Bu işle ilgili kalan açık noktalar.

## Durum

| Yer | Önce | Şimdi | Doğrulama |
|---|---|---|---|
| Görev çubuğu (Router penceresi) | 16 px favicon 32 px'e büyütülmüş, bulanık | Orion.exe pencereye gerçek 32/40/48 px ikon veriyor | Test programıyla canlı pencerede denendi; Edge ikonu 3 kez sıfırladı, her seferinde geri kondu. Yayınlanmış Orion.exe ile **henüz denenmedi**. |
| Başlık çubuğu (Edge'in çizdiği) | Kırık çizgili 16 px | Sadeleştirilmiş 16 px | Yeni derleme Edge'de görüldü |
| Panel logosu (sol üst) | 2,4 MB SVG içinde PNG | 36/72/108 px PNG | Yeni derleme Edge'de görüldü |

Görev çubuğu düzeltmesi yalnızca kurulu sürümde, Orion.exe çalışırken etkilidir. Kaynak koddan
çalıştırırken Orion.exe yoktur ve görev çubuğu ikonu bulanık kalır.

## Neden kalitesizdi

- Panelin tek ikonu `orion-router/dashboard/public/favicon.svg` dosyasıydı. Bu dosya vektör değildi: içine
  base64 olarak gömülmüş 1254×1254 px'lik bir PNG vardı (2,4 MB). Logo ince çizgilerden oluşuyor. Edge bu büyük
  resmi tek adımda 16 px'e küçültünce çizgiler kırılıp kesik kesik görünüyordu.
- Edge'in `--app` pencerelerinde pencere ikonu sayfanın favicon'undan gelir ve yalnız 16 px'liktir
  (%100 ölçekte). Görev çubuğu 32 px ister, Windows da 16 px'i 32 px'e büyütür. Bu yüzden favicon ne kadar iyi
  olursa olsun görev çubuğu tek başına düzelmez. Ölçüm: pencereye `WM_GETICON` gönderildiğinde küçük ikon
  16 px, büyük ikon da aynı resmin 32 px'e büyütülmüş hâliydi.
- 16 px çok küçük. Logodaki küçük noktalar ve artılar bu boyutta leke gibi görünüyor; ince çizgiler de
  yarım pikselin altına düşüp kayboluyor.

## Ne yapıldı

### Router deposu: ikon dosyaları

`orion-router/dashboard/scripts/make_icons.py` bütün ikonları logodan üretir. Pillow gerekir; çıktılar depoya
eklenir, derleme Pillow istemez.

```
python dashboard/scripts/make_icons.py
```

- Kaynak: `dashboard/brand/orion-router-logo.png`. Eski SVG'nin içindeki PNG budur; panelle birlikte
  yayınlanmaz.
- Her boyut ayrı çizilir. Önce hedefin 8 katı büyüklükte bir kopya hazırlanır. Çizgiler sonuçta en az 0,85 px
  olacak kadar kalınlaştırılır (`LINE`), logo beyaz yuvarlak kareye yerleştirilir ve tek seferde küçültülür.
- 24 px ve altında (`SIMPLE`) takımyıldızın çevresindeki küçük noktalar ve artılar (`ORNAMENTS`) çıkarılır.
  Kalan çizim kareyi daha çok doldurur (`SIMPLE_CONTENT`). 32 px ve üstünde logo eksiksizdir.
- Çıktılar:
  - `public/favicon.ico`: 16, 20, 24, 32, 40, 48, 64 ve 256 px. Windows %100'de 16/32, %125'te 20/40,
    %150'de 24/48 ister.
  - `public/logo-36.png`, `logo-72.png`, `logo-108.png`: kenar çubuğu logosu (1x, 2x, 3x).
- `app/layout.tsx` favicon olarak `favicon.ico`'yu, `components/DashboardLayout.tsx` logoyu `srcSet` ile
  kullanır. `favicon.svg` silindi.

### Hub deposu: görev çubuğu (Orion.exe)

Görev çubuğu favicon'la düzelmediği için pencereye ikonu Orion.exe veriyor
(`scripts/release/launcher/Orion.cs`, `Host.RouterWindowIcon`):

- Yarım saniyede bir, `Chrome_WidgetWin_1` sınıfında ve başlığı tam olarak "Orion Router" olan pencereyi arar.
- Pencerenin büyük ikonu kendi verdiği ikon değilse, kurulu Router'ın
  `<router code_path>\dashboard\out\favicon.ico` dosyasından Windows'un istediği boyutları (`SM_CXICON`,
  `SM_CXSMICON`) okur ve `WM_SETICON` ile pencereye verir. `code_path` bilgisini `install.json`'dan alır.
- Edge ikonu geri değiştirirse (örneğin sayfa yüklenince) bir sonraki adımda yeniden verir.
- Bu dosyası olmayan eski bir Router kuruluysa hiçbir şey yapmaz; Edge'in kendi ikonu kalır.
- İkon tutamaçları Orion.exe yaşadığı sürece açık kalmalıdır. Kısa süren bir süreç ikon verirse, süreç
  kapanınca tutamaç da geçersiz olur. Denemede bu görüldü; bu yüzden ikonu sürekli çalışan Orion.exe veriyor.

Orion.cs değiştiği için bir sonraki sürümde güncelleyici (bootstrap) sürümü de yükselir. Sürüm aracı bunu
kendisi yapar.

## İkon değiştirirken kurallar

Bu kurallar Router ve Hub ikonlarının hepsi için geçerli:

1. **Kaynak büyük ve temiz olsun.** Ya gerçek bir vektör SVG (yollardan oluşan) ya da en az 1024 px kare PNG.
   SVG içine PNG gömme; buna dışarıdan SVG denir ama tarayıcı onu bir resim gibi küçültür.
2. **Küçük boyutu ayrıca düşün.** 16–24 px'te:
   - çizgiler en az 1 piksel kalınlığında olmalı;
   - küçük süsler (noktalar, artılar, yazı) çıkarılmalı;
   - kenar boşluğu az olmalı, çizim kareyi doldurmalı.

   En iyi sonuç, logonun 16 px için elle sadeleştirilmiş ayrı bir çiziminden gelir. Betik bunu
   kaba bir şekilde, otomatik olarak yapıyor.
3. **Her boyutu ayrı üret.** Windows ikonu (`.ico`) içinde 16, 20, 24, 32, 40, 48, 64 ve 256 px hazır bulunmalı.
   Tek bir büyük resmi Windows'a ya da tarayıcıya küçültmek için bırakma.
4. **Gerçek boyutta bak.** Her sonucu hem gerçek boyutunda hem 4 kat büyütülmüş (piksel piksel) kontrol et.
   Koyu ve açık zeminde ayrı ayrı bak: görev çubuğu koyu, Başlat menüsü açık olabilir.
5. **Edge pencerelerinde favicon yetmez.** Bir `--app` penceresinin görev çubuğu ikonunu yalnız Orion.exe
   düzeltebilir (yukarıdaki bölüm).

## Nerede hangi ikon var

### Router (`orion-router`)

| Nerede görünüyor | Dosya | Nasıl üretiliyor |
|---|---|---|
| Sekme, başlık çubuğu, görev çubuğu | `dashboard/public/favicon.ico` | `dashboard/scripts/make_icons.py` |
| Panel logosu | `dashboard/public/logo-{36,72,108}.png` | aynı betik |

Router logosunu değiştirmek için:

1. Yeni PNG'yi `dashboard/brand/orion-router-logo.png` olarak koy (en az 1024 px, kare).
2. Çizim değiştiyse betikteki koordinatları yeni çizime göre güncelle:
   - `CONTENT`: beyaz kenar boşluğu hariç çizimin kutusu;
   - `STROKE`: çizgi kalınlığı (kaynak pikseli);
   - `ORNAMENTS`: küçük boyutta silinecek süsler;
   - `SIMPLE_CONTENT`: süsler silindikten sonraki kare kutu.
3. `python dashboard/scripts/make_icons.py` çalıştır, çıktıları büyütülmüş hâlde kontrol et.
4. `npm run build` (dashboard). Kaynaktan başlayan Router bunu kendisi yapar (`bin/dashboard_build.py`).

### Hub (`orion-ai-assistant` ve `orion-ai-assistant-flutter`) (6 Kasım 2026'dan beri tek kaynak)

Hub'ın bütün ikonları tek betikten çıkar: `orion-ai-assistant-flutter/tools/make_hub_icons.py [kaynak.png]`
(yalnız Pillow gerekir; derleme istemez, çıktılar depoya eklenir):

```
uv run --no-project --with pillow python tools/make_hub_icons.py
```

- 11 Kasım 2026'dan beri her yerde Router panelindeki **beyaz yuvarlak kare amblem** kullanılır. Kaynak
  varsayılan olarak `orion-router/dashboard/brand/orion-router-logo.png`'dir; Router betiğiyle aynı çizim
  yöntemi uygulanır (8 kat büyük çizim, çizgiler en az 0,85 px'e kalınlaştırılır, 24 px ve altında süsler
  çıkarılır, tek Lanczos adımı). İki uygulamanın ikonları bu yüzden birebir aynıdır.
- Her boyut ayrı üretilir; hiçbir yer tek resmi esnetmez. Flutter içi logolar 1x/2x/3x olarak gösterildikleri
  boyut için çizilir: `orion_logo` (64, giriş), `orion_mark` (36, kenar çubuğu), `orion_mark_large`
  (96, açılış ekranı).
- Android başlatıcı ikonları (`android/app/src/main/res/mipmap-*/ic_launcher.png`) ve kurulum sayfasının
  ikonu (`installer/app/ui/favicon.png`) da bu betikten çıkar.

| Nerede görünüyor | Dosya |
|---|---|
| Flutter masaüstü penceresi, görev çubuğu, exe | `main_app/app/windows/runner/resources/app_icon.ico` (16–256 px) |
| Flutter içi logo (giriş, kenar çubuğu, dar ekran başlığı, açılış ekranı) | `assets/brand/orion_logo.png`, `orion_mark.png`, `orion_mark_large.png` + `2.0x/` + `3.0x/`, `lib/core/presentation/orion_logo.dart` |
| Android başlatıcı | `android/app/src/main/res/mipmap-*/ic_launcher.png` |
| Kurulum sayfası | `installer/app/ui/favicon.png` |
| Flutter web sekmesi / PWA | `web/favicon.ico`, `web/favicon.png`, `web/icons/*` |
| Orion.exe | `scripts/release/launcher/orion.ico` |
| Hub'ın kendi penceresi (OrionUi.exe): başlık çubuğu, görev çubuğu | `main_app/app/windows_host/orion.ico` (exe'ye gömülür) |
| Hub web arayüzü | `services/hub/src/orion/api/ui/favicon.ico`, `logo-{36,72,108}.png` (`/favicon.ico`, `/brand/...`) |
| Hub yönetim/sahip panelleri | `owner_ui/` ve `admin_ui` aynı dosyaları `/owner-assets/` altından kullanır |

Logoyu değiştirmek için betiği yeni PNG ile çalıştır, büyütülmüş hâlde bak, sonra commit et. Kod değişmez.
Geliştirici başlangıcı (`start_hub`) Flutter web'i ve OrionUi.exe'yi kendisi yeniden derler (aşağıda).

## İkon değişince yenisi nasıl gelir (Ctrl+Shift+R gerekmez)

Kural: **bir ikon ya da logo değişince hiçbir yerde Ctrl+Shift+R, önbellek temizleme ya da dosya adı
değiştirme gerekmez.** Güncelleme gelince (ya da geliştirici olarak Hub yeniden başlayınca) yeni ikon kendiliğinden
görünür. Bunu sağlayan parçalar aşağıda; bir yeri değiştirirken bunları bozma.

| Nerede | Nasıl tazeleniyor | Kod |
|---|---|---|
| Flutter web (8090) sekme/pencere ikonu | `index.html` sunulurken her ikon linkine dosyanın içerik hash'i eklenir (`favicon.ico?v=<hash>`). Dosya değişince URL değişir, tarayıcı yenisini hemen ister. | `orion-ai-assistant-flutter/main_app/app/tool/serve_web.py` (`versioned_index`) |
| Flutter web uygulama içi logo ve kod | Her dosya `Cache-Control: no-cache` ile sunulur (değişmediyse 304). Eski Flutter service worker'ı her yüklemede kendini siler. Uygulama service worker kaydetmez (`web/flutter_bootstrap.js`); kaydetseydi Flutter her açılış ve yenilemede onu 4 s bekliyordu. | aynı dosya (`end_headers`, `KILL_WORKER`) |
| Hub yönetim panelleri (`/panel`, `/admin` ...) | Sayfa sunulurken `/owner-assets/favicon.ico` ve `logo-*.png` adreslerine içerik hash'i eklenir; dosyalar `no-cache`. | `services/hub/src/orion/api/main.py` (`versioned_icons`, `RevalidatedFiles`) |
| Hub web arayüzü `/favicon.ico`, `/brand/...` | `no-cache` ile sunulur. | `main.py` |
| Hub'ın kendi penceresi (OrionUi.exe) | İkon exe'ye gömülüdür. Pencere açık kaldıkça eski exe çalışır; bu yüzden Hub başlarken çalışan pencerenin exe'si yeni derlemeden eskiyse pencere kapatılıp yeni exe ile açılır. | `services/shared/flutter_launcher.py` (`ui_outdated`, `open_ui`) |
| Router paneli | Router'ın kendi `favicon.ico`'su her açılışta Router'dan alınır (`/dashboard/favicon.ico`). | `flutter_launcher.py` (`open_router_window`) |

### Geliştirici olarak: kaynak değişince otomatik derleme

Router'ın paneli (`orion-router/bin/dashboard_build.py`) gibi: `start_hub` ile başlayan Hub, Flutter deposunun
kaynaklarını (`lib`, `web`, `assets`, `pubspec.*`, yerel paketler, `windows_host`) son derlemeyle karşılaştırır.
Değişen bir şey varsa önce `OrionUi.exe`'yi, sonra `flutter build web`'i çalıştırır; pencere derleme bitince
açılır. Değişiklik yoksa derlemez. Kayıtlar: `main_app/app/build/.orion-web-build.json`,
`.orion-ui-host-build.json`. Kod: `services/shared/flutter_dev_build.py`. Kurulu sürümde kaynak olmadığı için
bu adım hiç çalışmaz.

- Hub logunda `[*] Flutter web: sources changed, rebuilding` / `rebuilt` satırları görünür. Derleme başarısız
  olursa `[!] ... the previous build stays` yazar ve eski derleme sunulur.
- Kaynak değişince Hub'ın açılışı derleme yüzünden 1-2 dakika uzar.
- `flutter run -d chrome` (8095) bundan bağımsızdır; kaynaktan çalışır, hot reload ile hemen yansır.

## Pencereler (6 Kasım 2026)

- **Hub'ın penceresi** `OrionUi.exe`'dir (WebView2; kaynak `main_app/app/windows_host/OrionUi.cs`, derleme
  `windows_host/build_ui_host.py`). Ekran ölçeğini bilir (bulanık büyütme yok), ikonu Windows'un istediği tam
  boyutta alır. Başlık çubuğu eski Edge Router penceresindeki koyu gridir (seçiliyken `#202020`, değilken
  `#2B2B2B`), F11 pencereyi tam ekran yapar (görev çubuğu dahil; tekrar F11 geri alır, sayfadaki bir video
  tam ekrana geçerse pencere de geçer), "başlık çubuklarında vurgu rengi" açık olsa da çubuk boyanmaz; yalnız kenarlık Windows'a
  bırakılır (vurgu / duvar kağıdı rengi). Başlık yazısı pencere seçili değilken de grileşmez. Flutter masaüstü
  penceresi (`windows/runner/win32_window.cpp`) aynıdır.
- **Router panel logosu** 36–288 px (1x–8x) dosyalarından seçilir (`orion-router/dashboard/scripts/make_icons.py`,
  `components/OrionLogo.tsx`). Tarayıcı `srcSet` dosyasını yalnız yüklenirken seçer; sonradan büyütülünce
  (Ctrl + tekerlek) küçük dosyayı gerer. `OrionLogo` ölçek (`devicePixelRatio`) değişince resmi yeniden kurar,
  tarayıcı da o ölçeğe uygun dosyayı yeniden seçer.
- **Router paneli ve Hub yönetim paneli** (`/panel`) Hub penceresinin süreci içinde, ayrı bir pencere olarak
  açılır (`panel` komutu): tarayıcı motoru zaten çalıştığı için anında gelir ve aynı Orion uygulamasıdır. Her
  panelin çerezleri ayrı WebView2 profilindedir (`panel-router`, `panel-panel`). Hub penceresi çalışmıyorsa
  ya da eski bir exe ise ayrı bir `OrionUi.exe --panel` süreci açılır (birkaç saniye).
- **Router paneli** (bu bilgisayardaki Router) kendi ikonuyla, aynı başlıkla açılır.
  Kapatınca gerçekten kapanır, mikrofon izni almaz, Hub'ın dinleme durumuna karışmaz. Açıkken yeniden açılırsa
  ikinci pencere açılmaz, açık olan öne gelir. Uzak bir Router'ın paneli (HTTPS) ya da exe'si olmayan bir Client'ta
  Edge `--app` penceresi kullanılır. "Bu bilgisayarda mı" kararı önce kurulum kaydından, kayıt bu Router'ı
  tanımıyorsa (örneğin kaynaktan çalışan geliştirici Router'ı) aynı Router'ın `127.0.0.1` üzerinden sabitlenmiş
  TLS kimliğiyle verilir (`services/hub/src/orion/kernel/router_panel.py`).
- **Edge pencereleri ayrı profillerde:** Hub penceresi `flutter-ui-profile`, Router `router-ui-profile`, Hub
  yönetim paneli `panel-ui-profile`. Kısayol (masaüstü, Başlat) her zaman Hub arayüzünü açar; önceden Router ya da
  panel penceresi aynı profilde olduğu için kısayol onu öne getiriyordu.

## Orion.exe nedir

Hub'ın kendi küçük başlatıcısıdır, 0.2.14'ten beri kurulumla gelir. Kaynağı
`scripts/release/launcher/Orion.cs`; sürüm çıkarılırken Windows'ta hazır bulunan .NET Framework derleyicisiyle
(`csc.exe`) derlenir (`scripts/release/build_launcher.py`). Kurulu kopyası
`%LOCALAPPDATA%\Orion\bootstrap\Orion.exe`.

- Masaüstü, Başlat menüsü ve Windows açılışı kısayolları bunu çalıştırır.
- Bildirim alanındaki Orion simgesi odur (başlık ve "Çık").
- Bütün Orion süreçlerini tek bir Windows işi (job) içinde tutar. Görev Yöneticisi'nde "Orion AI Assistant"
  için "Görevi sonlandır" denince her şey birlikte kapanır.
- Windows kapanırken servislerden düzgün durmalarını ister.
- Yalnız Router kurulumunda adı "Orion Router" olur.

Ayrı bir Windows sürümü değildir; Orion'un Windows'taki başlatıcısıdır.

### Neden SmartScreen uyarısı çıkmıyor

SmartScreen yalnız tarayıcının ya da e-posta programının "internetten indirildi" işareti
(`Zone.Identifier`, "Mark of the Web") koyduğu dosyalarda devreye girer. `get-orion.ps1` paketi
`Invoke-WebRequest` ile indirir ve `Expand-Archive` ile açar. Bu yolda işaret oluşmaz; güncellemeler de aynı
şekilde gelir. Kurulu Orion.exe'de işaret yoktur ve dosya imzasızdır (`NotSigned`).

Bir kurulum dosyası (`setup.exe`/`.msi`) tarayıcıdan indirilirse işaret oluşur ve imzasız dosyada
"Windows bilgisayarınızı korudu" uyarısı çıkar. Windows 11'de Akıllı Uygulama Denetimi açık olan bir bilgisayar
imzasız programı işaret olmasa da engelleyebilir. Kalıcı çözüm kod imzalamadır. İmzalı dosyada da uyarı,
yeterince kişi indirip çalıştırana kadar sürebilir (SmartScreen itibarı). Alternatif: Microsoft Store.

## Sınırlar ve açık noktalar

- 16 px'te bu kadar detaylı bir logo tam net olamaz. Sadeleştirilmiş sürüm otomatik olarak elde edilebilecek
  en iyi sonuç. Daha iyisi için logonun 16 px'e özel, elle çizilmiş bir sürümü gerekir.
- Görev çubuğu düzeltmesi kurulu sürüme bir sonraki yayınla gelecek. O zaman kontrol et:
  - görev çubuğu ikonu net mi;
  - panelde sayfa değiştirip yenileyince de net kalıyor mu.
- Hub penceresi (OrionUi.exe) ve Router paneli penceresi 6 Kasım 2026'da düzeltildi (yukarıda "Pencereler").
  Kurulu sürüme Client paketinin bir sonraki yayınıyla gelir; o zaman görev çubuğu ikonlarını ve kısayolun
  Router açıkken Hub'ı açtığını kontrol et.
- DPI farkındalığından sonra Hub penceresi bir kerelik biraz küçük açılabilir (kayıtlı boyut eski ölçekle
  saklanmıştı); büyütülünce yeni boyut hatırlanır.
