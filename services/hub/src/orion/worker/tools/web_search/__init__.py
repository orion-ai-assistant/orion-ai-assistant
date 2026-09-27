from .._base import Category
from .research import RESEARCH_WEB

CATEGORY = Category(
    id="web_search", name="Web araması",
    description="Web'de araştır, kaynak sayfalarını oku.",
    tools=(RESEARCH_WEB,),
)
