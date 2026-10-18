# İki feed, bileşen ekleme, kaldırma (19 Ekim 2026)

Sahibin kararları ve karşılıkları. Ayrıntı: `scripts/release/README.md` ("Two feeds", "Install, add a component, uninstall"); denemeler: `08-manual-test.md` son bölüm.

* **Router'ın kendi feed'i** (`orion-ai-assistant/orion-router`, tag `feed`), aynı anahtar ve aynı `trusted-root.json`. `publish_release.py --feed hub|router` feed başına yayınlar; metadata sürümü feed başına sayılır; her feed'in kendi bootstrap paketi vardır. Hub feed'i artık Router hedefi içermez.
* **`release-source.json` şema 2** = feed listesi (şema 1 aynen geçerli). Her feed kendi `ReleaseClient`/TUF önbelleğiyle denetlenir; yeni sürümler **tek** işlemde birleşir (`compatible()` birleşik küme + kurulu sequence'lar, tek yeniden başlatma); erişilemeyen feed diğerini engellemez. Çalışan bootstrap kendi paketindeki kaynak listesini kuruluma birleştirir (mevcut Hub kurulumları Router feed'ini böyle öğrenir).
* **Router tek başına**: `get-orion-router.ps1/.cmd` (Router deposu). Kimlik "Orion Router" (kısayol, `OrionRouter.exe` açıklaması, Uygulamalar girdisi).
* **Ekleme**: `install_new.py` kayıt varsa *add* yoluna girer (`services/shared/component_setup.py`); journal + geri alma; mevcut bileşenlere dokunulmaz.
* **Kaldırma**: `services/shared/uninstall.py` + `uninstall.ps1` (Uygulamalar girdisi, tepsi); `package_maintenance` üzerine kurulu; veri varsayılan korunur ve işaretlenir (yeniden kurulum devralır); güvenlik duvarı UAC'si reddedilebilir.
* **Başlangıç adı hatası**: Explorer `StartupApproved\StartupFolder` değerini bağlantı *adına* göre tutar ve silmez; eski `Orion.lnk` kalıntısı Görev Yöneticisi'nde hayalet "Orion.exe" satırı yapıyordu. `clean_startup_approved` kurulum/güncelleme (`refresh_links`) ve kaldırmada Orion'a ait yetim değerleri siler.

Yayın sırası: Router feed → Hub feed. Sürümler: Hub 0.1.10 (seq 10), bootstrap 0.1.4 (seq 5), Router 0.1.9 (seq 7).
