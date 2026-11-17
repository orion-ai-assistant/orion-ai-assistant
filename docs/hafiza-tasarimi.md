# Orion hafıza tasarımı (gerçek kullanım için)

Durum: öneri. Kod değişikliği yok. Kararlar kullanıcının; açık sorular en sonda, her birinde bir önerim var.

## 1. Neden değişmeli?

Bugünkü hafıza tek bir yığın. Her şey `orion_memory` tablosunda satır:
not, sohbet kaydı, resim, ses, video, dosya. Bir "hafıza" (`group_id`) birlikte kaydedilen parçaları toplar.
Arama, vektör (pgvector HNSW, tür başına kısmi indeks) + kelime (pg_trgm) ile yapılıyor.
Uzun içerik çekirdek + bağlam pencerelerine bölünüyor, bir de "bütün" vektörü var. Medya sha256 ile tek kopya.
Sohbet kayıtları ilk/son mesaj id'sini tutuyor. Model araçları: `search_memory`, `open_memory_files`,
`memory_context`, `add_memory`, `delete_memory`.

Bu altyapı iyi. Eksik olan **anlam katmanı**:

- "Annem kim, doğum günü ne zaman?" sorusu bir pencere parçasında aranıyor. Kısa, net bir bilgi yok.
- Bilgi değişince (taşındım, işim değişti) eski satır ya kalıyor ya siliniyor. Geçmiş yok, "şu an doğru olan" yok.
- Atatürk hakkında bir sohbet ile "kızımın adı Elif" aynı yığında. Kişisel soru genel bilgiyle kirleniyor.
- "Bugün" diye kaydedilen olayın tarihi metnin içinde kayboluyor.
- Test verisinde bir hafızada çok resim/metin var. Gerçekte bir hafıza tek bir şeydir: bir dosya, bir olay, bir bilgi.

Bu belge, var olanın üstüne kurulacak bir yapı öneriyor. Vektör tablosu, parçalama, medya deposu, model taşıma
(`memory_move`), harita, sayaçlar aynen kalır.

## 2. Başkaları nasıl yapıyor, ne alıyoruz?

Kaynaklar bu oturumda yeniden açılmadı; özetler yayımlanmış makale ve duyurulara dayanır.

| Sistem | Nasıl çalışıyor | Bizim aldığımız |
|---|---|---|
| **Mem0** (Chhikara ve ark., 2025, arXiv:2504.19413) | Sohbetten LLM ile kısa "bilgi"ler çıkarır. Her yeni bilgi için benzer eskileri bulur, LLM'e ADD / UPDATE / DELETE / NOOP seçtirir. Graf sürümü (Mem0g) varlık + ilişki tutar. | Çıkarım + "var olanla karşılaştır" adımı, JSON işlem listesi. **DELETE yerine "geçmişe al"** (geçmiş kaybolmasın). |
| **Zep / Graphiti** (Rasmussen ve ark., 2025, arXiv:2501.13956) | Zamansal bilgi grafı. Ham "episode"lar (mesajlar) saklanır; onlardan varlık düğümleri ve olgu kenarları çıkar. Her kenarda iki zaman: olayın geçerli olduğu aralık (`valid_at`/`invalid_at`) ve sisteme giriş/çıkış (`created_at`/`expired_at`). Çelişen eski kenar silinmez, geçersiz yapılır. Arama: vektör + BM25 + graf komşuluğu, sonra yeniden sıralama (RRF, MMR). Göreli tarihler kaydedilirken mutlak tarihe çevrilir. | **İki zamanlı (bi-temporal) bilgiler**, varlık düğümleri, her bilginin kaynağına (episode = mesaj aralığı) bağlanması, göreli tarihi kayıtta çözme, karma arama + RRF (zaten var). |
| **ChatGPT memory** (OpenAI, Şub 2024; sohbet geçmişine başvurma Nis 2025) | İki ayrı şey: "kayıtlı anılar" (kısa cümleler, kullanıcı görür/siler, "Hafıza güncellendi" bildirimi) ve geçmiş sohbetlere başvurma. Geçici sohbet hafızaya yazmaz. | **Kullanıcının gördüğü, kısa cümle bilgiler**; model bir şey kaydedince sohbette görünür iz; sohbet arşivi ayrı. Geçici sohbet fikri (açık soru 9). |
| **Letta / MemGPT** (Packer ve ark., 2023, arXiv:2310.08560) | Bağlamda hep duran küçük "çekirdek bellek blokları" (kişi, kullanıcı), dışarıda büyük "arşiv" ve "geçmiş" belleği. Model kendi belleğini araçlarla düzenler; sohbet uzayınca özetlenir. | Küçük, **hep hazır bir profil bloğu** (açık soru 3), arşiv/sohbet ayrımı, uzayan sohbetin özetlenmesi (sıkıştırma). Modelin serbest yeniden yazması **alınmıyor**: bizde model silemez, üzerine yazamaz. |

