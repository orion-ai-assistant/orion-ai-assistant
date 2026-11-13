# Architecture

Project architecture documentation for Orion AI Assistant.

## Veri ve bellek (2026-11-13)

- **PostgreSQL tek kayıttır.** Sohbetler, mesajlar, medya (`orion_chat_media`) orada. Redis yalnız canlı durum
  tutar (akan yanıt, kilitler, bildirimler); sohbet geçmişinin Redis kopyası yoktur.
- Listeler ve geçmiş sayfa sayfa okunur (anahtar sırasıyla sayfalama, geçmiş pencereleri); istemci medyayı
  önbelleğe alır (medya yanıtları değişmez, uzun süre önbelleklenir).
- Hub'ın PostgreSQL'i (port 5445) ve Router'ınki (port 5433) **küçük bellekle** başlar:
  `shared_buffers=32MB`, `max_connections=50`.
  - Neden: Windows `shared_buffers`'ı açılışta tümüyle ayırır (varsayılan 128 MB, sunucu başına) ve bu
    Windows'un bellek sınırına sayılır. 3-10 kullanıcı için 32 MB yeter; verinin geri kalanını Windows'un dosya
    önbelleği tutar. İkisi birlikte ~190 MB daha az ayırır.
  - Bağlantılar: Hub havuzu en fazla 8 (`kernel/registry.py`, boşta 5 dk sonra kapanır), Router havuzu en fazla
    20 (`database/manager.py`). 50 bunlara rahat yeter.
  - Ayar komut satırında verilir (`postgresql.conf`'a yazılmaz):
    `services/shared/native_postgres.py` ve Router'da `core/native_postgres.py` içindeki `MEMORY_SETTINGS`.
    İki dosya birebir aynı kalmalı.
  - Vektör veritabanı (pgvector) gelince de bu ayar uygundur. Dizin büyürse (yüz binlerce vektör) önce
    `shared_buffers`'ı 64-128 MB'a çıkarmak düşünülür; HNSW dizini kurarken geçici `maintenance_work_mem`
    yükseltmek daha çok fark eder.
