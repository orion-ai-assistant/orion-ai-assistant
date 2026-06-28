# Architecture Guardrails

Bu dosyanin amaci: yeni ozellik eklerken kodun tek bir dosyada yama gibi birikmesini onlemek ve her degisiklikte mimari tutarliligi korumak.

## 1) Once Buraya Bak

Her yeni feature'dan once su 3 soruyu cevapla:

1. Bu degisiklik mevcut bir sorumlulugu genisletiyor mu, yoksa yeni bir sorumluluk mu getiriyor?
2. Bu degisiklik bir `contract` degisikligi gerektiriyor mu?
3. Bu degisiklik API davranisini mi, worker davranisini mi, yoksa ikisini birden mi etkiliyor?

Eger yeni sorumluluk varsa, mevcut fonksiyona if/else eklemek yerine yeni abstraction ekle.

## 2) Katman Kurallari

- `contracts/`:
  - Sadece DTO/schema/event/ortak sabitler.
  - Runtime bagimliligi, Redis/FastAPI/worker logic olmaz.
- `api/services/`:
  - Is akisi orchestrasyonu, pre-check, state transitions.
  - HTTP tasima detaylari en aza indirilmeli.
- `api/routes.py`:
  - Request/response baglama, endpoint semantigi, status code sinirlari.
- `worker/`:
  - Queue'dan alma, token emit etme, state update etme.
  - Event formati sadece `contracts.events` uzerinden.

## 3) OCP (Open/Closed) Prensibi Uygulama Rehberi

Hedef: mevcut davranisi bozacak if/except yamalari yerine genisletilebilir model.

- Yeni durum eklerken:
  - `Literal`/enum benzeri durumlar tek yerde tanimlanir.
  - Mesaj map'leri tek fonksiyonda tutulur.
- Control flow:
  - Exception-driven degil, sonuc-model driven (result object) tercih et.
- Yeni policy eklerken:
  - "bir yere daha if ekle" yerine yeni policy/fonksiyon ekleyip mevcut akisa bagla.

## 4) Contracts-First Gelistirme Akisi

Yeni davranis eklerken sira:

1. `contracts/` modelini tanimla/guncelle.
2. API service bu contract'i kullanacak sekilde is akisina entegre et.
3. Worker gerekiyorsa ayni contract ile event/state uret.
4. Route sadece endpoint semantigini baglasin.
5. README/docs guncelle.

## 5) Event Tasarimi Kurali

SSE event ailesi tek yerden yonetilir:

- `accepted`
- `token`
- `done`
- `error`

Hata durumunda mumkunse HTTP hata firlatmak yerine ayni event protokolunde anlamli `error` event uret.

## 6) User-Room Modeli Kurali

Bu projede global stream modeli kullanilir:

- Stream aboneligi: `GET /api/v1/chat/stream?user_id=...`
- Publish kanali: `room:user:<user_id>`
- Event icinde `chat_id` bulunur, client routing'i bununla yapar.

Yeni kod yazarken chat-room geri getirilmez; net bir ihtiyac varsa ayri RFC/karar kaydi acilir.

## 7) Degisiklik Checklist (PR Oncesi)

- [ ] Yeni davranis `contracts` ile tanimlandi mi?
- [ ] Service katmaninda patch hissi veren `if/except` birikimi olustu mu?
- [ ] Route katmani sadece tasima isi mi yapiyor?
- [ ] Worker event'leri tek event modeliyle mi cikiyor?
- [ ] README veya docs guncellendi mi?
- [ ] Degisen dosyalarda static/syntax hata yok mu?

## 8) Kotu / Iyi Ornek

Kotu:
- Tek bir fonksiyona tekrar tekrar ozel durum `if` eklemek.
- Service icinde HTTPException firlat/yakala ile is akisi kontrol etmek.

Iyi:
- Sonuc modeli (`...Result`) ile karar vermek.
- Mesaj map'ini tek yerde tutmak.
- Yeni durumu contract + mapping + orchestration ile eklemek.

## 9) Karar Kayit Formati

Mimari degisikliklerde kisa bir not ekle:

- Problem:
- Secilen yaklasim:
- Neden:
- Etkilenen katmanlar:
- Geriye donuk etkiler:

Bu kayitlari `docs/` altinda tut.
