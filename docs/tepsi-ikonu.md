# Bildirim alanındaki (tepsi) Orion simgesi güncellemede eski kalıyordu (13 Kasım 2026)

İkon değiştirirken genel rehber: [ikon-degistirme.md](ikon-degistirme.md). Bu sayfa yalnız tepsi simgesi sorununu
ve çözümünü anlatır; ileride "tepsideki ikon güncellemeden sonra eski kaldı" olursa buradan başla.

## Belirti

İkon değişen bir güncellemeden sonra kısayol, Başlat, pencereler yeni ikonu gösteriyordu; Windows'un "Gizli simgeler"
(bildirim alanı) bölümündeki küçük Orion simgesi eski kalıyordu. Orion'u kapatıp açınca düzeliyordu.

## Sebep

Tepsi simgesini kurulumun kalıcı `Orion.exe` süreci (host, `scripts/release/launcher/Orion.cs`) çizer. Güncelleme
çalışan exe'yi silemez; yan tarafa `Orion.exe.<kod>.old` diye taşır, yenisini aynı yola yazar. Host her 500 ms'de
`Orion.exe`'nin yazılma zamanına bakar (`RefreshTrayIcon`); değiştiyse ikonu yeni dosyadan okuyup tepsiye verir.

Hata: ikon `PrivateExtractIconsW` ile **aynı dosya yolundan** okunuyordu. Aynı süreçte Windows, bir yoldan ilk
okuduğu ikonu hatırlar ve ikinci okumada dosya değişmiş olsa da eski resmi verir. Yani kod "yeniledim" diyor, ama eski
ikonu tekrar atıyordu (atama başarılıydı, hata da yoktu). Yeni başlayan süreç ilk kez okuduğu için doğru ikonu alıyordu;
bu yüzden kapatıp açmak çözüyordu.

Ayrıntı: ayrı bir PowerShell sürecinde aynı yeni exe'den okuma doğru (yeni) ikonu verdi; yani dosya ve ikon kaynağı
sağlamdı, takılan yalnız çalışan sürecin kendi okumasıydı.

## Nasıl bulundu (izole deney)

Gerçek Hub'a dokunmadan, geçici klasörde sahte bir kurulumla (kendi `install.json` + kimlik) `Orion.exe` derlenip
çalıştırıldı; çalışırken exe, güncellemenin yaptığı gibi yan tarafa taşınıp yeni ikonlu sürümle değiştirildi:

| Deneme | Tepsi simgesi |
|---|---|
| Kodun kendisi (log'lu): yenileme çalıştı, `icon.Icon` atandı, hata yok | eski kaldı |
| Simgeyi gizleyip yeniden ekleme (`Visible=false/true`) | eski kaldı |
| Tamamen yeni `NotifyIcon` nesnesi | eski kaldı |
| Aynı simgeyi Windows'un sarı uyarı ikonuyla değiştirme | **değişti** (değiştirme çalışıyor) |
| Exe'yi benzersiz adlı geçici kopyadan okuma | **yeni ikon** |
| Süreci kapatıp yeni exe ile sıfırdan başlatma | yeni ikon |

Sarı ikonun değişmesi, sorunun tepsiyi güncellemede değil, okunan resmin kendisinde (eski önbellek) olduğunu gösterdi.

Not: Windows `HKCU\Control Panel\NotifyIconSettings` altında her tepsi simgesi için exe yoluna bağlı bir `IconSnapshot`
saklar; bu kayıt **ekrandaki simgeyi belirlemiyor** (kopya eski kalsa da sıfırdan başlatılan simge yeni görünüyor).
Orada eski bir ikon görürsen sebebin o olduğunu varsayma.

## Çözüm (`f6331c8`)

`RefreshTrayIcon` ikonu `Orion.exe`'nin **benzersiz adlı geçici kopyasından** okur (`%TEMP%\orion-icon-<guid>.exe`),
sonra kopyayı siler. Windows bunu hiç görmediği bir yol sayıp dosyadan gerçekten okur.

- Kopya o sırada yazılıyorsa (kilit) hata verir; `launcherWriteTime` yalnız başarıdan sonra güncellendiği için bir
  sonraki 500 ms'de yeniden denenir. Eskiden bir kez başarısız olunca bir daha denenmezdi.
- Sonuç `%LOCALAPPDATA%\Orion\bootstrap\orion-host.log` dosyasına yazılır:
  - `tray icon refreshed from Orion.exe written <zaman>`: yenileme yapıldı;
  - `tray icon refresh failed (retries): <hata>`: başarısız, tekrar dener (aynı hata bir kez yazılır);
  - `tray icon: no icon at N px in ...`: exe'de o boyutta ikon yok.
  Dosya 100 KB'ı geçince silinip baştan başlar.

## Ne zaman devreye girer

1. Düzeltme **yeni Orion.exe'nin içinde**. Çalışan eski exe onu taşımaz: bu düzeltmeyi içeren sürüme ilk geçişte tepsi
   yine eski kalabilir; Orion'u bir kez kapatıp açmak yeter. Sonraki ikon değişikliklerinde gerekmez.
2. İkon değişince exe yeniden dağıtılır: `build_launcher.py` exe'yi `orion.ico` ve `Orion.cs` ile derler,
   `build_bootstrap.py` bunu paketler, `bump_versions.bump_bootstrap` canlı yayınla bayt farkına bakıp bootstrap
   sürümünü kendisi yükseltir. `install_entry` yeni exe'yi kuruluma yazar (çalışan eskiyse yan tarafa taşır).

## Bu sorun olmayan şey (dokunma)

Windows Arama / Başlat'taki uygulama ikonu bazen ilk açılışta eski görünür, yeniden yazınca yeni gelir. Bu, Windows
Arama'nın kendi ikon önbelleğidir; koddan yenilenemez ve kendiliğinden eskir. `SHChangeNotify` yalnız Explorer'ın
önbelleğini temizler.

## Release sonrası kontrol

- [ ] Güncellemeden sonra `bootstrap\orion-host.log` içinde "tray icon refreshed" satırı var.
- [ ] Tepsi simgesi yeni ikon (Orion'u kapatıp açmadan; ilk geçiş hariç).
- [ ] `refresh failed` satırı varsa mesajına bak (kilit, izin, yer).
