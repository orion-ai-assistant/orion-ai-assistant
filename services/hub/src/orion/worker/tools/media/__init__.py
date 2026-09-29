from .._base import Category
from .tools import MEDIA_CONTROL

CATEGORY = Category(id='media', name='YouTube ve YouTube Music',
    description='Açık YouTube ve YouTube Music sekmelerinde oynatmayı tek araçla yönet.',
    tools=(MEDIA_CONTROL,), requires_browser=True)
