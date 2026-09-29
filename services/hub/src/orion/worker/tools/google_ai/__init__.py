from .._base import Category
from .tools import GOOGLE_AI_MODE

CATEGORY = Category(
    id="google_ai", name="Google AI Mode",
    description="Get Google Search AI Mode answers and source links (experimental).",
    tools=(GOOGLE_AI_MODE,),
    requires_browser=True,
)
