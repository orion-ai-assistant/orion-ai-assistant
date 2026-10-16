# 03 takip — Türkçe ve Unicode PostgreSQL kurulum yolu

16 Ekim 2026. [Ana rehber](../orion-installation-lifecycle-implementation-plan.md) · [03 sonuçları](03-installation-paths-and-migration.md) · [Devir](03-session-handoff.md).

Kullanıcı 04 öncesi Türkçe yol sorununun giderilmesini istedi. **Yeni native Hub/Router kurulumlarında bu sorun giderildi ve gerçek servislerle doğrulandı.** Önceki PG16.3 Unicode engeli yeni kurulumlar için güncel durum değildir. 04’e başlanmadı. 01/03’ün diğer açık kabul maddeleri ve tam DB/runtime taşıma borcu sürer.

## Neden ve çözüm

PG16.3, Windows ANSI yolunu UTF-8 SQL içinde kullanırken hata veriyordu. Kısa NTFS adlarıyla bir deneme geçti; her diskte bulunmadığından ürün çözümü yapılmadı. Yalnız UTF-8 manifesti eklenen 16.3 kopyası da bu makinenin `Turkish_Türkiye.utf8` locale save/restore hatasına takıldı. Bu iki denemenin başarısızlığı saklandı; sistem locale/code page ayarı değiştirilmedi.

Yeni kaynak kurulum PostgreSQL **16.15-1** kullanır. `services/shared/windows_postgres.py` (Router’da aynı kaynak `core/windows_postgres.py`) üreticinin arşivini sabit SHA256 ile doğrular; yalnız bin/lib/share ve sunucu/komut satırı lisanslarını yeni staging klasörüne çıkarır. PostgreSQL EXE’lerinin mevcut uygulama manifestlerini koruyarak `activeCodePage=UTF-8` ekler, sonra yeni runtime klasörünü yayımlar. Bu Windows’un uygulamaya özel ayarıdır; kullanıcının Windows dili veya bölgesel ayarı değişmez. **Windows 10 1903+** gerekir; kısa dosya adına, junction’a veya sürücü harfi eşlemesine dayanmaz.

Üretici arşivi: `https://get.enterprisedb.com/postgresql/postgresql-16.15-1-windows-x64-binaries.zip`, SHA256 `25e6fcdfb8caec38691bf461125e7564508760666f7b8e5dc6a5f0818f58f81e`. Bu, HTTPS üzerinden alınan üretici dosyasının bu uygulamada sabitlenmiş özetidir; bağımsız üretici imzası doğrulandı iddiası değildir. Dosya değişirse kurulum reddedilir. İmzalı EXE dönüşümü reddedilir; imzalı ve ayrı bir yükleyici olan StackBuilder runtime’a alınmaz/değiştirilmez. `.orion-postgres-runtime.json` üretici ve türetilmiş EXE özetlerini ayrı tutar. Bu yerel makbuz 05’in imzalı sürüm/paket güven protokolünün yerine geçmez. 05 paket imzası manifest hazırlığından **sonra**, son baytlara uygulanmalıdır.

`local_db_setup.py` ve Router `bin/common.py` yalnız **yeni** runtime hedefini hazırlar. Dolu/eksik kurulum hedefi yerinde silinmez veya yamalanmaz; onarma gerekir. Başarısız initdb’de mevcut verileri silip yeniden deneme kaldırıldı. Mevcut ASCII-root runtime/DB otomatik yükseltilmez. Mevcut Unicode-root legacy runtime da dosyaları değiştirilmeden açık onarma hatası verir; desteklenen yeni runtime dönüşümü 05/08 işidir.

`Postgres` Unicode yolda hazırlık makbuzunu ve kritik EXE özetlerini denetler. `psql` istemci kodlaması ve Python stdout decode’u açık UTF-8’tir; Türkçe SQL/yol dönüşü kayıpsızdır. Gerçek yeni Hub kurulumu, uygulama hesabının lifecycle `SHOW data_directory` kontrolü için `pg_read_all_settings` rolüne ihtiyaç duyduğunu ortaya çıkardı; bu okuma rolü yeni kurulumda verilir, `SUPERUSER` verilmez. Eski kullanıcı DB’lerine yetki değişikliği yapılmadı.

Native bootstrap’ın genel non-ASCII yasağı kalktı; eski kayıttaki Unicode runtime geçerli hazırlık kanıtı olmadan kabul edilmez. Kaynak planlayıcı ve üretilen PowerShell/Bash wrapper’ları aynı kodu içerir. Paketli/code-data-state ayrımı ve SDK’sız temiz VM kurulumu bu takipte tamamlanmadı.

