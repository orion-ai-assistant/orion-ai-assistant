from .._base import Category, Tool
from .tools import AnalyzeTextInput, FindInTextInput, ReplaceInTextInput, analyze_text, find_in_text, replace_in_text

CATEGORY = Category(
    id="text", name="Text operations", description="Count, find, or replace text.",
    tools=(
        Tool(id="analyze_text", name="Analyze text",
             description="Count characters, words, and lines in the provided text.",
             input_model=AnalyzeTextInput, handler=analyze_text),
        Tool(id="find_in_text", name="Find in text",
             description="Find every occurrence of a query in the provided text and return its positions.",
             input_model=FindInTextInput, handler=find_in_text),
        Tool(id="replace_in_text", name="Replace in text",
             description="Replace occurrences of a search string in the provided text. Does not modify files.",
             input_model=ReplaceInTextInput, handler=replace_in_text),
    ),
)
