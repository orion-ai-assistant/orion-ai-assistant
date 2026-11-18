#!/usr/bin/env python
"""Fake memory for a dev Orion: one invented Turkish person who used Orion for eight months.

For trying the memory page, search and the profile block with data that looks real. Everything goes through the Hub's own
kernel functions (real EmbeddingGemma embeddings through the Router, sources, versions, counters), never raw inserts into
the vector table. Run it with the Hub's Python, from anywhere, with the Hub and the Router running:

    services/hub/.venv/Scripts/python.exe scripts/dev/seed_memory.py            # make the fake data (user: the one with most chats)
    services/hub/.venv/Scripts/python.exe scripts/dev/seed_memory.py --check    # counts per collection and sample searches
    services/hub/.venv/Scripts/python.exe scripts/dev/seed_memory.py --remove    # take exactly what it made away again

Options: --user <id> (default: the user with most chats), --manifest <path>.

The persona: Emre Kaya, 31, backend developer at "Pusula Yazılım" in Bornova (İzmir); girlfriend Defne (UX designer), mother
Nermin in Manisa (retired teacher), sister Selin in Ankara, cat Tarçın, side project "Sofra" (a recipe app). Ten ordinary chats
between 2026-03-21 and 2026-11-08 are inserted with their real timestamps; the facts, events, notes, images and files are made
from them and carry those timestamps. Some facts change over time (home, the cat's food, the book, the release month).

What was made is written to `seed_memory.manifest.json` next to this script (chat ids, memory groups, entity/fact/event ids;
generated, not committed). `--remove` reads it, trashes and purges those memories, deletes the chats and rows, and deletes the
manifest; a run that stopped half way can be removed the same way. Nothing outside the manifest is touched. The real chats and
memories of the user are not touched either, but the script never deletes the user's existing memory: wipe it first yourself
(it refuses to seed while the user's memory has other rows unless --force is given).
"""
from __future__ import annotations

import argparse
import asyncio
import io
import json
import os
import sys
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
MANIFEST = HERE / "seed_memory.manifest.json"
TZ = timezone(timedelta(hours=3))  # Europe/Istanbul, no summer time since 2016.


def bootstrap() -> None:
    """Make the Hub's modules importable and give them the Hub's own database password (the installation's saved one wins
    over the default in .env.global, exactly as services.shared.native_services.hub_database does)."""
    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(ROOT / "services" / "hub" / "src"))
    os.chdir(ROOT / "services" / "hub")
    from services.shared.native_services import _saved_database_passwords
    saved = _saved_database_passwords(str(ROOT)).get("POSTGRES_PASSWORD")
    if saved:
        os.environ["POSTGRES_PASSWORD"] = saved


def at(day: str, clock: str = "20:30") -> datetime:
    return datetime.fromisoformat(f"{day}T{clock}:00").replace(tzinfo=TZ)


# --- The persona -------------------------------------------------------------------------------------------------------

# key: (name, kind, relation, aliases)
ENTITIES = {
    "nermin": ("Nermin", "person", "annem", ["anne", "annecim", "Nermin Hanım"]),
    "selin": ("Selin", "person", "kız kardeşim", ["kardeşim"]),
    "defne": ("Defne", "person", "sevgilim", ["kız arkadaşım"]),
    "cem": ("Cem", "person", "yakın arkadaşım", ["arkadaşım Cem"]),
    "kerem": ("Kerem", "person", "yöneticim", []),
    "hatice": ("Hatice Teyze", "person", "komşum", ["Hatice"]),
    "asli": ("Dr. Aslı", "person", "veterinerim", ["Aslı Hanım", "veteriner"]),
    "tarcin": ("Tarçın", "pet", "kedim", ["kedi", "Tarçın'ım"]),
    "pusula": ("Pusula Yazılım", "org", "işyerim", ["iş yerim", "şirket"]),
    "sofra": ("Sofra", "thing", "yan projem", ["tarif uygulaması"]),
    "datca": ("Datça", "place", None, []),
    "palamutbuku": ("Palamutbükü", "place", None, []),
    "manisa": ("Manisa", "place", None, []),
    "bornova": ("Bornova", "place", None, []),
}

