# Aşama 04 oturum devri — 16 Ekim 2026

**Güncel kullanıcı kararı:** Computer Use kullanılmayacak; manuel pencere ve gerçek oturum kabulü Aşama 05 sonrasındaki kullanıcı denemesine ertelendi. Kod/izole entegrasyon testleri yapılıp sonuçlar commit'lendikten sonra yeni oturumda yalnız 05'e geçilecek. [Hazır Aşama 05 promptu](05-session-prompt.md). Aşağıdaki önceki "ayrı talimat gerekir" ifadesi bu son kullanıcı talimatıyla karşılandı; 05 bu oturumda uygulanmadı.

17 Ekim ek kullanıcı işi: Router çevrimdışı hesap tamamlama, Router'a bağlı manuel sanal anahtar önceliği ve hedef/pin ayrımı uygulandı. [Güncel hesap erişimi sonuç ve arayüz kaydı](../router-account-access-followup.md). 05 yeni Hub/Router endpoint bağımlılığını ve kullanıcı seed/şifreli manuel key dosyalarını korumalıdır; bu iş 05'i başlatmadı.

[Ana rehber](../orion-installation-lifecycle-implementation-plan.md) · [04 kapsam/sonuç](04-launcher-and-autostart.md) · [Önceki Unicode devri](03-unicode-postgres-followup.md)

Kullanıcı yalnız 04'ü uygulama, testleri agent'ın sırayla çalıştırması, sonuçları MD'ye kaydetme, görev commit'i oluşturma ve durma talimatı verdi. Push yok. Gerçek hesabın Windows başlangıcını etkinleştirme yetkisi yok; izole Startup fixture testi yetkilidir. D'deki üç development deposu ve ilgisiz kullanıcı diff'leri korunur. Orion AppData kurulum/test alanları gerektiğinde yeniden oluşturulabilir; başka uygulamalara dokunulmaz.

**04 DOĞRULAMA EKSİK.** Kaynak kurulum tek başlatıcısı, iki ayrı tercih, Windows link adaptörü, yerel hata/repair erişimi ve tekrar pencere açma/focus kodu uygulandı. Temiz VM'de gerçek logon/logoff, gerçek browser HWND/pencere kapanışı ve WinForms görsel etkileşim kabulü bu makinede yapılmadı. Fixture Startup çağrısı gerçek Windows logon testi değildir. 01/03 açık kabulleri ve 05/08 eski DB/runtime dönüşümü kapatılmadı.

Sabit giriş `<registry-parent>/bootstrap/Orion.ps1`; kayıt adı `install.json`. Hub'ın installer/Hub venv'i üzerinden `services.shared.launcher` çağrılır. `install_entry` masaüstü linkini hazırlar; Startup tercih açılmadıkça kurulmaz. Başarılı açık bootstrap kaydı giriş kurulumunu çağırır; testlerde `ORION_SKIP_GLOBAL_REGISTRATION=1` korunur. Kaynak ve packaged runtime ayrımı açık kalır; 05'e SDK'sız dağıtım kanıtı verilmez.

`launch` registry operation kilidini tutar ve mevcut native start/stop/ownership mekanizmasını çağırır. `operation_lock(path, timeout=15)` yalnız opsiyonel bekleme parametresi kazandı; launcher 180 saniye bekler. Bu canonical kaynak değişikliği nedeniyle `install.ps1` ve `install.sh` yeniden üretildi. Gelecekte yardımcı/taşıma/güncelleyici aynı kilit sırasını korumalıdır.

Pencereyi kapatmak servisleri kapatmaz. Elle normal açılış pencereyi ister; Windows açılışında pencere `show_client` tercihine bağlıdır. `--background` hiçbir pencere istemez. Başlatıcı supervisor'ın otomatik pencere açmasını kapatır; Flutter server yaşam döngüsü Hub'da kalır. Pencere açmadan önce kayıtlı Flutter süreci/listener/health denetlenir. Tekrar açmada aynı dedicated profile'ın mevcut Windows penceresi bulunur; OS focus kabulü hâlâ açıktır.

Gerçek son ürün kanıtı `.tmp/lifecycle/product-aswv20ee/result.json`: başarı; Türkçe/Çince kod/veri kökü, 1 Startup + 2 elle eşzamanlı çağrı, tek supervisor, gerçek Flutter build/server, DB/TLS/UUID ve örnek satır korunması, stop/restart. Tüm linkler yalnız `.tmp` altındaki fixture Startup klasöründeydi. İlk başarısız fixture ve sandbox kısıtları 04 sonuç dosyasında saklıdır. Loglar ignored kalır; kararlı özetler MD'de commit'e girer.

Regresyon: **75 test + 10 alt test** (kayıt/başlatıcı/bootstrap/host), **51 test + 2 alt test** (Hub/installer), **1 headless ekran testi**, son ek Desktop/Startup ve gerçek sentetik listener/health ile **17 launcher testi**. Örtüşür; toplanmaz. Tam komut/kanıt/ilk hata geçmişi 04 sonuç dosyasındadır. Son denetimde görev fixture'larında yaşayan süreç/DB yok, gerçek kullanıcı Startup'ında Orion linki yok. Wrapper eşitliği ve 11 Python AST geçti.

Önceden var olan Hub `docs/orion-update-strategy-proposal.md`, `services/hub/uv.lock`, Flutter `main_app/app/test/chat_presentation_test.dart` diff'leri görev commit'ine alınmaz; Router'da yeni görev diff'i yok. Son commit kimliği için `git log -1 --oneline` kullan. Kendi süreçlerini kontrollü stop ile kapatmadan state/DB silme. Push yapılmadı; 05'e başlanmadı.

C AppData son takip: `stage04-desktop-dhz35mg9/result.json` kod/entegrasyon audit'i başarılı; gerçek izole Startup/Desktop `.lnk` çağrıları, DB örnek satırı/kimlikler ve aynı supervisor korundu. Test kapatıldı; ürün çocukları `[]`, DB owner `null`, gerçek Startup hash'leri aynı ve Orion linki yok. Computer Use focus/close kabulü araç URL politika kontrolünde durdu, ardından kullanıcı bu testleri iptal etti; geçmiş sayılmaz. Ayrıntılar 04 sonuç dosyasının son bölümündedir.

Ek kod testleri gerçek bir dar kapanış açığı buldu ve düzeltti: `job_safety.queue_watchdog` Redis await'i iptali yuttuğunda pending cancellation ile sonsuz döngüye devam etmiyor. Yeni deterministik regresyon ve mevcut gerçek Redis setiyle **35 test + 9 alt test**, ardından Hub/installer **51 test + 2 alt test** geçti. C kanıtları `stage04-code-d4yqmp8c` / `stage04-code-4jqpyo3p`; ilk timeout ve async görev tanıları silinmedi. Kod/test yalnız bu düzeltmeyle sınırlıdır; 01 diğer kabul borcu açık kalır.
