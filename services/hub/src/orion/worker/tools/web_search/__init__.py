from .._base import Category
from .research import RESEARCH_WEB

CATEGORY = Category(
    id="web_search", name="Web search",
    description="Research the web and read source pages.",
    tools=(RESEARCH_WEB,),
)