# Each chat: key, title, first message time, [(user text, assistant text), ...]. A turn is a user message and the answer.
CHATS = [
    {"key": "tuy", "title": "Kedim tüy kusuyor, ne yapmalı?", "start": at("2026-03-21", "21:10"), "turns": [
        ("Selam, ben Emre. Kedim Tarçın son iki gündür sürekli tüy kusuyor, endişelenmeli miyim? 3 yaşında tekir bir kedi, 2023 nisanında barınaktan sahiplenmiştim. Karşıyaka'da yaşıyoruz, evde tek başına dolaşıyor.",
         "Merhaba Emre! Kediler özellikle tüy döktüğü dönemlerde tüy yumağı kusabilir; haftada bir iki kez olması çoğu zaman normaldir. Ama günlerce arka arkaya kusuyorsa, iştahı azalmışsa ya da halsizse veterinere göstermek iyi olur. Tüy dökümü yoğun mu, nasıl besleniyor?"),
        ("Tüyü bol dökülüyor evet, haftada bir tarıyorum sadece. Maması Royal Canin Sterilised, yıllardır onu yiyor ve çok seviyor.",
         "Tarama sıklığını haftada 3-4 güne çıkarmak yumağı belirgin azaltır. Mamasını değiştirmeye gerek yok gibi görünüyor; ama su tüketimini artırmak için ıslak mama ya da su çeşmesi deneyebilirsin. Veterinere de danışmanı öneririm."),
        ("Geçen perşembe zaten veterinerimize götürmüştüm, Dr. Aslı Hanım karın bölgesine baktı, bir sorun yok dedi ve malt macunu önerdi.",
         "Güzel haber, bir sorun olmaması çok iyi. Malt macununu genelde haftada 2-3 kez, parmağına sürüp yalatarak verebilirsin; tüylerin sindirim yoluyla atılmasına yardımcı olur. Taramayı da ihmal etme."),
        ("Tamam, deneyeceğim. Bu arada geçen cumartesi doğum günümdü (14 Mart), sevgilim Defne bana kedi desenli bir kupa aldı :) Tarçın kupayı masadan düşürmeye çalıştı tabii.",
         "Doğum günün kutlu olsun, geç de olsa! Kedi desenli kupa çok tatlı bir hediye; Tarçın'ın da ilgisini çekmesi doğal :) Kupayı masanın kenarından uzak tutmanı öneririm."),
        ("Teşekkürler! Defne UX tasarımcısı, Şubat 2024'ten beri birlikteyiz, Tarçın'a bayılıyor. Bazen onu bana tercih ediyor gibi hissediyorum 😄",
         "Kedilerin yanında yarışmak zor 😄 Tarçın için günlük kısa oyun saatleri koyarsan hem tüy yumağı hem de sindirim için faydalı olur. Bir şey değişirse yine yaz."),
    ]},
    {"key": "tansiyon", "title": "Annemin tansiyonu için ne yapabilirim?", "start": at("2026-04-14", "19:30"), "turns": [
        ("Annemin tansiyonu son zamanlarda yükselmiş, doktor ilaç başlattı. Manisa'da yaşıyor, ben İzmir'deyim ve biraz uzak hissediyorum. Neler yapabilirim?",
         "Geçmiş olsun, annenin sağlığını düşünmen çok anlaşılır. Uzaktan destek için ilaçlarını düzenli içtiğinden emin olabilir, evde tansiyon ölçümü için bir cihaz alıp değerleri birlikte takip edebilirsiniz. Tuz tüketimi ve günlük yürüyüş de ilacı tamamlayan önemli alışkanlıklar. Annenin günlük rutini nasıl?"),
        ("Annem Nermin, 61 yaşında, emekli sınıf öğretmeni. Her sabah ilacını alıyor. Tuzu azaltmaya çalışıyor ama turşuyu çok seviyor 😅",
         "Turşu tuz bakımından yüksek olduğu için miktarı azaltmak ya da evde az tuzlu yapmak iyi bir orta yol olabilir. Doktorunun önerdiği diyeti esas almasını hatırlatman yeterli. Emekli bir öğretmen olarak aktif kalması da sağlığına iyi gelecektir."),
        ("Geçen cumartesi annemi kontrole götürdüm, sonuçlar iyi çıktı. Doktor her gün yürüyüş yapmasını önerdi, ilaç aynen devam.",
         "Çok sevindim, iyi bir haber. Günde 30 dakikalık tempolu yürüyüş tansiyon için gerçekten faydalı. Yanında yürüyebileceği bir komşu ya da arkadaş olursa motivasyonu artar."),
        ("Ayrıca annemin doğum günü de yaklaşıyor, 3 Haziran. Ne alsam bilemedim. Çok kitap okuyor, emekli olduktan sonra elinden kitap düşmüyor.",
         "Kitap seven biri için e-kitap okuyucu harika bir hediye olabilir; ışığı gözünü yormaz, yazı boyutu ayarlanabilir ve yüzlerce kitap taşır. Alternatif olarak sevdiği yazarların özel baskıları ya da bir kitap kulübü üyeliği de düşünülebilir."),
    ]},
    {"key": "sofra", "title": "Tarif uygulaması için mimari fikirleri", "start": at("2026-05-03", "15:20"), "turns": [
        ("Yan proje olarak bir tarif uygulaması yapıyorum, adı Sofra. Arayüz Flutter, backend FastAPI. Dün akşam ilk ekranı çalıştırdım, çok heyecanlandım!",
         "Tebrikler! İlk ekranın çalışması hep motive edici bir an. Sofra için ilk sürümde hangi özellikleri düşünüyorsun: tarif listesi, favoriler, alışveriş listesi?"),
        ("Tarif listesi, favoriler ve en önemlisi malzemeden tarif arama: 'dolapta yumurta ve peynir var' deyince öneri çıksın. Veritabanı için Postgres düşünüyorum.",
         "Malzemeye göre arama için tarif-malzeme ilişkisini ayrı bir tabloda tutup, kullanıcının girdiği malzemelerle eşleşen oranı hesaplayabilirsin. Postgres'te pg_trgm ile yazım hatalarına da tolerans ekleyebilirsin."),
        ("İşte Python ve Go yazıyorum ama arayüz tarafı benim için yeni. Hafta sonları vakit ayırıyorum, aralık gibi yayınlamayı hedefliyorum.",
         "Backend tarafındaki deneyimin FastAPI'de çok işine yarayacak. Aralık hedefi için işi küçük parçalara bölmeni öneririm: önce tarif listesi ve detay ekranı, sonra arama, en son favoriler."),
        ("Defne arayüz tasarımı için yardım edecek, Figma'da çizmeye başlamış bile. Renk paletini de o seçti, sıcak tonlar.",
         "Harika, tasarımcıyla çalışmak Flutter tarafında işleri çok hızlandırır. Figma'dan tasarım token'larını (renkler, boşluklar, yazı stilleri) bir tema dosyasına taşırsan ekranlar tutarlı olur."),
    ]},
    {"key": "dogumgunu", "title": "Annemin doğum günü hediyesi ve pasta", "start": at("2026-06-04", "21:40"), "turns": [
        ("Dün annemin doğum günüydü! Manisa'ya gittik, kardeşim Selin de Ankara'dan geldi. Pasta aldık, annem çok mutlu oldu.",
         "Ne güzel, ailece bir araya gelmek doğum günlerinin en değerli yanı. Annen kaç yaşına girdi, pasta nasıldı?"),
        ("62 yaşına girdi. Çilekli bir pasta aldık, mumları ben üfletmeye çalıştım ama kendisi itiraz etti 😄 Hediye olarak e-kitap okuyucu verdim, hemen üç kitap indirdi.",
         "Harika bir seçim olmuş! Üç kitap indirmesi çok iyiye işaret :) Başlangıçta yazı boyutunu ayarlamasına yardım edersen alışması kolaylaşır."),
        ("Kız kardeşim Selin Ankara'da mimar olarak çalışıyor, 29 yaşında. Ayda bir görüşebiliyoruz ancak. Bu sefer üç gün kalabildi.",
         "Uzak şehirlerde yaşamak sık görüşmeyi zorlaştırıyor, bu yüzden bu tür buluşmalar çok kıymetli. Selin'le arada görüntülü konuşma saati belirlemek de iyi gelebilir."),
        ("Annem yine sütlaç yapmış, o kadar güzeldi ki tepsiyi bitirdik. Annemin sütlacının tarifini bir gün yazdıracağım.",
         "Anne yemeklerinin tarifleri aileler için çok değerli. Telefonda anlatırken adım adım not almak, ölçüleri 'göz kararı' yerine tahmini grama çevirmek işini kolaylaştırır."),
    ]},
    {"key": "datca", "title": "Datça'dan döndük, tatil anıları", "start": at("2026-08-04", "20:15"), "turns": [
        ("Datça'dan döndük, harikaydı! Defne'yle 27 Temmuz'dan 2 Ağustos'a kadar kaldık. Hayıtbükü'nde her gün yüzdük; ikimiz de kalabalık plajları sevmiyoruz, orası tam bize göreydi. Mesudiye'de küçük bir pansiyonda kaldık.",
         "Çok güzel bir tatil olmuş! Hayıtbükü gerçekten sakin ve berrak bir koy. Tekneyle koy gezisi yaptınız mı? Yarımadanın gezilecek çok güzel yerleri var."),
        ("Evet, bir gün tekne turuna çıktık, koyları gezdik. En güzel akşam 31 Temmuz'da Palamutbükü'nde balık yediğimiz akşamdı, gün batımı muhteşemdi.",
         "Gün batımında deniz kenarında balık yemek tatilin zirvesi gibi geliyor. Fotoğraf çekmiş olmalısın?"),
        ("Çektim, birkaçını hafızaya da ekledim. Tarçın'a komşum Hatice Teyze bakmış, ben yokken günde iki kez uğrayıp mamasını vermiş. Döndüğümüzde kucağıma atladı.",
         "Hatice Teyze gibi güvenilir bir komşu büyük şans. Tarçın seni özlemiş :) Kediler ayrı kalınca ilk günlerde biraz mesafeli de olabiliyor ama o hemen barışmış."),
        ("Güneş biraz yaktı, sırtım soyuluyor 😅 ama değdi. Bir daha gitmek istiyorum, belki sonbaharda.",
         "Soyulan ciltte nemlendirici ve aloe vera jel rahatlatır, bir süre güneşten uzak durmak iyi olur. Sonbahar Datça'da çok güzel, hem sakin hem deniz hâlâ ılık oluyor."),
    ]},
    {"key": "is", "title": "İş stresi ve iş değiştirme düşüncesi", "start": at("2026-09-05", "22:50"), "turns": [
        ("Bu hafta sprint teslimi vardı, geçen perşembe gece 1'e kadar çalıştım. Çok yoruldum, işten biraz soğuyorum.",
         "Geçmiş olsun, gece yarısına kadar süren teslimler insanı gerçekten tüketiyor. Teslim bitti, şimdi dinlenmek için vaktin var mı, yoksa yeni sprint hemen başlıyor mu?"),
        ("Yöneticim Kerem iyi biri ama hedefler çok sıkı. Pusula Yazılım'da 5 yıldır çalışıyorum, Şubat 2021'de başladım. Bornova'daki ofiste lojistik yazılımı geliştiriyoruz, ben backend ekibindeyim.",
         "Beş yıl, bir şirkette güzel bir süre. Hedeflerin sıkı olması bazen sprint kapasitesinin planlanmasındaki eksiklikten kaynaklanır; Kerem'le kapasiteyi birlikte gözden geçirmeyi deneyebilirsin."),
        ("Belki işi değiştirmeyi düşünüyorum, uzaktan çalışma ilanı gördüm. Ama maaşı ve rahat ekibi kaybetmek de istemiyorum.",
         "Karar vermeden önce neleri değiştirmek istediğini netleştirmek iyi olur: iş yükü mü, teknoloji mi, uzaktan çalışma mı? İstersen artı-eksi listesi çıkaralım, ya da mevcut işinde önce bu yükleri konuşmayı deneyebilirsin."),
        ("Cem ile de konuştum, üniversiteden yakın arkadaşım, İstanbul'da bir startup'ta çalışıyor. 'Gel' diyor ama İstanbul'a taşınmak istemiyorum.",
         "Taşınma kararı hayatı çok değiştirir, İzmir'i bırakmak istememen çok anlaşılır. Cem'in şirketinde uzaktan çalışma mümkünse önce bunu sorabilirsin."),
    ]},
    {"key": "mide", "title": "Kedimin midesi ve yeni mama", "start": at("2026-09-14", "20:40"), "turns": [
        ("Tarçın hafta sonu iki kere kustu ve iştahı azaldı, cumartesi veterinere götürdük.",
         "Geçmiş olsun, Tarçın'ın durumu kontrol altına alınmış olsun. Veteriner ne dedi?"),
        ("Dr. Aslı Hanım midesi hassas olabilir dedi, Pro Plan Delicate hindili mamaya geçmemizi önerdi. Royal Canin'i bırakıyoruz, geçişi 7 günde yapacağız.",
         "Hassas mideli kediler için mama geçişini yavaş yapmak çok önemli: ilk günler yüzde 25 yeni mama, yüzde 75 eski mama; her iki günde bir oranı artırabilirsin. Bu sürede kusma ya da ishal olursa veterinere haber ver."),
        ("Tavuklu ödül mamalarına bayılıyor ama şimdilik yasak 😿 Çok yalvarıyor.",
         "Ne yazık ki mide hassasiyetinde ödüller de yan etki yapabilir. Geçiş tamamlanıp mide yatışınca veterinerine sorarak tek malzemeli, katkısız bir ödül deneyebilirsin."),
    ]},
    {"key": "tasinma", "title": "Taşınma sonrası kutular ve kedi", "start": at("2026-10-03", "22:15"), "turns": [
        ("Geçen perşembe, 1 Ekim'de Karşıyaka'dan Bornova'ya taşındım! İş yerime artık 10 dakika. Kutular hâlâ dolu, mutfağı bile kuramadım.",
         "Yeni eve hayırlı olsun! İş yerine 10 dakika çok büyük bir konfor. Taşınma sonrası en iyi yöntem önce yatak odası ve mutfağı kurmak; kalanı yavaş yavaş açabilirsin."),
        ("Teşekkürler! Tarçın ilk gün yatağın altına saklandı, bugün pencere önünde güneşleniyor, kuşları izliyor 😊",
         "Kediler taşınmada ilk günler saklanır, normaldir. Pencere önünde rahatlaması iyi işaret; tanıdık eşyalarını (battaniyesi, tırmalaması) yerleştirmen alışmasını hızlandırır."),
        ("Ev iki oda, balkonlu. Balkon güzel ama polen alerjim var, bahar aylarında zor olacak 😅 Defne hafta sonları gelip kalıyor artık.",
         "Balkonda bitkileri seçerken alerjenlerden uzak durmak işe yarayabilir. Alerjinin yoğun olduğu dönemde pencereleri sabah erken saatlerde açıp gün içinde kapalı tutmak da faydalı olur."),
        ("Veterinerimiz Karşıyaka'da kaldı, yakında daha yakın birini bulmam gerekecek. Şimdilik Dr. Aslı'ya devam edeceğiz.",
         "Tarçın'ın tıbbi geçmişini bildiği için Dr. Aslı'yla devam etmek mantıklı; acil durumlar için yakında bir klinik not etmek de iyi olur."),
    ]},
    {"key": "kitap", "title": "Tutunamayanlar hakkında", "start": at("2026-10-25", "17:30"), "turns": [
        ("Oğuz Atay'ın Tutunamayanlar'ını okumaya başladım, 3 haftadır. Yarısındayım. Biraz ağır ama çok etkileyici.",
         "Tutunamayanlar Türk edebiyatının en özgün romanlarından biri; Turgut Özben'in iç dünyası ve dil oyunları ilk başta yorucu olabiliyor ama devam edince çok şey katıyor. Hangi bölüm seni en çok etkiledi?"),
        ("Kitapları genelde hafta sonu sabahları kahvemle okurum, kahveyi sade, sütsüz şekersiz içerim. Dışarı çıkmadan önce bir iki saat kitap oluyor.",
         "Bu güzel bir hafta sonu rutini. Uzun ve yoğun metinler için sabahın sakin saatleri gerçekten ideal; telefonu başka odada bırakırsan odaklanma daha da kolay olur."),
        ("Annem de okumuş, gençliğinde herkes okurmuş diyor. E-kitap okuyucuya Tutunamayanlar'ı da indirmiş.",
         "Birlikte aynı kitabı okumak güzel bir ortak konu olur; telefonda ya da bir hafta sonu buluşmasında kitabı konuşabilirsiniz."),
    ]},
    {"key": "manti", "title": "Annemin mantı tarifi", "start": at("2026-11-08", "14:50"), "turns": [
        ("Dün annemin tarifiyle mantı yaptım, 3 saat sürdü ama değdi! Defne de geldi, birlikte yedik. En sevdiğim yemek zaten mantı.",
         "Elde mantı yapmak sabır işi ama sonuç çok ödüllendirici! Mantıyı hangi boyutta kestin, yoğurt ve sos nasıldı?"),
        ("Küçük, kare kare kestim. Yoğurt sosuna sarımsak ve nane, üstüne tereyağında kırmızı biber. Tarifi annem telefonda anlattı, hafızana kaydeder misin? Hamur: 2 su bardağı un, 1 yumurta, tuz, biraz su. İç: 250 gr kıyma, 1 soğan, tuz, karabiber.",
         "Tabii, tarifi kaydettim. Hamuru dinlendirmek ve çok ince açmak mantının inceliğini belirliyor. Mantıyı önce fırında kızartıp sonra sıcak su eklemek de lezzet katar; annenin yönteminde bu var mı?"),
        ("Evet fırında kızartıyoruz sonra sıcak su ekliyoruz. Kişniş sevmem ama yoğurt sosu için nane ve sarımsak yeterli. Acı biberi bol severim, Defne ise az acı sever.",
         "Anne tarifleri genelde en iyi sonuçları verir. Acı konusunda sofraya ayrı bir acı biber yağı koyarsan herkes kendi tercihine göre ekleyebilir."),
        ("Bu arada Tutunamayanlar'ı bitirdim dün gece! Sırada Kürk Mantolu Madonna var. Sofra'yı da ocak ayına erteledim, arama özelliği zor çıktı.",
         "Tutunamayanlar'ı bitirmen büyük başarı, tebrikler! Kürk Mantolu Madonna çok daha akıcı bir okuma olacak. Sofra için gerçekçi bir erteleme iyi bir karar; arama özelliğini sağlam yapmak ürünün asıl değeri."),
    ]},
]

