# Hafıza v2: genel durum (8 adım + bitiriş turu)

Tasarım: [hafiza-tasarimi.md](hafiza-tasarimi.md). Her adımın devir notu: `memory-v2-step-3.md`, `memory-v2-step4.md` ... `memory-v2-step8.md`
(1 ve 2. adımın ayrı notu yok; aşağıda). Hepsi `memory_v2` bayrağının arkasında, kullanıcı başına; kapalıyken bugünkü hafıza aynen çalışır.

## 8 adımda ne geldi

1. **Temel ve kaynaklar.** Şema taşımasından önce otomatik yedek (`pg_dump`), `memory_v2` bayrağı, kaynak bağları (`orion_mem_source`),
   koleksiyonlar (Ben, Yaşadıklarım, Sohbetler, Belgeler + projeler/albümler), eski verinin geri alınabilir taşınması (Belgeler / "Eski hafıza"),
   `search_memory` `in` argümanı, sayfada koleksiyon şeridi.
2. **Çöp kutusu ve kalıcı silme.** `delete_memory` artık hiçbir şeyi hemen silmez; geri yüklenebilir çöp kutusu (30 gün), kalıcı silmede bütün kopyalar.
3. **Sohbetler dizini.** Sohbet mesajları mesaj sınırlı pencerelere bölünüp gömülür, genel + sohbet başına anahtar, "eski sohbetleri dizinle".
4. **Varlıklar ve bilgiler.** Kişi/yer/kurum/evcil/eşya + kullanıcının kendisi; tek cümlelik, sürümlü (iki zamanlı) bilgiler; hassas türler kilitli;
   geri alınabilir birleştirme + toplu kart; `add_memory type=fact`; Ben sekmesi.
5. **Olaylar ve albümler.** Tarihli olaylar (kesinlik, göreli tarih çözme), fotoğraf bağlama, albümler, yıllık tekrarlar, Yaşadıklarım zaman
   çizelgesi, `when` süzgeci.
6. **Öneriler ve görünürlük.** Tek kart makinesi (ekle, değiştir, birleştir, çöpe al), "Onay bekleyenler" ve "Son eklenenler" + Geri al.
7. **Çıkarım.** Ön-süzgeç, arka plan modeli, iş kuyruğu (iptal, yeniden deneme), **gölge mod + kalite kapısı raporu**, otomatik yazma (yalnız
   ekleme, varsayılan KAPALI), sohbet özeti, ilk hafta günlük özeti.
8. **Arama ve profil.** Onaylı sıralama politikası sayfa/panel/araçta tek kodla ve tutarlılık testiyle; `search_memory` `about`, `as_of`,
   `include_past`, `budget`, varlık kartı; her sohbete giren profil bloğu (sabitleme, 300 token sınırı, anında yeniden kurulum, Router log başlığı).

## memory_v2 nasıl açılır

1. Hub'ı güncelle; ilk açılışta şema 8'e kendisi taşınır (öncesinde otomatik yedek: Orion veri klasörü, `schema-N-to-8` dökümü).
2. Kullanıcının ayarlarında `memory_v2` = true (Ayarlar > Hafıza koleksiyonları, ya da `PUT` ayar). İlk kullanımda mevcut hafıza otomatik
   koleksiyonlara dizilir (geri alınabilir: `POST /api/v1/memory/collections/revert` bayrağı kapatır, üyelikleri boşaltır, eski satırlar yerinde).
3. Alt anahtarlar (hepsi `memory_v2` varken): `memory_index_chats` (sohbet dizini, açık), `memory_extract_chats` (gölge çıkarım, açık),
   `memory_background_model` (boş = sohbet modeli), `memory_profile` (profil bloğu, açık). Otomatik yazma ayar değil, ayrı bir anahtardır:
   `PUT /api/v1/memory/extraction/live {enabled, confirm: true}`; kalite kapısı raporunu (`GET /memory/extraction/report?format=text`) okumadan açma.
4. Önerilen sıra: bayrağı aç, eski sohbetleri dizinle, `POST /memory/extraction/backlog {limit: 20}` ile ~20 sohbeti gölgede geçir,
   raporu oku, ancak sonra otomatik yazmayı aç.

## Hız: arama neden 420-640 ms görünüyordu (bitiriş turu, 1. iş)

Ölçüm gerçek çağrı yolunda: `search_memory` aracı, `memory_v2` açık, gerçek bağlantı havuzu (`registry._connect`), tek sabit sorgu vektörü
(Router/gömme dışarıda), geçici PG, 39 500 satır, 80 kümeli 768 boyutlu vektörler (karışım: 28 000 sohbet penceresi, ~8 500 not, 1 200 bilgi,
800 olay; HNSW + trigram indeksleri, `ANALYZE` yapılmış), 20 sıcak çağrının ortancası. Betik repoda değil (scratchpad `bench_mem.py`).

