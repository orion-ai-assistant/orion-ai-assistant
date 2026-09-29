from .._base import Category
from .tools import YTMUSIC_CONTROL

CATEGORY = Category(
    id='ytmusic',
    name='YouTube Music',
    description='Search and control YouTube Music playback in the connected browser.',
    tools=(YTMUSIC_CONTROL,),
    requires_browser=True
)