Ortak ders: ham kaynak (mesaj, dosya) dokunulmadan kalır; ondan türeyen kısa bilgiler ayrı tutulur ve kaynağına
bağlanır. Arama önce kısa bilgilere, gerekirse ham kaynağa bakar.

## 3. Koleksiyonlar

Tek yığın yerine birkaç koleksiyon. Her öğe tam olarak bir koleksiyonda durur.

| Koleksiyon | İçerik | Ne zaman aranır |
|---|---|---|
| **Ben** | Kim olduğum: aile, arkadaşlar, tercihler, sağlık, iş, adres. Küçük, tek cümlelik bilgiler; çoğu bir varlığa ("Annem", "Elif", "İşyerim") bağlı. | Kişisel her soruda. |
| **Yaşadıklarım** | Tarihli olaylar: gezi, buluşma, doğum günü kutlaması, günlük. Fotoğraf/video çoğunlukla buraya bağlanır. | "Ne zaman…", "geçen yaz…", kişiyle ilgili son olaylar. |
| **Sohbetler** | Asistanla yapılan sohbetlerin arşivi. Otomatik dizinlenir. | Yalnızca istenince: "bunu konuşmuştuk", "geçen hafta ne demiştin". Kişisel aramaya karışmaz. |
| **Belgeler** | Dosyalar: PDF, metin, tablo. Bir dosya = bir düğüm, sayfalar = parçalar. **Çok parçalı tek yer burası.** | Belge sorulunca veya içerik eşleşince. |
| **Projeler** (kullanıcının) | Kullanıcının açtığı adlı koleksiyonlar: "Tez", "Ev tadilatı". Belge, not, olay, medya içerebilir. | Proje adı geçince veya seçilince. |

Atatürk sohbeti: Sohbetler'de kalır. Ondan **bilgi çıkarılmaz**, çünkü çıkarım yalnızca kullanıcı ve onun dünyası
hakkındaki bilgileri alır (bkz. §7). Kişisel arama Sohbetler'e bakmadığı için kirlenmez.

**Duygu ve düşünceler.** Ayrı koleksiyon önermiyorum. "Kahveyi sevmem" bir tercih, "işimden sıkıldım" zamanla değişen
bir görüş. İkisi de Ben'de bir bilgi, türü `tercih` / `görüş`. Günlük gibi yazılanlar ("bugün çok yorgundum") Yaşadıklarım'da
bir olay, isteğe bağlı `ruh_hali` alanıyla. Böylece aynı geçmiş/geçerlilik kuralı duygulara da işler: "eskiden
sevmezdim, artık seviyorum" iki sürüm olur. (Açık soru 1.)

**Bir hafıza = bir şey.** Bir bilgi, bir olay, bir dosya, bir not. Bir olaya birden çok fotoğraf bağlanabilir ama
fotoğraflar ayrı öğe kalır, olaya bağlantıyla eklenir. Bugünkü "Ekle ile parçaları zincirleme" yalnızca kullanıcı
açıkça bir albüm/proje kurduğunda kullanılır.

## 4. Kavramlar

- **Varlık (entity):** kişi, yer, kurum, evcil hayvan, eşya. Adı, takma adları ("annem", "Ayşe", "anne"),
  kullanıcıyla ilişkisi. Varlık tek başına bilgi taşımaz; bilgiler ona bağlanır.
- **Bilgi (fact):** tek, kısa, kendi başına anlaşılır cümle. "Annemin doğum günü 17 Kasım." Bir varlığa (veya
  kullanıcının kendisine) bağlı. Türü: `kimlik`, `ilişki`, `tercih`, `görüş`, `sağlık`, `iş`, `yer`, `tarih`, `diğer`.
- **Olay (event):** tarihli bir yaşantı. Tarih + kesinlik (gün/ay/yıl/yaklaşık), katılan varlıklar, yer, isteğe bağlı
  medya.
- **Öğe (item):** bugünkü anlamıyla içerik: not, dosya, resim, ses, video, sohbet penceresi. Parçalara bölünür,
  vektörleri `orion_memory`'de.
- **Kaynak (source):** bir bilginin/olayın nereden geldiği: sohbet + ilk/son mesaj id, bir öğe, ya da "kullanıcı
  sayfada yazdı".
- **Öneri (proposal):** onay bekleyen değişiklik. Onay kartı olarak görünür.

### Değişen bilgi, kaybolmayan geçmiş

