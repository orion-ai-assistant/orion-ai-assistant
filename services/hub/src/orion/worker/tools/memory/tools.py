from typing import Literal

from pydantic import Field

from .._base import ToolContext, ToolInput

KINDS = {"hepsi": "all", "metin": "text", "görsel": "image", "ses": "audio", "video": "video"}
KIND_LABELS = {"image": "görsel", "audio": "ses kaydı", "video": "video"}
TRACKS = {"frames": "görüntü", "audio": "ses", "both": "birlikte"}
MATCH_LABELS = {"meaning": "anlam", "both": "anlam+metin", "text": "metin", "similar": "benzer yazım"}
GROUP_NOTE = ("'birlikte_kaydedilenler' aynı anda kaydedilen diğer parçalardır (ör. bir görselin yanındaki konuşma); "
              "'ayni_kayittan' o id'li sonuçla birlikte kaydedildiğini söyler; 'parca' uzun bir metnin, sesin ya da "
              "videonun kaçıncı parçası olduğunu, 'zaman' o parçanın dakikasını söyler. Önceki/sonraki parçalar "
              "için hafiza_baglami'yı komsu=1-3 ile, tamamı ya da kaydedildiği sohbet için kaydın id'siyle çağır.")
MEDIA_NOTE = ("Görsel/ses/video kayıtlarında şimdilik etiketini ve tarihini görüyorsun. İçeriğine bakman gerekirse "
              "hafizadaki_medyayi_ac aracını bu kayıtların id'leriyle çağır (bir seferde en fazla 3); bir video "
              "parçası birkaç kare ve o parçanın sesiyle açılır.")


class MemoryInput(ToolInput):
    sorgu: str = Field(default="", max_length=500,
                       description="Aranacak şey, kısa bir cümle (ör. 'kullanıcının adı'). Boş bırakılırsa 'tur' "
                                   "türündeki en yeni kayıtlar listelenir.")
    tur: Literal["hepsi", "metin", "görsel", "ses", "video"] = Field(
        default="hepsi", description="Yalnız bu türdeki kayıtlar: metin, görsel, ses, video ya da hepsi")
    ek_sorgular: list[str] = Field(default_factory=list, max_length=4,
                                   description="İsteğe bağlı: 'sorgu' ile birlikte aranacak başka kısa metinler; "
                                               "hepsinin ortak anlamına yakın kayıtlar gelir.")
    haric: list[str] = Field(default_factory=list, max_length=5,
                             description="Bunlara yakın ya da bunları içeren kayıtlar sonuçtan çıkarılır "
                                         "(ör. ['köpek']).")
    sayfa: int = Field(default=1, ge=1, le=100,
                       description="Sonuç sayfası: 1 = en iyi 1-10, 2 = 11-20... İlk sayfada yoksa ve 'daha_var' "
                                   "true ise sonraki sayfaya bak.")


def _clock(seconds: float) -> str:
    from orion.kernel.memory import clock
    return clock(seconds)


def _row(row: dict, seen: dict | None = None) -> dict:
    kind = row.get("kind", "text")
    item = {"id": row["id"], "tarih": row["date"]}
    if kind in KIND_LABELS:
        item.update(tur=KIND_LABELS[kind], etiket=row["text"])
    else:
        item["metin"] = row["text"]
    if row.get("score") is not None:
        item["skor"] = row["score"]
    if row.get("match"):
        item["eslesme"] = MATCH_LABELS.get(row["match"], row["match"])
    if row.get("above") is not None:
        item["ortalamadan"] = row["above"]  # Score minus this query's typical score against all memories.
    if row.get("track") in TRACKS:
        item["iz"] = TRACKS[row["track"]]
    if row.get("span"):
        item["zaman"] = f"{_clock(row['span'][0])}–{_clock(row['span'][1])}"
    pieces = row.get("group") or {}
    if row.get("whole"):
        item["tamami"] = True  # The item's whole vector matched and none of its pieces is on this page.
        if pieces.get("pieces"):
            item["parca_sayisi"] = pieces["pieces"]
    elif (pieces.get("pieces") or 0) > 1:
        item["parca"] = f"{pieces['piece']}/{pieces['pieces']}"
        if row.get("via_whole"):
            item["eslesme_notu"] = "kaydın tamamı da eşleşti; en iyi eşleşen parçası bu"
    group = row.get("group")
    if group and group["parts"] > 1:
        first = None if seen is None else seen.setdefault(group["key"], row["id"])
        if first not in (None, row["id"]):
            item["ayni_kayittan"] = first  # Saved with that result above: its list says what else is there.
        elif group["source_kind"] == "file" and kind == "text":
            item["dosya"] = group["title"] or ""
            item.setdefault("parca", f"{group['index']}/{group['parts']}")
        else:
            item["birlikte_kaydedilenler"] = [
                {"id": other["id"], "tur": KIND_LABELS.get(other["kind"], "metin"), "ozet": other["text"]}
                for other in group["others"]]
            if group["parts"] - 1 > len(group["others"]):
                item["grupta_parca"] = group["parts"]
    return item


