from .._base import Category
from .tools import MEDIA_CONTROL

CATEGORY = Category(id='media', name='YouTube and YouTube Music',
    description='Control playback in open YouTube and YouTube Music tabs.',
    tools=(MEDIA_CONTROL,), requires_browser=True)
