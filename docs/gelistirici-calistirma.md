# Geliştirici olarak çalıştırma (kaynak koddan Router + Hub + Flutter)

Kurulu Orion (`%LOCALAPPDATA%\Orion`, paketli sürüm) yerine üç bileşeni kaynak koddan,
logları görünür şekilde çalıştırmak için. Üç depo yan yana durur:
`DevProjects/orion-ai-assistant` (Hub), `DevProjects/orion-router`, `DevProjects/orion-ai-assistant-flutter`.

## 1. Kurulu Orion'u durdur

İkisi aynı portları kullanır (Hub 8910, Router 20128, Hub'ın web penceresi 8090). Önce kurulu olanı kapat:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "$env:LOCALAPPDATA\Orion\bootstrap\Orion.ps1" -Stop -NonInteractive
```

Portların boşaldığını kontrol et:

```powershell
foreach ($p in 8910, 20128, 8090) { "$p " + [bool](Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue) }
```

## 2. Router ve Hub'ı kaynak koddan başlat

Hub deposunun kökünden (`orion-ai-assistant`):

```bash
python -c "
import os
from pathlib import Path
from services.shared.native_services import start_router, start_hub
print('router', start_router(Path(r'D:/krstlcm Workspace/DevProjects/orion-router/cli.py')))
print('hub', start_hub(os.getcwd()))
"
```

Durdurmak için aynı modülde `stop_hub(os.getcwd())` ve `stop_router(Path(...cli.py))` var.
Kod değişince Hub'ı yeniden başlatmak: önce `stop_hub`, sonra `start_hub`.

Sağlık kontrolü:

```bash
curl -s -o /dev/null -w "hub=%{http_code}\n" http://127.0.0.1:8910/health      # 200
curl -s -o /dev/null -w "router=%{http_code}\n" http://localhost:20128            # 307
```

Notlar:
- `manager.py start --local` kullanma: paketli Router'ı başlatmaya çalışır ve "Packaged runtime requires an
  explicit installation registry" ile durur.
- Kaynak Hub, kaynak Router'a TLS ile sabitlenmiştir (ilk güven). Kurulu Router'a bağlanırsa
  `ServerFingerprintMismatch` verir; o yüzden Router'ı da kaynaktan çalıştır.
- Kaynak Hub'ın verisi (veritabanı, eşleşmiş cihazlar, kimliği) kurulu Hub'dan ayrıdır. Telefon kurulu Hub'a
  eşliyse, telefonda Hub listesinden geliştirici Hub'ını seç (daha önce eşleştiyse QR istemez).
- Hub ilk açılışta kendi web penceresini (8090, `build/web`) açabilir; test için 8095'teki debug web'i kullan.

## 3. Flutter web (debug, ses tanılamalı)

```powershell
cd "D:\krstlcm Workspace\DevProjects\orion-ai-assistant-flutter\main_app\app"
flutter run -d chrome --web-port 8095 --dart-define=ORION_AUDIO_DIAGNOSTICS=true
```

Adres: http://localhost:8095. `ORION_AUDIO_DIAGNOSTICS=true` konsola her an Silero puanlarını, ses
seviyelerini, konuşmanın neden başladığını (`start=wakeWord` / `speechInterruption`) ve yakalama sonlarını yazar.

## 4. Telefon (Android, debug)

Telefon USB ile bağlıyken:

```powershell
adb devices                     # cihaz kimliğini al
cd "D:\krstlcm Workspace\DevProjects\orion-ai-assistant-flutter\main_app\app"
flutter run -d <cihaz-kimliği> --dart-define=ORION_AUDIO_DIAGNOSTICS=true
```

`flutter run` uygulamayı yükledikten sonra bazen kapanır; o zaman loglar için:
`adb -s <cihaz-kimliği> logcat -d | grep flutter`.

## 5. Loglar

| Ne | Nerede |
| --- | --- |
| Kaynak Hub (API + worker + Hub'ın web penceresi) | `orion-ai-assistant/services/hub/hub.log` (`Get-Content ... -Wait -Tail 20`) |
| Kurulu Hub | `%LOCALAPPDATA%\Orion\Hub\data\services\hub\hub.log` |
| Kurulu sürümün güncelleme günlüğü | `%LOCALAPPDATA%\Orion\updates\watcher.log`, `last-result.json` |
| Web debug | `flutter run` terminali (tarayıcı konsolu) |
| Telefon | `flutter run` terminali veya `adb logcat` |

Hub logunda yankı iptalli turlar için:
- Telefon mikrofonu: `Echo turn <cihaz>: ... reference X dBFS (<loopback cihazı>), mic Y dBFS -> cleaned Z dBFS, cpu ...,
  canceller removed E dB (...), residual suppression avg S dB on N playing hops (G% gated), delay D ms, leak ...`
  (referans −120 dBFS ise PC sesi hiç alınmıyor demektir) ve
  `Remote mic turn <cihaz> ended (...): microphone: phone '<telefon adı>', ... gain avg A max B`.
- PC mikrofonu (2026-10-24'ten beri telefon gibi: web "Orion"u ham seste dinler, Hub'a yalnızca turlar gider):
  bağlanınca `PC microphone pc:<kullanıcı>: <mikrofon> ("Orion" heard by the web on the raw microphone)`;
  her tur bitince telefon turundaki satırların aynısı, cihaz `pc:<kullanıcı>`:
  `Remote mic turn pc:... ended (...)` ve `Echo turn pc:...: ...`. Kayıtlar (`ORION_PC_MIC_DUMP=1`)
  `HHMMSS-pcturn-*.wav`. Eski sürekli akış ve yankı karesi kısıcısı 2026-10-24'te kaldırıldı.
- Kalıntı yankı bastırma (`audio/residual_echo.py`): iptalcinin çıkışında, bant bazında. "canceller removed":
  kullanıcı susarken iptalcinin sildiği ortalama dB; "residual suppression": bu katmanın PC çalarken ekstra
  kıstığı ortalama; "gated": iptalcinin anlık kaçırdığı, tüm kare kısılan hop oranı; "leak": öğrenilen kalıntı
  profili (<1k / 1-4k / 4-8k, referansa göre dB). Eski 15 dB kare kısıcısı kaldırıldı.
- Web logunda `Orion empty follow-up turn` konuşmadan açılıp boş çıkan bir takip turu demektir (PC sesinin
  sızdığını gösterir).

## 6. Testler

```bash
# Hub (depo kökü PYTHONPATH'te olmalı, yoksa auth_routes içe aktarılamaz)
cd orion-ai-assistant/services/hub
PYTHONPATH="D:/krstlcm Workspace/DevProjects/orion-ai-assistant" uv run pytest tests/test_remote_mic.py tests/test_remote_mic_endpoints.py tests/test_echo_cancel.py tests/test_level_gain.py tests/test_echo_live.py tests/test_residual_echo.py -q

# Flutter
cd orion-ai-assistant-flutter/main_app/app
flutter test test/phone_microphone_test.dart test/voice_assistant_test.dart
cd ../../packages/vad_audio && flutter test
```

## 7. Kurulu Orion'a geri dönmek

Kaynaktaki Hub/Router'ı durdur (`stop_hub`, `stop_router`), web/telefon `flutter run` terminallerini kapat,
sonra Orion'u başlat menüsünden / masaüstü kısayolundan aç ya da:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "$env:LOCALAPPDATA\Orion\bootstrap\Orion.ps1"
```

Kurulu sürüme kod göndermek release ile olur: bkz. [release-guide.md](release-guide.md).
