from .._base import Category
from .tools import YTMUSIC_CONTROL

CATEGORY = Category(
    id='ytmusic',
    name='YouTube Music',
    description='Bağlı tarayıcınızda YouTube Music şarkılarını ara, çal, duraklat ve parça geçişlerini (ileri/geri) yönet.',
    tools=(YTMUSIC_CONTROL,),
    requires_browser=True
)