# Facts: key, entity ("ben" = the user), category, sentence, chat, turn (0-based), since, repeats yearly on, replaces (a key).
# The sentence is the user's own words, one short sentence. Categories are the stored (Turkish) ones.
FACTS = [
    ("adim", "ben", "kimlik", "Adım Emre Kaya.", "tuy", 0, None, None, None),
    ("tarcin_ad", "tarcin", "ilişki", "Kedimin adı Tarçın.", "tuy", 0, None, None, None),
    ("tarcin_yas", "tarcin", "kimlik", "Kedim Tarçın 3 yaşında tekir bir kedi.", "tuy", 0, None, None, None),
    ("tarcin_sahiplen", "tarcin", "tarih", "Tarçın'ı Nisan 2023'te barınaktan sahiplendim.", "tuy", 0, "2023-04-01", None, None),
    ("ev1", "ben", "yer", "Karşıyaka'da yaşıyorum.", "tuy", 0, "2022-09-01", None, None),
    ("mama1", "tarcin", "tercih", "Kedim Tarçın Royal Canin Sterilised mama yiyor.", "tuy", 1, "2023-05-01", None, None),
    ("asli", "asli", "iş", "Dr. Aslı Tarçın'ın veteriner hekimi.", "tuy", 2, None, None, None),
    ("dogum", "ben", "tarih", "Doğum günüm 14 Mart.", "tuy", 3, None, "03-14", None),
    ("defne_ad", "defne", "ilişki", "Sevgilimin adı Defne.", "tuy", 3, None, None, None),
    ("defne_is", "defne", "iş", "Defne UX tasarımcısı.", "tuy", 4, None, None, None),
    ("defne_beri", "defne", "tarih", "Defne'yle Şubat 2024'ten beri birlikteyiz.", "tuy", 4, "2024-02-01", None, None),
    ("anne_yer", "nermin", "yer", "Annem Manisa'da yaşıyor.", "tansiyon", 0, None, None, None),
    ("anne_tansiyon", "nermin", "sağlık", "Annemin tansiyonu var ve her sabah ilaç kullanıyor.", "tansiyon", 0, None, None, None),
    ("anne_ad", "nermin", "ilişki", "Annemin adı Nermin.", "tansiyon", 1, None, None, None),
    ("anne_is", "nermin", "iş", "Annem emekli sınıf öğretmeni.", "tansiyon", 1, None, None, None),
    ("anne_tursu", "nermin", "tercih", "Annem turşuyu çok sever.", "tansiyon", 1, None, None, None),
    ("anne_dogum", "nermin", "tarih", "Annemin doğum günü 3 Haziran.", "tansiyon", 3, None, "06-03", None),
    ("anne_kitap", "nermin", "tercih", "Annem çok kitap okur.", "tansiyon", 3, None, None, None),
    ("sofra1", "sofra", "diğer", "Sofra, Flutter ve FastAPI ile yaptığım bir tarif uygulaması.", "sofra", 0, None, None, None),
    ("sofra_ara", "sofra", "diğer", "Sofra'da malzemeden tarif arama özelliği olacak.", "sofra", 1, None, None, None),
    ("python", "ben", "iş", "İşte Python ve Go yazıyorum.", "sofra", 2, None, None, None),
    ("sofra_tarih1", "sofra", "diğer", "Sofra'yı Aralık'ta yayınlamayı hedefliyorum.", "sofra", 2, "2026-05-03", None, None),
    ("defne_sofra", "defne", "diğer", "Defne Sofra'nın arayüz tasarımına yardım ediyor.", "sofra", 3, None, None, None),
    ("selin_ad", "selin", "ilişki", "Kız kardeşimin adı Selin.", "dogumgunu", 0, None, None, None),
    ("anne_ekitap", "nermin", "tercih", "Annem e-kitap okuyucusunu çok kullanıyor.", "dogumgunu", 1, "2026-06-03", None, None),
    ("selin_is", "selin", "iş", "Selin Ankara'da mimar olarak çalışıyor.", "dogumgunu", 2, None, None, None),
    ("anne_sutlac", "nermin", "tercih", "Annem sütlacı çok güzel yapar.", "dogumgunu", 3, None, None, None),
    ("kalabalik", "ben", "tercih", "Kalabalık plajları sevmem.", "datca", 0, None, None, None),
    ("defne_plaj", "defne", "tercih", "Defne kalabalık plajları sevmez, sakin koyları tercih eder.", "datca", 0, None, None, None),
    ("hayitbuku", "ben", "yer", "Datça'da en çok Hayıtbükü'nü sevdim.", "datca", 0, None, None, None),
    ("hatice", "hatice", "ilişki", "Hatice Teyze komşumdur, ben yokken Tarçın'a o bakar.", "datca", 2, None, None, None),
    ("pusula_is", "ben", "iş", "Pusula Yazılım'da backend geliştirici olarak çalışıyorum.", "is", 1, "2021-02-01", None, None),
    ("kerem", "kerem", "iş", "Kerem işyerimdeki yöneticim.", "is", 1, None, None, None),
    ("pusula", "pusula", "diğer", "Pusula Yazılım Bornova'da lojistik yazılımı geliştiren bir şirket.", "is", 1, None, None, None),
    ("is_degistir", "ben", "görüş", "İşimi değiştirmeyi düşünüyorum, son aylarda çok yoruldum.", "is", 2, "2026-09-05", None, None),
    ("cem_ad", "cem", "ilişki", "Cem üniversiteden yakın arkadaşım.", "is", 3, None, None, None),
    ("cem_is", "cem", "iş", "Cem İstanbul'da bir startup'ta çalışıyor.", "is", 3, None, None, None),
    ("mama2", "tarcin", "tercih", "Kedim Tarçın artık Pro Plan Delicate hindili mama yiyor.", "mide", 1, "2026-09-12", None, "mama1"),
    ("tarcin_mide", "tarcin", "diğer", "Kedim Tarçın'ın midesi hassas.", "mide", 1, "2026-09-12", None, None),
    ("tarcin_odul", "tarcin", "tercih", "Kedim Tarçın tavuklu ödül mamalarını çok sever.", "mide", 2, None, None, None),
    ("ev2", "ben", "yer", "Bornova'da yaşıyorum.", "tasinma", 0, "2026-10-01", None, "ev1"),
    ("tarcin_pencere", "tarcin", "tercih", "Kedim Tarçın yeni evde pencere önünde kuş izlemeyi sever.", "tasinma", 1, None, None, None),
    ("ev_balkon", "ben", "yer", "Yeni evim iki odalı ve balkonlu.", "tasinma", 2, "2026-10-01", None, None),
    ("polen", "ben", "sağlık", "Polen alerjim var.", "tasinma", 2, None, None, None),
    ("kitap1", "ben", "diğer", "Şu sıralar Tutunamayanlar'ı okuyorum.", "kitap", 0, "2026-10-04", None, None),
    ("kahve", "ben", "tercih", "Kahveyi sütsüz ve şekersiz içerim.", "kitap", 1, None, None, None),
    ("hafta_sonu", "ben", "tercih", "Hafta sonu sabahları kahvemle kitap okumayı severim.", "kitap", 1, None, None, None),
    ("mantı", "ben", "tercih", "En sevdiğim yemek mantıdır.", "manti", 0, None, None, None),
    ("kisnis", "ben", "tercih", "Kişniş sevmem.", "manti", 2, None, None, None),
    ("aci", "ben", "tercih", "Acı biberi bol severim.", "manti", 2, None, None, None),
    ("defne_aci", "defne", "tercih", "Defne az acı sever.", "manti", 2, None, None, None),
    ("kitap2", "ben", "diğer", "Tutunamayanlar'ı bitirdim, şimdi Kürk Mantolu Madonna'yı okuyorum.", "manti", 3, "2026-11-07", None, "kitap1"),
    ("sofra_tarih2", "sofra", "diğer", "Sofra'yı Ocak'ta yayınlamayı hedefliyorum.", "manti", 3, "2026-11-08", None, "sofra_tarih1"),
]

