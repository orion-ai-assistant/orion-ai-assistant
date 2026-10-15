# Flutter ana arayüz geçişi — 15 Ekim 2026

Hub sunucusu, API, worker ve Router korunur. `/hub` karşılaştırma ve geri dönüş için açık kalır.

| Akış / ayar | Eski Hub | Flutter | Bu geçiş |
| --- | --- | --- | --- |
| Giriş, kayıt, sohbet, geçmiş, yeniden adlandırma, silme, durdurma | Var | Var | Mevcut API ve hesap oturumu |
| Model, talimat, sıcaklık, düşünme, başlık | Var | Var | Şemadaki tüm kullanıcı ayarları korunur |
| STT/TTS model, dil, ses, modele göre ses hafızası | Var | Var | Mevcut ses testleriyle doğrulama |
| Gecikme, zaman aşımı, araç sınırları, TTL, worker sınırı | Var | Var, gelişmiş ayarlarda | Dinamik şema korunur |
| Araç seçimi ve varsayılan araçlar | Var | Var, araçlar ekranında | Ayarlardan da erişim |
| Chrome / Edge / Brave seçimi, bağlan, sürdür, kes, durum | Var | Eksik | Bağımsız tarayıcı kartı ekle |
| QR, davet, cihaz bağlantısı | Var | Var | Eski bağlantıları kesmeden koru |
| Ekler, görsel, ses, video, sesli asistan | Var | Var | Tarayıcı izinleri ve codec desteğine bağlı |
| Tema ve cihaz ses ayarları | Var | Var | Flutter cihaz tercihi |
| Dil | TR/EN | Arayüz Türkçe | İngilizce yerelleştirme sonraki aşama |
| İşlem / bağlantı durumu | Ayrı durum görünümü | Sohbet faaliyetleri ve bağlantı ekranı | Ayrı durum ekranı eşdeğerliği henüz tamamlanmadı |
| Hesap onayı, davet yönetimi, global/kullanıcı override, sıfırlama, Router yönetimi | Sahibe özel panel | Ayrı panel | Yetki korumalı panel bağlantısı; kullanıcı formuna taşınmaz |
| Python ile web sunumu | Hub içinde | `tool/serve_web.py`, 8090 | Hub yaşam döngüsüne bağla |

## Uygulama sırası

1. Bu envanter ve port/başlatma sözleşmesi.
2. Hub ile Flutter web sunucusu ve bağımsız uygulama penceresi; çakışma kontrolü, hazır olma kontrolü ve kapanışta süreç takibi.
3. Tarayıcı kartı, yerel cihaz denetimi, dar kapsamlı Flutter kaynak izni ve ilgili regresyon testleri.
4. Ayarlardan mevcut araçlar ve yönetim paneline erişim; şema kapsamı kontrolü.
5. Web derleme ve etkilenen giriş, bağlantı, ek, ses/video, ayar testleri. Gerçek cihaz mikrofon/kamera ve tarayıcı izinleri ayrıca elle denenmeli.
6. Eksik yerelleştirme/durum ekranı ve gerçek cihaz kabulünden sonra eski giriş bağlantıları değiştirilir. `/hub` bu aşamada kapatılmaz. Eski davet/QR bağlantıları aynı Hub'da çalışmaya devam eder; gelecekte yönlendirme token fragmentlerini korumalı ve uzak cihazları localhost'a yönlendirmemelidir.

## Port ve cihaz sözleşmesi

- Flutter: `ORION_FLUTTER_PORT=8090`, yalnızca `127.0.0.1`; eski Hub listener portları değişmez.
- Kaynak: `ORION_FLUTTER_APP_DIR`, varsayılan kardeş depo `orion-ai-assistant-flutter/main_app/app`.
- `ORION_FLUTTER_AUTOSTART=0` sunumu; `ORION_FLUTTER_OPEN_WINDOW=0` pencere açmayı kapatır. Derleme eksikse açık hata verilir; sunucu/API çalışmaya devam eder.
- Flutter web bilgisayarda normal sekme veya uygulama penceresi olabilir. Yerel tarayıcı yönetimi için sunucu gerçek socket peer denetimi yapar.
- Native masaüstü yalnızca bağlandığı Hub bu bilgisayardaysa tarayıcı yönetebilir. Telefon/uzak web aynı hesabı kullansa da bu yetkiyi kazanmaz. Mobil cihaz eşleştirmesi korunur.
- Yönetim paneli ayrı yerel yetki/giriş gerektirir. Flutter kullanıcı oturumu yöneticiye yükseltilmez.
