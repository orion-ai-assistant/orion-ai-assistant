from .._base import Category
from .tools import BROWSER_CONTROL, BROWSER_TABS

CATEGORY = Category(id='browser', name='Browser',
    description='Browse connected tabs and interact with pages.',
    tools=(BROWSER_TABS, BROWSER_CONTROL), requires_browser=True)