# Events: key, chat, turn, title, body, date, end date, precision, the user's words for the date, mood, people, place, photos.
EVENTS = [
    ("sahiplen", "tuy", 0, "Tarçın'ı barınaktan sahiplendim", "Küçük bir tekir yavruydu, ilk gün koltuğun altına saklandı.",
     "2023-04", None, "approx", "yaklaşık 3 yıl önce nisanda", None, ["ben", "tarcin"], None, []),
    ("veteriner1", "tuy", 2, "Tarçın'ı veterinere götürdüm", "Dr. Aslı karın bölgesine baktı, bir sorun yok; malt macunu önerdi.",
     "2026-03-19", None, None, "geçen perşembe", None, ["ben", "tarcin", "asli"], None, []),
    ("dogum_gunum", "tuy", 3, "Doğum günümü Defne ile kutladım", "Defne bana kedi desenli bir kupa aldı.",
     "2026-03-14", None, None, "geçen cumartesi", None, ["ben", "defne"], None, []),
    ("anne_kontrol", "tansiyon", 2, "Annemi tansiyon kontrolüne götürdüm", "Sonuçlar iyi çıktı; doktor her gün yürüyüş önerdi, ilaç aynen devam.",
     "2026-04-11", None, None, "geçen cumartesi", None, ["ben", "nermin"], "manisa", []),
    ("sofra_ilk", "sofra", 0, "Sofra'nın ilk ekranını çalıştırdım", "Flutter ile yazdığım tarif listesi ekranı ilk kez çalıştı.",
     "2026-05-02", None, None, "dün", "heyecanlı", ["ben"], None, ["laptop"]),
    ("anne_dogum_gunu", "dogumgunu", 0, "Annemin doğum günü", "Çilekli pasta aldık, hediye olarak e-kitap okuyucu verdim; annem hemen üç kitap indirdi.",
     "2026-06-03", None, None, "dün", "mutlu", ["ben", "nermin", "selin"], "manisa", ["pasta"]),
    ("datca", "datca", 0, "Datça tatili", "Hayıtbükü'nde her gün yüzdük, bir gün tekneyle koyları gezdik; Mesudiye'de küçük bir pansiyonda kaldık.",
     "2026-07-27", "2026-08-02", "day", None, "mutlu", ["ben", "defne"], "datca", ["plaj", "tekne"]),
    ("balik", "datca", 1, "Palamutbükü'nde balık akşamı", "Gün batımında balık yedik, çok güzel bir akşamdı.",
     "2026-07-31", None, None, None, None, ["ben", "defne"], "palamutbuku", ["gunbatimi"]),
    ("sprint", "is", 0, "Sprint teslimi için gece 1'e kadar çalıştım", None,
     "2026-09-03", None, None, "geçen perşembe", "yorgun", ["ben", "kerem"], None, []),
    ("veteriner2", "mide", 0, "Tarçın'ı midesi için veterinere götürdük", "İki kez kusmuştu, iştahı azalmıştı. Yeni mama önerildi.",
     "2026-09-12", None, None, "geçen cumartesi", None, ["ben", "tarcin", "asli"], None, ["uyuyan_kedi"]),
    ("tasinma", "tasinma", 0, "Karşıyaka'dan Bornova'ya taşındım", "Yeni ev iki oda, balkonlu; işe 10 dakika. Tarçın ilk gün yatağın altına saklandı.",
     "2026-10-01", None, None, "geçen perşembe", None, ["ben", "tarcin"], "bornova", ["kutular", "kedi_pencere"]),
    ("manti", "manti", 0, "Annemin tarifiyle mantı yaptım", "Elde, küçük kare kare kestim; üç saat sürdü. Defne'yle birlikte yedik.",
     "2026-11-07", None, None, "dün", "mutlu", ["ben", "defne"], "bornova", ["mantı"]),
]

# Notes the user asked to be saved: key, title, text, when, source kind ("assistant": saved by the model on request).
NOTES = [
    ("n_vet", "Tarçın'ın veteriner notu",
     "Veteriner: Dr. Aslı (Karşıyaka). Tarçın'a haftada 2-3 kez malt macunu verilecek, haftada 3-4 kez taranacak. Aşı takvimi: her yıl mayıs ayında.",
     at("2026-03-21", "21:42"), "assistant"),
    ("n_anne", "Annem için hediye fikirleri",
     "Annem Nermin için e-kitap okuyucu aldım (Haziran 2026). Gelecek yıl için fikirler: sevdiği yazarların özel baskıları, kitap kulübü üyeliği, Manisa'da hafta sonu kahvaltısı.",
     at("2026-06-04", "21:52"), "assistant"),
    ("n_defne", "Defne'nin doğum günü",
     "Defne'nin doğum günü 9 Aralık. Hediye fikirleri: Figma için tablet kalemi, Datça fotoğraflarının baskısı, kedi desenli ev terliği.",
     at("2026-09-14", "21:10"), "manual"),
    ("n_kitap", "Okunacak kitaplar",
     "Kürk Mantolu Madonna (Sabahattin Ali), Körlük (Saramago), Sefiller (Hugo), Dönüşüm (Kafka). Hafta sonu sabahları için.",
     at("2026-10-25", "18:05"), "manual"),
    ("n_manti", "Mantı hamuru ve iç malzemesi",
     "Annemin mantısı. Hamur: 2 su bardağı un, 1 yumurta, tuz, biraz su. İç: 250 gr kıyma, 1 soğan, tuz, karabiber. Küçük kareler kesilir, fırında kızartılır, üzerine sıcak su eklenir. Yoğurt sosu: sarımsak ve nane.",
     at("2026-11-08", "15:02"), "assistant"),
]

# Images: key, file name, the user's caption, when. Drawn below (simple illustrations).
IMAGES = [
    ("kedi_kanepe", "tarcin-kanepe.png", "Tarçın kanepede güneşleniyor", at("2026-03-20", "18:12")),
    ("pasta", "annem-dogum-gunu-pastasi.png", "Annemin doğum günü pastası, çilekli", at("2026-06-03", "19:40")),
    ("laptop", "sofra-ilk-ekran.png", "Sofra'nın ilk ekranı çalıştı", at("2026-05-02", "22:15")),
    ("plaj", "hayitbuku-plaj.png", "Hayıtbükü'nde sabah, deniz çok berrak", at("2026-07-28", "10:20")),
    ("tekne", "datca-tekne-turu.png", "Tekne turunda koylardan biri", at("2026-07-30", "13:05")),
    ("gunbatimi", "palamutbuku-gun-batimi.png", "Palamutbükü'nde gün batımı", at("2026-07-31", "20:10")),
    ("uyuyan_kedi", "tarcin-uyuyor.png", "Tarçın mide sorunundan sonra yorgun uyuyor", at("2026-09-13", "16:30")),
    ("kutular", "tasinma-kutular.png", "Taşınma günü, kutular hâlâ dolu", at("2026-10-01", "17:45")),
    ("kedi_pencere", "tarcin-pencere.png", "Tarçın yeni evde pencere önünde", at("2026-10-03", "15:30")),
    ("mantı", "manti.png", "Annemin tarifiyle yaptığım mantı", at("2026-11-07", "20:05")),
]

