from datetime import datetime
from zoneinfo import ZoneInfo

from pydantic import Field

from ._base import Category, Tool, ToolContext, ToolInput


class TimeInput(ToolInput):
    timezone: str = Field(default="Europe/Istanbul", description="IANA time zone, e.g. Europe/Istanbul or UTC")


async def get_current_time(args: TimeInput, context: ToolContext) -> dict:
    now = datetime.now(ZoneInfo(args.timezone))
    return {"date": now.date().isoformat(), "time": now.time().isoformat(timespec="seconds"),
            "utc_offset": now.strftime("%z"), "timezone": args.timezone, "iso": now.isoformat()}


CATEGORY = Category(
    id="datetime", name="Tarih ve saat", description="Güncel tarih ve saat bilgileri.",
    tools=(Tool(id="get_current_time", name="Güncel saat", description="Get the actual current date and time in an IANA timezone.",
                input_model=TimeInput, handler=get_current_time),),
)