**Kök neden: ölçüm düzeneği, kod değil.** 4-8. adım benchleri testlerin `registry._connect` yamasıyla (her çağrıda YENİ `asyncpg.connect`, bu PC'de
~45 ms) koştu; bir arama 4-5 bağlantı açıyor (ayarlar x2, arama, kart/geçmiş). Uygulama havuz kullanır. Aynı kod, aynı veri, yalnız bağlantı
biçimi değişince: havuz **30 ms**, her çağrıda yeni bağlantı **216 ms**. Kalan fark ve 420-640 ms'nin tam kaynağı yeniden üretilemedi
(eski bench verisi/vektör gürültüsü yok); iki gerçek ek neden bulundu:
- **İstatistiksiz tablolar.** Toplu yüklemeden sonra `orion_mem_*` tablolarına `ANALYZE` yapılmadığında planlayıcı yanlış plan seçiyor
  (`about`: bağlantı tablosunda iç içe döngü, 192 ms; `ANALYZE` sonrası 1 ms). Gerçek veritabanında autovacuum bunu bir dakika içinde yapar;
  yalnız bench/ toplu yükleme sonrası ilk dakikada görünür.
- **Yakın yazım (trigram) araması, küçük sözcük dağarcığında.** Sohbet pencereleri 60 sözcükten kurulu sentetik metinde (her satır her sorguya
  "benziyor") `<%` süzgeci satırların %12'sini geçiriyor: arama 99 ms, "sohbetlerde" 264 ms. Gerçekçi (Zipf, 8 000 sözcük) metinde aynı sorgu 9 ms.
  Yakın yazım adayları satır başına ~10 µs; bunu azaltmak sonuç ölçütünü değiştirirdi, dokunulmadı.

Yapılanlar (sonuçları değiştirmez; testli, `tests/test_memory_speed.py`):
1. Ayarlar okuması: genel + kullanıcı ayarları iki bağlantı/iki sorgu yerine tek sorgu (`registry.fetch_setting_overrides_for`); her sohbet, araç ve
   sayfa isteğinin ayar okuması buradan geçer.
2. `ann_fetch`: pgvector'un `hnsw.iterative_scan`ı olup olmadığı süreçte bir kez öğrenilir (ilk aramada savepoint ile); sonra üç ayar tek
   `select set_config(...)` olur ve savepoint yok (arama başına 3 gidiş-dönüş az; yalnız `unrecognized configuration parameter` "yok" sayılır).
3. `word_similarity` (Python, sonuç işaretleri): sözcük trigramları önbellekte; aynı sonuç (400 rastgele çiftle eski kodla karşılaştırıldı).

Aşama tablosu (ms/çağrı, `search_memory` varsayılan, gerçekçi metin; SQL gidiş-dönüş sayısı 30 → 26, havuz bağlantısı 3 → 2):

| aşama | önce | sonra |
|---|---|---|
| ayarlar (`get_runtime_settings`) | 0.7 | 0.5 |
| bağlantı alma + `ensure_memory_table` + koleksiyon (`ensure_user`, `list_collections`) | 0.2 | 0.3 |
| sorgu vektörü (sabit, gömme dışarıda) | 0.5 | 0.5 |
| ANN + sıralama (`_nearest_groups`, `rank_matches`, `_scores`) | 3.2 | 3.1 |
| kelime/yakın yazım satırları (`_text_rows`, 2 SQL) | 9.9 | 10.4 |
| güç örneklemi (`sample_scores`) | 8.1 | 8.5 |
| kartlar (`_group_rows`, `text_candidates`, `_summary`, bilgi/olay) | 1.6 | 1.6 |
| varlık kartı (`search_extras`, `named_entities`) | 0.5 | 0.5 |
| kalan (Python: işaretler, kartlar, havuz dönüşü) | 5.3 | 3.6 |
| **toplam p50** | **30** | **29** |

| durum (p50 ms) | önce | sonra |
|---|---|---|
| gerçekçi metin, havuz: varsayılan / sohbetlerde / iki sorgu / about | 30 / 32 / 29 / 23 | 29 / 28 / 27 / 20 |
| küçük sözcük dağarcığı (en kötü): varsayılan / sohbetlerde / iki sorgu | 99 / 264 / 82 | 86 / 187 / 59 |
| eski düzenek (her çağrıda yeni bağlantı), varsayılan | 216 | 150 |
| bir varlığın adını yazan sorgu ("Kişi7") | 97 | 95 |

Hedef (gömme çağrısı hariç p50 ≤150 ms, 40 bin kümeli satır, `memory_v2` açık) gerçekçi metinde ve havuzla 29 ms ile tutuyor; küçük
sözcük dağarcıklı en kötü durumda "sohbetlerde" 187 ms ile üstünde (yakın yazım taraması, yukarıdaki neden). Hâlâ ölçülmedi: Router'a bağlı
gerçek `account_settings` (hesap anahtarı önbelleği boşken), havuz boşta kapandıktan (300 sn) sonraki ilk arama (+~45 ms bağlantı), 1M satır.

