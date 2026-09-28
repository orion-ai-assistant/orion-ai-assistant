from .._base import Category
from .tools import BROWSER_CONTROL, BROWSER_TABS

CATEGORY = Category(id='browser', name='Tarayıcı',
    description='Bağlı tarayıcınızdaki açık sekmeleri görün; sayfa açın, okuyun ve etkileşim kurun.',
    tools=(BROWSER_TABS, BROWSER_CONTROL), requires_browser=True)
