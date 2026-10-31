# Orion ikonları: Router düzeltmesi ve ikon değiştirme rehberi (31 Ekim 2026)

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
4. `npm run build` (dashboard). Edge favicon'u önbellekte tutar; yeni ikon için F5 ya da pencereyi kapatıp aç.

### Hub (`orion-ai-assistant` ve `orion-ai-assistant-flutter`)

| Nerede görünüyor | Dosya | Nasıl üretiliyor | Durum |
|---|---|---|---|
| Orion.exe: masaüstü/Başlat kısayolu, bildirim alanı, Görev Yöneticisi | `scripts/release/launcher/orion.ico` | `scripts/release/launcher/make_icon.py`, `installer/app/ui/favicon.png` (1024 px) logosundan | İyi: 16–256 px ayrı boyutlar |
| Hub penceresi (Flutter, Edge `--app`) | `orion-ai-assistant-flutter/main_app/app/web/favicon.png` | elle, tek boyut 64 px | **Açık:** tek boyut; görev çubuğu Router'daki gibi bulanık olabilir |
| Hub web arayüzü sekmesi | `services/hub/src/orion/api/ui/favicon.svg` | elle, gerçek vektör | İyi |
| Flutter uygulama ikonları (PWA, telefon) | `web/icons/Icon-*.png`, `manifest.json` | elle | Bu işte bakılmadı |

Hub logosunu değiştirmek için:

1. Yeni logoyu `installer/app/ui/favicon.png` olarak koy.
2. `python scripts/release/launcher/make_icon.py` çalıştır. Betik amblemi (yazısız kısmı) sabit koordinatlarla
   kırpar: `logo.crop((222, 150, 802, 730))`. Yeni logoda bu koordinatları güncelle.
3. Bu betik küçük boyutları sadeleştirmez, yalnız küçültür. Logo ince detaylıysa Router betiğindeki
   yöntemi (çizgi kalınlaştırma, küçük boyutta süs çıkarma) buraya da uygulamak gerekir.
4. Orion.exe bir sonraki sürümde yeni ikonla derlenir.

**Hub penceresinin görev çubuğu için yapılacaklar:** Flutter `web/` içine çok boyutlu bir `favicon.ico`
koy ve `index.html`'de onu göster. Ardından Orion.cs'deki `RouterWindowIcon` adımını Hub penceresini de
kapsayacak şekilde genişlet (pencere başlığı "Orion", ikon dosyası kurulu Client'ın `build/web/favicon.ico`'su).

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
- Hub (Flutter) penceresinin görev çubuğu ikonu aynı sebeple bulanık olabilir; henüz düzeltilmedi (yukarıda).