## Bitiriş turu: kalan açıklar (2. ve 3. iş)

Hepsi ayrı dallarda, hiçbiri birleştirilmedi/itilmedi:
- **Router, başlıklar** (`D:/krstlcm Workspace/DevProjects/orion-router`, worktree `orion-router-memory-v2-headers`, dal `memory-v2-headers`, b1ea7f8):
  `x-orion-log-content: hidden` isteğin logunda yalnız üst veri bırakır (model, birkaç düz parametre, `purpose`, kullanım, metrikler; mesajlar, araçlar
  ve cevap `[içerik gizli]`, `content_hidden: true`); `x-orion-memory-profile: tokens=N; facts=..` ile sistem mesajındaki profil bloğu loglanan
  kopyada `[hafıza profili: tokens=N; facts=..]` olur (sağlayıcıya giden metin aynen); `x-orion-log-content: show` açık "içeriği göster" tercihidir
  (profil dahil her şey loglanır). Bozuk/bilinmeyen değerler yok sayılır; başlıklar sağlayıcıya iletilmez. Yeni 8 test; Router paketi 306 test, 2 hata
  (biri ana dalda da var, biri yalnız worktree'de dosya yolu). Log sayfası (dashboard) ayrı bir "gizli" rozeti göstermiyor, ham JSON'da işareti var.
- **Hub, sayfa araması**: `GET /api/v1/memory` artık `about`, `as_of`, `include_past` alır (araçla aynı kod: `list_groups`); cevaba `entity`,
  `entity_candidates`, `past_facts` eklenir; `about` kimseyi bulmazsa `groups: []` + `note`, birden çok kişiyse `entity_candidates` (hata değil: sayfa 409'u
  "eski arama" diye yok sayar). `memory_v2` kapalıyken yok sayılır. 5 yeni test (`PageSearchRouteTests`).
- **Flutter** (`orion-ai-assistant-flutter-finish`, dal `memory-v2-finish`, 5a1cd63): "Profil" sayfası (blok, token ölçeri, sabitle / kendi sözünle / düzenle /
  kaldır, `replaced`/`gone`/`locked` rozetleri, taşma uyarısı) ve arama satırında "Kimin hakkında", "Şu tarihte", "Eski sürümler" çipleri, varlık kartı, ayrı
  "Eski sürümler" bölümü, aday çipleri. `flutter analyze` temiz; 11 hafıza test dosyası, 118 test (20 yeni) geçti. Bilinen: dosyayla/uzun metinle arama
  (`POST /memory/search`) bu üç süzgeci taşımaz; sayfada `when` çipi `main`de yoktu (kopyalanacak çip yok), mevcut hap şeritlerinin görünümü alındı; Hub'ın
  `PUT /profile/pinned` isteği eskimiş (replaced/gone/locked) satır varken reddediyor, arayüz bu durumda serbest satır eklemeyi engelliyor.
 (kod + gerçek Postgres testleri var, çalışan uygulamada yok)

- Çalışan Hub + telefon/web arayüzünde adım 3-7'nin Flutter ekranları (kart akışları, zaman çizelgesi, Onay bekleyenler / Son eklenenler) ve gerçek
  bir sohbet modelinin araçları seçmesi; akış içinde kartlar.
- Gerçek Router üzerinden arka plan modeli (istem kalitesi, JSON disiplini, token sayıları, maliyet); kalite kapısı raporu ~20 gerçek sohbetle;
  otomatik yazma uçtan uca.
- Büyük gerçek veritabanında (on binlerce satır) arka plan indeks kurulumu ve aramanın 150 ms hedefi; ölçümler 20 bin satırlık sahte kümeli
  vektörlerle yapıldı.
- Profil bloğu: gerçek bir sohbette modelin davranışı, istem önbelleğine etkisi (OpenAI/DeepSeek). Router'ın `x-orion-memory-profile` /
  `x-orion-log-content` başlıklarını okuması yazıldı (aşağıda) ama canlı Router + gerçek sohbetle denenmedi.
- Flutter: profil sayfası ve sayfa aramasındaki `about` / `as_of` / `include_past` yazıldı (aşağıda), analiz + birim/widget testleri geçti; çalışan
  Hub'a karşı ve gözle (web/telefon) bakılmadı.
- Çok süreçli çalışma (API ve Worker süreçlerinin ayrı çıkarım işçileri), Hub yeniden başlarken bekleyen zamanlayıcılar.
- Sohbet/mesaj silme kartı (§11.3), "Bilgi çıkar" düğmesi (belgeler), eski hafıza öneri geçişi, haritada varlık düğümleri, misafir hesapları:
  tasarımın "Sonra" listesinde, yapılmadı.
