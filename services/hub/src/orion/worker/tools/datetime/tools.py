from datetime import datetime
from zoneinfo import ZoneInfo

from pydantic import Field

from .._base import ToolContext, ToolInput


class TimeInput(ToolInput):
    timezone: str = Field(default="Europe/Istanbul", description="IANA time zone, e.g. Europe/Istanbul or UTC")


async def get_current_time(args: TimeInput, context: ToolContext) -> dict:
    now = datetime.now(ZoneInfo(args.timezone))
    return {"date": now.date().isoformat(), "time": now.time().isoformat(timespec="seconds"),
            "utc_offset": now.strftime("%z"), "timezone": args.timezone, "iso": now.isoformat()}