async def hafizada_ara(args: MemoryInput, context: ToolContext) -> dict:
    from orion.kernel import memory
    from orion.kernel.config import get_runtime_settings
    query = args.sorgu.strip()
    try:
        found = await memory.search(context.user_id, query, await get_runtime_settings(None, context.user_id),
                                    page=args.sayfa, kind=KINDS[args.tur], exclude=args.haric,
                                    also=args.ek_sorgular)
    except memory.MemoryUnavailable as exc:
        return {"hata": str(exc)}
    seen: dict = {}
    result = {"sayfa": args.sayfa, "kayitlar": [_row(row, seen) for row in found["rows"]], "daha_var": found["more"]}
    if query and result["kayitlar"]:
        result["siralama"] = ("En iyi eşleşme önce. 'skor' anlam benzerliğidir (0-1); görsel/ses için 0.2-0.4 "
                              "normaldir, sıraya bak. 'ortalamadan': skorun bu sorgunun tüm kayıtlara verdiği "
                              "ortalama skordan farkı (büyükse öne çıkıyor). Metinde geçen kayıtlar öne alınır.")
    elif not query:
        result["siralama"] = "En yeni kayıt önce."
    if any(row.get("kind", "text") != "text" for row in found["rows"]):
        result["medya_notu"] = MEDIA_NOTE
    if any((row.get("group") or {}).get("parts", 1) > 1 for row in found["rows"]):
        result["grup_notu"] = GROUP_NOTE
    if found.get("exclude_terms"):
        result["haric"] = {"terimler": found["exclude_terms"], "cikarilan": found["excluded"]}
    if not result["kayitlar"]:
        result["not"] = "Bu sayfada kayıt yok." if args.sayfa > 1 else "Hafızada bu aramayla kayıt bulunamadı."
    if found.get("reason"):
        result["uyari_anlam"] = f"Anlamca arama yapılamadı, yalnız metin eşleşmeleri: {found['reason']}"
    if found["other_spaces"]:
        result["uyari"] = (f"{found['other_spaces']} kayıt başka embedding modeliyle kaydedilmiş; onlarda yalnız "
                           "metin eşleşmesi aranır")
    return result


class MemoryAddInput(ToolInput):
    metin: str = Field(min_length=1, max_length=4000, description="Hatırlanacak bilgi, kendi başına anlaşılır tek bir cümle "
                                                                  "(ör. 'Kullanıcının kızının adı Elif.')")


class MemoryDeleteInput(ToolInput):
    idler: list[int] = Field(min_length=1, max_length=20, description="Silinecek kayıtların id'leri; hafizada_ara sonucundaki 'id' değerleri")


class MemoryOpenInput(ToolInput):
    idler: list[int] = Field(min_length=1, max_length=3,
                             description="Açılacak görsel/ses/video kayıtlarının id'leri (hafizada_ara sonucundaki "
                                         "'id'), en fazla 3. Bir video parçası birkaç kare ve sesiyle açılır.")


async def hafizaya_ekle(args: MemoryAddInput, context: ToolContext) -> dict:
    from orion.kernel import memory
    from orion.kernel.config import get_runtime_settings
    try:
        added = await memory.add_note(context.user_id, args.metin, await get_runtime_settings(None, context.user_id),
                                      source_kind="assistant")
    except (memory.MemoryUnavailable, ValueError) as exc:
        return {"hata": str(exc)}
    return {"eklendi": args.metin} if added else {"not": "Bu bilgi zaten hafızada vardı.", "metin": args.metin}


