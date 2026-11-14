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
    result = {"kayitlar": [{"tarih": row["date"], "metin": row["text"]} for row in found["rows"]]}
    if not result["kayitlar"]:
        result["not"] = "Hafızada ilgili kayıt bulunamadı."
    if found["other_spaces"]:
        result["uyari"] = f"{found['other_spaces']} kayıt başka embedding modeliyle kaydedilmiş, aranmadı"
    return result
