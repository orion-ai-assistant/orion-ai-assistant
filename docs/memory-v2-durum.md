# Hafıza v2: genel durum (8 adımın sonu)

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

## Hiç canlı denenmedi (kod + gerçek Postgres testleri var, çalışan uygulamada yok)

- Çalışan Hub + telefon/web arayüzünde adım 3-7'nin Flutter ekranları (kart akışları, zaman çizelgesi, Onay bekleyenler / Son eklenenler) ve gerçek
  bir sohbet modelinin araçları seçmesi; akış içinde kartlar.
- Gerçek Router üzerinden arka plan modeli (istem kalitesi, JSON disiplini, token sayıları, maliyet); kalite kapısı raporu ~20 gerçek sohbetle;
  otomatik yazma uçtan uca.
- Büyük gerçek veritabanında (on binlerce satır) arka plan indeks kurulumu ve aramanın 150 ms hedefi; ölçümler 20 bin satırlık sahte kümeli
  vektörlerle yapıldı.
- Profil bloğu: gerçek bir sohbette modelin davranışı, istem önbelleğine etkisi (OpenAI/DeepSeek), Router logunda `x-orion-memory-profile`
  başlığının gösterilmesi (Router bu başlığı henüz okumuyor; içeriği gizleme Router deposunda yapılacak iş).
- Profil için arayüz yok (Ayarlar > profil: görüntüle, düzenle, sabitle): yalnız API var (`/api/v1/memory/profile`).
- Sayfanın arama kutusunda `about` / `as_of` / `include_past` yok; yalnızca model aracında (sayfa aynı sıralama kodunu kullanır).
- Çok süreçli çalışma (API ve Worker süreçlerinin ayrı çıkarım işçileri), Hub yeniden başlarken bekleyen zamanlayıcılar.
- Sohbet/mesaj silme kartı (§11.3), "Bilgi çıkar" düğmesi (belgeler), eski hafıza öneri geçişi, haritada varlık düğümleri, misafir hesapları:
  tasarımın "Sonra" listesinde, yapılmadı.