async def hafizadan_sil(args: MemoryDeleteInput, context: ToolContext) -> dict:
    from orion.kernel import memory
    try:
        deleted = await memory.delete_items(context.user_id, args.idler)
    except memory.MemoryUnavailable as exc:
        return {"hata": str(exc)}
    if not deleted:
        return {"not": "Bu id'lerle kayıt bulunamadı, hiçbir şey silinmedi."}
    return {"silinenler": [{"id": row["id"], "metin": row["text"]} for row in deleted],
            "talimat": "Kullanıcıya neyi sildiğini kısaca söyle."}


def attachment_of(row: dict, chat_id: str) -> dict:
    """An opened memory file as the chat shows a user's attachment: {media_id, mime_type, name, memory_id}, a
    reference to the one stored copy (linked to the chat; `media_path` loads it as a user's attachment), never its
    bytes."""
    import mimetypes
    named = (row.get("text") or "").split(" — ")[0].strip()
    if not named or "." not in named[-6:] or len(named) > 120:
        ext = mimetypes.guess_extension(row.get("media_mime") or "") or ""
        named = f"{KIND_LABELS.get(row['kind'], 'Dosya').capitalize()} {row['id']}{ext}"
    from orion.kernel.media_store import media_path
    return {"media_id": row["media_id"], "mime_type": row.get("media_mime") or "application/octet-stream",
            "name": named, "memory_id": row["id"], "media_path": media_path(chat_id, row["media_id"])}


async def hafizadaki_medyayi_ac(args: MemoryOpenInput, context: ToolContext) -> dict:
    """Shows memory images/audio to the model. A tool message cannot carry media on OpenAI-compatible APIs, so the
    files go in `_medya`: the tool runner keeps them on the tool message as references and hands them to the
    model in a user message right after it, on this and every later turn (see tool_history.media_message)."""
    from orion.kernel import memory
    from orion.kernel.config import get_runtime_settings
    settings = await get_runtime_settings(None, context.user_id)
    can = await memory.chat_model_media(settings)
    kinds = {kind for kind in ("image", "audio") if can[kind]}
    try:
        rows = await memory.open_media(context.user_id, context.chat_id, args.idler, kinds)
    except memory.MemoryUnavailable as exc:
        return {"hata": str(exc)}
    opened, closed, media, attachments = [], [], [], []
    for row in rows:
        entry = {"id": row["id"], "tur": KIND_LABELS.get(row["kind"], "metin"), "etiket": row["text"]}
        if row["opened"]:
            shown = row.get("media") or []
            if row["kind"] == "video" or len(shown) > 1:
                entry["acilan"] = ", ".join(
                    f"{sum(1 for m in shown if m['kind'] == k)} {'kare' if k == 'image' else 'ses'}"
                    for k in ("image", "audio") if any(m["kind"] == k for m in shown))
            opened.append(entry)
            attachments.append(attachment_of(row, context.chat_id))
            for m in shown:
                media.append({"memory_id": row["id"], "kind": m["kind"], "media_id": m["media_id"],
                              "mime_type": m["mime"], "label": row["text"][:300]})
        elif not row["media_id"]:
            closed.append({**entry, "neden": "Bu kayıt metin; içeriği yukarıda."})
        elif row.get("missing"):
            closed.append({**entry, "neden": "Dosyası artık yok; yalnız etiketi var."})
        elif row.get("failed"):
            closed.append({**entry, "neden": "Dosya açılamadı; yalnız etiketi var."})
        else:
            sense = "sesi dinleyemiyor" if row["kind"] == "audio" else "görselleri göremiyor"
            closed.append({**entry, "neden": f"Şu anki sohbet modeli {sense}; yalnız etiketi var."})
    result = {}
    if opened:
        result["acilanlar"] = opened
        result["not"] = ("Açılan dosyalar bu sonuçtan hemen sonra gelen kullanıcı mesajında (onu kullanıcı yazmadı, "
                         "araç ekledi) ve sohbette kalır. Onlara bakarak cevap ver.")
        result["_medya"] = media
        result["_ekler"] = attachments  # The tool runner shows them to the user under this turn.
    if closed:
        result["acilamayanlar"] = closed
    missing = [i for i in dict.fromkeys(args.idler) if i not in {row["id"] for row in rows}]
    if missing:
        result["bulunamayanlar"] = missing
    return result


