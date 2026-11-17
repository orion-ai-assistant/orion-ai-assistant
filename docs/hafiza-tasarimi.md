# Orion hafıza tasarımı (gerçek kullanım için)

Durum: tasarım kullanıcı tarafından onaylandı (12 soru kararlandı, §15). Tek açık konu: sıralama politikası (§9.1),
öneri olarak duruyor. Kod değişikliği yok. Sıra: açık konular → bu belge → onay → 8 adım (§16).

## 1. Neden değişmeli?

Bugünkü hafıza tek bir yığın. Her şey `orion_memory` tablosunda satır:
not, sohbet kaydı, resim, ses, video, dosya. Bir "hafıza" (`group_id`) birlikte kaydedilen parçaları toplar.
Arama, vektör (pgvector HNSW, tür başına kısmi indeks) + kelime (pg_trgm) ile yapılıyor.
Uzun içerik çekirdek + bağlam pencerelerine bölünüyor, bir de "bütün" vektörü var. Medya sha256 ile tek kopya.
Sohbet parçaları ilk/son mesaj id'sini tutuyor. Model araçları: `search_memory`, `open_memory_files`,
`memory_context`, `add_memory`, `delete_memory`.

Bu altyapı iyi. Eksik olan **anlam katmanı**:

- "Annem kim, doğum günü ne zaman?" sorusu bir pencere parçasında aranıyor. Kısa, net bir bilgi yok.
- Bilgi değişince (taşındım, işim değişti) eski satır ya kalıyor ya siliniyor. Geçmiş yok, "şu an doğru olan" yok.
- Atatürk hakkında bir sohbet ile "kızımın adı Elif" aynı yığında. Kişisel soru genel bilgiyle kirleniyor.
- "Bugün" diye kaydedilen olayın tarihi metnin içinde kayboluyor.
- Test verisinde bir hafızada çok resim/metin var. Gerçekte bir hafıza tek bir şeydir: bir dosya, bir olay, bir bilgi.

Bu belge, var olanın üstüne kurulacak bir yapı. Vektör tablosu, parçalama, medya deposu, model taşıma
(`memory_move`), harita, sayaçlar aynen kalır.

## 2. Başkaları nasıl yapıyor, ne alıyoruz?

Kaynaklar bu oturumda yeniden açılmadı; özetler yayımlanmış makale ve duyurulara dayanır.

| Sistem | Nasıl çalışıyor | Bizim aldığımız |
|---|---|---|
| **Mem0** (Chhikara ve ark., 2025, arXiv:2504.19413) | Sohbetten LLM ile kısa "bilgi"ler çıkarır. Her yeni bilgi için benzer eskileri bulur, LLM'e ADD / UPDATE / DELETE / NOOP seçtirir. Graf sürümü (Mem0g) varlık + ilişki tutar. | Çıkarım + "var olanla karşılaştır" adımı, JSON işlem listesi. **Otomatik yazma yalnızca ekler**; UPDATE/DELETE karşılığı kart olur (geçmiş kaybolmasın). |
| **Zep / Graphiti** (Rasmussen ve ark., 2025, arXiv:2501.13956) | Zamansal bilgi grafı. Ham "episode"lar (mesajlar) saklanır; onlardan varlık düğümleri ve olgu kenarları çıkar. Her kenarda iki zaman: olayın geçerli olduğu aralık (`valid_at`/`invalid_at`) ve sisteme giriş/çıkış (`created_at`/`expired_at`). Çelişen eski kenar silinmez, geçersiz yapılır. Arama: vektör + BM25 + graf komşuluğu, sonra yeniden sıralama (RRF, MMR). Göreli tarihler kaydedilirken mutlak tarihe çevrilir. | **İki zamanlı (bi-temporal) bilgiler**, varlık düğümleri, her bilginin kaynağına (episode = mesaj aralığı) bağlanması, göreli tarihi kayıtta çözme, karma arama + sıra birleştirme. |
| **ChatGPT memory** (OpenAI, Şub 2024; sohbet geçmişine başvurma Nis 2025) | İki ayrı şey: "kayıtlı anılar" (kısa cümleler, kullanıcı görür/siler, "Hafıza güncellendi" bildirimi) ve geçmiş sohbetlere başvurma. Geçici sohbet hafızaya yazmaz. | **Kullanıcının gördüğü, kısa cümle bilgiler**; "son eklenenler" ve Geri al; sohbet arşivi ayrı; sohbet başına "hafızaya alma" anahtarı. |
| **Letta / MemGPT** (Packer ve ark., 2023, arXiv:2310.08560) | Bağlamda hep duran küçük "çekirdek bellek blokları" (kişi, kullanıcı), dışarıda büyük "arşiv" ve "geçmiş" belleği. Model kendi belleğini araçlarla düzenler; sohbet uzayınca özetlenir. | Küçük, **hep hazır profil bloğu** (§9.2), arşiv/sohbet ayrımı, uzayan sohbetin özetlenmesi. Modelin serbest yeniden yazması **alınmıyor**: bizde model silemez, üzerine yazamaz. |

Ortak ders: ham kaynak (mesaj, dosya) dokunulmadan kalır; ondan türeyen kısa bilgiler ayrı tutulur ve kaynağına
bağlanır. Arama önce kısa bilgilere, gerekirse ham kaynağa bakar.

## 3. Koleksiyonlar

Tek yığın yerine birkaç koleksiyon. Her öğe tam olarak bir ana koleksiyonda durur.

| Koleksiyon | İçerik | Ne zaman aranır |
|---|---|---|
| **Ben** | Kim olduğum: aile, arkadaşlar, tercihler, sağlık, iş, adres. Küçük, tek cümlelik bilgiler; her biri bir varlığa ("Annem", "Elif", "İşyerim", "Ben") bağlı. | Kişisel her soruda. |
| **Yaşadıklarım** | Tarihli olaylar: gezi, buluşma, doğum günü kutlaması, günlük. Fotoğraf/video çoğunlukla buraya bağlanır. | "Ne zaman…", "geçen yaz…", kişiyle ilgili son olaylar. |
| **Sohbetler** | Asistanla yapılan sohbetlerin arşivi. Otomatik dizinlenir. | Yalnızca istenince: "bunu konuşmuştuk", "geçen hafta ne demiştin". Kişisel aramaya karışmaz. |
| **Belgeler** | Dosyalar: PDF, metin, tablo. Bir dosya = bir düğüm, sayfalar = parçalar. **Çok parçalı tek kayıt türü bu.** | Belge sorulunca veya içerik eşleşince. |
| **Projeler / albümler** (kullanıcının) | Kullanıcının açtığı adlı koleksiyonlar: "Tez", "Ev tadilatı", "Kapadokya 2026". Belge, not, olay, medya içerebilir. | Adı geçince veya seçilince. |

