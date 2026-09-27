import re

from pydantic import Field

from .._base import ToolContext, ToolInput


class AnalyzeTextInput(ToolInput):
    text: str = Field(min_length=1, max_length=10000, description="Analiz edilecek metin")


async def analyze_text(args: AnalyzeTextInput, context: ToolContext) -> dict:
    return {
        "characters": len(args.text),
        "words": len(args.text.split()),
        "lines": args.text.count("\n") + 1,
    }


class FindInTextInput(ToolInput):
    text: str = Field(min_length=1, max_length=10000, description="Aranacak metin")
    query: str = Field(min_length=1, max_length=200, description="Bulunacak ifade")
    case_sensitive: bool = Field(default=False, description="Büyük-küçük harf ayrımı yapılsın mı?")


async def find_in_text(args: FindInTextInput, context: ToolContext) -> dict:
    matches = re.finditer(re.escape(args.query), args.text,
                          flags=0 if args.case_sensitive else re.IGNORECASE)
    positions = [match.start() for match in matches]
    return {"positions": positions,
            "case_sensitive": args.case_sensitive}


class ReplaceInTextInput(ToolInput):
    text: str = Field(min_length=1, max_length=10000, description="Değiştirilecek metin")
    search: str = Field(min_length=1, max_length=200, description="Aranacak ifade")
    replacement: str = Field(max_length=200, description="Yerine yazılacak ifade")
    max_replacements: int = Field(default=20, ge=1, le=20, description="En çok kaç eşleşme değiştirilecek?")


async def replace_in_text(args: ReplaceInTextInput, context: ToolContext) -> dict:
    count = min(args.text.count(args.search), args.max_replacements)
    return {"text": args.text.replace(args.search, args.replacement, args.max_replacements),
            "replacements": count}
