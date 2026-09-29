from .._base import Category, Tool
from .tools import TimeInput, get_current_time

CATEGORY = Category(
    id="datetime", name="Date and time", description="Current date and time information.",
    tools=(Tool(id="get_current_time", name="Current time", description="Get the actual current date and time in an IANA timezone.",
                input_model=TimeInput, handler=get_current_time),),
)
