from .._base import Category
from .tools import YOUTUBE_CONTROL

CATEGORY = Category(id='youtube', name='YouTube',
    description='Search, open, and control YouTube videos in the connected browser.',
    tools=(YOUTUBE_CONTROL,), requires_browser=True)