# Files (text): key, name, text, caption, when.
FILES = [
    ("f_sofra", "sofra-proje-notlari.md", """# Sofra: proje notları

## Amaç
Evdeki malzemelerden tarif öneren, tarifleri kaydedip alışveriş listesi çıkaran bir mobil uygulama.

## Teknoloji
- Arayüz: Flutter (Android ve iOS), Riverpod ile durum yönetimi
- Backend: FastAPI, PostgreSQL, tarif-malzeme ilişkisi ayrı tabloda
- Arama: pg_trgm ile yazım hatasına toleranslı malzeme eşleştirme, eşleşen malzeme oranına göre sıralama
- Tasarım: Figma, Defne'nin hazırladığı sıcak renk paleti (turuncu, krem, koyu kahve)

## Yapılacaklar
1. Tarif listesi ve detay ekranı (bitti)
2. Favoriler ve alışveriş listesi
3. "Dolapta ne var" aramasının sıralama ayarı
4. Kullanıcı girişi, tarif paylaşımı
5. Mağaza yayını

## Notlar
- İlk hedef aralıktı, arama özelliği zor çıktığı için ocak ayına ertelendi.
- Hafta sonları 4-5 saat çalışıyorum, haftaiçi sadece küçük düzeltmeler.
""", "Sofra projesinin mimari ve yapılacaklar notları", at("2026-05-10", "11:30")),
    ("f_manti", "manti-tarifi-annem.txt", """Annemin mantı tarifi (4 kişilik)

Hamur
- 2 su bardağı un
- 1 yumurta
- 1 tutam tuz
- Yumuşak bir hamur olacak kadar su

İç
- 250 gr orta yağlı kıyma
- 1 küçük kuru soğan, rendelenmiş
- Tuz, karabiber

Yapılışı
1. Hamuru yoğurup yarım saat dinlendir, ince aç ve küçük kareler kes.
2. Her karenin ortasına çok az iç koy, köşelerini birleştir.
3. Mantıları yağlı tepsiye diz, fırında üstleri hafif kızarana kadar pişir.
4. Üzerine sıcak tuzlu su ekle, bir on dakika daha pişir.
5. Yoğurdu sarımsakla çırp. Tereyağında kırmızı biberi yak, mantının üstüne gezdir. Kuru naneyle servis et.

Not: Kişniş koyma. Acı sevenlere ayrı pul biber.
""", "Annemin telefonda anlattığı mantı tarifi", at("2026-11-08", "15:10")),
    ("f_cv", "emre-kaya-cv.md", """# Emre Kaya
Backend geliştirici, İzmir

## Deneyim
**Pusula Yazılım, Bornova** (Şubat 2021 - devam ediyor)
- Lojistik yönetim yazılımının backend ekibinde Python ve Go ile servis geliştirme
- Sipariş ve rota hesaplama servislerinin performans iyileştirmesi (ortalama yanıt süresi %40 düştü)
- Kod incelemesi, ekip içi mentorluk

**Ege Bilişim, Alsancak** (2018 - 2021)
- Müşteri portalı için REST API geliştirme, PostgreSQL

## Eğitim
Ege Üniversitesi, Bilgisayar Mühendisliği (2013 - 2018)

## Yetenekler
Python, Go, FastAPI, PostgreSQL, Docker, Flutter (öğreniyor)

## Yan proje
Sofra: Flutter ve FastAPI ile geliştirilen tarif uygulaması
""", "Güncel özgeçmişim", at("2026-09-10", "23:05")),
    ("f_datca", "datca-gezi-plani.txt", """Datça gezi planı (27 Temmuz - 2 Ağustos)

Konaklama: Mesudiye'de küçük bir pansiyon, Hayıtbükü'ne yürüme mesafesi.
Yol: İzmir'den arabayla yaklaşık 4,5 saat, Marmaris yolu üzerinden.

Plan
- 27 Temmuz: Yolculuk, akşam Datça merkezde yemek
- 28-29 Temmuz: Hayıtbükü'nde plaj, akşam Eski Datça
- 30 Temmuz: Tekne turu, koylarda yüzme
- 31 Temmuz: Palamutbükü'nde balık akşamı, gün batımı
- 1 Ağustos: Knidos antik kenti
- 2 Ağustos: Dönüş

Tarçın için Hatice Teyze günde iki kez uğrayacak.
""", "Datça tatili için hazırladığımız plan", at("2026-07-10", "22:20")),
]


# --- Illustrations (Pillow) -------------------------------------------------------------------------------------------

W, H, S = 640, 480, 2  # Drawn at twice the size and scaled down: smooth edges.


def _font(size: int, bold: bool = False):
    from PIL import ImageFont
    for name in ("segoeuib.ttf", "segoeui.ttf", "arialbd.ttf", "arial.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size * S)
        except OSError:
            continue
    return ImageFont.load_default()


def _canvas(top, bottom):
    from PIL import Image, ImageDraw
    image = Image.new("RGB", (W * S, H * S), top)
    draw = ImageDraw.Draw(image)
    for y in range(H * S):
        t = y / (H * S - 1)
        draw.line([(0, y), (W * S, y)], fill=tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3)))
    return image, draw


def _png(image) -> bytes:
    from PIL import Image
    out = io.BytesIO()
    image.resize((W, H), Image.LANCZOS).save(out, "PNG", optimize=True)
    return out.getvalue()


def _p(*values):
    return [v * S for v in values]


def _cat(d, cx, cy, s, fur=(222, 152, 72), stripe=(150, 92, 36), eyes=True):
    """A sitting tabby cat; (cx, cy) the middle of the body, s its size."""
    cx, cy, s = cx * S, cy * S, s * S
    d.arc([cx + 0.5 * s, cy - 0.1 * s, cx + 1.7 * s, cy + 1.5 * s], 250, 90, fill=fur, width=int(0.28 * s))  # Tail.
    d.ellipse([cx - 0.85 * s, cy - 0.35 * s, cx + 0.85 * s, cy + 1.2 * s], fill=fur)  # Body.
    for k in (-0.45, -0.1, 0.25):
        d.arc([cx + k * s - 0.2 * s, cy - 0.2 * s, cx + k * s + 0.5 * s, cy + 1.0 * s], 200, 340, fill=stripe, width=int(0.07 * s))
    for side in (-1, 1):  # Ears.
        d.polygon([(cx + side * 0.62 * s, cy - 0.95 * s), (cx + side * 0.55 * s, cy - 1.7 * s), (cx + side * 0.15 * s, cy - 1.25 * s)], fill=fur)
        d.polygon([(cx + side * 0.52 * s, cy - 1.0 * s), (cx + side * 0.5 * s, cy - 1.5 * s), (cx + side * 0.25 * s, cy - 1.2 * s)], fill=(240, 170, 160))
    d.ellipse([cx - 0.75 * s, cy - 1.45 * s, cx + 0.75 * s, cy - 0.2 * s], fill=fur)  # Head.
    for k in (-0.2, 0, 0.2):
        d.line([cx + k * s, cy - 1.42 * s, cx + k * s, cy - 1.15 * s], fill=stripe, width=int(0.06 * s))
    if eyes:
        for side in (-1, 1):
            d.ellipse([cx + side * 0.42 * s - 0.17 * s, cy - 0.98 * s - 0.17 * s, cx + side * 0.42 * s + 0.17 * s, cy - 0.98 * s + 0.17 * s], fill=(120, 190, 90))
            d.ellipse([cx + side * 0.42 * s - 0.05 * s, cy - 0.98 * s - 0.14 * s, cx + side * 0.42 * s + 0.05 * s, cy - 0.98 * s + 0.14 * s], fill=(20, 20, 20))
    else:
        for side in (-1, 1):
            d.arc([cx + side * 0.42 * s - 0.17 * s, cy - 1.0 * s, cx + side * 0.42 * s + 0.17 * s, cy - 0.8 * s], 0, 180, fill=(60, 40, 20), width=int(0.04 * s))
    d.polygon([(cx - 0.07 * s, cy - 0.7 * s), (cx + 0.07 * s, cy - 0.7 * s), (cx, cy - 0.6 * s)], fill=(235, 130, 130))
    for side in (-1, 1):
        for dy in (-0.05, 0.05):
            d.line([cx + side * 0.25 * s, cy - 0.62 * s, cx + side * 0.85 * s, cy - 0.7 * s + dy * s * 3], fill=(250, 250, 250), width=max(1, int(0.015 * s)))


def draw_kedi_kanepe() -> bytes:
    image, d = _canvas((246, 232, 210), (236, 214, 184))
    d.rectangle(_p(0, 330, 640, 480), fill=(190, 150, 110))  # Floor.
    d.rounded_rectangle(_p(60, 230, 580, 400), 40, fill=(88, 120, 130))  # Sofa back.
    d.rounded_rectangle(_p(40, 330, 600, 420), 30, fill=(110, 146, 156))
    d.rectangle(_p(70, 415, 100, 445), fill=(80, 60, 40))
    d.rectangle(_p(540, 415, 570, 445), fill=(80, 60, 40))
    d.polygon(_p(130, 0, 330, 0, 400, 200, 60, 200), fill=(255, 246, 200))  # Window light.
    d.rectangle(_p(150, 20, 310, 180), fill=(182, 220, 240))
    d.line(_p(230, 20, 230, 180), fill=(255, 255, 255), width=6 * S)
    _cat(d, 330, 310, 85)
    d.rounded_rectangle(_p(420, 285, 520, 350), 16, fill=(214, 120, 90))  # Cushion.
    return _png(image)


def draw_uyuyan_kedi() -> bytes:
    image, d = _canvas((70, 78, 110), (40, 44, 72))
    d.rectangle(_p(0, 340, 640, 480), fill=(98, 70, 90))
    d.ellipse(_p(120, 280, 520, 440), fill=(186, 150, 190))  # Blanket.
    d.ellipse(_p(170, 250, 470, 410), fill=(222, 152, 72))  # Curled body.
    for k in range(4):
        d.arc(_p(190 + k * 55, 262, 280 + k * 55, 390), 200, 340, fill=(150, 92, 36), width=8 * S)
    d.ellipse(_p(150, 280, 280, 380), fill=(222, 152, 72))  # Head.
    d.polygon(_p(158, 295, 160, 245, 200, 280), fill=(222, 152, 72))
    d.polygon(_p(240, 282, 262, 238, 278, 298), fill=(222, 152, 72))
    d.arc(_p(170, 318, 205, 338), 0, 180, fill=(60, 40, 20), width=3 * S)
    d.arc(_p(222, 318, 257, 338), 0, 180, fill=(60, 40, 20), width=3 * S)
    d.arc(_p(280, 330, 500, 440), 20, 160, fill=(222, 152, 72), width=22 * S)  # Tail around.
    f = _font(40)
    for k, (x, y) in enumerate(((330, 190), (372, 150), (420, 118))):
        d.text((x * S, y * S), "z", font=_font(30 + k * 10), fill=(220, 220, 250))
    d.ellipse(_p(520, 60, 580, 120), fill=(250, 240, 200))  # Moon.
    d.ellipse(_p(540, 52, 600, 112), fill=(55, 60, 95))
    return _png(image)


