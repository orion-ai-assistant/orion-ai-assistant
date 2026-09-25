from pydantic import BaseModel, ConfigDict, Field


class ToolSelection(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    categories: dict[str, bool] = Field(default_factory=dict)
    functions: dict[str, bool] = Field(default_factory=dict)


def default_tool_selection() -> ToolSelection:
    # New plugins never become enabled merely by being installed.
    return ToolSelection(categories={"datetime": True}, functions={"get_current_time": True})