Atatürk sohbeti: Sohbetler'de kalır. Ondan **bilgi çıkarılmaz**: ön-süzgeç kişisel olmayan sohbeti eler, çıkarım
yalnızca kullanıcının kendisi ve çevresi hakkında söylediklerini alır (§7). Kişisel arama Sohbetler'e bakmaz.

**Duygu ve düşünceler** ayrı koleksiyon değil (karar 1). "Kahveyi sevmem" Ben'de `tercih`, "işimden sıkıldım"
Ben'de `görüş` türü bilgi. Günlük gibi yazılanlar ("bugün çok yorgundum") Yaşadıklarım'da olay, `mood` alanıyla.
Aynı geçmiş kuralı duygulara da işler: "eskiden sevmezdim, artık seviyorum" iki sürüm olur.

**Bir hafıza = bir şey** (karar 9). Bir bilgi, bir olay, bir dosya, bir not, bir fotoğraf. Albüm bir koleksiyondur:
tek tek kayıtlar koleksiyona üye olur, sayfada birlikte gösterilir. Kayıtların `group_id`'si değişmez, bu yüzden
`open_memory_files` ve `delete_memory(whole_memory=true)` bugünkü gibi çalışır: "bütün hafıza" bir kaydın kendisidir,
albümün tamamı değil. Bir kayıt birden çok albümde olabilir (üyelik tablosu), ana koleksiyonu tektir.

## 4. Kavramlar

- **Varlık (entity):** kişi, yer, kurum, evcil hayvan, eşya, ve kullanıcının kendisi (`self`). Adı, takma adları
  ("annem", "Ayşe", "anne"), kullanıcıyla ilişkisi. Varlık tek başına bilgi taşımaz; bilgiler ona bağlanır.
- **Bilgi (fact):** tek, kısa, kendi başına anlaşılır cümle. "Annemin doğum günü 17 Kasım." Bir varlığa bağlı.
  Türü: `kimlik`, `ilişki`, `tercih`, `görüş`, `iş`, `yer`, `tarih`, `diğer` ve hassas türler (§11.1).
- **Olay (event):** tarihli bir yaşantı. Tarih + kesinlik (`precision`: gün/ay/yıl/yaklaşık), katılan varlıklar,
  yer, isteğe bağlı medya.
- **Öğe (item):** bugünkü anlamıyla içerik: not, dosya, resim, ses, video, sohbet penceresi. Parçalara bölünür,
  vektörleri `orion_memory`'de.
- **Kaynak (source):** bir bilginin/olayın nereden geldiği: sohbet + ilk/son mesaj id, bir öğe, ya da "kullanıcı
  sayfada yazdı". **Kaynaksız bilgi yazılmaz.** Kaynak bağları yapılacak ilk şey (§16 adım 1).
- **Öneri (proposal):** onay bekleyen değişiklik. Onay kartı olarak görünür; birkaç öneri tek kartta toplanabilir.

### Değişen bilgi, kaybolmayan geçmiş

