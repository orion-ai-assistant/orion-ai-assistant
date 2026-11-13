# İkon değiştirme rehberi (kısa yol)

Hub ya da Router ikonunu değiştirirken bu sayfayı izle. Ayrıntılar, sebepler ve hangi dosyanın nerede
göründüğü: [ikonlar.md](ikonlar.md).

## Kısa cevaplar

- **Bütün boyutları elle mi vereceğiz?** Hayır. Sen yalnız **tek bir büyük kaynak resim** verirsin. Betik her
  boyutu (16 px'ten 288/1024 px'e kadar) ayrı ayrı çizer. Elle tek tek boyut verme; tek bir resmi Windows'a ya da
  tarayıcıya küçültmek/büyütmek için de bırakma, çünkü bulanık ya da kırık çıkar.
- **Kullanıcının bilgisayarında eski ikon kalır mı?** Uygulamanın içinde ve pencerelerde kalmaz. Güncelleme
  gelince yeni ikon kendiliğinden görünür, kimsenin Ctrl+Shift+R yapması gerekmez (aşağıdaki tablo). Tek istisna,
  Windows'un kendi ikon önbelleği (aşağıda "Windows tarafı").
- **Kod değişir mi?** Hayır. Betiği çalıştır, çıktıları commit et, yayınla.

## Hub ikonunu değiştirmek

1. Kaynak: kare PNG, **en az 1024 px**. Arka plan şeffaf olabilir ya da yuvarlak karenin dışı saf siyah olabilir;
   betik siyahı otomatik şeffaf yapar. Gerçek bir vektör SVG de olur, ama içine PNG gömülmüş SVG olmaz.
2. Çalıştır (Pillow, numpy, scipy gerekir):

   ```
   cd orion-ai-assistant-flutter
   python tools/make_hub_icons.py <yeni-logo.png>
   ```

3. Betik şunları yazar, hepsi ayrı ayrı çizilmiş boyutlarla:
   - Flutter içi logo (`assets/brand/...`, 1x/2x/3x), web favicon ve PWA ikonları;
   - Flutter masaüstü exe ikonu, Hub penceresi (`windows_host/orion.ico`), Orion.exe (`orion.ico`);
   - Hub web arayüzü ve yönetim panelleri (`favicon.ico`, `logo-36/72/108.png`).
4. Çıktılara gerçek boyutta ve büyütülmüş hâlde bak: koyu ve açık zeminde, özellikle 16 ve 32 px.
5. İki depoyu da commit et (Flutter ve Hub). Geliştirici olarak `start_hub` yeterli: Flutter web'i ve Hub
   penceresini (OrionUi.exe) kendisi derler, eski pencereyi yenisiyle değiştirir.
6. Yayın: Client ve Hub paketleri birlikte çıkmalı (ikon iki depoda da var). Bkz. [release-guide.md](release-guide.md).

## Router ikonunu değiştirmek

1. Yeni PNG'yi `orion-router/dashboard/brand/orion-router-logo.png` olarak koy (en az 1024 px, kare).
2. Çizim değiştiyse `dashboard/scripts/make_icons.py` içindeki koordinatları güncelle (`CONTENT`, `STROKE`,
   `ORNAMENTS`, `SIMPLE_CONTENT`; açıklaması ikonlar.md'de).
3. `python dashboard/scripts/make_icons.py`. Bu `favicon.ico` (16–256 px) ve `logo-36 ... logo-288.png` (1x–8x)
   dosyalarını yazar.
4. Bak, commit et. Kaynaktan başlayan Router paneli kendisi derler (`bin/dashboard_build.py`).

## Yeni bir boyut ya da yeni bir yer eklerken kurallar

- Yeni bir yerde logo gösteriyorsan, **betiğe o yerin dosyasını ekle**, elle kopya koyma. (Hub penceresinin ikonu
  bir ara betikte yoktu, bu yüzden eski ikon kaldı.)
- Sayfada logo: tek dosya değil, `srcSet` ile birkaç yoğunluk ver (en az 1x–3x, büyütülebilen pencerede 8x'e
  kadar). Tarayıcı dosyayı yalnız yüklenirken seçer; büyütmede yeniden seçsin diye Router'da `OrionLogo.tsx`
  resmi ölçek değişince yeniden kurar.
- Windows ikonu (`.ico`): içinde 16, 20, 24, 32, 40, 48, 64, 256 px (Hub'da 128 de) hazır olsun. Windows %100'de
  16/32, %125'te 20/40, %150'de 24/48 ister.
- İkon URL'lerini sabit bırakma: sayfalar sunulurken ikon adreslerine içerik hash'i (`?v=...`) eklenir. Yeni bir
  sayfa yazıyorsan ikonunu da bu yoldan ver (Flutter `serve_web.py`, Hub `main.py` `versioned_icons`).

## Kullanıcının bilgisayarında ne olur (güncellemeden sonra)

| Yer | Yeni ikon ne zaman görünür |
|---|---|
| Flutter arayüzü (logo, sekme ikonu) | Pencere sayfayı bir sonraki yüklemede. URL'ler içerik hash'i taşır, dosyalar `no-cache`; eski service worker kendini siler. |
| Hub yönetim panelleri | Sayfa bir sonraki açılışta (ikon adreslerinde hash, `no-cache`). |
| Hub penceresi (başlık, görev çubuğu) | Güncelleme Orion'u yeniden başlatınca: eski exe'yle açık kalan pencere kapatılıp yeni exe ile açılır. |
| Router paneli ve penceresi | Router güncellenince; pencere ikonu her açılışta Router'dan alınır. |
| Masaüstü / Başlat kısayolları | Kısayol yeniden yazılınca; ardından Windows'un ikon önbelleği yenilenir (`SHChangeNotify` ve `ie4uinit.exe -ClearIconCache`). |
| Gizli simgeler (bildirim alanı / tepsi ikonu) | Güncelleme yeni Orion.exe'yi yazınca 500 ms içinde; çalışan yerleşik host süreci yeni ikonu dinamik yükler. Düzeltme gelmeden önceki exe bunu yapamıyordu (aynı yoldan eski ikonu okuyordu); düzeltmeyi içeren sürüme ilk geçişte Orion'u bir kez kapatıp açmak gerekebilir. Ayrıntı: [tepsi-ikonu.md](tepsi-ikonu.md). |

### Windows tarafı (bizim elimizde olmayan kısım)

- Windows ikonları kendi önbelleğinde tutar. **Görev çubuğuna sabitlenmiş** bir Orion bazen eski ikonu bir süre
  gösterebilir. Çözüm: sabitlemeyi kaldırıp yeniden sabitlemek ya da oturumu kapatıp açmak.
- Kısayol ikonu boş/eski görünürse aynı şey geçerli; Orion kısayolu yeniden yazarken önbelleği yenilemeyi zaten
  dener.

## Yayından sonra kontrol listesi

- [ ] Hub penceresinin görev çubuğu ve başlık ikonu yeni ve net (%100, %125, %150 ölçekte).
- [ ] Flutter içindeki logo ve sekme ikonu yeni; Ctrl+Shift+R yapmadan.
- [ ] Yönetim panelleri (`/panel`) yeni logo.
- [ ] Router paneli: logo yeni, pencere büyütülünce (Ctrl + tekerlek) net.
- [ ] Masaüstü ve Başlat kısayolu yeni ikon.
