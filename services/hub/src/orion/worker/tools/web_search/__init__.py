from .._base import Category
from .tools import SEARCH_WEB

CATEGORY = Category(
    id="web_search", name="Web araması",
    description="DuckDuckGo üzerinden web kaynakları bul.",
    tools=(SEARCH_WEB,),
)
