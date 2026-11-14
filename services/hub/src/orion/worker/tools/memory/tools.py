from pydantic import Field

from .._base import ToolContext, ToolInput


class MemoryInput(ToolInput):
    sorgu: str = Field(min_length=1, max_length=500, description="Hafızada aranacak şey, kısa bir cümle (ör. 'kullanıcının adı')")


async def hafizada_ara(args: MemoryInput, context: ToolContext) -> dict:
    from orion.kernel import memory
    from orion.kernel.config import get_runtime_settings
    try:
        found = await memory.search(context.user_id, args.sorgu, await get_runtime_settings(None, context.user_id))
    except memory.MemoryUnavailable as exc:
        return {"hata": str(exc)}
    result = {"kayitlar": [{"id": row["id"], "tarih": row["date"], "metin": row["text"]} for row in found["rows"]]}
    if not result["kayitlar"]:
        result["not"] = "Hafızada ilgili kayıt bulunamadı."
    if found["other_spaces"]:
        result["uyari"] = f"{found['other_spaces']} kayıt başka embedding modeliyle kaydedilmiş, aranmadı"
    return result


class MemoryAddInput(ToolInput):
    metin: str = Field(min_length=1, max_length=4000, description="Hatırlanacak bilgi, kendi başına anlaşılır tek bir cümle "
                                                                  "(ör. 'Kullanıcının kızının adı Elif.')")


class MemoryDeleteInput(ToolInput):
    idler: list[int] = Field(min_length=1, max_length=20, description="Silinecek kayıtların id'leri; hafizada_ara sonucundaki 'id' değerleri")


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
