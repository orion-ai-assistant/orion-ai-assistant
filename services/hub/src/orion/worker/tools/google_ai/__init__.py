from .._base import Category
from .tools import GOOGLE_AI_MODE

CATEGORY = Category(
    id="google_ai", name="Google AI Modu",
    description="Google Arama'nın AI Modu yanıtını ve kaynak bağlantılarını al (deneysel).",
    tools=(GOOGLE_AI_MODE,),
    requires_browser=True,
)