def draw_kedi_pencere() -> bytes:
    image, d = _canvas((250, 238, 220), (236, 222, 200))
    d.rectangle(_p(150, 40, 490, 330), fill=(120, 80, 50))  # Frame.
    d.rectangle(_p(165, 55, 475, 315), fill=(168, 214, 240))
    d.ellipse(_p(380, 70, 440, 130), fill=(255, 226, 120))  # Sun.
    d.ellipse(_p(190, 90, 290, 130), fill=(255, 255, 255))
    d.ellipse(_p(230, 80, 330, 125), fill=(255, 255, 255))
    d.rectangle(_p(165, 250, 475, 315), fill=(90, 160, 100))  # Hills.
    d.line(_p(320, 55, 320, 315), fill=(120, 80, 50), width=8 * S)
    d.line(_p(165, 185, 475, 185), fill=(120, 80, 50), width=8 * S)
    d.line(_p(430, 150, 442, 142), fill=(60, 60, 70), width=3 * S)  # A bird.
    d.arc(_p(405, 140, 435, 160), 200, 340, fill=(60, 60, 70), width=3 * S)
    d.arc(_p(430, 140, 460, 160), 200, 340, fill=(60, 60, 70), width=3 * S)
    d.rectangle(_p(120, 330, 520, 360), fill=(150, 104, 66))  # Sill.
    _cat(d, 320, 270, 62, eyes=True)
    d.rectangle(_p(0, 360, 640, 480), fill=(210, 190, 160))
    return _png(image)


def draw_pasta() -> bytes:
    image, d = _canvas((255, 232, 226), (250, 210, 200))
    d.ellipse(_p(60, 330, 580, 440), fill=(240, 240, 240))  # Plate.
    d.ellipse(_p(80, 340, 560, 430), fill=(252, 252, 252))
    d.rectangle(_p(150, 230, 490, 370), fill=(246, 214, 170))  # Cake layers.
    d.ellipse(_p(150, 350, 490, 395), fill=(246, 214, 170))
    d.rectangle(_p(150, 285, 490, 305), fill=(214, 90, 110))
    d.rectangle(_p(150, 330, 490, 350), fill=(214, 90, 110))
    d.ellipse(_p(150, 205, 490, 255), fill=(255, 243, 235))  # Cream top.
    d.rectangle(_p(150, 230, 490, 250), fill=(255, 243, 235))
    for k in range(8):  # Strawberries.
        x = 175 + k * 40
        d.ellipse(_p(x, 190, x + 30, 225), fill=(220, 50, 70))
        d.polygon(_p(x + 6, 192, x + 15, 182, x + 24, 192), fill=(70, 150, 70))
    for x in (260, 320, 380):  # Candles.
        d.rectangle(_p(x, 150, x + 10, 205), fill=(120, 170, 230))
        d.ellipse(_p(x - 3, 118, x + 13, 152), fill=(255, 190, 60))
        d.ellipse(_p(x + 1, 128, x + 9, 150), fill=(255, 240, 150))
    f = _font(26)
    d.text((320 * S, 60 * S), "Doğum günün kutlu olsun", font=f, fill=(170, 60, 90), anchor="mm")
    for x, y, c in ((90, 90, (255, 200, 80)), (540, 110, (120, 190, 240)), (120, 200, (240, 120, 160)), (520, 230, (140, 210, 140))):
        d.ellipse(_p(x, y, x + 26, y + 26), fill=c)
    return _png(image)


