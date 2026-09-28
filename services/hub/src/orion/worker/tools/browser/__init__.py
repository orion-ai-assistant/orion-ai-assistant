from .._base import Category
from .tools import BROWSER_CONTROL

CATEGORY = Category(id='browser', name='Tarayıcı',
    description='Bağlı tarayıcınızda sayfa aç, oku ve etkileşim kur.',
    tools=(BROWSER_CONTROL,), requires_browser=True)