Hiçbir bilgi üzerine yazılmaz. Değişince yeni sürüm eklenir, eski sürüm kapanır (Graphiti'deki gibi iki zaman):

- `valid_from` / `valid_to`: dünyada ne zaman doğruydu. ("2019'dan 2026 Ekim'e kadar Ankara'da yaşadı.")
- `recorded_at` / `expired_at`: Orion bunu ne zaman öğrendi, ne zaman geçersiz saydı.
- `superseded_by`: eski sürüm yenisini gösterir.

"Şu an doğru olan" = `valid_to is null and expired_at is null`. "Eskiden" sorusu kapalı sürümleri de alır.
"2022'de neredeydim?" = o tarihte geçerli olan sürüm.

Örnek, taşınma: "İstanbul'a taşındım" → eski "Ankara'da yaşıyor" bilgisine `valid_to = bugün`, `superseded_by = yeni`;
yeni "İstanbul'da yaşıyor", `valid_from = bugün`. Sayfada Ankara gri, "geçmiş" altında görünür.

Örnek, "bugün annemin doğum günüydü" (17 Kasım 2026'da söylendi):
1. **Olay** (Yaşadıklarım): "Annemin doğum günü", tarih 2026-11-17 (kesinlik: gün), varlık: Annem.
2. **Bilgi** (Ben, Annem'e bağlı): "Annemin doğum günü 17 Kasım.", `tekrar = yıllık --11-17`.
   Zaten varsa eklenmez (NOOP); yalnızca olay eklenir.

**Göreli tarih:** "bugün", "dün", "geçen cuma" kayıt anında mutlak tarihe çevrilir. Hub modele şimdiki zamanı ve
kullanıcının saat dilimini zaten veriyor (araç `tarih` alanı). Model ISO tarih yazar; Hub doğrular ve sohbetin
mesaj zamanına göre kontrol eder (sohbet dün yazıldıysa "bugün" dündür: mesajın kendi tarihi esas). Orijinal ifade
(`bugün`) de saklanır, yanlış çözüm sonradan düzeltilebilsin.

## 5. Veri modeli (taslak SQL)

İlke: **tek vektör tablosu kalır.** Bilgi ve olayların vektörleri de `orion_memory`'de satır olur. Böylece model
taşıma, `vector_space`, sayaçlar, tür başına kısmi HNSW, kelime araması tek kodla çalışır. Yeni tablolar yalnızca
anlamı taşır.

```sql
-- Koleksiyonlar: her kullanıcıya hazır 4 tane + kullanıcının projeleri.
create table orion_mem_collection (
    id          text primary key,              -- 'ben','yasadiklarim','sohbetler','belgeler' veya 'p_<uuid>'
    user_id     text not null,
    kind        text not null,                 -- builtin | project
    name        text not null,
    created_at  timestamptz not null default now()
);

-- Varlıklar: kişi, yer, kurum...
create table orion_mem_entity (
    id          bigserial primary key,
    user_id     text not null,
    kind        text not null,                 -- person | place | org | pet | thing | self
    name        text not null,                 -- "Ayşe"
    relation    text,                          -- "annem" (kullanıcıya göre)
    aliases     text[] not null default '{}',  -- {"anne","annem","Ayşe Hanım"}
    folded      text,                          -- küçük harf, aksansız: ad + takma adlar (trigram araması)
    merged_into bigint references orion_mem_entity(id),
    created_at  timestamptz not null default now()
);
create index on orion_mem_entity using gin (folded gin_trgm_ops);

-- Bilgiler: iki zamanlı, sürümlü.
create table orion_mem_fact (
    id            bigserial primary key,
    user_id       text not null,
    entity_id     bigint not null references orion_mem_entity(id),  -- kullanıcının kendisi de bir varlık ('self')
    category      text not null,             -- kimlik|ilişki|tercih|görüş|sağlık|iş|yer|tarih|diğer
    statement     text not null,             -- "Annemin doğum günü 17 Kasım."
    value         jsonb,                     -- isteğe bağlı yapı: {"date":"--11-17"} {"city":"İstanbul"}
    recurring     text,                      -- 'yearly:11-17' gibi; yaklaşan günler için
    valid_from    date, valid_to date,       -- dünyadaki geçerlilik (bilinmiyorsa null)
    recorded_at   timestamptz not null default now(),
    expired_at    timestamptz,               -- Orion artık geçersiz sayıyor
    superseded_by bigint references orion_mem_fact(id),
    sensitive     boolean not null default false,  -- sağlık vb.: profil bloğuna girmez, kilitli gösterilir
    origin        text not null,             -- user | model | extraction
    collection_id text not null default 'ben'
);
create index on orion_mem_fact (user_id, entity_id) where expired_at is null and valid_to is null;

-- Olaylar.
create table orion_mem_event (
    id            bigserial primary key,
    user_id       text not null,
    title         text not null,             -- "Annemin doğum günü"
    body          text,                      -- serbest anlatım (günlük)
    occurred_on   date,                      -- başlangıç günü
    occurred_end  date,                      -- çok günlü gezi
    precision     text not null default 'day',   -- day | month | year | approx
    said_as       text,                      -- "bugün", "geçen yaz"
    mood          text,                      -- isteğe bağlı
    recorded_at   timestamptz not null default now(),
    expired_at    timestamptz,               -- yanlış kayıt düzeltildiyse
    superseded_by bigint references orion_mem_event(id),
    origin        text not null,
    collection_id text not null default 'yasadiklarim'
);
create index on orion_mem_event (user_id, occurred_on);

-- Olay <-> varlık ve olay <-> öğe (fotoğraf, video) bağları.
create table orion_mem_link (
    user_id   text not null,
    from_type text not null, from_id bigint not null,   -- 'event'
    to_type   text not null, to_id   text   not null,   -- 'entity' | 'item'
    role      text,                                     -- 'katılan', 'yer', 'fotoğraf'
    primary key (from_type, from_id, to_type, to_id)
);

-- Kaynak: türeyen her bilgi/olay nereden geldi. Silme zinciri bunun üzerinden yürür.
create table orion_mem_source (
    user_id      text not null,
    target_type  text not null, target_id bigint not null,   -- 'fact' | 'event'
    chat_id      text, first_message bigint, last_message bigint,
    item_id      text,                                       -- bir öğeden (dosya/not) çıkarıldıysa
    created_at   timestamptz not null default now()
);
create index on orion_mem_source (user_id, chat_id);
create index on orion_mem_source (user_id, item_id);

-- Onay bekleyen değişiklikler (onay kartları).
create table orion_mem_proposal (
    id          bigserial primary key,
    user_id     text not null,
    action      text not null,     -- add | supersede | expire | delete | merge_entities | move
    payload     jsonb not null,    -- eski/yeni hali, hedef id'ler
    reason      text,              -- modelin gerekçesi, tek cümle
    chat_id     text, message_id bigint,
    state       text not null default 'pending',  -- pending | accepted | rejected | edited
    created_at  timestamptz not null default now(),
    decided_at  timestamptz
);

-- Sohbet özeti / çıkarım imleci.
create table orion_mem_chat_state (
    user_id        text not null,
    chat_id        text primary key,
    indexed_upto   bigint,      -- Sohbetler dizini bu mesaja kadar
    extracted_upto bigint,      -- bilgi çıkarımı bu mesaja kadar
    summary        text,        -- kısa sohbet özeti
    summary_upto   bigint,
    summary_stale  boolean not null default false
);
```

`orion_memory` (vektör/parça tablosu) üç sütun alır:

```sql
alter table orion_memory add column collection_id text;      -- 'ben','yasadiklarim','sohbetler','belgeler','p_..'
alter table orion_memory add column owner_type text;         -- 'fact' | 'event' | null (bugünkü öğeler)
alter table orion_memory add column owner_id bigint;         -- fact.id / event.id
alter table orion_memory add column current boolean not null default true;  -- sürüm geçmişe alınınca false
-- kind yeni değerler alır: 'fact', 'event', 'chat'. Tür başına kısmi HNSW indeksi zaten var; onlara da açılır.
-- Koleksiyon başına kısmi indeks yalnızca büyük olanlara: sohbetler ve belgeler.
```

Bir bilgi yeni sürüme geçince eski sürümün vektör satırı silinmez; `current = false` olur; `kind='fact'`
kısmi indeksi `where current` koşuluyla yalnızca güncel sürümleri tutar. Geçmiş sürümler, "eskiden" sorularında tam
taramayla (az sayıda) bulunur.

## 6. Ne gömülür (embedding), ne gömülmez

| Tür | Gömülen metin | Not |
|---|---|---|
| Bilgi | Varlık adı açık cümle: "Annem (Ayşe): doğum günü 17 Kasım." | Kısa, tek vektör. Zamir yok ("o", "onun"), çünkü tek başına okunur. |
| Olay | "2026-11-17 Salı, Kasım 2026: Annemin doğum günü. Katılanlar: Annem, Elif. …" | Tarih sözcükleri biraz yardım eder ama tarih filtresi SQL'de (`occurred_on`). Uzun günlük → bugünkü pencerelerle bölünür. |
| Sohbet | Mesaj sınırına oturan pencereler (~300 token çekirdek, komşu mesajlardan bağlam). Her mesajın başında "Kullanıcı:"/"Asistan:". | Bir mesaj pencereden uzunsa yalnız o bölünür. Araç çağrısı çıktıları gömülmez. |
| Belge | Sayfa başına parça (PDF sayfası); uzun sayfa bugünkü pencerelerle. Dosyanın bütün vektörü kalır. | Bir dosya = bir düğüm. |
| Medya | Bugünkü gibi (resim, ses, video kare/ses/birleşik). | Olaya bağlantıyla eklenir. |
| Ad, başlık, dosya adı, takma ad | **Gömülmez.** Yalnızca kelime/trigram (`folded`). | Bir adın vektörü gürültü. "Annem" araması ada ve takma adlara kelimeyle oturur. |

Model değişince (`memory_move`) bilgi ve olay vektörleri de bugünkü gibi yeni alana kopyalanır; kaynak metin
tabloda durduğu için yeniden gömmek kolay.

## 7. Sohbet sıkıştırma ve bilgi çıkarımı

Mem0'ın çıkar-karşılaştır adımı + Graphiti'nin kaynak bağı:

1. **Tetik:** sohbet 10 dk boşta kaldığında veya kapanınca, `extracted_upto`'dan sonra yeterince yeni mesaj varsa.
   Sabit eşik test verisine göre değil, maliyete göre: bir geçiş = bir LLM çağrısı. Hub boştayken, en düşük öncelikle.
2. **Girdi:** yeni mesajlar (ilk/son id ile), şimdiki tarih ve saat dilimi, ve bu mesajlarda geçen varlıkların
   güncel bilgileri (ad + takma ad eşleşmesi, sonra vektörle en yakın birkaç bilgi). Böylece LLM tekrar eklemez.
3. **Çıktı (JSON):**
   ```json
   {"entities": [{"ref": "e1", "name": "Ayşe", "relation": "annem", "match": 12}],
    "ops": [
      {"op": "add_event", "title": "Annemin doğum günü", "date": "2026-11-17", "precision": "day",
       "said_as": "bugün", "entities": ["e1"], "messages": [4410, 4412]},
      {"op": "add_fact", "entity": "e1", "category": "tarih", "statement": "Annemin doğum günü 17 Kasım.",
       "recurring": "yearly:11-17", "messages": [4410, 4410]},
      {"op": "supersede", "fact": 88, "statement": "İstanbul'da yaşıyor.", "valid_from": "2026-11-01",
       "messages": [4420, 4421]},
      {"op": "noop", "fact": 31}
    ]}
   ```
   Kural: yalnızca kullanıcı ve çevresi hakkında, kullanıcının **söylediği** bilgiler. Genel bilgi (Atatürk,
   kod, tarif) çıkarılmaz. Asistanın tahmini çıkarılmaz.
4. **Uygulama:** Hub her işlemi doğrular (id var mı, tarih geçerli mi, ilk/son mesaj bu sohbette mi). İzin tablosuna
   (§8) göre ya doğrudan yazar ya da öneri kartı açar. Her yazılan şeyin `orion_mem_source` kaydı olur.
5. **Özet:** aynı geçiş sohbetin kısa özetini günceller (`summary`). Özet Sohbetler'de sohbet kartında görünür ve
   uzun sohbette modele eski kısmın yerine verilebilir (Letta'daki gibi). Kaynak mesajlardan biri silinirse
   `summary_stale = true`, sonraki geçişte yeniden yazılır.
6. **Model:** Router'daki seçilebilir bir "arka plan modeli" (açık soru 8). Yerel CPU'ya yük yok; yalnızca yeni
   bilgilerin gömülmesi yerel.

Sohbetler dizini ayrı ve LLM'siz: yeni mesajlar pencerelere bölünüp gömülür (`indexed_upto`). Çıkarım kapalı
olsa bile arşiv araması çalışır.

## 8. Model izinleri

| Model ne yapabilir | Nasıl |
|---|---|
| Yeni bilgi / olay ekleme | Doğrudan. Sohbette küçük iz: "Hafızaya eklendi: Annemin doğum günü 17 Kasım. [Geri al]" |
| Var olan varlığa bilgi ekleme, olaya fotoğraf bağlama | Doğrudan. |
| Bir bilgiyi "artık geçerli değil" yapma (yeni sürümle) | Sıradan bilgide doğrudan + iz. **Önemli** türlerde (kimlik, ilişki, sağlık, yer, `sensitive`) onay kartı. |
| Varlıkları birleştirme ("Ayşe" = "annem") | Onay kartı. |
| Var olan metni yeniden yazma | **Yok.** Düzeltme = yeni sürüm. |
| Silme | **Yok.** Model yalnızca silme **önerir**; kart kullanıcıya gider. |

Kullanıcı sayfada her şeyi yapabilir; onun yaptığı değişiklik kart açmaz.
Tüm değişiklikler sürüm olduğu için "geri al" her zaman mümkün (silme hariç; silme gerçekten siler, §11).

## 9. Arama

- **Koleksiyon hedefi:** model hangi koleksiyonlara bakacağını seçer. Varsayılan (model boş bırakırsa):
  Ben + Yaşadıklarım + Belgeler + Projeler. Sohbetler yalnızca istenince. Puana bakıp "zayıfsa Sohbetler'e de bak"
  gibi bir eşik yok (eşikler test verisine göre ayarlanmasın kuralı).
- **Varlık merkezli:** sorgu bir varlık adına/takma adına kelimeyle oturursa ("annem"), önce o varlığın kartı gelir:
  güncel bilgileri, son birkaç olayı, geçmiş sürüm sayısı. Graphiti'deki "düğüm komşuluğu" fikrinin sade hali,
  graf veritabanı olmadan.
- **Zaman:** `when` aralığı olaylarda `occurred_on`, bilgilerde geçerlilik aralığıyla süzülür. `as_of` verilirse o
  tarihte geçerli sürümler.
- **Sıralama:** bugünkü karma arama (vektör + kelime) ve RRF birleştirmesi koleksiyonlar arasında da kullanılır;
  ham puanlar türler arasında karşılaştırılmaz, sıralar karşılaştırılır (bugünkü "Her biri ayrı" ile aynı mantık).
- **Token bütçesi:** sabit "20 sonuç" yerine `budget` (varsayılan ~1500 token). Önce yoğun olanlar (bilgiler),
  sonra olaylar, sonra pencere parçaları sığdığı kadar. Her sonuçta id var; devamı `memory_context` ile açılır.
- **Profil bloğu (isteğe bağlı):** Ben'den küçük bir özet (~300 token) sistem mesajına hep konur: ad, yakınlar,
  birkaç temel tercih. Hassas bilgiler girmez. Değişmediği sürece aynı kalır, istem önbelleği bozulmaz. (Açık soru 3.)

Sayfa, harita ve araç aynı arama işlevini kullanır (tek uygulama kuralı). Bugünkü `memory_groups.search_queries`
genişler; ayrı bir "bilgi araması" yazılmaz.

## 10. Araçlar (yalın)

Araç sayısı artmıyor; beş araç kalır, ikisi değişir, biri yeniden adlanır.

**`search_memory`** (genişler)
- yeni: `in: ["ben","yasadiklarim","sohbetler","belgeler","<proje adı>"]`, `about: "annem"` (varlık),
  `when: {"from": "2026-06-01", "to": "2026-08-31"}`, `as_of: "2022-05-01"`, `include_past: false`,
  `budget: 1500`.
- kalan: `queries` (metin/id, like/unlike), `mode`, `kind`, `model`.
- döner: `entity` kartı (varsa), `facts: [{id, statement, since, until?, past?}]`, `events: [{id, date, title,
  entities}]`, `parts: [{id, collection, kind, text, score, date}]`, `rest` (bütçeye sığmayan sayısı).

**`add_memory`** (genişler)
- `type: "fact" | "event" | "note"`, `text`, `about` (varlık adı; yoksa yeni varlık), `category` (bilgide),
  `date` + `said_as` (olayda; Hub doğrular), `replaces: <fact id>` (eskisini geçmişe alır; önemliyse kart),
  `items: [<id>]` (olaya bağlanacak fotoğraflar), `collection` (proje).
- döner: `{added: {...}}` veya `{proposal: id, note: "Kullanıcı onayı bekleniyor."}` veya `{same_as: id}`.

**`forget_memory`** (`delete_memory`'nin yerine; eski adlar `RENAMED_TOOLS` ile taşınır)
- `ids`, `reason`. Hiçbir şey silmez; onay kartı açar. Döner: `{proposal: id}`.

**`memory_context`**, **`open_memory_files`**: aynı kalır. `memory_context` bir bilgi/olay id'si alınca kaynağını
(sohbet mesajları, dosya sayfası) ve geçmiş sürümlerini döner.

Araç açıklamalarına tek satır: "Kişisel soru → ben/yasadiklarim. Genel bilgi sorusu için hafızaya bakma.
Daha önce konuşulanı soruyorsa → sohbetler."

## 11. Gizlilik ve silme zinciri

"Geçmişe almak" ile "unutmak" ayrı şeyler. Geçmiş = hâlâ saklı, sadece güncel değil. Unutmak = gerçekten silmek.

- **Bir bilgiyi unut:** bilgi, tüm sürümleri, vektörleri, kaynak kayıtları silinir. Profil bloğu yeniden kurulur.
- **Bir varlığı unut:** varlığın bilgileri silinir; olaylardan bağı kopar. Olayın kendisi kalsın mı? Kart sorar
  ("Bu kişiyle 4 olay var: olayları da sil / yalnızca bağı kaldır").
- **Bir sohbeti sil:** Sohbetler pencereleri ve özeti silinir. O sohbetten **yalnızca** çıkarılmış bilgiler/olaylar
  için tek kart: "Bu sohbetten 3 bilgi çıkarılmıştı: tut / sil." Başka kaynağı da olan bilgi kaynağını kaybeder ama
  kalır. (Açık soru 7.)
- **Bir mesajı sil:** o mesaja dayanan bilgilerin kaynağı düşer; tek kaynak oysa aynı kart. Özet "eski" işaretlenir.
- **Dosya/medya sil:** bugünkü gibi (medya bağı düşer, sha256 deposu kimse kullanmıyorsa temizlenir); olaydan bağı kalkar.
- **Hepsini unut:** kullanıcının tüm hafıza tabloları. Bugünkü "Tümünü sil" bunu yapar.
- Hassas (`sensitive`) bilgiler profil bloğuna girmez, sayfada kilitli/bulanık gösterilir, dışa aktarımda işaretlenir.
- Çok kullanıcı: her tabloda `user_id`; ileride misafir hesapları hafızaya hiç erişmez (planlanan misafir kuralı).

## 12. Sayfa (Hafıza) değişiklikleri

- **Üstte koleksiyon şeridi:** Tümü · Ben · Yaşadıklarım · Sohbetler · Belgeler · projeler · "+ Proje".
  Bugünkü Metin/Görsel/Video çipleri tür süzgeci olarak kalır.
- **Ben:** varlık listesi (Annem, Elif, İşyerim, "Ben") + her varlığın kartı. Kartta güncel bilgiler türe göre
  gruplu; her bilginin altında küçük "geçmiş (2)" açılır listesi, gri ve tarih aralıklı. Bilgiye dokununca kaynağı
  (sohbet mesajı) açılır.
- **Yaşadıklarım:** zaman çizelgesi (gün / ay / yıl yakınlaştırma). "Bu hafta yaklaşanlar" (yıllık tekrar eden
  bilgilerden: doğum günleri). Olay kartında fotoğraflar, kişiler.
- **Sohbetler:** sohbet kartları, özet, dizinlenme durumu, "bu sohbetten çıkarılanlar" sayısı.
- **Belgeler:** bugünkü dosya kartı + sayfa görüntüleyici (ekrandaki ⤢ ile açılan görünüm).
- **Onay bekleyenler:** üstte rozet. Kart: eski → yeni farkı, gerekçe, kaynak sohbete bağlantı, Onayla / Düzenle /
  Reddet. Aynı kart sohbette de çıkar. Sıralı tıklamalar kuyruklanır, sayfa kilitlenmez (son niyet kazanır kuralı).
- **Harita:** varlıklar büyük düğüm olur, bilgiler/olaylar onlara bağlanır; koleksiyon renkli halka.
- **Sohbette iz:** model bir şey eklediğinde/önerdiğinde araç kartında tek satır + "Geri al" / "İncele".

## 13. Var olan verinin taşınması

Tahmin yapmadan, hiçbir şey kaybetmeden:

1. Tablolar eklenir; her kullanıcıya 4 hazır koleksiyon + **"Eski hafıza"** projesi açılır.
2. `orion_memory` satırları: `source_kind='file'` → Belgeler. Geri kalan her şey (not, sohbet kaydı, resim, ses,
   video) → "Eski hafıza". `group_id`, parçalar, vektörler aynen kalır; yalnızca `collection_id` dolar.
3. İsteğe bağlı tek seferlik "Öneri geçişi" (kullanıcı başlatır): kısa notlar ve sohbet kayıtları çıkarım
   adımından geçer; çıkan bilgi/olaylar **öneri kartı** olarak gelir ("Bunu Ben'e ekleyeyim mi?"). Kabul edilen
   öğenin kaynağı eski satıra bağlanır; eski satır Eski hafıza'da kalır, kullanıcı isterse siler.
4. Eski sohbetler Sohbetler'e otomatik dizinlenmez; "Eski sohbetleri dizinle" düğmesi arka planda yapar
   (gömme yerel GPU/CPU'yu yorar; kullanıcı seçsin).
5. Araç adları `RENAMED_TOOLS` ile taşınır (`delete_memory` → `forget_memory`).

## 14. Ölçek (yıllarca kullanım)

Kabaca tek kullanıcı, 5 yıl: bilgiler birkaç bin, olaylar on binler, sohbet pencereleri yüz binler, belge sayfaları
on binler, medya binler.

- Bilgi ve olay aramaları küçük: kısmi HNSW (yalnızca güncel sürümler) yeter; geçmiş sürümler tam taramayla.
- Sohbetler en büyük koleksiyon: kendi kısmi HNSW indeksi; varsayılan aramada hiç taranmaz, bu yüzden günlük
  aramayı yavaşlatmaz.
- Kelime araması `folded` + trigram (bugünkü). Ad araması varlık tablosunda, çok küçük.
- Sayaçlar bugünkü tetikleyici tablolarla; koleksiyon sütunu eklenir.
- Çıkarım: sohbet başına, yalnızca yeni mesajlar; LLM uzakta (Router). Yerelde yalnızca gömme, düşük öncelikli kuyruk.
- Bellek: profil bloğu ve varlık listesi küçük; büyük listeler sayfalı (bugünkü sayfa/pencere kuralı).
- İleride gerekirse: `halfvec` ile vektör boyutunu yarıya indirme; çok eski sohbet pencerelerini yalnızca özetle
  tutma (kullanıcı kararıyla).

## 15. Açık sorular (her birinde önerim)

1. **Duygu/düşünceler ayrı koleksiyon mu?** Öneri: Hayır. Ben'de `tercih`/`görüş` türü bilgi, günlükte olay +
   `ruh_hali`. Aynı geçmiş kuralı işler.
2. **Çıkarılan yeni bilgiler doğrudan mı yazılsın, hepsi onaya mı?** Öneri: Yeni ve hassas olmayan eklemeler doğrudan
   (sohbette iz + Geri al). Önemli türlerde değişiklik, birleştirme ve her silme kartla. Hepsi onaya giderse kartlar
   birikir ve okunmaz.
3. **Her istemde küçük profil bloğu olsun mu?** Öneri: Evet, ~300 token, hassas bilgi yok, sayfada görünür ve
   düzenlenebilir.
4. **Sağlık gibi hassas bilgiler çıkarılsın mı?** Öneri: Yalnızca kullanıcı kendisi açıkça söylerse; `sensitive`
   işaretli, profilde yok, sayfada kilitli. Ayarla tamamen kapatılabilir.
5. **Eski veri nereye?** Öneri: Dosyalar Belgeler'e, kalanı "Eski hafıza" projesine; Ben'e geçiş yalnızca öneri
   kartlarıyla.
6. **Sohbetler hepsi otomatik mi dizinlensin?** Öneri: Yeni sohbetler otomatik; eskiler düğmeyle. Sohbet başına
   "hafızaya alma" anahtarı (ChatGPT'deki geçici sohbet gibi).
7. **Sohbet silinince ondan çıkan bilgiler?** Öneri: Tek kart sorar, varsayılan "tut" (bilgi kullanıcının, sohbet
   yalnızca kaynaktı). "Her şeyi unut" seçeneği yanında.
8. **Çıkarım hangi modelle?** Öneri: Router'da ayrı "arka plan modeli" ayarı, varsayılan sohbet modeli. Ucuz model
   seçilebilsin.
9. **Çok parçalı hafızalar (albüm) kalsın mı?** Öneri: Bir hafıza = bir şey. Çok parça yalnızca Belgeler'de ve
   kullanıcının açtığı projelerde/albümlerde. Fotoğraflar olaya bağlantıyla eklenir.
10. **Varlık birleştirme ("Ayşe" = "annem")?** Öneri: Model kart ile önerir, kullanıcı onaylar; takma adlar birikir.
11. **Olay boyutu: gün başına bir günlük mü, olay başına bir kayıt mı?** Öneri: Olay başına kayıt; zaman çizelgesi
    günü gruplar.
12. **Belgelerden de bilgi çıkarılsın mı (ör. kira sözleşmesinden adres)?** Öneri: Otomatik değil; belge kartında
    "Bilgi çıkar" düğmesi, sonuçlar öneri kartı.

## 16. Aşamalı plan (küçük, yayımlanabilir adımlar)

Her adım kendi başına çalışır ve eski veriyi bozmaz. Sayfa, araç ve API her adımda aynı kodu paylaşır.

1. **Koleksiyon sütunu + Belgeler + Eski hafıza.** `collection_id`, hazır koleksiyonlar, taşıma (§13 adım 1-2),
   sayfada koleksiyon şeridi, `search_memory` `in` argümanı. Dosya = tek düğüm, sayfalar parça.
2. **Sohbetler dizini.** Mesaj sınırlı pencereler, `indexed_upto`, sohbet silinince pencereler silinir, sohbet
   başına "hafızaya alma" anahtarı, eski sohbetleri dizinle düğmesi. LLM yok.
3. **Varlıklar + bilgiler.** Tablolar, `add_memory type=fact`, `about`, `replaces` (sürümleme), Ben sekmesi:
   varlık kartı, geçmiş listesi. Bilgi vektörleri `orion_memory`'de `kind='fact'`.
4. **Olaylar + tarih çözme.** `type=event`, `date`/`said_as` doğrulaması, fotoğraf bağlama, Yaşadıklarım zaman
   çizelgesi, yıllık tekrarlar ("yaklaşan doğum günleri").
5. **Öneriler + izinler + unutma.** `orion_mem_proposal`, onay kartları (sayfa + sohbet), `forget_memory`,
   silme zinciri (§11), Geri al.
6. **Çıkarım/sıkıştırma.** Arka plan modeli ayarı, tetik, JSON işlemleri, var olanla karşılaştırma, kaynak bağları,
   sohbet özeti ve geçersizleşmesi. Önce varsayılan kapalı, canlı denemeden sonra açık.
7. **Arama cilası.** Token bütçesi, varlık merkezli sonuç kartı, `as_of`/`include_past`, profil bloğu (soru 3'e göre).
8. **Projeler ve Eski hafıza öneri geçişi.** Kullanıcı koleksiyonları, eski kayıtlardan öneri kartları.
9. **Sonra:** belgelerden bilgi çıkarma düğmesi, haritada varlık düğümleri, misafir hesaplarına göre erişim.
