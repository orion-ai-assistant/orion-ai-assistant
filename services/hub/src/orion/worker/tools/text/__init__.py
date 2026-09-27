from .._base import Category, Tool
from .tools import AnalyzeTextInput, FindInTextInput, ReplaceInTextInput, analyze_text, find_in_text, replace_in_text

CATEGORY = Category(
    id="text", name="Metin işlemleri", description="Metni say, içinde ifade bul veya değiştir.",
    tools=(
        Tool(id="analyze_text", name="Metni analiz et",
             description="Verilen metindeki karakter, kelime ve satır sayısını hesaplar.",
             input_model=AnalyzeTextInput, handler=analyze_text),
        Tool(id="find_in_text", name="Metinde bul",
             description="Verilen metinde bir ifadenin geçtiği konumları bulur.",
             input_model=FindInTextInput, handler=find_in_text),
        Tool(id="replace_in_text", name="Metni değiştir",
             description="Verilen metindeki bir ifadeyi yenisiyle değiştirir; dosyalara dokunmaz.",
             input_model=ReplaceInTextInput, handler=replace_in_text),
    ),
)