Hiçbir bilgi üzerine yazılmaz. Değişince yeni sürüm eklenir, eski sürüm kapanır (Graphiti'deki gibi iki zaman):

- `valid_from` / `valid_to`: dünyada ne zaman doğruydu. ("2019'dan 2026 Ekim'e kadar Ankara'da yaşadı.")
- `recorded_at` / `expired_at`: Orion bunu ne zaman öğrendi, ne zaman geçersiz saydı.
- `superseded_by`: eski sürüm yenisini gösterir.

"Şu an doğru olan" = `valid_to is null and expired_at is null and deleted_at is null`. "Eskiden" sorusu kapalı
sürümleri de alır. "2022'de neredeydim?" = o tarihte geçerli olan sürüm.

Geçmişi kapatmak (bir sürümü geçmişe almak) **hiçbir zaman otomatik değildir**: çıkarım bir çelişki görürse kart
açar (§8). Kullanıcı sayfada veya kartla onaylar.

Örnek, taşınma: "İstanbul'a taşındım" → kart: "Ankara'da yaşıyor → İstanbul'da yaşıyor (1 Kasım 2026'dan beri)?".
Onaylanınca eski bilgiye `valid_to`, `superseded_by`; yeni bilgi `valid_from`. Sayfada Ankara gri, "geçmiş" altında.

Örnek, "bugün annemin doğum günüydü" (17 Kasım 2026'daki bir mesajda):
1. **Olay** (Yaşadıklarım): "Annemin doğum günü", tarih 2026-11-17 (`precision = day`), varlık: Annem.
2. **Bilgi** (Ben, Annem'e bağlı): "Annemin doğum günü 17 Kasım.", `recurring = yearly:11-17`.
   Zaten varsa eklenmez (yinelenen); yalnızca olay eklenir.

**Göreli tarih** (karar 11): "bugün", "dün", "geçen cuma" **mesajın kendi zamanına** ve kullanıcının saat dilimine
göre mutlak tarihe çevrilir (sohbet dün yazıldıysa "bugün" dündür). Model ISO tarih yazar, Hub mesaj zamanına göre
doğrular. Orijinal ifade (`said_as`) ve kesinlik (`precision`) saklanır; "geçen yaz" `approx` olur.

## 5. Veri modeli (taslak SQL)

İlke: **tek vektör tablosu kalır ve hiç silinmez.** Bilgi ve olayların vektörleri de `orion_memory`'de satır olur.
Böylece model taşıma, `vector_space`, sayaçlar, tür başına kısmi HNSW, kelime araması tek kodla çalışır. Yeni tablolar
yalnızca anlamı taşır. Her yeni tabloda `user_id` var ve her sorgu onunla süzülür (kullanıcı ayrımı testleri, §16).

```sql
-- Koleksiyonlar: her kullanıcıya hazır 4 tane + projeler/albümler.
create table orion_mem_collection (
    id          text primary key,              -- 'ben','yasadiklarim','sohbetler','belgeler' veya 'p_<uuid>'
    user_id     text not null,
    kind        text not null,                 -- builtin | project | album
    name        text not null,
    deleted_at  timestamptz,
    created_at  timestamptz not null default now()
);
-- Albüm/proje üyeliği: bir kayıt (group_id) birden çok albümde olabilir.
create table orion_mem_collection_item (
    user_id text not null, collection_id text not null, group_id text not null,
    position bigint, added_at timestamptz not null default now(),
    primary key (collection_id, group_id)
);

-- Varlıklar.
create table orion_mem_entity (
    id          bigserial primary key,
    user_id     text not null,
    kind        text not null,                 -- person | place | org | pet | thing | self
    name        text not null,                 -- "Ayşe"
    relation    text,                          -- "annem" (kullanıcıya göre)
    aliases     text[] not null default '{}',  -- {"anne","annem","Ayşe Hanım"}; birleştirmede birikir
    folded      text,                          -- küçük harf, aksansız: ad + takma adlar (trigram araması)
    merged_into bigint references orion_mem_entity(id),
    deleted_at  timestamptz,
    created_at  timestamptz not null default now()
);
create index on orion_mem_entity using gin (folded gin_trgm_ops);

-- Birleştirme kaydı: geri alınabilsin diye taşınan bağlar tutulur.
create table orion_mem_entity_merge (
    id bigserial primary key, user_id text not null,
    from_id bigint not null, into_id bigint not null,
    moved jsonb not null,                      -- {"facts":[..], "links":[..], "aliases":[..]}
    merged_at timestamptz not null default now(), undone_at timestamptz
);

-- Bilgiler: iki zamanlı, sürümlü.
create table orion_mem_fact (
    id            bigserial primary key,
    user_id       text not null,
    entity_id     bigint not null references orion_mem_entity(id),
    category      text not null,             -- kimlik|ilişki|tercih|görüş|iş|yer|tarih|diğer|<hassas türler>
    statement     text not null,             -- "Annemin doğum günü 17 Kasım."
    value         jsonb,                     -- isteğe bağlı yapı: {"date":"--11-17"} {"city":"İstanbul"}
    recurring     text,                      -- 'yearly:11-17'
    valid_from    date, valid_to date,       -- dünyadaki geçerlilik (bilinmiyorsa null)
    recorded_at   timestamptz not null default now(),
    expired_at    timestamptz,
    superseded_by bigint references orion_mem_fact(id),
    sensitive     boolean not null default false,  -- hassas tür ise true (§11.1)
    origin        text not null,             -- user | model | extraction
    run_id        bigint,                    -- otomatik çıkarımdan geldiyse (Geri al, günlük özet)
    collection_id text not null default 'ben',
    deleted_at    timestamptz                -- çöp kutusunda
);
create index on orion_mem_fact (user_id, entity_id)
    where expired_at is null and valid_to is null and deleted_at is null;

-- Olaylar.
create table orion_mem_event (
    id            bigserial primary key,
    user_id       text not null,
    title         text not null,
    body          text,
    occurred_on   date, occurred_end date,
    precision     text not null default 'day',   -- day | month | year | approx
    said_as       text,                          -- "bugün", "geçen yaz"
    mood          text,
    recorded_at   timestamptz not null default now(),
    expired_at    timestamptz,
    superseded_by bigint references orion_mem_event(id),
    origin        text not null, run_id bigint,
    collection_id text not null default 'yasadiklarim',
    deleted_at    timestamptz
);
create index on orion_mem_event (user_id, occurred_on) where deleted_at is null;

-- Olay <-> varlık ve olay <-> öğe (fotoğraf, video) bağları.
create table orion_mem_link (
    user_id   text not null,
    from_type text not null, from_id bigint not null,   -- 'event'
    to_type   text not null, to_id   text   not null,   -- 'entity' | 'item'
    role      text,                                     -- 'katılan', 'yer', 'fotoğraf'
    primary key (from_type, from_id, to_type, to_id)
);

-- Kaynak: her bilgi/olay en az bir kaynakla yazılır. Silme kartları bunun üzerinden kurulur.
create table orion_mem_source (
    user_id      text not null,
    target_type  text not null, target_id bigint not null,   -- 'fact' | 'event'
    chat_id      text, first_message bigint, last_message bigint,
    item_id      text,                                       -- bir öğeden (dosya/not) çıkarıldıysa
    by_user      boolean not null default false,             -- kullanıcı sayfada yazdı
    created_at   timestamptz not null default now()
);
create index on orion_mem_source (user_id, chat_id);
create index on orion_mem_source (user_id, item_id);

-- Onay bekleyen değişiklikler (kartlar). batch_id: birkaç öneri tek kartta.
create table orion_mem_proposal (
    id          bigserial primary key,
    user_id     text not null,
    batch_id    bigint,
    action      text not null,     -- add | supersede | expire | trash | merge_entities | move | restore
    payload     jsonb not null,    -- eski/yeni hali, hedef id'ler, kaynaklar
    reason      text,
    chat_id     text, message_id bigint,
    state       text not null default 'pending',  -- pending | accepted | rejected | edited
    created_at  timestamptz not null default now(),
    decided_at  timestamptz
);

-- Çıkarım koşuları ve adayları (gölge mod da burada; §7.4).
create table orion_mem_extraction_run (
    id bigserial primary key, user_id text not null, chat_id text not null,
    first_message bigint, last_message bigint,
    mode text not null,                -- shadow | live
    prefilter text,                    -- passed | dropped:<neden>
    state text not null,               -- queued | running | done | cancelled | failed
    model text, tokens_in int, tokens_out int,
    started_at timestamptz, finished_at timestamptz
);
create table orion_mem_candidate (
    id bigserial primary key, user_id text not null, run_id bigint not null,
    op jsonb not null,                 -- add_fact / add_event / supersede / merge ...
    bucket text not null,              -- direct | ambiguous   (çıkarsanan hiç kaydedilmez)
    unsupported_risk text,             -- low | medium | high: kaynakta açıkça yazmıyor olabilir
    dedupe text not null,              -- new | duplicate_of:<id> | conflicts_with:<id>
    merge_target bigint,               -- eşleştiği varlık
    outcome text                       -- written | carded | skipped | rejected_by_user
);

-- Profil bloğu: üretilen kısım + kullanıcının sabitlediği düzenlemeler.
create table orion_mem_profile (
    user_id     text primary key,
    pinned      jsonb not null default '[]',  -- [{"text": "...", "fact_id": 12|null}] kullanıcının yazdığı/sabitlediği
    generated   jsonb not null default '[]',  -- [{"fact_id": 31, "text": "..."}]
    tokens      int, built_at timestamptz
);

-- Sohbet başına durum.
create table orion_mem_chat_state (
    user_id        text not null,
    chat_id        text primary key,
    memory_enabled boolean not null default true,  -- sohbet başına anahtar
    indexed_upto   bigint,
    extracted_upto bigint,
    summary        text, summary_upto bigint,
    summary_stale  boolean not null default false
);
```

`orion_memory` (vektör/parça tablosu) sütun alır, hiçbir şey silinmez:

```sql
alter table orion_memory add column collection_id text;      -- null = bugünkü davranış (taşıma öncesi)
alter table orion_memory add column owner_type text;         -- 'fact' | 'event' | null (öğeler)
alter table orion_memory add column owner_id bigint;
alter table orion_memory add column current boolean not null default true;  -- sürüm geçmişe alınınca false
alter table orion_memory add column deleted_at timestamptz;  -- çöp kutusu
-- kind yeni değerler alır: 'fact', 'event', 'chat'. Tür başına kısmi HNSW indeksi onlara da açılır,
-- koşulu "where current and deleted_at is null". Koleksiyon başına kısmi indeks yalnızca büyüklere: sohbetler, belgeler.
```

Eski sürümün vektör satırı silinmez, `current = false` olur; geçmiş sürümler "eskiden" sorularında tam taramayla
(az sayıda) bulunur.

## 6. Ne gömülür (embedding), ne gömülmez

| Tür | Gömülen metin | Not |
|---|---|---|
| Bilgi | Varlık adı açık cümle: "Annem (Ayşe): doğum günü 17 Kasım." | Kısa, tek vektör. Zamir yok, tek başına okunur. |
| Olay | "2026-11-17 Salı, Kasım 2026: Annemin doğum günü. Katılanlar: Annem, Elif. …" | Tarih filtresi SQL'de (`occurred_on`). Uzun günlük bugünkü pencerelerle bölünür. |
| Sohbet | Mesaj sınırına oturan pencereler (~300 token çekirdek, komşu mesajlardan bağlam). Her mesajın başında "Kullanıcı:"/"Asistan:". | Bir mesaj pencereden uzunsa yalnız o bölünür. Araç çıktıları gömülmez. Pencere ilk/son mesaj id'sini tutar (var). |
| Belge | Sayfa başına parça; uzun sayfa bugünkü pencerelerle. Dosyanın bütün vektörü kalır. | Bir dosya = bir düğüm. |
| Medya | Bugünkü gibi (resim, ses, video kare/ses/birleşik). | Olaya/albüme bağlantıyla eklenir. |
| Ad, başlık, dosya adı, takma ad | **Gömülmez.** Yalnızca kelime/trigram (`folded`). | "Annem" araması ada ve takma adlara kelimeyle oturur. |

Model değişince (`memory_move`) bilgi ve olay vektörleri de bugünkü gibi yeni alana kopyalanır.

## 7. Dizinleme, çıkarım ve kalite kapısı

### 7.1 Sohbetler dizini (karar 6)

Otomatik ve LLM'siz: yeni mesajlar pencerelere bölünüp gömülür (`indexed_upto`). Genel anahtar (Ayarlar) + sohbet
başına anahtar (`memory_enabled`, ChatGPT'deki geçici sohbet gibi). Anahtar kapalı sohbet ne dizinlenir ne çıkarıma girer.

### 7.2 Çıkarım akışı

1. **Tetik:** sohbet kapanınca veya boşta kalınca. Süren sohbette yalnızca `extracted_upto`'dan sonraki yeni mesajlar
   işlenir; daha önce çıkarılanla yinelenen elenir.
2. **Ön-süzgeç (ucuz, LLM'siz):** yalnızca kullanıcı mesajlarına bakar: birinci tekil şahıs/aidiyet işaretleri
   ("ben, benim, annem, kızım, I, my"), bilinen varlık adı/takma adı eşleşmesi, tarih ifadeleri. Hiçbiri yoksa sohbet
   elenir ve nedeni koşu kaydına yazılır (`prefilter = dropped:<neden>`). Dil listesi genişletilebilir. Fazla mı
   eliyor, gölge mod raporunda görülür (§7.4).
3. **Girdi:** yeni mesajlar (id'leriyle), her mesajın zamanı, saat dilimi, ve geçen varlıkların güncel bilgileri
   (ad eşleşmesi, sonra vektörle en yakın birkaç bilgi). Hassas türlerin içeriği girdiye konmaz, yalnızca "bu
   varlıkta kilitli bilgi var" işareti.
4. **Çıktı (JSON):** her aday için işlem, kaynak mesaj aralığı ve kova:
   ```json
   {"entities": [{"ref": "e1", "name": "Ayşe", "relation": "annem", "match": 12}],
    "ops": [
      {"op": "add_event", "title": "Annemin doğum günü", "date": "2026-11-17", "precision": "day",
       "said_as": "bugün", "entities": ["e1"], "messages": [4410, 4412], "bucket": "direct"},
      {"op": "add_fact", "entity": "e1", "category": "tarih", "statement": "Annemin doğum günü 17 Kasım.",
       "recurring": "yearly:11-17", "messages": [4410, 4410], "bucket": "direct"},
      {"op": "supersede", "fact": 88, "statement": "İstanbul'da yaşıyor.", "valid_from": "2026-11-01",
       "messages": [4420, 4421], "bucket": "direct"}
    ]}
   ```
5. **Kovalar** (karar 2):
   - **direct:** kullanıcı açıkça söyledi, anlamı ve kime ait olduğu net → kalite kapısından geçtiyse otomatik
     yazılabilir (yalnızca ekleme).
   - **ambiguous:** söylendi ama anlamı, kapsamı ya da kimden bahsettiği belirsiz ("Ayşe" hangi Ayşe?) → öneri kartı.
   - **çıkarsanan** (hiç söylenmedi, LLM tahmini) → hiçbir yere yazılmaz, kart da açılmaz. İstem bunu ister; Hub
     her adayın kaynak aralığının gerçekten var olduğunu ve kullanıcı mesajı içerdiğini denetler.
6. **Uygulama kuralları:**
   - Yazmadan önce yinelenme kontrolü (aynı varlıkta aynı/çok yakın bilgi → yazılmaz, kaynak eklenir).
   - Otomatik yazma **yalnızca ekler**. `supersede`, çelişki, birleştirme → kart. Geçmiş kendiliğinden kapanmaz.
   - Hassas türler hiçbir zaman otomatik yazılmaz; en fazla kart (ve yalnız kullanıcı açıkça kendisi söylediyse).
   - Her yazılan şeyin en az bir `orion_mem_source` kaydı ve `run_id`'si olur.
7. **Özet:** aynı geçiş sohbetin kısa özetini günceller. Kaynak mesajlardan biri silinirse `summary_stale`,
   sonraki geçişte yeniden yazılır.
8. **İş:** her koşu bir iş: sırada/çalışıyor/bitti, iptal edilebilir. Router logunda yalnızca üst veri (sohbet id,
   mesaj aralığı, token, süre, aday sayıları); içerik gizli.
9. **Model:** Router'da "arka plan modeli" ayarı, varsayılan sohbet modeli (karar 8).

### 7.3 Görünürlük

- **Son eklenenler:** sayfada ve profil yanında, otomatik yazılan her şey kaynağıyla, tek tıkla **Geri al**.
- Otomatik yazma açıldıktan sonraki **ilk hafta**: her gün bir özet kartı ("Dün hafızaya 4 bilgi, 2 olay eklendi").

### 7.4 Kalite kapısı (otomatik yazmadan önce)

Otomatik yazma, kullanıcı gölge mod sonuçlarını görüp onaylamadan açılmaz.

- **Deneme seti:** ~20 çeşitli sohbet: kısa/uzun, kişisel/genel karışık, göreli tarihler, belirsiz adlar
  (iki Ayşe), hassas konular, birden çok dil.
- **Tam gölge mod:** koşular `mode = shadow`; adaylar `orion_mem_candidate`'e yazılır, gerçek hafızaya hiçbir şey
  yazılmaz (onay olmadan).
- **Rapor (her aday için):** kaynak aralığı, bilgi, direct/ambiguous, desteksiz iddia riski, yinelenme durumu,
  birleştirme hedefi. Ayrıca **hiçbir şey çıkarılmayan** ve **ön-süzgeçte elenen** sohbetler nedenleriyle listelenir.
- Kullanıcı onaylarsa `live` moda geçilir; §7.3'teki ilk hafta özetleriyle kademeli.

## 8. Model izinleri

| Model ne yapabilir | Nasıl |
|---|---|
| Yeni bilgi / olay ekleme (açıkça söylenmiş, net) | Doğrudan; sohbette iz + "son eklenenler" + Geri al. Otomatik çıkarımda yalnızca kalite kapısından sonra. |
| Belirsiz bir şeyi ekleme | Kart. |
| Çıkarsanan, söylenmemiş bilgi | **Hiçbir yere.** |
| Var olan varlığa bilgi ekleme, olaya fotoğraf bağlama | Doğrudan (yukarıdaki kurallarla). |
| Bir bilgiyi geçmişe alma / çelişki | **Kart.** Otomatik kapatma yok. |
| Hassas bilgi | En fazla kart; kullanıcı açıkça kendisi söylediyse. |
| Varlık birleştirme | Kart (toplu kart olabilir), geri alınabilir. |
| Var olan metni yeniden yazma | **Yok.** Düzeltme = yeni sürüm. |
| Kaldırma (çöp kutusu) | **Yok.** Model yalnızca önerir (`forget_memory` → kart). |

Kullanıcı sayfada her şeyi yapabilir; onun değişikliği kart açmaz.

## 9. Arama

- **Koleksiyon hedefi:** model `in` ile seçer. `in` verilmezse bugünkü davranış (tüm hafıza). v2 bayrağı açıkken
  araç açıklaması modele "kişisel soru → ben/yasadiklarim; daha önce konuşulan → sohbetler; genel bilgi → hafızaya
  bakma" der. Puana bağlı "zayıfsa Sohbetler'e de bak" eşiği yok.
- **Varlık merkezli:** sorgu bir varlık adına/takma adına kelimeyle oturursa ("annem") önce o varlığın kartı gelir:
  güncel bilgiler, son olaylar, geçmiş sürüm sayısı.
- **Zaman:** `when` aralığı olaylarda `occurred_on`, bilgilerde geçerlilik aralığıyla süzülür. `as_of` verilirse o
  tarihte geçerli sürümler.
- **Token bütçesi:** `budget` verilirse sonuçlar bütçeye sığdığı kadar (önce bilgiler, sonra olaylar, sonra
  parçalar); verilmezse bugünkü sabit sayı. Her sonuçta id var; devamı `memory_context` ile açılır.
- **Hassas bilgiler** aramada dönmez; yalnızca kullanıcı doğrudan sorduğunda (`sensitive: true`, §11.1).
- **Çöp kutusundakiler** hiçbir aramada, profilde, haritada dönmez.
- Sayfa, harita ve araç aynı arama işlevini kullanır (bugünkü `memory_groups.search_queries` genişler).

### 9.1 Sıralama politikası (ÖNERİ, karar bekliyor)

İstenenler: karışık modda beş tür (bilgi, olay, metin parçası, medya, sohbet penceresi) için **tek politika**;
anlam + kelime gerçekten birleşsin; tarih katkısı sınırlı (sorguda zaman niyeti yoksa yalnızca eşitlik bozucu);
türe özel yapay normalizasyon yok; gösterilen % gerçek benzerlik kalsın ve sıra ile % ilişkisi arayüzde
açıklanabilsin; "benzemesin" anlamı aynı kararın parçası; aynı sorgu + veri + süzgeç → her yerde aynı sıra.

**Karar (kullanıcı):** Türler ayrı bölümlere ayrılmaz ve türler arası yapay normalizasyon yapılmaz; tek liste,
gerçek benzerlik sırası. Görselin metinden düşük çıkması bir sorun değildir. Belli bir tür gerekiyorsa kullanıcı
sayfada, model `search_memory`'de tür süzgecini kullanır. Bu yüzden aşağıdaki C seçeneği elendi.

Seçenekler:

| | Nasıl | Artı | Eksi |
|---|---|---|---|
| **A. RRF** (bugün "Her biri ayrı"da k=60) | Anlam listesi ve kelime listesi ayrı sıralanır, `Σ 1/(k+sıra)` ile birleşir. | Ölçek bağımsız; türler arası ham puan karşılaştırmaz; normalizasyon gerekmez; kanıtlanmış (Zep de kullanıyor). | Puan büyüklüğünü atar: %90 ile %60 arasındaki fark kaybolur; gösterilen % ile sıra çelişebilir, açıklaması "iki listedeki yeri" olur, kullanıcıya zor. `k` bir ayar. |
| **B. Gerçek benzerlik + sınırlı kelime artısı** | Puan = kosinüs + β·kelime eşleşmesi (0-1 arası, doygun: tam ifade, tüm kelimeler, bazı kelimeler). Gösterilen % = kosinüs; arayüz "kelime eşleşmesi +x" rozeti gösterir. | % gerçek kalır; sıra = % + görünür artı, açıklaması kolay; bugünkü "metin artısı" mantığına yakın; ucuz. | Türler arasında kosinüs ölçekleri farklı (resim-metin benzerliği genelde düşük) → karışık modda medya alta kayabilir; β bir ayar. |
| **C. Kendi havuzuna göre duruş (z) + RRF** | Her türün sonucu kendi havuzundaki dağılıma göre ne kadar öne çıktığıyla (bugünkü `mf.standing`) sıralanır, sonra birleşir. | Türler arası adil. | Fiilen tür başına normalizasyon (istenmiyor); veriye göre oynar, aynı sorgu veri büyüdükçe farklı sıralanır. |

Önerim: **karışık modda A (RRF), tek tür süzgecinde B**, ikisi aynı fonksiyonda; arayüz her sonuçta gerçek %'yi ve
"sıra: anlam #3, kelime #1" gibi kısa bir açıklamayı gösterir. B tek başına seçilirse türler arası kayma ölçülmeli.

**Tarih:** sorguda zaman niyeti yoksa (`when`/`as_of` yok, tarih sözcüğü yok) yalnızca eşit puanlarda yeni olan öne.
Zaman niyeti varsa önce süzgeç (aralık dışı elenir), aralık içinde yakınlık küçük ve üstten sınırlı bir artı.

**Benzemesin:** iki seçenek. (1) **Süzgeç:** "benzemesin" sorgusunun kendi havuzunda belirgin öne çıkanlar (bugünkü
`STRONG_Z` mantığı) atılır; kalanların sırası değişmez, açıklaması kolay. (2) **Ceza:** puan −= λ·benzerlik;
yumuşak ama % ile sıra ilişkisini bulandırır. Önerim: süzgeç (bugünkü davranış), metin "benzemesin" kelime
eşleşmesinde de süzgeç.

**Tutarlılık:** eşit puanda sabit bozucu (id). Bugünkü tutarlılık testi yeni türleri (bilgi, olay, sohbet) ve
"benzemesin"i kapsayacak şekilde genişler: sayfa, harita, araç aynı sırayı vermeli.

### 9.2 Profil bloğu (karar 3)

- Güncel bilgilerden kurulur (geçmiş, çöp kutusu ve hassas olanlar hariç). Her istemin sistem mesajında durur.
- Kullanıcının düzenlemeleri **sabitlenir** (`pinned`) ve yeniden üretimde korunur.
- **Üst sınır 300 token.** Kırpma kuralı: (1) sabitlenenler her zaman girer; tek başına 300'ü aşarsa sayfa uyarır,
  kullanıcı kısaltır, üretilen kısım boş kalır. (2) Kalan yer türe göre doldurulur: kimlik → yakın ilişkiler
  (aile, eş, çocuk) → iş → yaşadığı yer → tercihler → diğer. (3) Tür içinde en son doğrulanan/kaynak sayısı fazla
  olan önce. (4) Bilgi asla yarıda kesilmez; sığmayan bütün olarak düşer.
- Bilgi değişince, silinince ya da çöp kutusuna gidince hemen yeniden kurulur. Değişmediği sürece metin aynı kalır,
  istem önbelleği bozulmaz.
- **Router logu** varsayılan olarak yalnızca token sayısı, bilgi id'leri ve zamanı gösterir; içerik gizli,
  istek başına "içeriği göster" ile açılır.

## 10. Araçlar (yalın)

Araç adları ve açıklamaları İngilizce kalır. Yeni argümanların varsayılanı bugünkü davranıştır. Beş araç kalır.

**`search_memory`** (genişler)
- yeni: `in: ["me","experiences","chats","documents","<collection name>"]` (varsayılan: hepsi = bugünkü),
  `about: "annem"`, `when: {"from": "...", "to": "..."}`, `as_of`, `include_past: false`, `budget` (varsayılan
  yok = bugünkü sabit sayı), `sensitive: false` (yalnızca kullanıcı o konuyu doğrudan sorduğunda true).
- kalan: `queries` (metin/id, like/unlike), `mode`, `kind`, `model`.
- döner: `entity` kartı (varsa), `facts: [{id, statement, since, until?, past?}]`, `events: [{id, date, precision,
  title, entities}]`, `parts: [...]` (bugünkü biçim), `rest`.

**`add_memory`** (genişler)
- `type: "note" | "fact" | "event"` (varsayılan `note` = bugünkü), `text`, `about`, `category`, `date` + `said_as`,
  `replaces: <fact id>` (her zaman kart), `items: [<id>]`, `collection`.
- döner: `{added: {...}}`, `{proposal: id}` veya `{same_as: id}`.

**`delete_memory`** (adı ve argümanları kalır, davranışı değişir): hiçbir şeyi kaldırmaz, çöp kutusuna alma kartı
açar. `whole_memory` bugünkü anlamda (kayıt, albüm değil). Döner: `{proposal: id}`.
(İlk taslaktaki `forget_memory` adı düşürüldü: ad değişmeyince kayıtlı seçimler ve geçmiş çağrılar taşınmaz.)

**`memory_context`**, **`open_memory_files`**: aynı kalır. `memory_context` bir bilgi/olay id'si alınca kaynağını
(sohbet mesajları, dosya sayfası) ve geçmiş sürümlerini döner.

## 11. Gizlilik, silme ve hassas bilgiler

### 11.1 Hassas türler (karar 4)

Liste (KVKK md. 6 "özel nitelikli kişisel veri" ve GDPR md. 9 esas alındı, finans ve kimlik eklendi):
`sağlık` (hastalık, tanı, ilaç, ruh sağlığı, hamilelik, engellilik), `cinsel_yaşam` (yönelim dahil),
`din_inanç`, `siyasi_görüş`, `etnik_köken`, `sendika_dernek`, `biyometrik_genetik`, `adli` (suç, dava, ceza),
`finans` (gelir, borç, hesap, maaş), `kimlik_no` (TC kimlik, pasaport, kart numarası; parola ve kart bilgisi hiç
saklanmaz, yalnızca "kayıtlı" bilgisi).

Kurallar:
- **Çıkarsama yok.** Yalnızca kullanıcının kendisi açıkça söylediği; "ilaç adı geçti → hasta" gibi tahmin yok.
- Hiçbir zaman otomatik yazılmaz; en fazla kart.
- **Kilitli:** yalnızca kullanıcı doğrudan sorduğunda döner. Profil bloğuna, otomatik çıkarım girdisine ve
  log içeriğine (içeriği göster dahil) hiç girmez; logda yalnızca "kilitli bilgi, id".
- Sayfada kilitli/bulanık; dokununca açılır.
- Şifreleme bu tasarımın kapsamı dışında.

### 11.2 Kaldırma ve kalıcı silme

İki ayrı işlem:

1. **Kaldır → çöp kutusu.** `deleted_at` dolar. Varsayılan 30 gün (ayarlanabilir), sonra kalıcı silinir.
   Geri yüklenebilir. Bu sürede aramada, profilde, haritada, model araçlarında yoktur.
2. **Kalıcı sil.** Ayrı ve açık onay ("geri alınamaz"). Siler: her modelin vektör kopyaları
   (`coalesce(copied_from, id)` aynı olan tüm satırlar), dosya (sha256 deposu başka bağ yoksa), küçük resim,
   kelime/trigram indeksi (satırla gider), sohbet özetleri ve aday kayıtlarındaki içerik, Hub önbellekleri; istemcilere
   olay gönderilir, yerel medya önbelleklerini temizlerler.

Profil bloğu ve önbellekler her iki işlemde **hemen** güncellenir.

### 11.3 Zincir kuralları

Varsayılan: **kaynaklara zincirleme yok.** Bir bilgiyi silmek onun sohbetini, bir sohbeti silmek ondan çıkan bilgiyi
silmez.

- **Bilgi kaldır:** yalnızca o bilgi (ve tüm sürümleri, vektörleri). Kaynak sohbet/dosya kalır.
- **Sohbet kaldır** (karar 7): Sohbetler pencereleri ve özeti gider. O sohbetten çıkarılmış bilgi/olaylar için kart:
  her biri **tek tek**, kaynaklarıyla listelenir; varsayılan **tut**. Tutulanın kaynak kaydı "silinmiş sohbet"
  olarak kalır (id + mesaj aralığı, içerik yok).
- **Mesaj sil:** o mesaja dayanan bilgiler aynı kartla; özet "eski" işaretlenir.
- **Varlık kaldır:** kart sorar: varlığın bilgileri (varlıksız anlamsızdır) varsayılan olarak birlikte çöp kutusuna.
  Olaylar kalır. Çöp kutusundayken olaydaki bağ gri "kaldırılmış kişi" olarak görünür, geri yükleyince düzelir.
  Kalıcı silinince bağ kalkar; olay başlığı/metni adı içeriyorsa kart bunu gösterir ve olayları da kaldırmayı önerir.
- **Dosya/medya kaldır:** olay ve albüm bağları gri görünür; kalıcı silinince kalkar.
- **Hepsini unut:** kullanıcının tüm hafıza tabloları; bugünkü "Tümünü sil" önce çöp kutusuna, sonra kalıcı.
- Çok kullanıcı: her tabloda `user_id`; misafir hesapları hafızaya hiç erişmez.

## 12. Sayfa (Hafıza) değişiklikleri

- **Koleksiyon şeridi:** Tümü · Ben · Yaşadıklarım · Sohbetler · Belgeler · projeler/albümler · "+ Yeni".
  Metin/Görsel/Video çipleri tür süzgeci olarak kalır.
- **Ben:** varlık listesi + varlık kartı: güncel bilgiler türe göre gruplu, her bilginin altında "geçmiş (2)",
  gri ve tarih aralıklı; dokununca kaynak mesaj açılır. Hassaslar kilitli.
- **Yaşadıklarım:** zaman çizelgesi (gün/ay/yıl), kesinliği düşük olanlar "yaklaşık" etiketli; "bu hafta
  yaklaşanlar" (yıllık tekrarlar).
- **Sohbetler:** sohbet kartları, özet, dizinlenme durumu, "hafızaya alma" anahtarı, çıkarım iş durumu (iptal).
- **Belgeler:** dosya kartı + sayfa görüntüleyici. Büyük dosyada "Bilgi çıkar" önce tahmini maliyeti gösterir
  (sayfa, token, süre) ve bugünkü parçalı/ilerlemeli/iptal edilebilir iş hattını kullanır (karar 12).
- **Albümler:** üyeleri birlikte gösterir; bir kayıt albümden çıkarılabilir (kaydı silmeden).
- **Son eklenenler:** otomatik ve model eklemeleri, kaynak + Geri al. İlk hafta günlük özet kartı.
- **Onay bekleyenler:** rozet. Kart: eski → yeni, gerekçe, kaynak, Onayla / Düzenle / Reddet. Birleştirmeler ve
  sohbet silme bilgileri **toplu kart**ta tek tek işaretlenir. Sohbette de çıkar. Tıklamalar kuyruklanır, sayfa
  kilitlenmez.
- **Çöp kutusu:** kaldırılanlar, kalan gün, Geri yükle / Kalıcı sil.
- **Ayarlar:** genel dizinleme/çıkarım anahtarı, arka plan modeli, çöp kutusu süresi, profil bloğu (görüntüle,
  düzenle, sabitle).
- **Harita:** varlıklar büyük düğüm; koleksiyon renkli halka.

## 13. Var olan verinin taşınması (karar 5)

- **Önce otomatik yedek:** her şema taşımasından önce ilgili tabloların dökümü (`pg_dump -t`) Orion veri klasörüne,
  tarihli. Başarısız taşımada geri dönülür.
- **Vektör tablosu hiç silinmez,** sütunlar yalnızca eklenir.
- **Geri alınabilir:** taşıma yalnızca yeni sütunları ve tabloları doldurur; geri almak = bayrağı kapatıp yeni
  sütunları boşaltmak. Eski satırlar yerinde.
- **Geçişte eski hafıza aranabilir kalır:** `collection_id is null` olan satırlar bugünkü gibi aranır.
- Adımlar: (1) hazır koleksiyonlar + **"Eski hafıza"** projesi; (2) `source_kind='file'` → Belgeler, kalanı →
  Eski hafıza; `group_id`, parçalar, vektörler aynen; (3) isteğe bağlı "öneri geçişi": eski notlar ve sohbet
  kayıtları gölge çıkarımdan geçer, sonuçlar **kart** olarak gelir; (4) eski sohbetler "Eski sohbetleri dizinle"
  düğmesiyle arka planda.

## 14. Ölçek ve performans

Kabaca tek kullanıcı, 5 yıl: bilgiler birkaç bin, olaylar on binler, sohbet pencereleri yüz binler, belge sayfaları
on binler, medya binler.

- **Hedef: arama 150 ms** (bugünkü hedef) korunur. Her adımda önce/sonra ölçümü raporlanır (aynı veri, aynı sorgular).
- Bilgi/olay aramaları küçük: kısmi HNSW (yalnızca güncel, çöpte olmayan); geçmiş sürümler tam taramayla.
- Sohbetler en büyük koleksiyon: kendi kısmi HNSW indeksi; varsayılan kişisel aramada taranmaz.
- Kelime araması `folded` + trigram. Ad araması varlık tablosunda, çok küçük.
- Sayaçlar bugünkü tetikleyici tablolarla; koleksiyon ve `deleted_at` eklenir.
- Çıkarım: sohbet başına, yalnızca yeni mesajlar, ön-süzgeçten geçenler; LLM uzakta (Router). Yerelde yalnızca gömme,
  düşük öncelikli kuyruk.
- İleride: `halfvec`; çok eski sohbet pencerelerini yalnızca özetle tutma (kullanıcı kararıyla).

## 15. Kararlar

Kullanıcının 12 soruya verdiği kararlar (hepsi kabul, eklemeleriyle). Ayrıntı ilgili bölümlerde.

| # | Konu | Karar |
|---|---|---|
| 1 | Duygu/düşünce | Ayrı koleksiyon yok; `tercih`/`görüş` bilgisi, olayda `mood` (§3). |
| 2 | Otomatik yazma | Üç kova: açık+net → kalite kapısından sonra otomatik (yalnızca ekleme); belirsiz → kart; çıkarsanan → hiçbir yer. Hassas asla otomatik. Her bilgi kaynaklı. Yazmadan önce yinelenme kontrolü. Çelişki → kart. Son eklenenler + Geri al (§7, §8). |
| 3 | Profil bloğu | Güncel bilgilerden; sabitlenen düzenlemeler korunur; 300 token + kırpma kuralı; Router logunda içerik gizli (§9.2). |
| 4 | Hassas bilgiler | Çıkarsama yok; liste §11.1; kilitli, yalnız doğrudan sorulunca; profil/çıkarım/log içeriğinde yok; şifreleme kapsam dışı. |
| 5 | Eski veri | Geçişte aranabilir; geri alınabilir taşıma; vektör tablosu silinmez; taşıma öncesi otomatik yedek (§13). |
| 6 | Sohbet dizini | Otomatik; çıkarım kapanınca/boşta; süren sohbette yalnız yeni mesajlar; ön-süzgeç; genel + sohbet başına anahtar; iptal edilebilir iş, logda üst veri (§7.1-7.2). |
| 7 | Sohbet silinince | Varsayılan tut; kart bilgileri tek tek, kaynaklarıyla listeler (§11.3). |
| 8 | Çıkarım modeli | Arka plan modeli ayarı; otomatik yazmadan önce ~20 sohbetlik gölge mod kalite kapısı; onaydan sonra kademeli, ilk hafta günlük özet (§7.4). |
| 9 | Albümler | Koleksiyon olarak; tek kayıtlar gruplanır, birlikte gösterilir; `open_memory_files` / `whole_memory` uyumlu (§3). |
| 10 | Varlık birleştirme | Geri alınabilir, takma adlar korunur, bağlar tek işlemde (transaction) taşınır, toplu kart (§5, §12). |
| 11 | Olay tarihi | `precision` alanı; göreli tarihler sohbet mesajının zamanına göre (§4). |
| 12 | Belgelerden bilgi | Bugünkü parçalı/ilerlemeli/iptal iş hattı; büyük dosyada önce tahmini maliyet (§12). |
| — | Silme | Kaldır (çöp kutusu, 30 gün, geri yüklenir) ve kalıcı sil (ayrı onay, her şeyi temizler); zincir kuralları §11.3. |
| — | Sıralama | **Açık**: öneri §9.1, kullanıcı kararı bekleniyor. |

## 16. Aşamalı plan

### Genel kurallar

- Her adım eski aramayı çalışır bırakır; yeni yapı bir **bayrak** arkasında (`memory_v2`), kapalıyken bugünkü davranış.
- **Kaynak bağları ilk iş.** Kaynaksız bilgi yazılamaz.
- Yeni her tablo için **kullanıcı ayrımı testi** (başka kullanıcının satırı hiçbir yoldan görünmez/silinmez).
- **150 ms** arama hedefi; her adımın raporunda önce/sonra ölçümü.
- Araç adları/açıklamaları İngilizce; yeni argümanların varsayılanı bugünkü davranış.
- Sayfa, panel, araç aynı kodu paylaşır; sıralama tutarlılık testi yeni türleri ve "benzemesin"i kapsar.
- Her rapor, **canlı denenmeyen** şeyleri açıkça yazar.

### Sıra

Açık konular (sıralama kararı) → bu belge → onay → 8 adım:

1. **Temel ve kaynaklar.** Otomatik yedek, `memory_v2` bayrağı, `orion_mem_source`, koleksiyon tabloları ve
   `collection_id`, Belgeler + Eski hafıza taşıması (geri alınabilir), `search_memory` `in` argümanı, koleksiyon şeridi.
2. **Çöp kutusu ve kalıcı silme.** `deleted_at` (var olan öğeler dahil), geri yükleme, süre ayarı, kalıcı silmede
   tüm kopyalar/dosya/önbellekler, zincir kuralları, `delete_memory` → kart.
3. **Sohbetler dizini.** Mesaj sınırlı pencereler, genel + sohbet başına anahtar, eski sohbetleri dizinle düğmesi.
4. **Varlıklar + bilgiler.** Tablolar, sürümleme, `add_memory type=fact`, Ben sekmesi (varlık kartı, geçmiş),
   hassas türler ve kilit, geri alınabilir birleştirme + toplu kart.
5. **Olaylar + albümler.** `type=event`, tarih çözme ve `precision`, fotoğraf bağlama, albüm koleksiyonları,
   Yaşadıklarım zaman çizelgesi, yıllık tekrarlar.
6. **Öneriler ve görünürlük.** `orion_mem_proposal` kartları (sayfa + sohbet), son eklenenler, Geri al.
7. **Çıkarım.** Ön-süzgeç, arka plan modeli, iş (iptal, log üst verisi), **gölge mod + kalite kapısı raporu**.
   Kullanıcı onayından sonra otomatik yazma (yalnızca ekleme) ve ilk hafta günlük özet. Sohbet özeti.
8. **Arama ve profil.** Onaylanan sıralama politikası, token bütçesi, varlık kartı, `as_of`/`include_past`,
   profil bloğu (sabitleme, kırpma, Router logu).

Sonra: belgelerden "Bilgi çıkar" düğmesi, eski hafıza öneri geçişi, haritada varlık düğümleri, misafir hesapları.