class MemoryContextInput(ToolInput):
    id: int = Field(description="hafizada_ara sonucundaki bir kaydın 'id' değeri")
    sohbet: bool = Field(default=False, description="true: kaydın geldiği sohbetteki mesajlar da gelsin")
    mesaj_sayisi: int = Field(default=6, ge=1, le=20, description="Sohbetten kaç mesaj gelsin")
    sohbet_konumu: Literal["cevre", "bas"] = Field(
        default="cevre", description="cevre: kaydedilen mesaj ve çevresi; bas: sohbetin ilk mesajları")
    baslangic: int = Field(default=1, ge=1, le=1000,
                           description="Uzun bir kaydın kaçıncı parçasından devam edilsin (önceki sonuçta 'kalan' "
                                       "varsa)")
    komsu: int = Field(default=0, ge=0, le=5,
                       description="0: kaydın tamamı. 1-5: yalnız bu parça ve önceki/sonraki bu kadar parça (uzun bir "
                                   "metin, ses ya da videonun eşleşen yerinin çevresi)")


def _context_part(part: dict) -> dict:
    item = {"id": part["id"], "sira": part["index"], "tur": KIND_LABELS.get(part["kind"], "metin"),
            ("metin" if part["kind"] == "text" else "etiket"): part["text"]}
    if part.get("pieces"):
        item["parca"] = f"{part['piece']}/{part['pieces']}"
    if part.get("span_start") is not None:
        item["zaman"] = f"{_clock(part['span_start'])}–{_clock(part['span_end'])}"
    if part.get("track") in TRACKS:
        item["iz"] = TRACKS[part["track"]]
    if part.get("caption"):
        item["aciklama"] = part["caption"]
    if part.get("asked"):
        item["sorulan"] = True
    return item


CHAT_STATES = {
    "unknown": "Bu kaydın hangi sohbetten geldiği bilinmiyor (not, dosya ya da eski bir kayıt).",
    "deleted": "Kaydın geldiği sohbet silinmiş; yalnız hafızadaki metin duruyor.",
    "cleared": "Kaydın geldiği sohbet temizlenmiş; o mesajlar artık yok.",
}
ROLES = {"user": "kullanıcı", "assistant": "asistan"}
SOURCES = {"chat_save": "sohbetten kaydedildi", "file": "dosyadan eklendi", "manual": "not olarak eklendi",
           "assistant": "asistan ekledi"}


async def hafiza_baglami(args: MemoryContextInput, context: ToolContext) -> dict:
    """A memory row's whole group (what was saved with it, full texts) and, on request, the conversation it was
    saved from."""
    from orion.kernel import memory, memory_groups
    from orion.kernel.config import get_runtime_settings
    try:
        found = await memory_groups.memory_context(
            context.user_id, args.id, await get_runtime_settings(None, context.user_id), with_chat=args.sohbet,
            count=args.mesaj_sayisi, where="start" if args.sohbet_konumu == "bas" else "around",
            start=args.baslangic - 1, neighbours=args.komsu)
    except memory.MemoryUnavailable as exc:
        return {"hata": str(exc)}
    if found is None:
        return {"hata": "Bu id ile hafızada kayıt yok."}
    result = {"id": args.id, "kaynak": SOURCES.get(found["source_kind"], found["source_kind"]),
              "tarih": found["date"], "parca_sayisi": found["part_count"],
              "parcalar": [_context_part(part) for part in found["parts"]]}
    if found.get("neighbours"):
        result["kapsam"] = f"yalnız {args.id} ve önceki/sonraki {found['neighbours']} parça"
    if found["title"]:
        result["dosya" if found["source_kind"] == "file" else "sohbet_basligi"] = found["title"]
    if found["rest"]:
        result["kalan"] = (f"{found['rest']} parça daha var; devamı için baslangic="
                           f"{found['parts'][-1]['index'] + 1} ile tekrar çağır.")
    if any(part["kind"] != "text" for part in found["parts"]):
        result["medya_notu"] = "Görsel/sesin içeriğine bakmak için hafizadaki_medyayi_ac aracını kullan."
    chat = found.get("chat")
    if chat is not None:
        if chat["state"] == "ok":
            result["sohbet"] = {"baslik": chat.get("title"), "mesajlar": [
                {"rol": ROLES.get(m["role"], m["role"]), "metin": m["text"], "tarih": m["date"],
                 **({"kaydedilen": True} if m.get("saved") else {})} for m in chat["messages"]]}
        else:
            result["sohbet_durumu"] = CHAT_STATES[chat["state"]]
    return result
