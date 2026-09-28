from .._base import Category
from .tools import YOUTUBE_CONTROL

CATEGORY = Category(id='youtube', name='YouTube',
    description='Bağlı tarayıcınızda YouTube videosu ara, aç ve oynatmayı yönet.',
    tools=(YOUTUBE_CONTROL,), requires_browser=True)