Teknik dayanaklar: [PostgreSQL yol hatası](https://www.postgresql.org/message-id/16926-e4ef345545d185ab@postgresql.org), [Türkçe locale düzeltmesinin 16’ya geri taşınması](https://www.postgresql.org/message-id/E1swtMA-002CCz-Sv@gemulon.postgresql.org), [Microsoft süreç bazında UTF-8 manifesti](https://learn.microsoft.com/en-us/windows/apps/design/globalizing/use-utf8-code-page).

## Kabul ve regresyon kanıtı

- Gerçek Hub, yeni yerel DB kurulum betiği, manager offline setup/reinstall, üç eşzamanlı start, repeat start, durdurma, offline Models taşıma ve restart: `C:/Users/krsta/AppData/Local/Orion/lifecycle-tests/product-yu3su1mb/result.json` **başarılı**. Kod ve veri kökü `Türkçe İıŞşĞğ 中文/Hub`; SQL Türkçe/Çince roundtrip, `pg_trgm` native DLL/extension yüklemesi, DB örnek satırı ve TLS/UUID hash koruması geçti. Kaynak modeller korundu, canlı kullanımda taşıma reddedildi. Test kurulumunda PATH/profil/Windows başlangıcı değişmedi.
- Bağımsız gerçek Router: `.tmp/lifecycle/product-lcskvj_g/result.json` **başarılı**. Aynı Unicode kök, ilk initdb, üç start/repeat/restart/stop, süreç sahipliği, sabitlenmiş TLS/UUID, API ve uygulama DB şeması geçti. Mevcut Router’a dokunulmadı.
- Önceki elle hazırlanmış Hub fixture’ı `product-dgl0s2um` da geçti; son gerçek kurulum kanıtının yerine kullanılmaz. Gerçek setup ilk koşusu `product-neftd35f` uygulama rolünün ayar okuma yetkisini yakaladı; kendi test DB’si doğrulanmış sahiplikle kontrollü kapatıldı. İlk hazırlık `product-05___rb7` imzalı StackBuilder’ı reddetti; yalnız sunucu paketi seçimiyle düzeltildi, hedef runtime yayımlanmadı.
- Runtime manifest/bağımlılık/çalıştırma seviyesi korunması, hash hatası, dolu hedef, ZIP yol kaçışı, staging hatası ve EXE değişikliği sınırları; Windows/Git Bash standalone planı; bootstrap kayıt ve Router doğrulama regresyonu: **45 test + 4 alt test geçti**, `.tmp/lifecycle/results/unicode-postgres-regression.xml`. Daha dar 24 test bununla örtüşür; toplanmaz.
- Yaşam döngüsü regresyonu: **40 geçti**, 86.72 s, `baseline-ev37wet_-lifecycle.{json,xml}`. İzole test sarmalayıcısında beklenmedik DB/pool bağlantısı 0/0. İlk sandbox koşusunda Windows Job/erişim engelleri görüldü ve kesildi; başarı diye sayılmadı. Aynı set normal kullanıcı bağlamında geçti.

- Hub/installer regresyonu: **48 test + 2 alt test geçti**, 30.60 s, `baseline-fxfjxh7p-hub.{json,xml}`; beklenmedik DB/pool bağlantısı 0/0.
- Router regresyonu: **9 test geçti**, 2.34 s, `baseline-y6xdc3eo-router.{json,xml}`; beklenmedik DB/pool bağlantısı 0/0. Önceden var olan locale/aiohttp deprecation uyarıları bu takibin hatası değildir.
- Son AST kontrolü 13 değişen Python dosyasında geçti; MD belge bağlantıları, üretilen wrapper eşitliği, Router vendored kaynak eşitliği ve `git diff --check` geçti. `.tmp/lifecycle/results/unicode-final-audit.json` kendi fixture süreçleri `[]`, sandbox test PostgreSQL süreçleri `[]` kaydeder. İlk başarısız fixture’ın kalan DB’si son süreç denetiminde yakalanıp tam Unicode yol ve süreç sahipliğiyle kontrollü kapatıldı; isim/port üzerinden sinyal gönderilmedi.

Yeni çalıştırma seçenekleri: `scripts/lifecycle/run_product_acceptance.py hub --registered-paths --setup-offline --unicode-root --postgres-archive <mutlak-arşiv-yolu> --scratch-base <ayrı-test-kökü>` ve bağımsız `router --unicode-root --postgres-archive ...`. Arşiv yalnız önceden indirilmiş fixture girdisidir; koşucu kullanıcı runtime’ını güncellemez. Son gerçek ürün/DB kabul koşuları sırayla çalıştırıldı.

## Kalan sınır ve 04’e devir

Türkçe karakter için kullanıcıdan manuel test beklenmiyor; yeni native kurulum gerçek testten geçti. Tam eski DB/runtime/Hub/Router konum taşıması hâlâ kapalıdır. Yeni runtime hazırlamak mevcut veritabanını taşımak değildir. 05’in imzalı paketleri, temiz VM/SDK’sız kabulü, 08’in mevcut kurulum dönüşümü ve 01’in açık kullanıcı/OS kabulü geçmiş sayılmaz. Yeni başlatıcı bu ayrı işlerin tamamlandığını varsaymamalıdır.
