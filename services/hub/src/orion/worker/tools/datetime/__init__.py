from .._base import Category, Tool
from .tools import TimeInput, get_current_time

CATEGORY = Category(
    id="datetime", name="Tarih ve saat", description="Güncel tarih ve saat bilgileri.",
    tools=(Tool(id="get_current_time", name="Güncel saat", description="Get the actual current date and time in an IANA timezone.",
                input_model=TimeInput, handler=get_current_time),),
)
