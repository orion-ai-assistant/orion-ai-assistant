from typing import Literal

from pydantic import Field

from .._base import ToolContext, ToolInput

KINDS = {"hepsi": "all", "metin": "text", "görsel": "image", "ses": "audio"}
KIND_LABELS = {"image": "görsel", "audio": "ses kaydı"}
MATCH_LABELS = {"meaning": "anlam", "both": "anlam+metin", "text": "metin", "similar": "benzer yazım"}
MEDIA_NOTE = ("Görsel/ses kayıtlarında şimdilik etiketini ve tarihini görüyorsun. İçeriğine bakman gerekirse "
              "hafizadaki_medyayi_ac aracını bu kayıtların id'leriyle çağır (bir seferde en fazla 3).")


class MemoryInput(ToolInput):
    sorgu: str = Field(default="", max_length=500,
                       description="Aranacak şey, kısa bir cümle (ör. 'kullanıcının adı'). Boş bırakılırsa 'tur' "
                                   "türündeki en yeni kayıtlar listelenir.")
    tur: Literal["hepsi", "metin", "görsel", "ses"] = Field(
        default="hepsi", description="Yalnız bu türdeki kayıtlar: metin, görsel, ses ya da hepsi")
    sayfa: int = Field(default=1, ge=1, le=100,
                       description="Sonuç sayfası: 1 = en iyi 1-10, 2 = 11-20... İlk sayfada yoksa ve 'daha_var' "
                                   "true ise sonraki sayfaya bak.")


def _row(row: dict) -> dict:
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
    return item


async def hafizada_ara(args: MemoryInput, context: ToolContext) -> dict:
    from orion.kernel import memory
    from orion.kernel.config import get_runtime_settings
    query = args.sorgu.strip()
    try:
        found = await memory.search(context.user_id, query, await get_runtime_settings(None, context.user_id),
                                    page=args.sayfa, kind=KINDS[args.tur])
    except memory.MemoryUnavailable as exc:
        return {"hata": str(exc)}
    result = {"sayfa": args.sayfa, "kayitlar": [_row(row) for row in found["rows"]], "daha_var": found["more"]}
    if query and result["kayitlar"]:
        result["siralama"] = ("En iyi eşleşme önce. 'skor' anlam benzerliğidir (0-1); görsel/ses için 0.2-0.4 "
                              "normaldir, sıraya bak. Metinde geçen kayıtlar öne alınır.")
    elif not query:
        result["siralama"] = "En yeni kayıt önce."
    if any(row.get("kind", "text") != "text" for row in found["rows"]):
        result["medya_notu"] = MEDIA_NOTE
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
                             description="Açılacak görsel/ses kayıtlarının id'leri (hafizada_ara sonucundaki 'id'), en fazla 3")


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
    opened, closed, media = [], [], []
    for row in rows:
        entry = {"id": row["id"], "tur": KIND_LABELS.get(row["kind"], "metin"), "etiket": row["text"]}
        if row["opened"]:
            opened.append(entry)
            media.append({"memory_id": row["id"], "kind": row["kind"], "media_id": row["media_id"],
                          "mime_type": row["media_mime"], "label": row["text"][:300]})
        elif not row["media_id"]:
            closed.append({**entry, "neden": "Bu kayıt metin; içeriği yukarıda."})
        elif row.get("missing"):
            closed.append({**entry, "neden": "Dosyası artık yok; yalnız etiketi var."})
        else:
            sense = "görselleri göremiyor" if row["kind"] == "image" else "sesi dinleyemiyor"
            closed.append({**entry, "neden": f"Şu anki sohbet modeli {sense}; yalnız etiketi var."})
    result = {}
    if opened:
        result["acilanlar"] = opened
        result["not"] = ("Açılan dosyalar bu sonuçtan hemen sonra gelen kullanıcı mesajında (onu kullanıcı yazmadı, "
                         "araç ekledi) ve sohbette kalır. Onlara bakarak cevap ver.")
        result["_medya"] = media
    if closed:
        result["acilamayanlar"] = closed
    missing = [i for i in dict.fromkeys(args.idler) if i not in {row["id"] for row in rows}]
    if missing:
        result["bulunamayanlar"] = missing
    return result