def draw_laptop() -> bytes:
    image, d = _canvas((226, 232, 240), (206, 214, 228))
    d.polygon(_p(40, 400, 600, 400, 640, 450, 0, 450), fill=(170, 176, 190))  # Base.
    d.rounded_rectangle(_p(90, 50, 550, 400), 18, fill=(60, 64, 76))
    d.rectangle(_p(105, 66, 535, 384), fill=(255, 246, 232))  # Screen: the app.
    d.rectangle(_p(105, 66, 535, 110), fill=(214, 112, 58))
    d.text((320 * S, 88 * S), "Sofra", font=_font(26), fill=(255, 255, 255), anchor="mm")
    cards = (("Menemen", "15 dk", (250, 214, 170)), ("Mercimek çorbası", "30 dk", (240, 190, 130)),
             ("Mantı", "90 dk", (232, 168, 108)), ("Sütlaç", "45 dk", (246, 226, 190)))
    for i, (name, minutes, color) in enumerate(cards):
        x, y = 125 + (i % 2) * 205, 130 + (i // 2) * 120
        d.rounded_rectangle(_p(x, y, x + 190, y + 105), 12, fill=color)
        d.text(((x + 14) * S, (y + 30) * S), name, font=_font(17), fill=(80, 40, 20), anchor="lm")
        d.text(((x + 14) * S, (y + 70) * S), minutes, font=_font(14), fill=(120, 70, 40), anchor="lm")
    d.rounded_rectangle(_p(125, 345, 515, 372), 14, fill=(255, 255, 255), outline=(214, 112, 58), width=2 * S)
    d.text((140 * S, 358 * S), "dolapta ne var?  yumurta, peynir", font=_font(14), fill=(140, 110, 90), anchor="lm")
    d.ellipse(_p(310, 408, 330, 420), fill=(150, 156, 170))
    return _png(image)


def draw_plaj() -> bytes:
    image, d = _canvas((150, 208, 240), (210, 236, 250))
    d.ellipse(_p(480, 40, 560, 120), fill=(255, 232, 130))
    d.rectangle(_p(0, 200, 640, 320), fill=(40, 156, 196))  # Sea.
    for k, y in enumerate(range(215, 315, 22)):
        d.line(_p(0, y, 640, y), fill=(72, 180, 214) if k % 2 else (30, 140, 184), width=3 * S)
    d.polygon(_p(0, 200, 120, 150, 260, 200), fill=(120, 150, 110))  # Far hill.
    d.polygon(_p(200, 200, 340, 160, 470, 200), fill=(100, 138, 98))
    d.rectangle(_p(0, 320, 640, 480), fill=(240, 220, 170))  # Sand.
    d.polygon(_p(0, 320, 640, 320, 640, 345, 0, 365), fill=(250, 236, 196))
    d.line(_p(420, 300, 420, 420), fill=(110, 80, 50), width=5 * S)  # Umbrella.
    d.pieslice(_p(340, 260, 500, 360), 180, 360, fill=(230, 80, 70))
    d.pieslice(_p(370, 260, 470, 360), 180, 360, fill=(250, 250, 250))
    d.rounded_rectangle(_p(150, 400, 300, 440), 8, fill=(70, 130, 180))  # Towel.
    for k in range(5):
        d.line(_p(160 + k * 30, 400, 160 + k * 30, 440), fill=(240, 240, 240), width=3 * S)
    return _png(image)


def draw_tekne() -> bytes:
    image, d = _canvas((170, 216, 242), (220, 240, 250))
    d.ellipse(_p(60, 50, 120, 110), fill=(255, 232, 130))
    d.rectangle(_p(0, 230, 640, 480), fill=(28, 128, 180))
    for k, y in enumerate(range(240, 470, 26)):
        d.arc(_p(30 + (k % 3) * 70, y, 130 + (k % 3) * 70, y + 20), 200, 340, fill=(90, 190, 220), width=3 * S)
    d.polygon(_p(380, 230, 640, 160, 640, 235), fill=(172, 150, 112))  # Rocky coast.
    d.polygon(_p(430, 232, 560, 190, 640, 232), fill=(196, 176, 134))
    d.polygon(_p(150, 330, 450, 330, 410, 400, 190, 400), fill=(250, 250, 250))  # Hull.
    d.rectangle(_p(150, 345, 450, 355), fill=(190, 60, 60))
    d.polygon(_p(290, 90, 290, 320, 420, 320), fill=(252, 244, 226))  # Sail.
    d.polygon(_p(270, 120, 270, 320, 180, 320), fill=(244, 226, 190))
    d.line(_p(290, 80, 290, 330), fill=(110, 80, 50), width=5 * S)
    d.polygon(_p(290, 80, 330, 92, 290, 104), fill=(220, 60, 60))
    d.ellipse(_p(160, 405, 460, 435), fill=(60, 160, 200))
    return _png(image)


def draw_gunbatimi() -> bytes:
    image, d = _canvas((250, 160, 80), (130, 70, 120))
    d.ellipse(_p(240, 160, 400, 320), fill=(255, 220, 130))  # Sun, half under the sea.
    d.rectangle(_p(0, 300, 640, 480), fill=(88, 60, 120))
    for k, y in enumerate(range(305, 470, 14)):
        w = 150 - k * 6
        d.line(_p(320 - max(w, 20), y, 320 + max(w, 20), y), fill=(250, 190, 110) if k % 2 == 0 else (200, 120, 110), width=4 * S)
    d.polygon(_p(0, 300, 90, 250, 210, 300), fill=(60, 40, 80))  # Far land.
    d.polygon(_p(480, 300, 580, 262, 640, 300), fill=(60, 40, 80))
    d.polygon(_p(430, 296, 500, 296, 485, 316, 445, 316), fill=(40, 28, 60))  # A small boat.
    d.line(_p(466, 260, 466, 296), fill=(40, 28, 60), width=3 * S)
    d.polygon(_p(466, 262, 466, 292, 490, 292), fill=(40, 28, 60))
    d.ellipse(_p(60, 80, 90, 95), fill=(255, 210, 170))
    d.ellipse(_p(500, 100, 560, 116), fill=(255, 200, 160))
    return _png(image)


def draw_kutular() -> bytes:
    image, d = _canvas((240, 236, 228), (214, 206, 190))
    d.rectangle(_p(0, 360, 640, 480), fill=(190, 170, 140))
    boxes = ((90, 250, 260, 400, "MUTFAK"), (270, 280, 420, 400, "KİTAP"), (430, 230, 590, 400, "YATAK ODASI"), (130, 140, 270, 250, "KIRILIR"))
    for x0, y0, x1, y1, label in boxes:
        d.rectangle(_p(x0, y0, x1, y1), fill=(196, 150, 96), outline=(140, 100, 60), width=3 * S)
        d.rectangle(_p((x0 + x1) // 2 - 12, y0, (x0 + x1) // 2 + 12, y1), fill=(222, 196, 140))
        d.text((((x0 + x1) // 2) * S, ((y0 + y1) // 2 + 20) * S), label, font=_font(15), fill=(80, 50, 30), anchor="mm")
    d.polygon(_p(300, 280, 310, 242, 330, 272), fill=(222, 152, 72))  # The cat's ears behind the box.
    d.polygon(_p(350, 272, 372, 242, 380, 282), fill=(222, 152, 72))
    d.ellipse(_p(296, 266, 388, 300), fill=(222, 152, 72))
    d.ellipse(_p(318, 275, 332, 290), fill=(120, 190, 90))
    d.ellipse(_p(350, 275, 364, 290), fill=(120, 190, 90))
    return _png(image)


def draw_manti() -> bytes:
    image, d = _canvas((236, 224, 208), (214, 196, 170))
    d.ellipse(_p(70, 120, 570, 440), fill=(250, 250, 250), outline=(200, 200, 200), width=3 * S)  # Plate.
    d.ellipse(_p(100, 150, 540, 410), fill=(246, 244, 238))
    d.ellipse(_p(150, 190, 490, 380), fill=(252, 248, 236))  # Yogurt.
    import random
    rng = random.Random(7)
    for _ in range(46):  # Manti.
        x, y = rng.randint(170, 450), rng.randint(205, 350)
        d.polygon(_p(x, y + 14, x + 10, y, x + 20, y + 14, x + 10, y + 26), fill=(232, 188, 120), outline=(200, 150, 90))
        d.ellipse(_p(x + 7, y + 10, x + 13, y + 16), fill=(206, 160, 100))
    for k in range(9):  # Red pepper butter.
        x = 190 + k * 33
        d.line(_p(x, 215, x + 30, 330), fill=(200, 50, 30), width=5 * S)
    for x, y in ((220, 250), (300, 300), (380, 240), (330, 215)):  # Mint.
        d.ellipse(_p(x, y, x + 14, y + 8), fill=(70, 150, 70))
    d.rectangle(_p(540, 360, 590, 380), fill=(190, 190, 190))  # Fork.
    d.line(_p(560, 380, 600, 470), fill=(190, 190, 190), width=6 * S)
    return _png(image)


DRAW = {"kedi_kanepe": draw_kedi_kanepe, "pasta": draw_pasta, "laptop": draw_laptop, "plaj": draw_plaj, "tekne": draw_tekne,
        "gunbatimi": draw_gunbatimi, "uyuyan_kedi": draw_uyuyan_kedi, "kutular": draw_kutular,
        "kedi_pencere": draw_kedi_pencere, "mantı": draw_manti}


# --- The manifest ------------------------------------------------------------------------------------------------------

class Manifest:
    """What this run made, saved after every step: `--remove` can clean up a run that stopped half way."""

    def __init__(self, path: Path, user: str):
        self.path = path
        self.data = {"user": user, "made_at": datetime.now(TZ).isoformat(timespec="seconds"), "chats": [], "entities": [],
                     "facts": [], "events": [], "groups": []}

    @classmethod
    def load(cls, path: Path) -> "Manifest | None":
        if not path.exists():
            return None
        found = cls(path, "")
        found.data = json.loads(path.read_text(encoding="utf-8"))
        return found

    def add(self, key: str, value) -> None:
        if value not in self.data[key]:
            self.data[key].append(value)
        self.save()

    def save(self) -> None:
        self.path.write_text(json.dumps(self.data, ensure_ascii=False, indent=1), encoding="utf-8")


# --- Making ------------------------------------------------------------------------------------------------------------

async def pick_user(conn) -> str:
    user = await conn.fetchval("select user_id from orion_chats group by user_id order by count(*) desc limit 1")
    if not user:
        raise SystemExit("No user with chats: nothing to seed for.")
    return user


async def make_chats(conn, user: str, manifest: Manifest) -> dict:
    """The chats and their messages with their real timestamps; {chat key: {"id", "turns": [(user msg id, answer id, time)]}}."""
    from orion.kernel import registry
    await registry._ensure_tables(conn)
    made = {}
    for chat in CHATS:
        chat_id = str(uuid.uuid4())
        manifest.add("chats", chat_id)
        turns, moment = [], chat["start"]
        async with conn.transaction():
            await conn.execute("insert into orion_chats (id, user_id, title, created_at, updated_at) values ($1, $2, $3, $4, $4)",
                               chat_id, user, chat["title"], moment)
            for number, (question, answer) in enumerate(chat["turns"]):
                turn_id = str(uuid.uuid4())
                asked, answered = moment + timedelta(minutes=number * 6), moment + timedelta(minutes=number * 6, seconds=48)
                ids = []
                for role, text, when in (("user", question, asked), ("assistant", answer, answered)):
                    message = {"role": role, "content": text, "turn_id": turn_id}
                    plain, folded = registry._search_columns(message)
                    ids.append(await conn.fetchval(
                        "insert into orion_chat_messages (chat_id, role, content_json, plain_text, search_text, turn_id, created_at) "
                        "values ($1, $2, $3, $4, $5, $6, $7) returning id", chat_id, role, json.dumps(message), plain, folded,
                        turn_id, when))
                turns.append((ids[0], ids[1], asked))
            await conn.execute("update orion_chats set updated_at = $2 where id = $1", chat_id, turns[-1][2] + timedelta(seconds=48))
        made[chat["key"]] = {"id": chat_id, "turns": turns, "ended": turns[-1][2] + timedelta(seconds=48)}
        print(f"  chat: {chat['title']} ({len(turns)} turns)")
    return made


async def stamp_group(conn, user: str, group: str | None, when: datetime) -> None:
    if group:
        await conn.execute("update orion_memory set created_at = $3 where user_id = $1 and group_id = $2", user, group, when)


async def make_media(conn, user: str, settings, manifest: Manifest) -> dict:
    """Notes, images and files as memories; {key: group id}."""
    from orion.kernel import memory
    groups = {}
    for key, title, text, when, source in NOTES:
        result = await memory.add_note_to(user, text, settings, source_kind=source, title=title)
        groups[key] = result["group_id"]
        manifest.add("groups", result["group_id"])
        await stamp_group(conn, user, result["group_id"], when)
        print(f"  note: {title}")
    for key, name, caption, when in IMAGES:
        result = await memory.add_file(user, name, "image/png", DRAW[key](), settings, caption=caption)
        groups[key] = result["group_id"]
        manifest.add("groups", result["group_id"])
        await stamp_group(conn, user, result["group_id"], when)
        print(f"  image: {name}" + (f" ({result['warnings'][0]})" if result.get("warnings") else ""))
    for key, name, text, caption, when in FILES:
        result = await memory.add_file(user, name, "text/markdown" if name.endswith(".md") else "text/plain", text.encode("utf-8"),
                                       settings, caption=caption)
        if result.get("job"):  # A long text is embedded in the background: wait for it.
            for _ in range(240):
                state = await conn.fetchval("select state from orion_memory_media_jobs where user_id = $1 and group_id = $2 "
                                            "order by started_at desc limit 1", user, result["group_id"])
                if state in (None, "done", "failed", "cancelled"):
                    break
                await asyncio.sleep(1)
        groups[key] = result["group_id"]
        manifest.add("groups", result["group_id"])
        await stamp_group(conn, user, result["group_id"], when)
        print(f"  file: {name}")
    return groups


async def make_memory(conn, user: str, settings, chats: dict, media: dict, manifest: Manifest) -> None:
    from orion.kernel import memory_entities as me, memory_events as ev
    entities, first_seen = {}, {}
    for key, (name, kind, relation, aliases) in ENTITIES.items():
        entities[key] = await me.create_entity(user, settings, name, kind, relation, aliases)
        manifest.add("entities", entities[key]["id"])
    self_entity = None
    facts, event_ids = {}, {}

    def source_of(chat_key: str, turn: int) -> dict:
        asked, answered, _ = chats[chat_key]["turns"][turn]
        return {"chat_id": chats[chat_key]["id"], "first_message": asked, "last_message": answered}

    def moment_of(chat_key: str, turn: int) -> datetime:
        return chats[chat_key]["turns"][turn][2]

    for chat in CHATS:
        key = chat["key"]
        for fact_key, who, category, sentence, chat_key, turn, since, repeats, replaces in FACTS:
            if chat_key != key:
                continue
            if who == "ben":
                async with me._db() as c:
                    entity, _ = await me.resolve_entity(c, user, "Ben")
                self_entity = entity
            else:
                entity = entities[who]
            when = moment_of(key, turn)
            if replaces:
                done = await me.supersede(user, settings, facts[replaces]["id"], sentence, origin="model",
                                          source=source_of(key, turn), since=since, recurring=repeats)
            else:
                done = await me.add_fact(user, settings, entity, category, sentence, origin="model", source=source_of(key, turn),
                                         since=since, recurring=repeats)
            fact = done.get("fact") or {"id": done["same_as"]}
            facts[fact_key] = fact
            manifest.add("facts", fact["id"])
            if replaces:
                old = facts[replaces]["id"]
                await conn.execute("update orion_mem_fact set expired_at = $3 where user_id = $1 and id = $2", user, old, when)
            await conn.execute("update orion_mem_fact set recorded_at = $3 where user_id = $1 and id = $2", user, fact["id"], when)
            await conn.execute("update orion_mem_source set created_at = $3 where user_id = $1 and target_type = 'fact' and target_id = $2",
                               user, fact["id"], when)
            group = await conn.fetchval("select group_id from orion_mem_fact where id = $1", fact["id"])
            if group:
                manifest.add("groups", group)
                await stamp_group(conn, user, group, when)
            first_seen.setdefault("self" if who == "ben" else who, when)
            print(f"  fact [{category}] {sentence}" + ("  (versiyon)" if replaces else ""))
        for (event_key, chat_key, turn, title, body, day, end, precision, said, mood, people, place, photos) in EVENTS:
            if chat_key != key:
                continue
            when = moment_of(key, turn)
            resolved = ev.resolve_date(day, end, precision, said, reference=when)
            expected = date.fromisoformat(day if len(day) == 10 else day + "-01")
            if resolved["on"] != expected:
                raise SystemExit(f"Event {event_key}: the date resolved to {resolved['on']}, expected {expected} ({resolved['note']})")
            names = ["Ben" if p == "ben" else entities[p]["name"] for p in people]
            where = entities[place]["name"] if place else None
            done = await ev.add_event(user, settings, title, resolved, body=body, mood=mood, participants=names, place=where,
                                      items=[media[p] for p in photos], origin="model", source=source_of(key, turn))
            event = done.get("event") or {"id": done["same_as"]}
            event_ids[event_key] = event["id"]
            manifest.add("events", event["id"])
            await conn.execute("update orion_mem_event set recorded_at = $3 where user_id = $1 and id = $2", user, event["id"], when)
            await conn.execute("update orion_mem_source set created_at = $3 where user_id = $1 and target_type = 'event' and target_id = $2",
                               user, event["id"], when)
            group = await conn.fetchval("select group_id from orion_mem_event where id = $1", event["id"])
            manifest.add("groups", group)
            await stamp_group(conn, user, group, when)
            for p in people + ([place] if place else []):
                first_seen.setdefault("self" if p == "ben" else p, when)
            print(f"  event {resolved['on']} {title}")
    for key, when in first_seen.items():  # An entity is as old as the first time it was spoken of.
        entity = self_entity if key == "self" else entities[key]
        if entity:
            await conn.execute("update orion_mem_entity set created_at = $3 where user_id = $1 and id = $2", user, entity["id"], when)
    if self_entity:
        manifest.add("entities", self_entity["id"])


async def ensure_v2(user: str, settings):
    if getattr(settings, "memory_v2", False):
        return settings
    from orion.kernel.config import get_runtime_settings, upsert_setting_overrides
    await upsert_setting_overrides(user, {"memory_v2": "true"})
    print("  memory_v2 turned on for the user (restart the Hub or wait for the settings cache if the page still shows it off)")
    return await get_runtime_settings(None, user)


async def seed(user: str | None, manifest_path: Path, force: bool) -> None:
    from orion.kernel import memory, memory_collections, memory_profile
    from orion.kernel.config import get_runtime_settings
    if manifest_path.exists():
        raise SystemExit(f"{manifest_path.name} exists: run --remove first (or delete it if the data is gone).")
    conn = await memory._connect()
    try:
        await memory.ensure_memory_table(conn)
        user = user or await pick_user(conn)
        settings = await ensure_v2(user, await get_runtime_settings(None, user))
        others = await conn.fetchval("select count(*) from orion_memory where user_id = $1", user)
        if others and not force:
            raise SystemExit(f"User {user!r} already has {others} memory rows. Wipe the memory first, or pass --force to add the seed beside it.")
        print(f"User: {user}")
        manifest = Manifest(manifest_path, user)
        manifest.save()
        await memory_collections.ensure_user(conn, user)
        print("Chats")
        chats = await make_chats(conn, user, manifest)
        print("Notes, images, files")
        media = await make_media(conn, user, settings, manifest)
        print("Entities, facts, events")
        await make_memory(conn, user, settings, chats, media, manifest)
        print("Chat index")
        block = await memory_profile.build(user, settings)
        print("Profile:\n" + (block or {}).get("text", ""))
    finally:
        await conn.close()
    print(f"Done. Remove with: --remove (manifest: {manifest_path})")


async def remove(manifest_path: Path) -> None:
    from orion.kernel import memory, memory_profile, memory_trash
    manifest = Manifest.load(manifest_path)
    if manifest is None:
        raise SystemExit(f"No manifest at {manifest_path}: nothing to remove.")
    data, user = manifest.data, manifest.data["user"]
    conn = await memory._connect()
    try:
        await memory.ensure_memory_table(conn)
        groups = list(data["groups"])
        for table in ("orion_mem_fact", "orion_mem_event"):
            ids = data["facts" if table.endswith("fact") else "events"]
            if ids:
                groups += [r["group_id"] for r in await conn.fetch(
                    f"select group_id from {table} where user_id = $1 and id = any($2::bigint[]) and group_id is not null", user, ids)]
        groups = sorted(set(groups))
        if groups:
            for start in range(0, len(groups), 150):
                trashed = await memory_trash.trash_groups(user, groups[start:start + 150])
                if trashed["trash_id"]:
                    await memory_trash.purge(user, [trashed["trash_id"]])
        for table, key in (("orion_mem_source", "facts"), ("orion_mem_source", "events")):
            if data[key]:
                await conn.execute(f"delete from {table} where user_id = $1 and target_type = $2 and target_id = any($3::bigint[])",
                                   user, key[:-1], data[key])
        if data["events"]:
            await conn.execute("delete from orion_mem_link where user_id = $1 and from_type = 'event' and from_id = any($2::bigint[])",
                               user, data["events"])
        if data["facts"]:
            await conn.execute("update orion_mem_profile set pinned = (select coalesce(jsonb_agg(p), '[]') from jsonb_array_elements(pinned) p "
                               "where (p->>'fact_id') is null or (p->>'fact_id')::bigint <> all($2::bigint[])) where user_id = $1",
                               user, data["facts"])
            await conn.execute("delete from orion_mem_fact where user_id = $1 and id = any($2::bigint[])", user, data["facts"])
        if data["events"]:
            await conn.execute("delete from orion_mem_event where user_id = $1 and id = any($2::bigint[])", user, data["events"])
        if data["entities"]:
            await conn.execute("delete from orion_mem_entity_merge where user_id = $1 and (from_id = any($2::bigint[]) or into_id = any($2::bigint[]))",
                               user, data["entities"])
            left = await conn.fetch("select e.id from orion_mem_entity e where e.user_id = $1 and e.id = any($2::bigint[]) and not exists "
                                    "(select 1 from orion_mem_fact f where f.entity_id = e.id)", user, data["entities"])
            await conn.execute("delete from orion_mem_entity where user_id = $1 and id = any($2::bigint[])", user, [r["id"] for r in left])
        for chat_id in data["chats"]:
            await conn.execute("delete from orion_mem_chat_state where user_id = $1 and chat_id = $2", user, chat_id)
            await conn.execute("delete from orion_chat_messages where chat_id = $1", chat_id)
            await conn.execute("delete from orion_chats where id = $1 and user_id = $2", chat_id, user)
        await memory_profile.forget_user(conn, user)
    finally:
        await conn.close()
    manifest_path.unlink()
    print(f"Removed: {len(data['chats'])} chats, {len(groups)} memories, {len(data['facts'])} facts, {len(data['events'])} events.")


# --- Checking ----------------------------------------------------------------------------------------------------------

async def check(user: str | None) -> None:
    from orion.kernel import memory, memory_entities as me, memory_groups, memory_profile
    from orion.kernel.config import get_runtime_settings
    conn = await memory._connect()
    try:
        await memory.ensure_memory_table(conn)
        user = user or (Manifest.load(MANIFEST).data["user"] if MANIFEST.exists() else await pick_user(conn))
        settings = await get_runtime_settings(None, user)
        print(f"User {user}, memory_v2 {settings.memory_v2}")
        for name in ("ben", "yasadiklarim", "belgeler"):
            found = await memory_groups.list_groups(user, settings, "", 1, 0, collections=[name])
            print(f"  {name}: {found.get('matching', found.get('total', '?'))}")
        print("  all:", (await memory_groups.list_groups(user, settings, "", 1, 0)).get("matching"))
        for query, scope in (("kedim ne yer", None), ("annemin doğum günü", None), ("geçen yaz tatil", None),
                             ("mantı tarifi", None), ("taşındım", ["yasadiklarim"])):
            found = await memory_groups.list_groups(user, settings, query, 4, 0, collections=scope)
            print(f"  '{query}'" + (f" in {scope}" if scope else ""))
            for card in found["groups"][:4]:
                text = (card.get("text") or card.get("title") or "")[:110].replace("\n", " ")
                print(f"      {card.get('score', '')!s:>6} {card.get('source_kind', card.get('kind', ''))}: {text}")
        print((await memory_profile.get(user, settings)).get("text", ""))
        entity = (await me.list_entities(user, settings))
        print("  entities:", ", ".join(f"{e['name']}({e['facts']})" for e in entity))
    finally:
        await conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--remove", action="store_true", help="remove everything the seed made (reads the manifest)")
    parser.add_argument("--check", action="store_true", help="print counts and sample searches")
    parser.add_argument("--user", help="user id (default: the user with most chats)")
    parser.add_argument("--manifest", default=str(MANIFEST))
    parser.add_argument("--force", action="store_true", help="seed even when the user's memory is not empty")
    args = parser.parse_args()
    bootstrap()
    if args.check:
        asyncio.run(check(args.user))
    elif args.remove:
        asyncio.run(remove(Path(args.manifest)))
    else:
        asyncio.run(seed(args.user, Path(args.manifest), args.force))


if __name__ == "__main__":
    main()
