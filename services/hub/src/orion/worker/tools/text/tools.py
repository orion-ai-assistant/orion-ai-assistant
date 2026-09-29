import re

from pydantic import Field

from .._base import ToolContext, ToolInput


class AnalyzeTextInput(ToolInput):
    text: str = Field(min_length=1, max_length=10000, description="Text to analyze")


async def analyze_text(args: AnalyzeTextInput, context: ToolContext) -> dict:
    return {
        "characters": len(args.text),
        "words": len(args.text.split()),
        "lines": args.text.count("\n") + 1,
    }


class FindInTextInput(ToolInput):
    text: str = Field(min_length=1, max_length=10000, description="Text to search")
    query: str = Field(min_length=1, max_length=200, description="Phrase to find")
    case_sensitive: bool = Field(default=False, description="Whether matching is case sensitive")


async def find_in_text(args: FindInTextInput, context: ToolContext) -> dict:
    matches = re.finditer(re.escape(args.query), args.text,
                          flags=0 if args.case_sensitive else re.IGNORECASE)
    positions = [match.start() for match in matches]
    return {"positions": positions,
            "case_sensitive": args.case_sensitive}


class ReplaceInTextInput(ToolInput):
    text: str = Field(min_length=1, max_length=10000, description="Text to modify")
    search: str = Field(min_length=1, max_length=200, description="Phrase to replace")
    replacement: str = Field(max_length=200, description="Replacement phrase")
    max_replacements: int = Field(default=20, ge=1, le=20, description="Maximum number of replacements")


async def replace_in_text(args: ReplaceInTextInput, context: ToolContext) -> dict:
    count = min(args.text.count(args.search), args.max_replacements)
    return {"text": args.text.replace(args.search, args.replacement, args.max_replacements),
            "replacements": count}
